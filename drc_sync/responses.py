"""Bounded, secret-redacted server-response capture for the local history viewer."""
import json
import re
from urllib.parse import quote

MAX_BODY = 256 * 1024
REDACTED = '[REDACTED]'
SENSITIVE = {'apikey','xapikey','authorization','proxyauthorization','accesstoken',
             'refreshtoken','token','password','secret','clientsecret'}


def redact_text(text, secrets=()):
    for secret in secrets:
        if secret:
            for value in {secret, quote(secret, safe=''), json.dumps(secret)[1:-1]}:
                text = text.replace(value, REDACTED)
    text = re.sub(r'(?i)\b(?:dsk_|cg_read_|cg_admin_)[A-Za-z0-9_-]+', REDACTED, text)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', lambda m: m[1]+' '+REDACTED, text)
    pattern = r'''(?im)(["']?(?:x[-_ ]?api[-_ ]?key|api[-_ ]?key|authorization|proxy-authorization|access_token|refresh_token|password|client_secret|token)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n,;]+)'''
    return re.sub(pattern, lambda m: m[1]+REDACTED, text)


def safe_body(raw, secrets=(), overflow=False):
    # Oversized bodies are omitted rather than exposing partially captured credentials.
    if overflow or len(raw) > MAX_BODY:
        return '[Response body omitted: exceeds the 256 KB limit.]', True
    text = raw.decode('utf-8', errors='replace')
    try:
        data = json.loads(text)
        def clean(value):
            if isinstance(value, dict):
                return {redact_text(key, secrets):REDACTED if re.sub(r'[^a-z0-9]', '', key.lower()) in SENSITIVE else clean(item) for key,item in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return redact_text(value, secrets) if isinstance(value, str) else value
        text = json.dumps(clean(data), indent=2, ensure_ascii=False)
    except RecursionError:
        return '[Response body omitted: structured content is too deeply nested to safely redact.]', False
    except (ValueError, TypeError):
        text = redact_text(text, secrets)
    if len(text.encode('utf-8')) > MAX_BODY:
        return '[Response body omitted: formatted response exceeds the 256 KB limit.]', True
    return text, False


def queued_acceptance(status, body):
    """DRC accepted transmission; this is distinct from a completed import."""
    if status is None or not 200 <= status < 300:
        return False
    try:
        data = json.loads(body)
        return (isinstance(data, dict) and data.get('success') is True and
                str(data.get('message', '')).strip().rstrip('.').lower() == 'push queued successfully' and
                not any(data.get(k) for k in ('errors', 'failed', 'rejected', 'partial', 'failedItems')))
    except (ValueError, TypeError, RecursionError):
        return False


def response_description(job, response=None):
    if response is None:
        if job['state'] == 'blocked':
            return 'No request sent—no server response.'
        if job['manual']:
            return 'No server response was saved for this manual request.\n' + job['detail']
        return 'No response details were recorded for this job by this app version.'
    state = 'Needs review' if response['state'] == 'review' else response['state'].capitalize()
    if queued_acceptance(response['status'], response['body']): state = 'Sent to DRC (queued for processing)'
    status = str(response['status']) if response['status'] is not None else 'No server response'
    text = (f"Job #{job['id']} • Request attempt {response['attempt']}\n"
            f"Result: {state}\nHTTP status: {status}\nResponse time: {response['elapsed_ms']} ms\n"
            f"Endpoint: {response['endpoint']}\nFile: {response['filename']}\nInventory: {response['kind']}\n")
    if response['error']:
        text += '\n' + response['error'] + '\n'
    if response['truncated']:
        text += '\nResponse truncated: body omitted at the 256 KB limit.\n'
    return text + '\n' + (response['body'] or '[Empty response body]' if response['status'] is not None else 'No response body received.')
