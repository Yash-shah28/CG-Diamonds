"""Offline setup checks and exception metadata that never include exception messages."""
import re
import traceback
import httpx


def failure_detail(stage, exc, dispatched=False):
    kind = re.sub(r'[^A-Za-z0-9_.]', '', type(exc).__name__)[:100]
    frames = traceback.extract_tb(exc.__traceback__)[-5:]
    location = ' > '.join(f'{frame.name}:{frame.lineno}' for frame in frames)
    outcome = 'Needs review—remote outcome uncertain. Do not resend until verified.' if dispatched else 'No request sent—no server response.'
    return f'Failure stage: {stage}\nException type: {kind}\nLocation: {location or "unavailable"}\n{outcome}'


def run_preflight(kind, key_reader=None, client_factory=None):
    if key_reader is None:
        from .credentials import read_key
        key_reader = read_key
    checks = []
    for stage, check in (
        ('credential lookup', lambda: bool(key_reader(kind))),
        ('client setup', lambda: _check_client(client_factory or httpx.Client)),
    ):
        try:
            available = check()
            checks.append({'stage':stage, 'ok':available, 'detail':'Ready' if available else 'No credential is stored for this inventory type'})
        except Exception as exc:
            checks.append({'stage':stage, 'ok':False, 'detail':failure_detail(stage,exc)})
    return {'inventory':kind, 'offline':True, 'requests_sent':0, 'ok':all(c['ok'] for c in checks), 'checks':checks}


def _check_client(factory):
    client = factory(timeout=45, follow_redirects=False)
    client.close()
    return True
