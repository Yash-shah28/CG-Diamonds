"""Qt desktop, tray lifecycle, and single serialized background sync worker."""
import json
import os
import sys
import time
from queue import SimpleQueue, Empty
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from PySide6.QtCore import QLockFile, QThread, Signal, QTimer, Qt
from PySide6.QtGui import QAction, QIcon, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QPlainTextEdit, QLabel, QTabWidget, QLineEdit, QComboBox, QMessageBox,
    QFileDialog, QInputDialog, QSystemTrayIcon, QMenu, QStyle, QDialog,
    QFormLayout, QTableWidget, QTableWidgetItem, QDialogButtonBox, QDoubleSpinBox, QFrame)
from .core import Store, parse_excel, deliver, save_attempt, attempt_snapshot, ENDPOINT, SERVICE, FormulaResultError
from .brand import STYLE, app_icon, brand_pixmap
from .rates import apply_website_rate, rate_config, RateConfigError
from .ui import build_ui, nav_icon, configure_table, status_badge
from .dialogs import dialog_layout, dialog_buttons, choose_item, MappingField, ColumnLabelDelegate
from .monitor import Stability
from .instance import Instance
from .contract import FIELDS, new_profile, suggested_field
from .responses import response_description, redact_text
from .diagnostics import failure_detail, run_preflight
from .workbook_errors import workbook_error

class Events(FileSystemEventHandler):
    def __init__(self, wake):
        self.wake = wake
    def on_any_event(self, event):
        if event.event_type in ('modified', 'created', 'moved', 'deleted'):
            for path in (event.src_path, getattr(event, 'dest_path', '')):
                if path and not Path(path).name.startswith('~$'):
                    self.wake(str(Path(path).resolve()))

from .pull_worker import PullWorker as Worker
from .portal_store import PortalStore as Store

EXAMPLE = new_profile()

