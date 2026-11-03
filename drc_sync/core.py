"""Persistence and configurable, deliberately conservative DRC adapter."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from decimal import Decimal, InvalidOperation
import httpx
from openpyxl import load_workbook

from .contract import ENDPOINT, FORMAT, migrate_profile
from .responses import MAX_BODY, safe_body, redact_text, queued_acceptance
from .diagnostics import failure_detail
from .rates import rate_config, RateConfigError
SERVICE = "DRC Inventory Sync"

class WorkbookValidationError(ValueError):
    """Validated, user-actionable workbook error safe to display after redaction."""


class FormulaResultError(WorkbookValidationError):
    """A mapped formula has no usable result saved by Excel."""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY, profile TEXT, created REAL, detail TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, profile TEXT, digest TEXT,
          payload TEXT, state TEXT, attempts INTEGER DEFAULT 0, due REAL DEFAULT 0,
          created REAL, detail TEXT DEFAULT '', records INTEGER, manual INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS responses(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL,
          attempt INTEGER, status INTEGER, elapsed_ms INTEGER, body TEXT, truncated INTEGER,
          error TEXT, state TEXT, created REAL, endpoint TEXT, filename TEXT, kind TEXT);
        ''')
        if 'manual' not in {r['name'] for r in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN manual INTEGER NOT NULL DEFAULT 0')
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(jobs)')}
        for column in ('filename', 'kind'):
            if column not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT DEFAULT ''")
        for profile in self.get('profiles', []):
            self.db.execute("UPDATE jobs SET filename=? WHERE profile=? AND filename=''", (profile.get('path', ''), profile['id']))
            self.db.execute("UPDATE jobs SET kind=? WHERE profile=? AND kind=''", (profile.get('kind', ''), profile['id']))
        self.db.execute("UPDATE jobs SET state='review',detail='Interrupted request: verify remote inventory before retry' WHERE state='sending'")
        self.db.execute("UPDATE jobs SET state='review',detail='Manual send interrupted before dispatch; explicitly send again' WHERE state='manual_ready'")
        profiles = self.get('profiles', [])
        updated = [migrate_profile(p) for p in profiles]
        if updated != profiles:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('profiles', json.dumps(updated)))
        self.db.commit()
    def get(self, key, default):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default
    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()
    def enqueue(self, profile, payload, config=None, *, force=False):
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        last = self.db.execute("SELECT digest,state FROM jobs WHERE profile=? AND state!='superseded' ORDER BY id DESC LIMIT 1", (profile,)).fetchone()
        if not force and last and last['digest'] == digest:
            return None
        self.db.execute("UPDATE jobs SET state='superseded',payload=NULL WHERE profile=? AND state IN ('pending','retry')", (profile,))
        cur = self.db.execute("INSERT INTO jobs(profile,digest,payload,state,created,records) VALUES (?,?,?,'pending',?,?)", (profile,digest,raw,time.time(),len(payload['diamonds'])))
        config = config or next((p for p in self.get('profiles', []) if p['id'] == profile), {})
        self.db.execute('UPDATE jobs SET filename=?,kind=? WHERE id=?', (config.get('path', ''), config.get('kind', ''), cur.lastrowid))
        self.db.commit()
        return cur.lastrowid
    def finish(self, job, state, detail, delay=0):
        self.db.execute('UPDATE jobs SET state=?,detail=?,attempts=attempts+1,due=? WHERE id=?', (state,detail,time.time()+delay,job['id']))
        if state in ('success', 'sent'):
            self.db.execute('UPDATE jobs SET payload=NULL WHERE id=?', (job['id'],))
        self.db.commit()
    def enqueue_manual(self, profile, payload, config=None):
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        self.db.execute("UPDATE jobs SET state='superseded',payload=NULL WHERE profile=? AND state IN ('pending','retry')", (profile,))
        cur = self.db.execute("INSERT INTO jobs(profile,digest,payload,state,created,records,manual) VALUES (?,?,?,'manual_ready',?,?,1)", (profile,digest,raw,time.time(),len(payload['diamonds'])))
        config = config or next((p for p in self.get('profiles', []) if p['id'] == profile), {})
        self.db.execute('UPDATE jobs SET filename=?,kind=? WHERE id=?', (config.get('path', ''), config.get('kind', ''), cur.lastrowid))
        self.db.commit()
        return cur.lastrowid
    def record_response(self, job, profile, key, status, elapsed, raw, overflow, error, state):
        body, truncated = safe_body(raw, (key,), overflow)
        self.db.execute('INSERT INTO responses(job_id,attempt,status,elapsed_ms,body,truncated,error,state,created,endpoint,filename,kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (job['id'],job['attempts']+1,status,elapsed,body,int(truncated),error,state,time.time(),
             redact_text(profile.get('endpoint', ENDPOINT), (key,)),redact_text(profile.get('path', ''), (key,)),profile.get('kind', '')))
        self.db.commit()
    def error(self, profile, detail):
        last = self.db.execute('SELECT detail,created FROM errors WHERE profile=? ORDER BY id DESC LIMIT 1', (profile,)).fetchone()
        if not last or last['detail'] != detail or time.time() - last['created'] > 300:
            self.db.execute('INSERT INTO errors(profile,created,detail) VALUES (?,?,?)', (profile,time.time(),detail))
            self.db.commit()

def parse_excel(profile):
    path = Path(profile['path'])
    if path.suffix.lower() != '.xlsx' or path.name.startswith('~$'):
        raise WorkbookValidationError('Select a valid .xlsx inventory workbook')
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    wb = load_workbook(path, read_only=True, data_only=False)
    cached = None
    try:
        if profile.get('kind') == 'Lab Grown':
            cached = load_workbook(path, read_only=True, data_only=True)
        if profile['sheet'] not in wb.sheetnames:
            raise WorkbookValidationError(f"Worksheet '{profile['sheet']}' was not found. Choose the correct worksheet in Excel Mapping.")
        sheet = wb[profile['sheet']]
        rows = sheet.iter_rows()
        headers = [cell.value for cell in next(rows, ())]
        cached_rows = cached[profile['sheet']].iter_rows() if cached else None
        if cached_rows is not None: next(cached_rows, None)
        headers = [str(v).strip() if v is not None else '' for v in headers]
        mapping = profile['mapping']
        if not mapping or len(set(mapping.values())) != len(mapping):
            raise WorkbookValidationError('Column mapping is empty or assigns multiple Excel columns to the same API field. Correct Excel Mapping.')
        for column in mapping:
            if headers.count(column) != 1:
                raise WorkbookValidationError(f"Column '{column}' is missing. Update Excel Mapping to match the workbook headers." if headers.count(column) == 0 else f"Column '{column}' appears more than once. Give each Excel header a unique name.")
        result = []; source_rows = []
        for number, row in enumerate(rows, 2):
            calculated = next(cached_rows, ()) if cached_rows is not None else ()
            if all(cell.value is None for cell in row):
                continue
            record = {}
            for column, field in mapping.items():
                index = headers.index(column)
                cell = row[index]
                value = cell.value
                if cell.data_type == 'f':
                    if cached is None:
                        raise WorkbookValidationError(f"Row {number}, column '{column}': a formula is present. Natural workbooks require saved values instead of formulas.")
                    result_cell = calculated[index] if index < len(calculated) else None
                    if result_cell is None or result_cell.value is None:
                        raise FormulaResultError(f'Row {number}, {column}: formula has no saved result. Open the workbook in Excel, recalculate, then save it again.')
                    if result_cell.data_type == 'e':
                        raise FormulaResultError(f'Row {number}, {column}: formula result is an Excel error. Correct it in Excel and save again.')
                    value = result_cell.value
                elif cell.data_type == 'e':
                    raise WorkbookValidationError(f"Row {number}, column '{column}': cell contains an Excel error. Correct the cell in Excel.")
                if isinstance(value, str):
                    value = value.strip()
                    if value.startswith('='):
                        raise WorkbookValidationError(f"Row {number}, column '{column}': a formula is present. Natural workbooks require saved values instead of formulas.")
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    raise WorkbookValidationError(f"Row {number}, column '{column}': unsupported cell type. Use text or a number.")
                if value in (None, '') and profile.get('omit_blank'):
                    continue
                if field in profile.get('numeric', []) or field in profile.get('decimal_fields', []) or field == 'PricePerCarat':
                    try:
                        if isinstance(value, bool):
                            raise WorkbookValidationError()
                        number_value = Decimal(str(value))
                        if not number_value.is_finite() or ((field in profile.get('numeric', []) or field == 'PricePerCarat') and number_value <= 0):
                            raise WorkbookValidationError()
                        value = format(number_value.normalize(), 'f') if field in profile.get('string_fields', []) else float(number_value)
                    except (TypeError, ValueError, InvalidOperation):
                        raise WorkbookValidationError(f"Row {number}, column '{column}' ({field}): invalid numeric value. Enter a {'positive ' if field in profile.get('numeric', []) or field == 'PricePerCarat' else ''}finite number.")
                elif field in profile.get('string_fields', []) and value is not None:
                    value = str(value)
                record[field] = value
            if profile.get('request_format') == FORMAT:
                expected_type = {'Natural':'NATURAL', 'Lab Grown':'LAB_GROWN'}[profile['kind']]
                actual_type = record.get('Diamond Type')
                if actual_type is not None:
                    normalized = str(actual_type).upper().replace(' ', '').replace('_', '').replace('-', '').replace('/', '')
                    normalized = {'NATURAL':'NATURAL', 'NATURALDIAMOND':'NATURAL', 'NATURALDIAMONDS':'NATURAL', 'LAB':'LAB_GROWN', 'LABGROWN':'LAB_GROWN', 'LABGROWNDIAMOND':'LAB_GROWN', 'LABGROWNDIAMONDS':'LAB_GROWN', 'LGD':'LAB_GROWN'}.get(normalized)
                    if normalized != expected_type:
                        source_column = next((c for c, f in mapping.items() if f == 'Diamond Type'), 'Diamond Type')
                        raise WorkbookValidationError(f"Row {number}, column '{source_column}': Diamond Type does not match the {profile['kind']} connection. Correct its value or mapping.")
                record['Diamond Type'] = expected_type
            for field in profile.get('required', []):
                if record.get(field) in (None, ''):
                    raise WorkbookValidationError(f"Row {number}: missing {field}. Fill the mapped cell or map the correct Excel column.")
            result.append(record); source_rows.append(number)
        if not result:
            raise WorkbookValidationError('Empty inventory blocked. No diamond rows were found; the last published feed will be kept.')
        identity = profile.get('identity', '')
        if identity:
            ids = [r.get(identity) for r in result]
            if any(v in (None, '') for v in ids) or len(set(ids)) != len(ids):
                seen = {}
                for row_number, item in zip(source_rows, ids):
                    if item in (None, ''):
                        raise WorkbookValidationError(f'Row {row_number}: missing Stock ID. Fill the mapped stock identifier cell.')
                    if item in seen:
                        raise WorkbookValidationError(f'Row {row_number}: duplicate Stock ID (also row {seen[item]}). Stock identifiers must be unique.')
                    seen[item] = row_number
            result.sort(key=lambda r: str(r[identity]))
        json.dumps(result, allow_nan=False)
    finally:
        if cached is not None: cached.close()
        wb.close()
    if before != (path.stat().st_size, path.stat().st_mtime_ns):
        raise WorkbookValidationError('Workbook changed while reading; waiting for next scan')
    return {'diamonds': result}

def attempt_snapshot(job, profile, key, state, detail, status=None, elapsed=0, raw=b'', overflow=False, error=''):
    body, truncated = safe_body(raw, (key,), overflow)
    result = {'saved':False, 'job':{'id':job['id'],'manual':int(job['manual']), 'state':state,'detail':detail},
              'response':{'attempt':job['attempts']+1,'status':status,'elapsed_ms':elapsed,'body':body,'truncated':int(truncated),
                          'error':error,'state':state,'created':time.time(),'endpoint':redact_text(profile.get('endpoint',ENDPOINT),(key,)),
                          'filename':redact_text(profile.get('path',''),(key,)),'kind':profile.get('kind','')}}
    return result


def save_attempt(store, job, profile, key, state, detail, status=None, elapsed=0, raw=b'', overflow=False, error='', delay=0, dispatched=False):
    result = attempt_snapshot(job,profile,key,state,detail,status,elapsed,raw,overflow,error)
    try:
        store.record_response(job, profile, key, status, elapsed, raw, overflow, error, state)
        store.finish(job, state, detail, delay)
        result['saved'] = True
    except Exception as exc:
        message = failure_detail('history saving', exc, dispatched)
        result['response']['error'] = '\n'.join(filter(None, (error, message)))
        result['response']['state'] = result['job']['state'] = 'review' if dispatched else 'blocked'
        result['job']['detail'] = message
        try:
            store.db.rollback()
            store.finish(job, result['job']['state'], message)
        except Exception:
            pass
    return result


def deliver(store, job, profile, key, client=None, *, manual=False):
    if job['manual'] and not manual:
        return
    try:
        rate_config(store.get('website_rates', {}), profile.get('website', 'drc'))
    except RateConfigError:
        store.finish(job, 'blocked', 'Invalid website rate. Correct Change Rates before uploading.')
        return
    rule = profile.get('success_field')
    has_rule = profile.get('confirmed') is True and isinstance(rule, str) and bool(rule.strip())
    if not manual and not has_rule and profile.get('upload_on_save') is not True:
        store.finish(job, 'blocked', 'No request sent—no server response. Confirm API contract and response success rule in profile')
        return
    if not key:
        store.finish(job, 'blocked', 'API credential missing; no request sent')
        return
    endpoint = profile.get('endpoint', ENDPOINT)
    try:
        url = httpx.URL(endpoint)
        allowed = url.scheme == 'https' and url.host == 'connect.drc.diamonds' and url.port in (None, 443) and not (url.username or url.password or url.query or url.fragment)
    except (httpx.InvalidURL, TypeError):
        allowed = False
    if not allowed:
        store.finish(job, 'blocked', 'Endpoint must be HTTPS on connect.drc.diamonds without credentials or query; no request sent')
        return
    # A superseded job must never be resurrected after the worker fetched it.
    changed = store.db.execute("UPDATE jobs SET state='sending' WHERE id=? AND state IN ('pending','retry','manual_ready')", (job['id'],)).rowcount
    store.db.commit()
    if not changed:
        return
    owned = client is None
    started = time.monotonic()
    status, raw, overflow, error = None, b'', False, ''
    stage, dispatched, chunks = 'client setup', False, []
    state, detail, delay = 'review', 'Needs review: import result has not been verified', 0
    retry_safe = not manual and profile.get('retry_safe') is True and not profile.get('upload_on_save')
    try:
        if client is None:
            client = httpx.Client(timeout=45, follow_redirects=False)
        stage = 'request preparation'
        payload = json.loads(job['payload'])
        stage = 'request dispatch'
        dispatched = True
        with client.stream('POST', endpoint, headers={'x-api-key':key}, json=payload) as response:
            status = response.status_code
            stage = 'response reading'
            chunks = []
            size = 0
            for chunk in response.iter_bytes(chunk_size=16384):
                size += len(chunk)
                if size > MAX_BODY:
                    overflow = True
                    chunks.clear()
                    break
                chunks.append(chunk)
            raw = b''.join(chunks)
            stage = 'response processing'
            if status == 429:
                retry = response.headers.get('Retry-After', '')
                delay = min(86400, max(30, int(retry))) if retry.isdigit() else 60
                state, detail = ('retry' if retry_safe else 'review'), 'Rate limited; needs review' if not retry_safe else 'Rate limited'
            elif status >= 500:
                state, detail = ('retry' if retry_safe else 'review'), 'Server error; remote outcome uncertain'
                delay = min(3600, 30 * 2 ** min(job['attempts'], 7))
            elif not 200 <= status < 300:
                state, detail = 'failed', f'HTTP {status}; inspect the saved server response'
            elif not overflow and queued_acceptance(status, raw):
                state, detail = 'sent', 'Successfully sent to DRC; queued for processing'
            elif has_rule and not overflow:
                try:
                    data = json.loads(raw)
                    value = data
                    for part in profile['success_field'].split('.'):
                        value = value[part]
                    valid = type(value) is type(profile.get('success_value', True)) and value == profile.get('success_value', True)
                    partial = any(data.get(k) for k in ('errors', 'failed', 'rejected', 'partial'))
                    if valid and not partial:
                        state, detail = 'success', 'Validated response'
                    else:
                        detail = 'Needs review: partial or unconfirmed import; inspect the server response'
                except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
                    detail = 'Needs review: response did not match the configured contract'
            elif overflow:
                detail = 'Needs review: response exceeds the 256 KB limit'
    except Exception as exc:
        state = ('retry' if retry_safe and isinstance(exc,httpx.RequestError) else 'review') if dispatched else 'blocked'
        error = detail = failure_detail(stage, exc, dispatched)
        if chunks and not raw and not overflow:
            # Partial bytes may end inside a credential; omit rather than leak a fragment.
            raw = b'[Partial response body omitted after interruption.]'
        delay = min(3600,30 * 2 ** min(job['attempts'],7))
    finally:
        elapsed = max(0, round((time.monotonic()-started)*1000))
        if owned and client is not None:
            try:
                client.close()
            except Exception as exc:
                error = '\n'.join(filter(None,(error,failure_detail('client cleanup',exc,dispatched))))
                state = 'review' if dispatched else 'blocked'
                detail = error
    return save_attempt(store,job,profile,key,state,detail,status,elapsed,raw,overflow,error,delay if state=='retry' else 0,dispatched)
