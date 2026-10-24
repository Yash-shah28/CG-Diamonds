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
from .credentials import read_key, save_key
from .instance import Instance
from .contract import FIELDS, new_profile, suggested_field
from .responses import response_description, redact_text
from .diagnostics import failure_detail, run_preflight

class Events(FileSystemEventHandler):
    def __init__(self, wake):
        self.wake = wake
    def on_any_event(self, event):
        if event.event_type in ('modified', 'created', 'moved', 'deleted'):
            for path in (event.src_path, getattr(event, 'dest_path', '')):
                if path and not Path(path).name.startswith('~$'):
                    self.wake(str(Path(path).resolve()))

class Worker(QThread):
    changed = Signal(str)
    sent_to_drc = Signal(str)
    prepared = Signal(object, object)
    inspect_failed = Signal(str)
    response_ready = Signal(int)
    inspection_problem = Signal(object)
    diagnostics_ready = Signal(object)
    def __init__(self, dbpath):
        super().__init__()
        self.dbpath = dbpath
        self.paused = False
        self.manual = False
        self.requests = SimpleQueue()
    def prepare_inspection(self, profile):
        self.requests.put(('prepare', json.loads(json.dumps(profile)), None))
    def send_inspection(self, profile, payload):
        self.requests.put(('send', json.loads(json.dumps(profile)), payload))
    def process_inspection(self, store, request):
        action, profile, payload = request
        if action == 'diagnose':
            self.diagnostics_ready.emit(run_preflight(profile['kind'], key_reader=read_key))
        elif action == 'prepare':
            try:
                self.prepared.emit(profile, apply_website_rate(parse_excel(profile), store.get('website_rates', {}), profile.get('website', 'drc')))
            except ValueError as exc:
                self.inspect_failed.emit(str(exc))
            except Exception:
                self.inspect_failed.emit('The workbook could not be prepared. Check the file, worksheet and mappings.')
        elif action == 'send':
            try:
                job_id = store.enqueue_manual(profile['id'], payload, profile)
                job = store.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            except Exception as exc:
                detail = failure_detail('history saving (request creation)',exc)
                result = attempt_snapshot({'id':0,'attempts':0,'manual':1},profile,'','blocked',detail,error=detail)
                try: store.db.rollback()
                except Exception: pass
                self.inspection_problem.emit(result)
                return
            try:
                key = read_key(profile['kind'])
            except Exception as exc:
                detail = failure_detail('credential lookup',exc)
                result = save_attempt(store,job,profile,'','blocked',detail,error=detail)
            else:
                try:
                    result = deliver(store, job, profile, key, manual=True)
                except Exception as exc:
                    detail = failure_detail('request dispatch or history saving',exc,True)
                    result = save_attempt(store,job,profile,key,'review',detail,error=detail,dispatched=True)
            if result and not result['saved']:
                self.inspection_problem.emit(result)
                return
            self.response_ready.emit(job_id)
    def run(self):
        store = Store(self.dbpath)
        signatures = {}
        stable = Stability()
        observer = Observer()
        watched = set()
        manual_profiles = set()
        # Periodic checks are authoritative; events reduce recovery latency.
        events = SimpleQueue()
        handler = Events(events.put)
        observer.start()
        try:
            while not self.isInterruptionRequested():
                try:
                    request = self.requests.get_nowait()
                except Empty:
                    request = None
                if request is not None:
                    self.process_inspection(store, request)
                profiles = store.get('profiles', [])
                dirty = set()
                while True:
                    try: dirty.add(events.get_nowait())
                    except Empty: break
                force = self.manual
                self.manual = False
                if force:
                    manual_profiles.update(p['id'] for p in profiles)
                for p in profiles:
                    force_profile = p['id'] in manual_profiles
                    if self.paused or (not p.get('enabled', True) and not force_profile):
                        continue
                    try:
                        path = Path(p['path']).resolve()
                        if str(path.parent) not in watched and path.parent.is_dir():
                            observer.schedule(handler, str(path.parent), recursive=False)
                            watched.add(str(path.parent))
                        sig = (path.stat().st_size, path.stat().st_mtime_ns, json.dumps(p, sort_keys=True))
                        name = p['id']
                        if p.get('upload_on_save') and name not in signatures and not force_profile:
                            signatures[name] = sig
                            stable.ready(name, sig, time.monotonic())
                            continue
                        if str(path) in dirty:
                            if not p.get('upload_on_save'): signatures.pop(name, None)
                            stable.states.pop(name, None)
                        if force_profile:
                            signatures.pop(name, None)
                        if not stable.ready(name, sig, time.monotonic()):
                            continue
                        if force_profile or signatures.get(name) != sig:
                            payload = apply_website_rate(parse_excel(p), store.get('website_rates', {}), p.get('website', 'drc'))
                            new_job = store.enqueue(name, payload, p, force=p.get('upload_on_save') is True)
                            if new_job is None:
                                manual_profiles.discard(name)
                            signatures[name] = sig
                            self.changed.emit('Workbook validated: ' + path.name)
                    except (FormulaResultError, RateConfigError) as exc:
                        detail = redact_text(str(exc))
                        store.error(p['id'], detail)
                        self.changed.emit(detail)
                    except Exception as exc:
                        # Exception messages from external libraries may contain paths or data.
                        detail = 'Workbook unavailable or invalid (' + type(exc).__name__ + '). Check sheet, mapping, cells and file access.'
                        store.error(p['id'], detail)
                        self.changed.emit(detail)
                if not self.paused:
                    jobs = store.db.execute("SELECT * FROM jobs WHERE manual=0 AND state IN ('pending','retry') AND due<=? ORDER BY id", (time.time(),)).fetchall()
                    for job in jobs:
                        profile = next((p for p in profiles if p['id']==job['profile']), None)
                        if self.paused: break
                        if profile and (profile.get('enabled', True) or profile['id'] in manual_profiles):
                            try:
                                try:
                                    rate_config(store.get('website_rates', {}), profile.get('website', 'drc'))
                                except RateConfigError:
                                    store.finish(job, 'blocked', 'Invalid website rate. Correct Change Rates before uploading.'); self.changed.emit('Invalid website rate; correct Change Rates'); continue
                                key = read_key(profile['kind'])
                                deliver(store, job, profile, key)
                                state = store.db.execute('SELECT state FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
                                if state == 'sent': self.sent_to_drc.emit(profile['kind'])
                                self.changed.emit('Sync ' + state + '; see history')
                                manual_profiles.discard(profile['id'])
                            except Exception:
                                store.finish(job, 'blocked', 'Credential storage or local processing unavailable')
                self.msleep(1000)
        finally:
            observer.stop()
            observer.join()
            store.db.close()

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
        self.store = Store(dbpath)
        self.worker = Worker(dbpath)
        self.worker.changed.connect(self.update_status)
        self.worker.sent_to_drc.connect(self.notify_sent)
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
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(3000)
        self.worker.start(); self.refresh()
    def navigate(self, index):
        self.pages.setCurrentIndex(index); self.page_title.setText(self.page_names[index])
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index); button.setIcon(nav_icon(number, '#1684e8' if number == index else '#334155'))

    def set_sidebar_collapsed(self, collapsed, persist=True):
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

    def save_rate(self, website='drc'):
        row = self.rate_rows[website]
        try:
            row['percent'].interpretText()
            settings = self.store.get('website_rates', {})
            settings[website] = {'mode':'increase' if row['mode'].currentIndex() else 'discount', 'percent':str(row['percent'].value())}
            rate_config(settings, website); self.store.set('website_rates', settings)
        except Exception:
            QMessageBox.warning(self, 'Could not save rate', 'Check the percentage and local storage.'); return
        row['invalid'] = False; row['dirty'] = False; row['status'].setObjectName('muted'); row['status'].setText('Saved: ' + format(row['percent'].value(), '.2f') + '% ' + row['mode'].currentText().lower())
        self.update_status('Website rate saved; waiting for the next Excel save'); self.refresh()

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
        if any(word in message for word in ('failed', 'review', 'blocked', 'invalid')):
            now = time.monotonic()
            if now - getattr(self, '_last_notice', 0) > 60:
                self.tray.showMessage('Chintamani Gems needs attention', message, QSystemTrayIcon.Warning)
                self._last_notice = now
    def sync(self):
        self.worker.manual = True
        self.update_status('Manual scan requested')
    def begin_inspection(self):
        try:
            profiles = self.read_profiles()
            if not profiles:
                QMessageBox.information(self, 'No inventory profile', 'Add an Excel file and configure its mappings first.'); return
            labels = [f"{p['id']} ({p['kind']}) — {Path(p['path']).name}" for p in profiles]
            chosen, ok = choose_item(self, 'Send once & view response', 'Choose inventory profile', labels, 0, False)
            if not ok: return
            self.inspect_button.setEnabled(False)
            self.update_status('Preparing inventory snapshot; nothing has been sent')
            self.worker.prepare_inspection(profiles[labels.index(chosen)])
        except ValueError as exc:
            QMessageBox.warning(self, 'Profile settings', str(exc))
    def confirm_inspection(self, profile, payload):
        dialog = QDialog(self); dialog.setWindowTitle('Review before sending real inventory'); dialog.resize(760, 580)
        layout = dialog_layout(dialog)
        summary = QPlainTextEdit(f"Endpoint: {profile.get('endpoint', ENDPOINT)}\nFile: {profile['path']}\nWorksheet: {profile['sheet']}\nInventory: {profile['kind']}\nRecords: {len(payload['diamonds'])}\n\nThis sends real inventory to DRC. It may update or replace remote inventory.\nOnly the prepared snapshot below will be sent; later workbook changes are not included.\nAn unverified result stays Needs review. This request will not retry automatically.\n\nFirst 10 records:\n" + json.dumps({'diamonds':payload['diamonds'][:10]}, indent=2, ensure_ascii=False))
        summary.setReadOnly(True); layout.addWidget(summary)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        send = buttons.addButton('Send inventory', QDialogButtonBox.AcceptRole)
        send.clicked.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.update_status('Sending one inventory request; waiting for DRC response')
            self.worker.send_inspection(profile, payload)
        else:
            self.inspect_button.setEnabled(True)
            self.update_status('Manual send cancelled; nothing was sent')
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
    def open_history_response(self, row):
        if 0 <= row < len(self.history_ids) and self.history_ids[row] is not None:
            self.show_response(self.history_ids[row])

    def connection_settings(self, inventory_kind):
        profiles = self.read_profiles()
        profile = next((p for p in profiles if p['kind'] == inventory_kind), None)
        if not profile:
            QMessageBox.information(self, 'Connect Excel first', 'Connect the ' + inventory_kind + ' workbook first.'); return
        dialog = QDialog(self); dialog.setWindowTitle(inventory_kind + ' API connection')
        form = QFormLayout(dialog)
        form.addRow(QLabel('Endpoint: ' + profile.get('endpoint', ENDPOINT)))
        key = QLineEdit(); key.setEchoMode(QLineEdit.Password); key.setPlaceholderText('Leave blank to keep the saved API key')
        form.addRow('API key', key)
        enabled = QComboBox(); enabled.addItems(['Monitoring enabled', 'Monitoring disabled']); enabled.setCurrentIndex(0 if profile.get('enabled', True) else 1)
        form.addRow('Automatic monitoring', enabled)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            try:
                if key.text().strip(): save_key(inventory_kind, key.text().strip())
                profile['enabled'] = enabled.currentIndex() == 0
                self.editor.setPlainText(json.dumps(profiles, indent=2)); self.save(); self.refresh()
            except Exception:
                QMessageBox.warning(self, 'Credential error', 'Windows Credential Manager is unavailable.')

    def refresh(self):
        from .dashboard import history_entries
        profiles = self.store.get('profiles', [])
        invalid_sites = []
        for website in self.rate_rows:
            try: rate_config(self.store.get('website_rates', {}), website)
            except RateConfigError: invalid_sites.append(website.upper())
        self.rate_warning.setText('Correct the saved rate in Change Rates before uploading to: ' + ', '.join(invalid_sites))
        self.rate_warning.setVisible(bool(invalid_sites))
        rows = self.store.db.execute("SELECT j.*, r.body AS response_body, r.status AS response_status, r.filename AS response_filename, r.kind AS response_kind FROM jobs j LEFT JOIN responses r ON r.id=(SELECT MAX(id) FROM responses WHERE job_id=j.id) ORDER BY j.id DESC").fetchall()
        entries = history_entries(rows, profiles)
        for error in self.store.db.execute('SELECT * FROM errors ORDER BY id DESC LIMIT 50'):
            profile = next((p for p in profiles if p['id'] == error['profile']), {})
            entries.append(dict(id=None, created=error['created'], kind=profile.get('kind', error['profile']), filename=Path(profile.get('path', '')).name, records='—', result='Failure', detail=error['detail']))
        entries.sort(key=lambda e: e['created'], reverse=True)
        counts = {label: sum(e['result'] == label for e in entries) for label in ('Sent to DRC', 'Failure', 'Needs review', 'Blocked')}
        def last(label):
            match = next((e for e in entries if e['result'] == label), None)
            return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(match['created'])) if match else 'None'
        unsuccessful = next((e for e in entries if e['result'] in ('Failure', 'Blocked', 'Needs review')), None)
        unsuccessful_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(unsuccessful['created'])) if unsuccessful else 'None'
        self.metric_values['Sent to DRC'].setText(str(counts['Sent to DRC']))
        self.metric_values['Connected files'].setText(str(len(profiles)))
        self.metric_values['Needs review'].setText(str(counts['Needs review'] + counts['Blocked']))
        self.metric_values['Failure'].setText(str(counts['Failure']))
        self.summary.setText('Last successful delivery: ' + last('Sent to DRC') + '\nLast failed upload: ' + last('Failure') + '\nLast issue: ' + unsuccessful_time)

        selected = self.history_filter.currentText()
        visible = [e for e in entries if selected == 'All results' or e['result'] == selected][:300]
        self.history.setRowCount(len(visible)); self.history_ids = [e['id'] for e in visible]
        for row, entry in enumerate(visible):
            values = [time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['created'])), entry['kind'], entry['filename'], str(entry['records']), entry['result'], entry['detail']]
            for column, value in enumerate(values):
                item = QTableWidgetItem('' if column == 4 else value)
                if column == 4: item.setData(Qt.UserRole,value);item.setToolTip(value)
                if column == 4: item.setForeground(QColor('#286346' if entry['result'] in ('Sent to DRC', 'Success') else '#8a6222' if entry['result'] in ('Needs review', 'Blocked') else '#a33b35' if entry['result'] == 'Failure' else '#64748b'))
                if column == 3: item.setTextAlignment(Qt.AlignRight|Qt.AlignVCenter)
                self.history.setItem(row, column, item)
                if column == 4: self.history.setCellWidget(row,column,status_badge(entry['result']))
        for kind, label in self.connection_labels.items():
            profile = next((p for p in profiles if p['kind'] == kind), None)
            label.setText((profile['path'] + '\nWorksheet: ' + profile['sheet'] + ' • ' + str(len(profile['mapping'])) + ' mapped columns' + (' • Monitoring enabled' if profile.get('enabled', True) else ' • Monitoring disabled')) if profile else 'No Excel workbook connected')
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
                if not isinstance(p['mapping'], dict) or not Path(p['path']).is_file(): raise ValueError()
                if not p['sheet']: raise ValueError()
                if any('key' in k.lower() for k in p): raise ValueError('Credentials belong in API credentials tab')
                if not isinstance(p.get('required', []), list) or not isinstance(p.get('numeric', []), list): raise ValueError()
                if not all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in p['mapping'].items()): raise ValueError()
                if len(set(p['mapping'].values())) != len(p['mapping']): raise ValueError()
            old_profiles = {p['id']: p for p in self.store.get('profiles', [])}
            new_profiles = {p['id']: p for p in profiles}
            for name, old in old_profiles.items():
                if new_profiles.get(name) != old:
                    self.store.db.execute("UPDATE jobs SET state='superseded',payload=NULL WHERE profile=? AND state IN ('pending','retry','blocked')", (name,))
            self.store.set('profiles', profiles)
            self.update_status('Profiles saved')
            return True
        except Exception:
            QMessageBox.warning(self, 'Invalid configuration', 'Check JSON, unique profile IDs, kind, existing file, sheet and mappings. Check workbook and column mappings.')
            return False
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
            note=QLabel('Match your Excel columns to DRC fields. Leave a field blank to skip it.');note.setWordWrap(True);note.setObjectName('muted');form.addWidget(note)
            table=QTableWidget(len(headers),2);table.setObjectName('mappingTable');table.setHorizontalHeaderLabels(['Excel column','DRC field']);configure_table(table,56,False);table.setColumnWidth(0,240);table.setItemDelegateForColumn(0,ColumnLabelDelegate(table))
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
                if not self.save(): self.reload_profiles()
                self.refresh()
        except PermissionError:
            QMessageBox.warning(self, 'Workbook unavailable', 'Windows denied access to the workbook. Check file permissions or close Excel temporarily, then try again.')
        except Exception as exc:
            QMessageBox.warning(self, 'Could not add workbook', f'The workbook could not be read or configured ({type(exc).__name__}). Check that it is a valid .xlsx file and choose its worksheet again. Your saved profiles are unchanged.')
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
        except Exception:
            QMessageBox.warning(self, 'Mapping failed', 'Check each configured workbook and worksheet. No mappings were saved.')
    def preview_payload(self):
        try:
            profiles = self.read_profiles()
            if not profiles:
                QMessageBox.information(self, 'No files', 'Add an Excel file first.'); return
            labels = [f"{p['id']} ({p['kind']})" for p in profiles]
            chosen, ok = choose_item(self, 'Payload preview', 'Choose inventory profile', labels, 0, False)
            if not ok: return
            profile = profiles[labels.index(chosen)]
            payload = apply_website_rate(parse_excel(profile), self.store.get('website_rates', {}), profile.get('website', 'drc'))
            dialog = QDialog(self); dialog.setWindowTitle('API payload preview — nothing is sent'); dialog.resize(780, 600)
            layout = dialog_layout(dialog)
            layout.addWidget(QLabel(f"Validated {len(payload['diamonds'])} records. Showing the first 10 records."))
            editor = QPlainTextEdit(json.dumps({'diamonds': payload['diamonds'][:10]}, indent=2, ensure_ascii=False))
            editor.setReadOnly(True); layout.addWidget(editor)
            buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
            dialog.exec()
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid payload', str(exc))
        except Exception:
            QMessageBox.warning(self, 'Preview failed', 'Check the workbook, worksheet and mapping configuration.')
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
        except Exception:
            QMessageBox.warning(self,'Preview failed','Workbook is unavailable or invalid.')
    def save_key(self):
        try:
            if not self.key.text().strip(): return
            save_key(self.kind.currentText(),self.key.text().strip())
            self.key.clear(); self.update_status('Credential saved securely')
        except Exception:
            QMessageBox.warning(self,'Credential error','Windows Credential Manager is unavailable.')
    def retry(self):
        number, ok = QInputDialog.getInt(self,'Retry inventory push','Job ID',1,1)
        if not ok: return
        if QMessageBox.question(self,'Confirm resend','Verify remote inventory first. Resend this job?') != QMessageBox.Yes: return
        job = self.store.db.execute('SELECT * FROM jobs WHERE id=?',(number,)).fetchone()
        if job and job['manual']:
            QMessageBox.information(self, 'Manual send', 'Manual requests are never queued for retry. Use Send once & view response to explicitly prepare and send again.'); return
        if not job or not job['payload'] or job['state'] in ('sending','superseded','success'): return
        newer = self.store.db.execute('SELECT 1 FROM jobs WHERE profile=? AND id>?',(job['profile'],number)).fetchone()
        if newer:
            QMessageBox.warning(self,'Outdated job','Use the latest snapshot; older jobs cannot be retried.'); return
        self.store.db.execute("UPDATE jobs SET state='pending',due=0 WHERE id=?",(number,)); self.store.db.commit()
    def closeEvent(self,event):
        if QSystemTrayIcon.isSystemTrayAvailable(): self.hide(); event.ignore()
        else: self.exit(); event.accept()
    def exit(self):
        self.worker.requestInterruption(); self.worker.wait(); self.tray.hide(); QApplication.quit()

def main():
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
    root = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DRCInventorySync'
    root.mkdir(parents=True,exist_ok=True)
    instance = Instance(root)
    if not instance.acquire():
        if not instance.notify():
            QMessageBox.information(None, 'App is starting', 'The app is still starting or an older version is running. Open it from the Chintamani Gems tray icon, or exit that copy and reopen this version.')
        return
    window = Window(root/'sync.db')
    if not startup or not QSystemTrayIcon.isSystemTrayAvailable(): window.show()
    instance.activated.connect(window.open_window)
    try:
        app.exec()
    finally:
        instance.close()

if __name__ == '__main__': main()
