"""Background pull-only inventory worker and serialized management requests."""
import json
import time
from pathlib import Path
from queue import SimpleQueue,Empty
from PySide6.QtCore import QThread,Signal
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .core import parse_excel,WorkbookValidationError
from .contract import FORMAT
from .portal_store import PortalStore
from .portal_client import PortalClient,HostingError,DEFAULT_BASE
from .workbook_errors import workbook_error
from . import portal_secrets
from .monitor import Stability
from .responses import redact_text

class FileEvents(FileSystemEventHandler):
    def __init__(self,queue):self.queue=queue
    def on_any_event(self,event):
        if event.event_type in ('modified','created','moved','deleted'):
            for path in (event.src_path,getattr(event,'dest_path','')):
                if path and not Path(path).name.startswith('~$'):self.queue.put(str(Path(path).resolve()))

class PullWorker(QThread):
    changed=Signal(str);problem=Signal(str,str);prepared=Signal(object,object);inspect_failed=Signal(str)
    response_ready=Signal(int);inspection_problem=Signal(object);diagnostics_ready=Signal(object);sent_to_drc=Signal(str)
    task_done=Signal(str,str);published=Signal(str)
    def __init__(self,dbpath):
        super().__init__();self.dbpath=dbpath;self.paused=False;self.manual=False;self.requests=SimpleQueue()
    def request(self,action,value=None):self.requests.put((action,value))
    def parse_profile(self, profile):
        validation = dict(profile)
        if profile.get('request_format') == FORMAT:
            validation['required'] = sorted(set(profile.get('required', [])) | {'StockId', 'Shape', 'Weight', 'Color', 'Clarity', 'PricePerCarat'})
            validation['identity'] = 'StockId'
        return parse_excel(validation)
    def prepare_inspection(self,profile):self.request('prepare',profile)
    def send_inspection(self,profile,payload):self.request('legacy-send')
    def process_inspection(self,store,request):
        action,profile,*_=request
        if action=='prepare':
            try:self.prepared.emit(profile,self.parse_profile(profile))
            except Exception as exc:self.inspect_failed.emit(workbook_error(profile,exc))
        else:self.inspect_failed.emit('Direct DRC requests are disabled. Publish inventory through API Access.')
    def _report(self,store,profile,exc):
        detail=workbook_error(profile,exc);store.error(profile.get('id',''),detail)
        self.problem.emit(profile.get('id',''),detail);self.changed.emit(detail)
    def _validate_company(self,store,c):
        for kind in json.loads(c['kinds']):
            p=next((p for p in store.get('profiles',[]) if p['kind']==kind and p.get('enabled',True)),None)
            if not p:raise WorkbookValidationError(kind+': connect and enable its Excel workbook first.')
            try:store.cache(p,self.parse_profile(p))
            except Exception as exc:
                self._report(store,p,exc)
                raise WorkbookValidationError(workbook_error(p,exc)) from None
        store.compose(c)
    def _task(self,store,action,value):
        if action=='prepare':self.process_inspection(store,('prepare',value));return
        if action=='legacy-send':self.inspect_failed.emit('Direct DRC requests are disabled. No request sent.');return
        if action=='google':
            from .google_mail import connect_google
            self.task_done.emit(action,'Google sender connected: '+connect_google(value));return
        if action=='smtp-test':
            from .google_mail import test_smtp_auth
            test_smtp_auth(value.get('sender'), value.get('password'))
            self.task_done.emit(action,'Gmail App Password authenticated successfully.');return
        if action=='hosting-test':
            client=PortalClient(store.get('portal_hosting',{}))
            try:client.admin()
            finally:client.close()
            self.task_done.emit(action,'Private inventory hosting is ready.');return
        if action in ('email','rotate'):
            c=store.company(value)
            if not c['active']:raise ValueError('This company is revoked. Create new access instead.')
            sender, app_pw = portal_secrets.get_smtp_credentials()
            if not app_pw and not portal_secrets.get_secret('google-credentials'):
                raise ValueError('Configure your Gmail App Password or connect Google account in Email & Hosting settings before emailing access.')
            self._validate_company(store,c)
            if action=='rotate':store.rotate(value)
            else:store.queue(value)
            store.db.execute('INSERT OR REPLACE INTO portal_mail_requests VALUES (?,?)',(value,'waiting'));store.db.commit()
            self.task_done.emit(action,'Access will be emailed after the current feed is confirmed online.');return
        if action=='revoke':store.revoke(value);self.task_done.emit(action,'Revocation queued. Access is revoked only after hosting confirms it.');return
        if action=='scope':
            store.change_scope(value['id'],value['kinds']);self.task_done.emit(action,'Inventory access change queued.');return
        raise ValueError('Unknown background task.')
    def process_jobs(self,store):
        job=store.pending()
        if job is None:return
        if self.paused or self.isInterruptionRequested():return
        if job['action']=='publish' and not store.company(job['client_id'])['active']:
            store.finish_portal(job,'superseded','Company was revoked; this prepared feed was not sent.');return
        store.db.execute("UPDATE portal_jobs SET state='publishing' WHERE id=?",(job['id'],));store.db.commit()
        client=None
        try:
            client=PortalClient(store.get('portal_hosting',{}))
            if job['action']=='publish':
                result=client.publish(job)
                if result.get('revision')!=job['revision']:raise HostingError('Hosting did not confirm this feed revision.')
                store.finish_portal(job,'published','Published to API. Import results from the receiving website are not confirmed.')
                self.published.emit(store.company(job['client_id'])['name'])
            else:
                result=client.control(job)
                if result.get('revision')!=job['revision']:raise HostingError('Hosting did not confirm this access revision.')
                store.finish_portal(job,'revoked' if job['action']=='revoke' else 'suspended','Hosting confirmed the access change.')
            self.changed.emit('API '+job['action']+' confirmed; see History')
        except HostingError as exc:
            state='failed' if exc.retryable else 'superseded'
            store.finish_portal(job,state,str(exc),retry=exc.retryable)
            self.problem.emit('hosting',str(exc))
        except Exception as exc:
            detail='Publishing failed ('+type(exc).__name__+'). Check hosting setup and local storage.'
            store.finish_portal(job,'failed',detail,retry=True);self.problem.emit('hosting',detail)
        finally:
            if client:client.close()
    def process_email(self,store):
        request=store.db.execute("SELECT * FROM portal_mail_requests WHERE state='waiting' LIMIT 1").fetchone()
        if not request or self.paused or self.isInterruptionRequested():return
        c=store.company(request['client_id'])
        if not c['active']:
            store.db.execute('DELETE FROM portal_mail_requests WHERE client_id=?',(c['id'],));store.db.commit();return
        if c['status']!='Published':return
        client=None;dispatched=False
        try:
            from .google_mail import send_invitation,MailError,gmail_credentials
            from .integration_guide import guide_pdf
            client=PortalClient(store.get('portal_hosting',{}))
            remote=client.status(c['id'])
            if not remote.get('enabled') or remote.get('key_hash')!=c['key_hash'] or remote.get('revision')!=c['revision']:
                raise ValueError('Current access is not confirmed online. Publish inventory before emailing access.')
            key=portal_secrets.company_key(c['id'],c['key_hash'])
            if not key:raise ValueError('This company key is not available on this PC. Rotate the key before emailing access.')
            api_url=client.base+'/api/v1/inventory';pdf=guide_pdf(api_url,json.loads(c['kinds']))
            store.db.execute("UPDATE portal_companies SET last_email='Sending' WHERE id=?",(c['id'],))
            store.db.execute("UPDATE portal_mail_requests SET state='sending' WHERE client_id=?",(c['id'],));store.db.commit()
            dispatched=True
            message_id=send_invitation(c,key,api_url,pdf)
            store.db.execute("UPDATE portal_companies SET last_email='Sent via Gmail' WHERE id=?",(c['id'],))
            store.history(c['id'],'Email','Sent via Gmail','Gmail accepted the invitation. Inbox delivery is not confirmed.')
            self.task_done.emit('email-sent','Invitation accepted by Gmail for '+c['email'])
        except Exception as exc:
            from .google_mail import MailError
            detail=str(exc) if isinstance(exc,(ValueError,MailError,HostingError)) else 'Email processing failed ('+type(exc).__name__+').'
            if dispatched and not isinstance(exc,MailError):detail='Email outcome uncertain. Check Gmail Sent mail before resending.'
            detail=redact_text(detail)
            store.db.execute('UPDATE portal_companies SET last_email=? WHERE id=?',(detail,c['id']))
            store.history(c['id'],'Email','Needs review' if dispatched else 'Failure',detail)
            self.problem.emit(c['id'],detail)
        finally:
            if client:client.close()
            store.db.execute('DELETE FROM portal_mail_requests WHERE client_id=?',(c['id'],));store.db.commit()
    def pump(self,store):
        try:
            self.process_jobs(store)
            if not self.isInterruptionRequested():self.process_email(store)
        except Exception as exc:
            try:store.db.rollback()
            except Exception:pass
            self.paused=True
            self.problem.emit('storage','Local history/storage failed ('+type(exc).__name__+'). Publishing is paused. Verify the remote outcome before resuming; email is not automatically resent.')
    def run(self):
        store=PortalStore(self.dbpath);events=SimpleQueue();observer=Observer();watched=set();signatures={};stable=Stability()
        observer.start()
        try:
            while not self.isInterruptionRequested():
                try:
                    action,value=self.requests.get_nowait()
                    try:self._task(store,action,value)
                    except Exception as exc:
                        detail=redact_text(str(exc)) if isinstance(exc,(ValueError,HostingError)) else 'Operation failed ('+type(exc).__name__+'). Check setup.'
                        self.task_done.emit('error',detail)
                except Empty:pass
                dirty=set()
                while True:
                    try:dirty.add(events.get_nowait())
                    except Empty:break
                force=self.manual;self.manual=False
                for p in store.get('profiles',[]):
                    if self.isInterruptionRequested():break
                    if self.paused or not p.get('enabled',True):continue
                    try:
                        path=Path(p['path']).resolve()
                        if str(path.parent) not in watched and path.parent.is_dir():observer.schedule(FileEvents(events),str(path.parent),recursive=False);watched.add(str(path.parent))
                        sig=(path.stat().st_size,path.stat().st_mtime_ns,json.dumps(p,sort_keys=True));name=p['id']
                        if name not in signatures and not force:
                            signatures[name]=sig;stable.ready(name,sig,time.monotonic());continue
                        if str(path) in dirty:stable.states.pop(name,None)
                        if not stable.ready(name,sig,time.monotonic()) and not force:continue
                        if force or signatures.get(name)!=sig:
                            store.cache(p,self.parse_profile(p));signatures[name]=sig
                            errors=store.queue_for_kind(p['kind'])
                            for detail in errors:self.problem.emit(name,detail)
                            self.changed.emit('Workbook validated: '+path.name)
                    except Exception as exc:self._report(store,p,exc)
                if not self.paused and not self.isInterruptionRequested():
                    self.pump(store)
                self.msleep(1000)
        finally:observer.stop();observer.join();store.db.close()
