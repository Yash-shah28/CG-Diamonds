"""Background pull-only inventory worker and serialized management requests."""
import json
import time
from pathlib import Path
from threading import Lock
from queue import SimpleQueue,Empty
from PySide6.QtCore import QThread,Signal
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .core import parse_excel,WorkbookValidationError
from .contract import FORMAT, REQUIRED_FIELDS
from .rates import apply_website_rate
from .portal_store import PortalStore
from .portal_client import PortalClient,HostingError,DEFAULT_BASE
from .workbook_errors import workbook_error
from . import portal_secrets
from .monitor import Stability
from .backup import publication_blocked
from .workspace import FEATURE_FOR_KIND, ScopedPortalClient, cloud_workspace, connection_for, feature_enabled, hosting_settings, portal_client
from .responses import redact_text
from .yahoo_mail import MailError, send_invitation, test_smtp_auth

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
    smtp_test_done = Signal(str)
    natural_validated = Signal(object)
    def __init__(self,dbpath):
        super().__init__();self.dbpath=dbpath;self.paused=False;self.manual=False;self.requests=SimpleQueue();self.publisher_lock=Lock()
    def request(self,action,value=None):self.requests.put((action,value))
    def parse_profile(self, profile):
        validation = dict(profile)
        if profile.get('request_format') == FORMAT:
            validation['required'] = sorted(set(profile.get('required', [])) | set(REQUIRED_FIELDS))
            validation['identity'] = 'StockId'
        return parse_excel(validation, pull_feed=True)
    def prepare_inspection(self,profile):self.request('prepare',profile)
    def send_inspection(self,profile,payload):self.request('legacy-send')
    def process_inspection(self,store,request):
        action,profile,*_=request
        if action=='prepare':
            try:
                payload = apply_website_rate(self.parse_profile(profile), {}, truncate=True)
                self.prepared.emit(profile, payload)
            except Exception as exc:self.inspect_failed.emit(workbook_error(profile,exc))
        else:self.inspect_failed.emit('Direct DRC requests are disabled. Publish inventory through API Access.')
    def _report(self,store,profile,exc):
        detail=workbook_error(profile,exc);store.error(profile.get('id',''),detail)
        self.problem.emit(profile.get('id',''),detail);self.changed.emit(detail)
    def _validate_company(self,store,c):
        for kind in json.loads(c['kinds']):
            p=next((p for p in store.get('profiles',[]) if p['kind']==kind and p.get('enabled',True)),None)
            if not p or not feature_enabled(store, FEATURE_FOR_KIND[kind]):raise WorkbookValidationError(kind+': connect and enable its Excel workbook first.')
            try:store.cache(p,self.parse_profile(p))
            except Exception as exc:
                self._report(store,p,exc)
                raise WorkbookValidationError(workbook_error(p,exc)) from None
        store.compose(c)
    def _task(self,store,action,value):
        if action=='prepare':self.process_inspection(store,('prepare',value));return
        if action=='legacy-send':self.inspect_failed.emit('Direct DRC requests are disabled. No request sent.');return
        if publication_blocked(store):
            raise HostingError('Publishing stays on the primary PC until the workspace is handed off.', False)
        if action == 'smtp-test':
            try:
                test_smtp_auth(value.get('sender'), value.get('password'))
            except MailError as exc:
                self.smtp_test_done.emit(str(exc))
            else:
                self.smtp_test_done.emit('Yahoo Mail login verified.')
            return
        if action=='hosting-test':
            client=portal_client(hosting_settings(store), PortalClient)
            try:client.admin()
            finally:client.close()
            self.task_done.emit(action,'Private inventory hosting is ready.');return
        if action in ('email','rotate'):
            c=store.company(value)
            if not c['active']:raise ValueError('This company is revoked. Create new access instead.')
            if not feature_enabled(store, 'buyer_feeds') or not feature_enabled(store, 'email' if action == 'email' else 'buyer_feeds'):
                raise ValueError('This feature is disabled for your company.')
            workspace = cloud_workspace(store)
            sender, app_pw = (None, None) if workspace else portal_secrets.get_smtp_credentials()
            if not workspace and (not sender or not app_pw):
                raise ValueError('Enter your Yahoo email address and app password in Email & Hosting settings before emailing access.')
            self._validate_company(store,c)
            if action=='rotate':store.rotate(value)
            else:store.queue(value)
            store.db.execute('INSERT OR REPLACE INTO portal_mail_requests VALUES (?,?)',(value,'waiting'));store.db.commit()
            self.task_done.emit(action,'Access will be emailed after the current feed is confirmed online.');return
        if action=='revoke':store.revoke(value);self.task_done.emit(action,'Revocation queued. Access is revoked only after hosting confirms it.');return
        if action == 'delete':
            company = store.company(value)
            if store.remove_company(value):
                self.task_done.emit('delete', company['name'] + ' was removed from Client Feeds.')
            else:
                self.task_done.emit('delete-pending', 'Removal queued. The feed is removed after hosting confirms revocation.')
            return
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
            client=portal_client(hosting_settings(store), PortalClient)
            if job['action']=='publish':
                c=store.company(job['client_id'])
                if isinstance(client, ScopedPortalClient):
                    try:
                        remote = client.status(c['id'])
                    except HostingError as error:
                        if getattr(error, 'http_status', None) != 404:
                            raise
                        remote = {}
                    client.register_buyer(c, remote.get('key_hash'))
                result=client.publish(job, company_name=c['name'])
                if result.get('revision')!=job['revision']:raise HostingError('Hosting did not confirm this feed revision.')
                store.finish_portal(job,'published','Published to API. Import results from the receiving website are not confirmed.')
                self.published.emit(store.company(job['client_id'])['name'])
            else:
                result=client.control(job)
                if result.get('revision')!=job['revision']:raise HostingError('Hosting did not confirm this access revision.')
                if job['action'] == 'delete':
                    company = store.company(job['client_id'])
                    store.finish_portal(job, 'removed', 'Hosting confirmed revocation.')
                    self.task_done.emit('delete', company['name'] + ' was removed from Client Feeds.')
                    return
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
            from .integration_guide import guide_pdf
            workspace = cloud_workspace(store)
            sender, password = (None, None) if workspace else portal_secrets.get_smtp_credentials()
            if not workspace and (not sender or not password):
                raise MailError('Configure Yahoo Mail in Email & Hosting settings before emailing access.')
            client=portal_client(hosting_settings(store), PortalClient)
            remote=client.status(c['id'])
            if not remote.get('enabled') or remote.get('key_hash')!=c['key_hash'] or remote.get('revision')!=c['revision']:
                raise ValueError('Current access is not confirmed online. Publish inventory before emailing access.')
            key=portal_secrets.company_key(c['id'],c['key_hash'])
            if not key:raise ValueError('This company key is not available on this PC. Rotate the key before emailing access.')
            api_url=(workspace['feed_url'] if workspace else client.base+'/api/v1/inventory');pdf=guide_pdf(api_url,json.loads(c['kinds']))
            store.db.execute("UPDATE portal_companies SET last_email='Sending' WHERE id=?",(c['id'],))
            store.db.execute("UPDATE portal_mail_requests SET state='sending' WHERE client_id=?",(c['id'],));store.db.commit()
            dispatched=True
            if workspace:
                with connection_for(workspace) as connection:
                    import base64
                    connection.integration('email', 'send', buyer_id=c['id'], key=key,
                                           guide=base64.b64encode(pdf).decode('ascii'))
            else:
                send_invitation(c, key, api_url, pdf, sender=sender, password=password)
            store.db.execute("UPDATE portal_companies SET last_email='Sent via Yahoo Mail' WHERE id=?",(c['id'],))
            store.history(c['id'],'Email','Sent via Yahoo Mail','Yahoo Mail accepted the invitation. Inbox delivery is not confirmed.')
            self.task_done.emit('email-sent','Invitation accepted by Yahoo Mail for '+c['email'])
        except Exception as exc:
            detail=str(exc) if isinstance(exc,(ValueError,HostingError)) else 'Email processing failed ('+type(exc).__name__+').'
            uncertain = exc.uncertain if isinstance(exc, MailError) else dispatched
            if uncertain and not isinstance(exc, MailError):
                detail = 'Email outcome uncertain. Check Yahoo Sent mail before resending.'
            detail=redact_text(detail)
            store.db.execute('UPDATE portal_companies SET last_email=? WHERE id=?',(detail,c['id']))
            store.history(c['id'],'Email','Needs review' if uncertain else 'Failure',detail)
            self.problem.emit(c['id'],detail)
        finally:
            if client:client.close()
            store.db.execute('DELETE FROM portal_mail_requests WHERE client_id=?',(c['id'],));store.db.commit()
    def pump(self,store):
        try:
            with self.publisher_lock:
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
                    if self.paused or not p.get('enabled',True) or not feature_enabled(store, FEATURE_FOR_KIND.get(p['kind'])):continue
                    try:
                        path=Path(p['path']).resolve()
                        if str(path.parent) not in watched and path.parent.is_dir():observer.schedule(FileEvents(events),str(path.parent),recursive=False);watched.add(str(path.parent))
                        sig=(path.stat().st_size,path.stat().st_mtime_ns,json.dumps(p,sort_keys=True));name=p['id']
                        if str(path) in dirty:stable.states.pop(name,None)
                        if not force and not stable.ready(name,sig,time.monotonic()):continue
                        if force or signatures.get(name)!=sig:
                            store.cache(p,self.parse_profile(p));signatures[name]=sig
                            store.db.execute("DELETE FROM errors WHERE profile=?", (name,));store.db.commit()
                            errors=[] if publication_blocked(store) else store.queue_for_kind(p['kind'])
                            for detail in errors:self.problem.emit(name,detail)
                            self.changed.emit('Workbook validated: '+path.name)
                            if p['kind'] == 'Natural':
                                self.natural_validated.emit(p)
                    except Exception as exc:self._report(store,p,exc)
                if not self.paused and not self.isInterruptionRequested() and not publication_blocked(store):
                    self.pump(store)
                self.msleep(1000)
        finally:observer.stop();observer.join();store.db.close()