class Window(QMainWindow):
    def __init__(self, dbpath):
        super().__init__()
        self.setWindowTitle('Chintamani Gems')
        self.setWindowIcon(app_icon())
        self.setMinimumSize(980, 680)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        self.resize(min(1240, max(980, available.width() - 48)) if available else 1240, min(890, max(680, available.height() - 64)) if available else 890)
        self.setStyleSheet(STYLE)
        self.store = Store(dbpath, recover=True)
        self.worker = Worker(dbpath)
        self.worker.changed.connect(self.update_status)
        self.worker.problem.connect(self.notify_problem)
        self.worker.task_done.connect(self.task_finished)
        self.worker.published.connect(self.notify_published)
        self.worker.prepared.connect(self.confirm_inspection)
        self.worker.inspect_failed.connect(self.inspection_failed)
        self.worker.response_ready.connect(self.inspection_finished)
        self.worker.inspection_problem.connect(self.inspection_problem)
        self.worker.diagnostics_ready.connect(self.show_diagnostics)
        build_ui(self)
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip('Chintamani Gems - Inventory Studio')
        menu = QMenu()
        for name, fn in [('Open', self.open_window),('Pause / Resume',self.pause),('Exit',self.exit)]:
            action = menu.addAction(name); action.triggered.connect(fn)
        self.tray.setContextMenu(menu); self.tray.activated.connect(lambda reason: self.open_window() if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
        self.tray.messageClicked.connect(lambda:self.show_latest_issue())
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(3000)
        self.worker.start(); self.refresh()
    def navigate(self, index):
        self.pages.setCurrentIndex(index); self.page_title.setText(self.page_names[index])
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index); button.setIcon(nav_icon([0,1,6,2,3][number], '#1684e8' if number == index else '#334155'))

    def set_sidebar_collapsed(self, collapsed, persist=True):
        self.update_button.setToolButtonStyle(Qt.ToolButtonIconOnly if collapsed else Qt.ToolButtonTextBesideIcon)
        self.sidebar_collapsed = bool(collapsed); self.sidebar.setFixedWidth(64 if collapsed else 224); self.sidebar_brand.setVisible(not collapsed)
        self.sidebar.layout().setContentsMargins(8 if collapsed else 12,20,8 if collapsed else 12,14)
        for button in self.nav_buttons:
            button.setToolButtonStyle(Qt.ToolButtonIconOnly if collapsed else Qt.ToolButtonTextBesideIcon)
            button.setStyleSheet('border-radius:22px;' if collapsed else 'border-radius:6px;')
        self.collapse_button.setIcon(nav_icon(5 if collapsed else 4)); self.collapse_button.setText('' if collapsed else 'Collapse sidebar'); self.collapse_button.setToolTip('Expand sidebar' if collapsed else 'Collapse sidebar'); self.collapse_button.setAccessibleName(self.collapse_button.toolTip())
        if persist: self.store.set('ui.sidebar_collapsed', self.sidebar_collapsed)

    def update_rate_draft(self, website='drc', initial=False):
        row = self.rate_rows[website]; mode = row['mode']; percent = row['percent']
        percent.blockSignals(True); percent.setMaximum(100 if mode.currentIndex() else 99.99); percent.blockSignals(False)
        if not initial:
            row['dirty'] = True; row['save'].setEnabled(True); row['status'].setText('Not saved'); row['status'].setObjectName('muted')

    def rebuild_rates(self):
        from .ui import rebuild_rate_rows
        rebuild_rate_rows(self)
    def save_rate(self, website):
        row=self.rate_rows[website]
        try:
            row['percent'].interpretText()
            self.store.set_rate(website,'increase' if row['mode'].currentIndex() else 'discount',str(row['percent'].value()))
            row['status'].setText('Saved - publication queued');row['save'].setEnabled(False)
            self.update_status('Company rate saved; publishing its prepared feed')
        except ValueError as exc:QMessageBox.warning(self,'Rate needs attention',redact_text(str(exc)))
        except Exception:QMessageBox.warning(self,'Rate could not be saved','Check local storage and enter a valid percentage.')
        self.refresh()
    def notify_problem(self, identity, detail):
        detail=redact_text(detail);self.latest_issue=detail
        self.update_status(detail)
        notices=getattr(self,'_issue_notices',{});fingerprint=(identity,detail);now=time.monotonic()
        if now-notices.get(fingerprint,-1000)>=60:
            self.tray.showMessage('Chintamani Gems - issue',detail,QSystemTrayIcon.Warning,10000)
            notices[fingerprint]=now
        self._issue_notices=notices
    def show_latest_issue(self):
        self.open_window();self.navigate(4)
        if getattr(self,'latest_issue',None):self.show_detail('Issue details',self.latest_issue)
    def show_detail(self,title,detail):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(760,460);layout=dialog_layout(dialog)
        editor=QPlainTextEdit(redact_text(detail));editor.setReadOnly(True);layout.addWidget(editor)
        buttons=QDialogButtonBox(QDialogButtonBox.Close);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons);dialog.exec()
    def task_finished(self, action, message):
        if action=='error':self.notify_problem('operation',message)
        else:
            self.api_setup_status.setText(message);self.update_status(message)
        self.refresh()
    def notify_published(self, company):
        self.update_status(company+' inventory published to API. Website import is not confirmed.')
        self.tray.showMessage('Inventory published',company+' can fetch its latest inventory.',QSystemTrayIcon.Information)
        self.refresh()
    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
    def notify_sent(self, inventory_kind):
        message = inventory_kind + ' inventory successfully sent to DRC. Push queued successfully.'
        self.update_status(message)
        self.tray.showMessage('Inventory sent to DRC', message, QSystemTrayIcon.Information, 10000)
        self.refresh()
    def update_status(self, message):
        self.monitor_badge.setText('Paused' if self.worker.paused else 'Monitoring')
        self.pause_button.setText('Resume monitoring' if self.worker.paused else 'Pause monitoring')
        self.status.setText(('Paused • ' if self.worker.paused else 'Active • ') + message)
    def sync(self):
        self.worker.manual = True
        self.update_status('Validating and publishing connected inventory; no direct DRC request will be made')
    def begin_inspection(self):
        self.sync()
    def confirm_inspection(self,profile,payload):
        self.show_detail('Validated inventory preview',json.dumps({'diamonds':payload['diamonds'][:10]},indent=2,ensure_ascii=False)+'\n\nRead-only preview. Direct DRC sending is disabled.')
    def inspection_failed(self, message):
        self.inspect_button.setEnabled(True)
        QMessageBox.warning(self, 'Could not prepare inventory', message)
    def inspection_finished(self, job_id):
        self.inspect_button.setEnabled(True)
        self.refresh()
        self.show_response(job_id)
    def inspection_problem(self, result):
        self.inspect_button.setEnabled(True)
        self.show_response(result['job']['id'], snapshot=result)
    def show_diagnostics(self, report):
        text = '\n\n'.join(f"{c['stage']}: {'Ready' if c['ok'] else 'Failed'}\n{c['detail']}" for c in report['checks'])
        QMessageBox.information(self, 'Local setup check — no inventory sent', text)
    def view_response(self):
        job_id, ok = QInputDialog.getInt(self, 'View response', 'Sync history job ID', 1, 1)
        if ok: self.show_response(job_id)
    def show_response(self, job_id, snapshot=None):
        job = snapshot['job'] if snapshot else self.store.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if job is None:
            QMessageBox.information(self, 'Job not found', 'Choose an existing sync history job ID.'); return
        responses = [snapshot['response']] if snapshot else self.store.db.execute('SELECT * FROM responses WHERE job_id=? ORDER BY id DESC', (job_id,)).fetchall()
        dialog = QDialog(self); dialog.setWindowTitle(f'DRC response — job #{job_id}' if job_id else 'DRC response — unsaved manual request'); dialog.resize(820, 640)
        layout = dialog_layout(dialog)
        if snapshot:
            layout.addWidget(QLabel('History could not be saved completely. This response is available in this window only.'))
        picker = QComboBox()
        for response in responses:
            picker.addItem(f"Request attempt {response['attempt']} • {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(response['created']))}")
        picker.setVisible(bool(responses)); layout.addWidget(picker)
        text = QPlainTextEdit(); text.setReadOnly(True); layout.addWidget(text)
        def update(index=0):
            text.setPlainText(response_description(job, responses[index] if responses else None))
        picker.currentIndexChanged.connect(update); update()
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        dialog.exec()
    def pause(self):
        self.worker.paused = not self.worker.paused
        self.update_status('Monitoring paused' if self.worker.paused else 'Monitoring resumed')
    def open_history_response(self,row):
        if not 0<=row<len(self.history_ids):return
        job_id=self.history_ids[row]
        if job_id and job_id>0:self.show_response(job_id)
        else:self.show_detail('History details',self.history_details[row])
    def connection_settings(self,inventory_kind):
        from .portal_ui import hosting_settings
        hosting_settings(self)
    def refresh(self):
        from .dashboard import history_entries
        from .portal_ui import refresh_companies, format_access_summary
        profiles=self.store.get('profiles',[])
        companies=self.store.companies()
        comp_by_id={c['id']:c for c in companies}

        rows=self.store.db.execute("SELECT j.*,r.body AS response_body,r.status AS response_status,r.filename AS response_filename,r.kind AS response_kind FROM jobs j LEFT JOIN responses r ON r.id=(SELECT MAX(id) FROM responses WHERE job_id=j.id) ORDER BY j.id DESC").fetchall()
        entries=history_entries(rows,profiles)
        for e in entries:
            e['diamond_type']='Legacy '+e['kind']
            e['op_type']='Legacy Publish'
        for error in self.store.db.execute('SELECT * FROM errors ORDER BY id DESC LIMIT 100'):
            p=next((p for p in profiles if p['id']==error['profile']),{})
            entries.append(dict(id=None,created=error['created'],diamond_type=p.get('kind',error['profile']),op_type='Validation Error',records=0,result='Failure',detail=error['detail']))
        for e in self.store.db.execute('SELECT * FROM portal_history ORDER BY id DESC LIMIT 300'):
            c=comp_by_id.get(e['client_id'])
            if c and c.get('kinds'):
                try:kinds_list=json.loads(c['kinds']);d_type=', '.join(kinds_list)
                except Exception:d_type='All'
            else:d_type='All'
            event=e['event'];cname=e['name']
            if 'fetch' in event.lower():op_type=f"Fetch by {cname}"
            elif event.lower() in ('publish','email'):op_type=f"{event.title()} ({cname})"
            else:op_type=f"{event} ({cname})"
            entries.append(dict(id=-e['id'],created=e['created'],diamond_type=d_type,op_type=op_type,records=e['records'],result=e['state'],detail=e['detail']))

        cache=getattr(self,'_access_cache',{})
        for cid,acc in cache.items():
            if acc and isinstance(acc,dict) and acc.get('fetch_count') and acc.get('last_fetched_at'):
                from datetime import datetime
                try:ts=acc['last_fetched_at'].replace('Z','+00:00');created_sec=datetime.fromisoformat(ts).timestamp()
                except Exception:created_sec=time.time()
                cname=acc.get('company_name') or comp_by_id.get(cid,{}).get('name') or 'Client'
                nat=acc.get('natural_count',0);lab=acc.get('lab_grown_count',0);tot=acc.get('total_count',nat+lab)
                if nat and lab:d_type='Natural & Lab Grown'
                elif nat:d_type='Natural'
                elif lab:d_type='Lab Grown'
                else:d_type='All'
                entries.append(dict(id=None,created=created_sec,diamond_type=d_type,op_type=f"Fetch by {cname}",records=tot,result='Success',detail=f"API download from IP {acc.get('last_ip')} using {acc.get('last_user_agent')}. Delivered: {nat} Natural, {lab} Lab Grown."))

        entries.sort(key=lambda e:e['created'],reverse=True)

        nat_row=self.store.db.execute("SELECT payload FROM portal_cache WHERE kind='Natural'").fetchone()
        nat_count=0
        if nat_row:
            try:nat_count=len(json.loads(nat_row['payload']).get('diamonds',[]))
            except Exception:pass
        lab_row=self.store.db.execute("SELECT payload FROM portal_cache WHERE kind='Lab Grown'").fetchone()
        lab_count=0
        if lab_row:
            try:lab_count=len(json.loads(lab_row['payload']).get('diamonds',[]))
            except Exception:pass

        if 'Natural diamonds' in self.metric_values:self.metric_values['Natural diamonds'].setText(f"{nat_count:,}" if nat_count else "0")
        if 'Lab Grown diamonds' in self.metric_values:self.metric_values['Lab Grown diamonds'].setText(f"{lab_count:,}" if lab_count else "0")
        if 'Sent to DRC' in self.metric_values:self.metric_values['Sent to DRC'].setText(str(sum(e['result']=='Published to API' for e in entries)))
        if 'Needs review' in self.metric_values:self.metric_values['Needs review'].setText(str(sum(e['result']=='Needs review' for e in entries)))
        if 'Connected files' in self.metric_values:self.metric_values['Connected files'].setText(str(len(profiles)))
        if 'Failure' in self.metric_values:self.metric_values['Failure'].setText(str(sum(e['result']=='Failure' for e in entries)))

        latest_fetch=None;latest_ts=""
        for cid,acc in cache.items():
            if acc and isinstance(acc,dict) and acc.get('last_fetched_at'):
                if acc['last_fetched_at']>latest_ts:latest_ts=acc['last_fetched_at'];latest_fetch=acc

        if hasattr(self,'dashboard_fetch_info'):
            if latest_fetch and latest_fetch.get('fetch_count'):
                cname=latest_fetch.get('company_name') or comp_by_id.get(latest_fetch.get('client_id'),{}).get('name') or 'Client'
                f_info=format_access_summary(latest_fetch)
                self.dashboard_fetch_info.setText(
                    f"<b>Company:</b> {cname} &nbsp;•&nbsp; <b>{f_info['header']}</b><br>"
                    f"<span style='color: #2563eb; font-weight: 600;'>{f_info['diamonds']}</span> &nbsp;•&nbsp; "
                    f"<span style='color: #64748b;'>IP: {f_info['ip']} ({f_info['agent']})</span>"
                )
            else:
                self.dashboard_fetch_info.setText("No client API fetches recorded yet.")

        def last(label):
            e=next((e for e in entries if e['result']==label),None)
            return time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(e['created'])) if e else 'None'
        self.summary.setText('Last publication: '+last('Published to API')+'\nLast failure: '+last('Failure')+'\nWebsite imports are not confirmed by publication. Legacy DRC requests remain read-only.')
        pending=self.store.db.execute("SELECT COUNT(*) FROM portal_jobs WHERE state IN ('pending','publishing')").fetchone()[0]
        self.rate_warning.setText(str(pending)+' publishing/access operation(s) pending. Check API Access setup if hosting is not ready.');self.rate_warning.setVisible(bool(pending))
        selected=self.history_filter.currentText();visible=[e for e in entries if selected=='All results' or e['result']==selected][:300]
        self.history.setRowCount(len(visible));self.history_ids=[e['id'] for e in visible];self.history_details=[e['detail'] for e in visible]
        for row,e in enumerate(visible):
            d_time=time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(e['created']))
            d_type=e.get('diamond_type','All')
            status=e['result']
            results=f"{e['records']} diamonds" if (isinstance(e['records'],int) and e['records']>0) else str(e['records'])
            op_type=e.get('op_type','Operation')
            detail=e['detail']

            values=[d_time,d_type,status,results,op_type,detail]
            for column,value in enumerate(values):
                item=QTableWidgetItem('' if column==2 else value);item.setToolTip(value)
                if column==2:item.setData(Qt.UserRole,value);self.history.setCellWidget(row,column,status_badge(value))
                self.history.setItem(row,column,item)
        for kind,label in self.connection_labels.items():
            p=next((p for p in profiles if p['kind']==kind),None)
            label.setText((p['path']+'\nWorksheet: '+p['sheet']+' - '+str(len(p['mapping']))+' mapped columns - '+('Monitoring enabled' if p.get('enabled',True) else 'Monitoring disabled')) if p else 'No Excel workbook connected')
        refresh_companies(self)
    def read_profiles(self):
        text = self.editor.toPlainText().strip()
        if not text:
            return []
        try:
            profiles = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Profile settings contain invalid JSON at line {exc.lineno}, column {exc.colno}. Use Reload saved settings to restore your saved configuration.') from None
        if isinstance(profiles, dict):
            profiles = [profiles] if profiles else []
        if not isinstance(profiles, list) or any(not isinstance(p, dict) or not {'id', 'mapping', 'path', 'sheet', 'kind'} <= p.keys() for p in profiles):
            raise ValueError('Profile settings must contain inventory profiles. Use Reload saved settings to restore your saved configuration, then use Add Excel file.')
        return profiles
    def reload_profiles(self):
        self.editor.setPlainText(json.dumps(self.store.get('profiles', []), indent=2))
        self.update_status('Saved profile settings restored')
    def save(self):
        try:
            profiles = self.read_profiles()
            if not isinstance(profiles, list): raise ValueError()
            ids = set()
            for p in profiles:
                if not p['id'] or p['id'] in ids or p['kind'] not in ('Natural','Lab Grown'): raise ValueError()
                ids.add(p['id'])
                if not isinstance(p['mapping'], dict) or not p['path']: raise ValueError()
                if not p['sheet']: raise ValueError()
                if any('key' in k.lower() for k in p): raise ValueError('Credentials belong in API credentials tab')
                if not isinstance(p.get('required', []), list) or not isinstance(p.get('numeric', []), list): raise ValueError()
                if not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in p['mapping'].items()): raise ValueError()
                if len(set(p['mapping'].values())) != len(p['mapping']): raise ValueError()
            self.store.set('profiles', profiles)
            self.update_status('Profiles saved')
            return True
        except Exception:
            QMessageBox.warning(self, 'Invalid configuration', 'Check JSON, unique profile IDs, kind, sheet and mappings. Check workbook and column mappings.')
            return False
    def remove_profile(self, kind):
        profiles = self.store.get('profiles', [])
        existing = next((p for p in profiles if p.get('kind') == kind), None)
        if not existing:
            return
        if QMessageBox.question(self, f'Disconnect {kind}', f'Disconnect {kind} Diamonds Excel workbook?\nYou can reconnect it anytime.') != QMessageBox.Yes:
            return
        profiles = [p for p in profiles if p.get('kind') != kind]
        self.store.set('profiles', profiles)
        self.store.db.execute('DELETE FROM portal_cache WHERE kind=?', (kind,))
        self.store.db.execute("DELETE FROM errors WHERE profile=?", (existing.get('id', ''),))
        self.store.db.commit()
        self.editor.setPlainText(json.dumps(profiles, indent=2))
        self.update_status(f'{kind} workbook disconnected')
        self.refresh()
    def add_file(self, inventory_kind=None, persist=False, edit=False):
        try:
            profiles = self.read_profiles()
        except ValueError as exc:
            QMessageBox.warning(self, 'Profile settings', str(exc)); return
        existing = next((p for p in profiles if p['kind'] == inventory_kind), None)
        if edit and not existing:
            QMessageBox.information(self, 'Connect Excel first', 'Connect a workbook before editing its mapping.'); return
        path = existing['path'] if edit else QFileDialog.getOpenFileName(self,'Select inventory workbook','','Excel (*.xlsx)')[0]
        if not path: return
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            try:
                sheet, ok = choose_item(self, 'Worksheet', 'Choose inventory sheet', wb.sheetnames, 0, False)
                if not ok: return
                headers = next(wb[sheet].values, ())
            finally:
                wb.close()
            dialog = QDialog(self); dialog.setWindowTitle('Column mapping'); dialog.resize(860,640);dialog.setMinimumSize(680,440)
            form = dialog_layout(dialog)
            heading=QLabel('Column mapping');heading.setObjectName('dialogTitle');form.addWidget(heading)
            kind = QComboBox(); kind.addItems([inventory_kind] if inventory_kind else ['Natural','Lab Grown']);kind.setMinimumHeight(40)
            if inventory_kind:
                kind.hide();badge=QLabel(inventory_kind+' diamonds');badge.setObjectName('badge');form.addWidget(badge,0,Qt.AlignLeft)
            else:form.addWidget(kind)
            note=QLabel('Match your Excel columns to inventory fields. Leave a field blank to skip it.');note.setWordWrap(True);note.setObjectName('muted');form.addWidget(note)
            table=QTableWidget(len(headers),2);table.setObjectName('mappingTable');table.setHorizontalHeaderLabels(['Excel column','API field']);configure_table(table,56,False);table.setColumnWidth(0,240);table.setItemDelegateForColumn(0,ColumnLabelDelegate(table))
            used = set()
            for index, header in enumerate(headers):
                table.setItem(index, 0, QTableWidgetItem(str(header or '')))
                field = QComboBox(); field.setEditable(True); field.lineEdit().setPlaceholderText('Skip column'); field.addItems([''] + list(FIELDS))
                suggested = (existing.get('mapping', {}).get(str(header).strip()) or suggested_field(header)) if existing else suggested_field(header)
                if suggested in used: suggested = ''
                if suggested: used.add(suggested)
                field.setCurrentText(suggested); table.setCellWidget(index, 1, MappingField(field))
            table.horizontalHeader().setStretchLastSection(True);form.addWidget(table,1)
            form.addWidget(dialog_buttons(dialog,'Save mapping'))
            if dialog.exec() != QDialog.Accepted: return
            p = dict(existing) if existing else new_profile(); p['path']=path; p['id']=existing['id'] if existing else 'feed-'+str(time.time_ns())
            p['sheet'] = sheet; p['kind'] = kind.currentText()
            p.setdefault('upload_on_save', True)
            p['mapping'] = {str(headers[i]).strip(): table.cellWidget(i, 1).currentText().strip() for i in range(len(headers)) if table.cellWidget(i, 1).currentText().strip()}
            if inventory_kind: profiles = [old for old in profiles if old['kind'] != inventory_kind]
            profiles.append(p); self.editor.setPlainText(json.dumps(profiles,indent=2))
            if persist:
                if self.save():
                    self.sync()
                else:
                    self.reload_profiles()
                self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, 'Could not add workbook', workbook_error({'path':path,'kind':inventory_kind or 'Inventory'},exc)+'\nYour saved profiles are unchanged.')
    def auto_map(self):
        try:
            from openpyxl import load_workbook
            profiles = self.read_profiles()
            for profile in profiles:
                wb = load_workbook(profile['path'], read_only=True, data_only=True)
                try: headers = next(wb[profile['sheet']].values, ())
                finally: wb.close()
                used = set(profile['mapping'].values())
                for header in headers:
                    column = str(header or '').strip()
                    target = suggested_field(column)
                    if column not in profile['mapping'] and target and target not in used:
                        profile['mapping'][column] = target
                        used.add(target)
                profile['confirmed'] = False
            self.editor.setPlainText(json.dumps(profiles, indent=2))
            self.update_status('Mappings suggested. Preview the API payload, then save profiles.')
        except Exception as exc:
            QMessageBox.warning(self, 'Mapping failed', workbook_error(locals().get('profile',{}),exc)+'\nNo mappings were saved.')
    def preview_payload(self):
        try:
            profiles = self.read_profiles()
            if not profiles:
                QMessageBox.information(self, 'No files', 'Add an Excel file first.'); return
            labels = [f"{p['id']} ({p['kind']})" for p in profiles]
            chosen, ok = choose_item(self, 'Payload preview', 'Choose inventory profile', labels, 0, False)
            if not ok: return
            profile = profiles[labels.index(chosen)]
            payload = parse_excel(profile)
            dialog = QDialog(self); dialog.setWindowTitle('API payload preview — nothing is sent'); dialog.resize(780, 600)
            layout = dialog_layout(dialog)
            layout.addWidget(QLabel(f"Validated {len(payload['diamonds'])} records. Showing the first 10 records."))
            editor = QPlainTextEdit(json.dumps({'diamonds': payload['diamonds'][:10]}, indent=2, ensure_ascii=False))
            editor.setReadOnly(True); layout.addWidget(editor)
            buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
            dialog.exec()
        except Exception as exc:
            QMessageBox.warning(self, 'Inventory preview failed', workbook_error(locals().get('profile',{}),exc))
    def preview(self):
        path, _ = QFileDialog.getOpenFileName(self,'Preview workbook','','Excel (*.xlsx)')
        if not path: return
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path,read_only=True,data_only=True)
            try:
                sheet, ok = choose_item(self,'Worksheet','Choose sheet',wb.sheetnames,0,False)
                if ok:
                    from itertools import islice
                    text = '\n'.join(str(r) for r in islice(wb[sheet].values, 11))
                    QMessageBox.information(self,'First 10 rows',text)
            finally: wb.close()
        except Exception as exc:
            QMessageBox.warning(self,'Preview failed',workbook_error({'path':path},exc))
    def save_key(self):
        QMessageBox.information(self,'Pull publishing','Manage company API access in API Access. DRC upload keys are not used.')
    def retry(self):
        QMessageBox.information(self,'Legacy history','Legacy DRC jobs are read-only and cannot be resent. Publish current inventory through the pull API instead.')
    def closeEvent(self,event):
        if QSystemTrayIcon.isSystemTrayAvailable(): self.hide(); event.ignore()
        else: self.exit(); event.accept()
    def exit(self):
        if hasattr(self, 'updater'): self.updater.stop()
        self.worker.requestInterruption(); self.worker.wait(); self.tray.hide(); QApplication.quit()

