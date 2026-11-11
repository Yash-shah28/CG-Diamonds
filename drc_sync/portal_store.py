"""Durable pull-publication workflow. No legacy DRC job is dispatched here."""
import hashlib
import json
import secrets
import time
import uuid
from decimal import Decimal, InvalidOperation
from .core import Store, WorkbookValidationError
from .rates import apply_website_rate, rate_config
from .responses import redact_text
from . import portal_secrets

KINDS = ('Natural', 'Lab Grown')
API_TYPES = {'Natural':'NATURAL','Lab Grown':'LAB_GROWN'}

def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class PortalStore(Store):
    def __init__(self, path, recover=False):
        super().__init__(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS portal_auth_audit(request_id TEXT PRIMARY KEY,created REAL NOT NULL,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS portal_cache(kind TEXT PRIMARY KEY,payload TEXT NOT NULL,profile TEXT NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS portal_companies(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL,kinds TEXT NOT NULL,mode TEXT NOT NULL,percent TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,key_hash TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,status TEXT NOT NULL DEFAULT 'Draft',published INTEGER NOT NULL DEFAULT 0,published_at REAL,last_email TEXT NOT NULL DEFAULT 'Not sent');
        CREATE TABLE IF NOT EXISTS portal_jobs(id INTEGER PRIMARY KEY,client_id TEXT NOT NULL,revision INTEGER NOT NULL,action TEXT NOT NULL,payload TEXT,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,created REAL NOT NULL,detail TEXT NOT NULL DEFAULT '',key_hash TEXT NOT NULL,kinds TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS portal_mail_requests(client_id TEXT PRIMARY KEY,state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS portal_history(id INTEGER PRIMARY KEY,created REAL NOT NULL,client_id TEXT NOT NULL,name TEXT NOT NULL,event TEXT NOT NULL,state TEXT NOT NULL,records INTEGER NOT NULL DEFAULT 0,detail TEXT NOT NULL);
        ''')
        if recover:
            self.db.execute("UPDATE portal_mail_requests SET state='review' WHERE state='sending'")
            # Publication is revision-idempotent; email delivery is not.
            self.db.execute("UPDATE portal_jobs SET state='pending' WHERE state='publishing'")
            self.db.execute("UPDATE portal_companies SET last_email='Needs review - send interrupted; verify before resending' WHERE last_email='Sending'")
        self.db.commit()

    def save_auth_audits(self, entries):
        import re
        from datetime import datetime
        results={'accepted','missing_key','unknown_key','revoked_key','suspended_company'}
        for event in entries:
            if not isinstance(event,dict) or event.get('result') not in results: continue
            request_id=event.get('request_id','')
            if not isinstance(request_id,str) or not re.fullmatch(r'[a-fA-F0-9-]{36}',request_id): continue
            fingerprint=event.get('key_fingerprint')
            if fingerprint is not None and not re.fullmatch(r'[a-f0-9]{12}',str(fingerprint)): continue
            cid=event.get('client_id')
            if cid is not None and not re.fullmatch(r'[a-f0-9]{32}',str(cid)): continue
            try:created=datetime.fromisoformat(event['time'].replace('Z','+00:00')).timestamp()
            except Exception:continue
            status=event.get('http_status')
            if type(status) is not int or not 100<=status<=599:continue
            record={k:redact_text(str(event.get(k,'')))[:limit] for k,limit in [('company_name',120),('ip',128),('user_agent',512),('method',12)]}
            if cid is None:record['company_name']='Unknown company'
            record.update(request_id=request_id,client_id=cid,key_fingerprint=fingerprint,result=event['result'],http_status=status,time=event['time'])
            self.db.execute('INSERT OR IGNORE INTO portal_auth_audit VALUES (?,?,?)',(request_id,created,encode(record)))
        self.db.commit()

    def companies(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM portal_companies ORDER BY name COLLATE NOCASE')]

    def company(self, client_id):
        row = self.db.execute('SELECT * FROM portal_companies WHERE id=?',(client_id,)).fetchone()
        if not row: raise ValueError('Select an existing company.')
        return dict(row)

    def history(self, client_id, event, state, detail, records=0):
        c = self.company(client_id)
        self.db.execute('INSERT INTO portal_history(created,client_id,name,event,state,records,detail) VALUES (?,?,?,?,?,?,?)',
                        (time.time(),client_id,c['name'],event,state,records,redact_text(detail)[:1800]))
        self.db.commit()

    def add_company(self, name, email, kinds, mode='discount', percent='0', secret_writer=None):
        import re
        name = name.strip(); email = email.strip()
        if not name or len(name)>120: raise ValueError('Enter a company name (up to 120 characters).')
        if len(email)>254 or not re.fullmatch(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+', email):
            raise ValueError('Enter one valid recipient email address.')
        kinds = list(dict.fromkeys(kinds))
        if not kinds or any(k not in KINDS for k in kinds): raise ValueError('Select Natural, Lab Grown, or both.')
        rate_config({'client':{'mode':mode,'percent':percent}},'client')
        client_id = uuid.uuid4().hex; key = 'cg_read_'+secrets.token_urlsafe(32); key_hash = digest(key)
        (secret_writer or portal_secrets.put_secret)(portal_secrets.company_slot(client_id,key_hash),key)
        self.db.execute('INSERT INTO portal_companies(id,name,email,kinds,mode,percent,key_hash) VALUES (?,?,?,?,?,?,?)',
                        (client_id,name,email,encode(kinds),mode,str(percent),key_hash))
        self.db.commit()
        self.history(client_id,'Company','Draft','Company saved. Publish valid inventory before emailing access.')
        return client_id

    def cache(self, profile, payload):
        if not payload.get('diamonds'): raise WorkbookValidationError('Empty inventory cannot replace the last valid feed.')
        self.db.execute('INSERT OR REPLACE INTO portal_cache VALUES (?,?,?,?)',
                        (profile['kind'],encode(payload),encode(profile),time.time()))
        self.db.commit()

    def compose(self, company):
        natural = []; lab_grown = []; seen = set()
        for kind in json.loads(company['kinds']):
            row = self.db.execute('SELECT * FROM portal_cache WHERE kind=?',(kind,)).fetchone()
            if not row: raise WorkbookValidationError(f'{kind}: no validated inventory is available. Connect and publish its workbook first.')
            profiles = self.get('profiles', [])
            current = next((p for p in profiles if p.get('kind')==kind and p.get('enabled',True)), None)
            cached_profile = json.loads(row['profile'])
            if current is None or any(current.get(k) != cached_profile.get(k) for k in ('id','path','sheet','mapping','required','numeric','decimal_fields','request_format')):
                raise WorkbookValidationError(f'{kind}: the Excel connection changed. Publish inventory again to validate the current file.')
            target_list = natural if kind == 'Natural' else lab_grown
            for number, original in enumerate(json.loads(row['payload'])['diamonds'],2):
                record = dict(original); record['Diamond Type'] = API_TYPES[kind]
                stock = record.get('StockId')
                if stock is None or not str(stock).strip():
                    raise WorkbookValidationError(f'{kind}, row {number}: missing StockId. Map the Stock ID column before publishing.')
                identity = (kind,str(stock))
                if identity in seen: raise WorkbookValidationError(f'{kind}, row {number}: duplicate StockId. Stock IDs must be unique within each inventory type.')
                seen.add(identity)
                try:
                    price = record.get('PricePerCarat')
                    if isinstance(price,bool) or price is None or not Decimal(str(price)).is_finite() or Decimal(str(price)) <= 0:
                        raise ValueError()
                except (ValueError,InvalidOperation):
                    raise WorkbookValidationError(f'{kind}, row {number}, PricePerCarat: enter a positive finite price per carat.') from None
                target_list.append(record)
        return apply_website_rate({'natural':natural,'lab_grown':lab_grown},{'client':{'mode':company['mode'],'percent':company['percent']}},'client')

    def queue(self, client_id, action='publish', force=False):
        company = self.company(client_id)
        if action == 'publish' and not company['active']: return None
        payload = encode(self.compose(company)) if action == 'publish' else None
        latest = self.db.execute("SELECT * FROM portal_jobs WHERE client_id=? ORDER BY id DESC LIMIT 1",(client_id,)).fetchone()
        if not force and action=='publish' and latest and latest['action']==action and latest['payload']==payload and latest['key_hash']==company['key_hash'] and latest['state'] in ('pending','publishing','published'):
            return latest['id']
        revision = self.db.execute('UPDATE portal_companies SET revision=revision+1,status=? WHERE id=? RETURNING revision',('Pending publication' if action=='publish' else 'Pending access change',client_id)).fetchone()[0]
        # Only replace unsent publication snapshots, never pending access-control changes.
        self.db.execute("UPDATE portal_jobs SET state='superseded',detail='Replaced by a newer prepared feed' WHERE client_id=? AND action='publish' AND state='pending'",(client_id,))
        cur = self.db.execute('INSERT INTO portal_jobs(client_id,revision,action,payload,state,created,key_hash,kinds) VALUES (?,?,?,?,?,?,?,?)',
                             (client_id,revision,action,payload,'pending',time.time(),company['key_hash'],company['kinds']))
        self.db.commit(); return cur.lastrowid

    def queue_for_kind(self, kind):
        errors = []
        for company in self.companies():
            if company['active'] and kind in json.loads(company['kinds']):
                try:self.queue(company['id'],force=True)
                except ValueError as exc:errors.append(redact_text(str(exc)))
        return errors

    def set_rate(self, client_id, mode, percent):
        rate_config({'client':{'mode':mode,'percent':percent}},'client')
        self.db.execute('UPDATE portal_companies SET mode=?,percent=? WHERE id=?',(mode,str(percent),client_id));self.db.commit()
        try: self.queue(client_id,force=True)
        except WorkbookValidationError:
            self.db.execute("UPDATE portal_companies SET status='Rate saved - validated inventory needed' WHERE id=?",(client_id,));self.db.commit();raise

    def revoke(self, client_id):
        self.db.execute('UPDATE portal_companies SET active=0 WHERE id=?',(client_id,)); self.db.commit()
        return self.queue(client_id,'revoke')

    def change_scope(self, client_id, kinds):
        if not kinds or any(k not in KINDS for k in kinds):raise ValueError('Select at least one inventory type.')
        self.db.execute('UPDATE portal_companies SET kinds=? WHERE id=?',(encode(list(dict.fromkeys(kinds))),client_id));self.db.commit()
        self.queue(client_id,'suspend')
        return self.queue(client_id)

    def rotate(self, client_id, secret_writer=None):
        c = self.company(client_id)
        if not c['active']:raise ValueError('This company is revoked. Create new access instead.')
        key = 'cg_read_'+secrets.token_urlsafe(32); key_hash=digest(key)
        (secret_writer or portal_secrets.put_secret)(portal_secrets.company_slot(client_id,key_hash),key)
        # Verify availability before changing the candidate key; current remote key remains active until commit.
        self.compose(c)
        self.db.execute("UPDATE portal_companies SET key_hash=?,last_email='Not sent' WHERE id=?",(key_hash,client_id));self.db.commit()
        return self.queue(client_id)

    def pending(self):
        return self.db.execute("SELECT * FROM portal_jobs j WHERE state='pending' AND due<=? AND NOT (action='publish' AND EXISTS (SELECT 1 FROM portal_jobs b WHERE b.client_id=j.client_id AND b.action IN ('revoke','suspend') AND b.state IN ('pending','publishing','failed'))) ORDER BY CASE WHEN action IN ('revoke','suspend') THEN 0 ELSE 1 END,id LIMIT 1",(time.time(),)).fetchone()

    def finish_portal(self, job, state, detail, retry=False):
        detail = redact_text(detail)[:1800]
        due = time.time()+min(300,5*2**min(job['attempts'],6))
        self.db.execute('UPDATE portal_jobs SET state=?,attempts=attempts+1,due=?,detail=? WHERE id=?',('pending' if retry else state,due,detail,job['id']))
        c = self.company(job['client_id'])
        if c['revision'] == job['revision']:
            self.db.execute('UPDATE portal_companies SET status=? WHERE id=?',('Awaiting retry' if retry else state.title(),c['id']))
        if state == 'published':
            payload_data = json.loads(job['payload'])
            count = len(payload_data['diamonds']) if 'diamonds' in payload_data else (len(payload_data.get('natural', [])) + len(payload_data.get('lab_grown', [])))
            self.db.execute('UPDATE portal_companies SET published=?,published_at=? WHERE id=?',(count,time.time(),c['id']))
        else:count=0
        self.db.commit()
        self.history(c['id'],job['action'].title(),'Published to API' if state=='published' else 'Awaiting retry' if retry else state.title(),detail,count)
