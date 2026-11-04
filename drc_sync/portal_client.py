"""Authenticated private-Blob publishing. Management credentials never leave headers."""
import hashlib
import json
from urllib.parse import urlsplit
import httpx
from . import portal_secrets

DEFAULT_BASE = 'https://chintamani-updates.vercel.app'
MAX_SNAPSHOT = 64*1024*1024

class HostingError(RuntimeError):
    def __init__(self, message, retryable=True):super().__init__(message);self.retryable=retryable


def clean_base(value):
    try:
        u=urlsplit(value.strip())
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443) or u.query or u.fragment or u.path.rstrip('/'):
            raise ValueError()
    except Exception:raise ValueError('Enter the HTTPS hosting origin only, without a path, query or password.') from None
    return value.strip().rstrip('/')

class PortalClient:
    def __init__(self, settings, token=None, http=None):
        self.base=clean_base(settings.get('base_url',DEFAULT_BASE))
        self.token=token if token is not None else portal_secrets.get_secret(portal_secrets.hosting_slot(self.base))
        if not self.token:raise HostingError('Hosting is not configured. Open API Access > Hosting & Google setup.')
        self.http=http or httpx.Client(timeout=httpx.Timeout(60,connect=15),follow_redirects=False)
        self.owned=http is None

    def close(self):
        if self.owned:self.http.close()

    def admin(self, payload=None):
        try:
            headers={'Authorization':'Bearer '+self.token}
            r=self.http.get(self.base+'/api/v1/admin',headers=headers) if payload is None else self.http.post(self.base+'/api/v1/admin',json=payload,headers=headers)
            if r.status_code==401:raise HostingError('Hosting authorization failed. Check the admin credential in Hosting & Google setup.')
            if r.status_code==503:raise HostingError('Inventory hosting is not ready. Configure the private inventory Blob store and admin credential on Vercel.')
            if r.status_code==409:raise HostingError('A newer feed or access change is already active; this job will not overwrite it.',False)
            if not 200<=r.status_code<300:raise HostingError('Hosting request failed (HTTP '+str(r.status_code)+').',r.status_code>=500 or r.status_code in (408,429))
            result=r.json()
            if not isinstance(result,dict) or result.get('ok') is not True:raise HostingError('Hosting returned an unverified response.')
            return result
        except HostingError:raise
        except Exception as exc:raise HostingError('Hosting connection failed ('+type(exc).__name__+'). No credentials are shown.') from None

    def status(self, client_id):return self.admin({'action':'status','client_id':client_id})

    def publish(self, job):
        raw=job['payload'].encode('utf-8');sha=hashlib.sha256(raw).hexdigest()
        if len(raw)>MAX_SNAPSHOT:raise HostingError('Prepared feed exceeds the 64 MB upload limit.',False)
        payload_data = json.loads(raw)
        records_count = len(payload_data['diamonds']) if 'diamonds' in payload_data else (len(payload_data.get('natural', [])) + len(payload_data.get('lab_grown', [])))
        metadata={'client_id':job['client_id'],'revision':job['revision'],'key_hash':job['key_hash'],
                  'kinds':json.loads(job['kinds']),'sha256':sha,'size':len(raw),'records':records_count}
        intent=self.admin({'action':'authorize',**metadata})
        if not intent.get('already_published'):
            url=intent.get('upload_url','');u=urlsplit(url)
            trusted = u.hostname in ('blob.vercel-storage.com', 'vercel.com') or (u.hostname and u.hostname.endswith('.vercel-storage.com'))
            if u.scheme!='https' or not trusted or u.username or u.password:
                raise HostingError('Hosting returned an untrusted upload address.',False)
            try:
                r=self.http.put(url,content=raw,headers={'Content-Type':'application/json'},timeout=120)
                if not 200<=r.status_code<300 and r.status_code!=409:raise HostingError('Private inventory upload failed (HTTP '+str(r.status_code)+').')
            except HostingError:raise
            except Exception as exc:raise HostingError('Private upload interrupted ('+type(exc).__name__+'); it will retry safely.') from None
        return self.admin({'action':'commit',**metadata})

    def control(self, job):
        return self.admin({'action':job['action'],'client_id':job['client_id'],'revision':job['revision']})