def main():
    if '--provision-credentials' in sys.argv or '--credential-self-test' in sys.argv:
        raise SystemExit(2)  # Legacy DRC provisioning is intentionally disabled.
    if '--portal-self-test' in sys.argv:
        import argparse,tempfile
        from .portal_store import PortalStore
        from .google_mail import SCOPES
        from .integration_guide import guide_pdf
        parser=argparse.ArgumentParser();parser.add_argument('--portal-self-test',action='store_true');parser.add_argument('--diagnostics-output',required=True);args=parser.parse_args()
        with tempfile.TemporaryDirectory() as directory:
            test_store=PortalStore(Path(directory)/'test.db')
            company_id=test_store.add_company('Offline test','test@example.com',['Natural'],secret_writer=lambda *a:None)
            assert test_store.company(company_id)['status']=='Draft'
            test_store.db.close()
        pdf=guide_pdf('https://inventory.example.com/api/v1/inventory',['Natural'])
        report={'ok':pdf.startswith(b'%PDF'),'offline':True,'requests_sent':0,'mode':'pull-only','legacy_dispatch':False,'google_scope_send_only':SCOPES[-1].endswith('gmail.send'),'pdf_ready':True}
        Path(args.diagnostics_output).write_text(json.dumps(report,indent=2));return
    if '--diagnose' in sys.argv:
        import argparse
        import threading
        parser = argparse.ArgumentParser()
        parser.add_argument('--diagnose',action='store_true')
        parser.add_argument('--inventory-kind',choices=['Natural','Lab Grown'],default='Natural')
        parser.add_argument('--diagnostics-output',required=True)
        args = parser.parse_args()
        result = []
        thread = threading.Thread(target=lambda: result.append(run_preflight(args.inventory_kind)))
        thread.start(); thread.join()
        Path(args.diagnostics_output).write_text(json.dumps(result[0],indent=2),encoding='utf-8')
        return
    startup = '--startup' in sys.argv
    app = QApplication([arg for arg in sys.argv if arg != '--startup'])
    app.setApplicationName('Chintamani Gems')
    app.setApplicationDisplayName('Chintamani Gems')
    app.setWindowIcon(app_icon())
    app.setStyle('Fusion')
    if os.name == 'nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('ChintamaniGems.Inventory')
    app.setQuitOnLastWindowClosed(False)
    # Inno Setup checks this mutex and never force-closes a running sync worker.
    mutex = None
    if os.name == 'nt':
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'Local\\ChintamaniGemsInventory')
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DRCInventorySync'
    root.mkdir(parents=True,exist_ok=True)
    instance = Instance(root)
    if not instance.acquire():
        if not instance.notify():
            QMessageBox.information(None, 'App is starting', 'The app is still starting or an older version is running. Open it from the Chintamani Gems tray icon, or exit that copy and reopen this version.')
        return
    window = Window(root/'sync.db')
    if os.environ.get('CHINTAMANI_DISABLE_UPDATES') != '1':
        from .update_ui import UpdateController
        window.updater = UpdateController(window, root/'updates', can_install=bool(getattr(sys, 'frozen', False)))
    else:
        window.update_button.setEnabled(False)
        window.update_button.setToolTip('Updates are disabled for this test session.')
    if not startup or not QSystemTrayIcon.isSystemTrayAvailable(): window.show()
    instance.activated.connect(window.open_window)
    try:
        app.exec()
    finally:
        instance.close()
        if mutex: ctypes.windll.kernel32.CloseHandle(mutex)

if __name__ == '__main__': main()
