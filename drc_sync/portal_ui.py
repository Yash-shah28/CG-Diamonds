"""Company and Feed management widgets; all network work stays on the serialized worker."""
import json
import re
from PySide6.QtCore import Qt, QSize, QThread, Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,
 QTableWidgetItem,QDialog,QFormLayout,QLineEdit,QCheckBox,QFileDialog,QMessageBox,QDialogButtonBox,QHeaderView,QTabWidget,QFrame,QAbstractItemView)
from .portal_client import PortalClient
from .dialogs import dialog_layout,dialog_buttons
from .portal_client import DEFAULT_BASE,clean_base
from . import portal_secrets


# ── column indices ──────────────────────────────────────────────
_COL_NAME     = 0
_COL_SCOPE    = 1
_COL_EMAIL    = 2
_COL_ACTIVITY = 3
_COL_STATUS   = 4
_COL_ACTIONS  = 5
_NUM_COLS     = 6


def format_access_summary(acc):
    if not acc or not isinstance(acc, dict) or not acc.get('fetch_count'):
        return None
    ts = acc.get('last_fetched_at', '')
    dt_str = ''
    if ts:
        try:
            from datetime import datetime
            clean_ts = ts.replace('Z', '+00:00')
            dt = datetime.fromisoformat(clean_ts).astimezone()
            now = datetime.now().astimezone()
            if dt.date() == now.date():
                time_part = dt.strftime('%I:%M %p').lstrip('0')
                dt_str = f"Today at {time_part}"
            elif (now.date() - dt.date()).days == 1:
                time_part = dt.strftime('%I:%M %p').lstrip('0')
                dt_str = f"Yesterday at {time_part}"
            else:
                dt_str = dt.strftime('%b %d, %I:%M %p').replace(' 0', ' ')
        except Exception:
            dt_str = ts[:16].replace('T', ' ')

    count = acc.get('fetch_count', 0)
    fetch_plural = 'fetch' if count == 1 else 'fetches'
    header_text = f"Last: {dt_str} ({count} {fetch_plural})" if dt_str else f"{count} {fetch_plural}"

    nat = acc.get('natural_count')
    lab = acc.get('lab_grown_count')
    tot = acc.get('total_count', 0)
    if nat is not None or lab is not None:
        nat_val = nat or 0
        lab_val = lab or 0
        diamonds_text = f"{nat_val} Natural, {lab_val} Lab Grown ({tot} Total)"
    elif tot:
        diamonds_text = f"{tot} Total Diamonds"
    else:
        diamonds_text = "Diamonds delivered"

    return {
        'header': header_text,
        'diamonds': diamonds_text,
        'ip': acc.get('last_ip', 'unknown'),
        'agent': acc.get('last_user_agent', 'unknown'),
        'count': count,
        'dt_str': dt_str or 'Recent',
        'nat': nat or 0,
        'lab': lab or 0,
        'tot': tot
    }


def _make_activity_cell(acc):
    info = format_access_summary(acc)
    widget = QWidget()
    vbox = QVBoxLayout(widget)
    vbox.setContentsMargins(6, 4, 6, 4)
    vbox.setSpacing(2)

    if not info:
        lbl = QLabel("Not fetched yet")
        lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-style: italic;")
        vbox.addWidget(lbl)
        vbox.addStretch()
        return widget

    lbl_top = QLabel(f"<b>{info['header']}</b>")
    lbl_top.setStyleSheet("color: #1e293b; font-size: 12px;")
    lbl_sub = QLabel(info['diamonds'])
    lbl_sub.setStyleSheet("color: #2563eb; font-size: 11px; font-weight: 600;")

    tooltip = (
        f"Total Fetches: {info['count']}\n"
        f"Last Fetched: {acc.get('last_fetched_at', '')}\n"
        f"Delivered: {info['diamonds']}\n"
        f"Client IP: {info['ip']}\n"
        f"Client Tool: {info['agent']}"
    )
    widget.setToolTip(tooltip)
    vbox.addWidget(lbl_top)
    vbox.addWidget(lbl_sub)
    vbox.addStretch()
    return widget


class ActivityFetchWorker(QThread):
    access_updated = Signal(str, object)

    def __init__(self, hosting_settings, client_ids):
        super().__init__()
        self.settings = hosting_settings
        self.client_ids = client_ids

    def run(self):
        try:
            client = PortalClient(self.settings)
            try:
                for cid in self.client_ids:
                    if self.isInterruptionRequested():
                        break
                    try:
                        res = client.status(cid)
                        if res and isinstance(res, dict):
                            self.access_updated.emit(cid, res.get('latest_access'))
                    except Exception:
                        pass
            finally:
                client.close()
        except Exception:
            pass


def _feed_status_style(status):
    """Return (bg, fg) for a status pill."""
    s = (status or '').lower()
    if 'active' in s or 'published' in s:
        return '#e6f4ea', '#1a6b3a'
    if 'draft' in s:
        return '#fff8e6', '#7a5500'
    if 'revoked' in s or 'suspended' in s:
        return '#fdecea', '#a33b35'
    return '#f1f4f7', '#526172'


def _make_pill(text):
    """Return a centred pill QLabel."""
    bg, fg = _feed_status_style(text)
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedHeight(26)
    lbl.setStyleSheet(
        f'background:{bg};color:{fg};border-radius:12px;'
        f'padding:2px 10px;font-size:12px;font-weight:600;'
    )
    return lbl


class FeedbackFetchThread(QThread):
    finished = Signal(object, str)
    def __init__(self, hosting_settings, client_id):
        super().__init__()
        self.settings = hosting_settings
        self.client_id = client_id
    def run(self):
        try:
            client = PortalClient(self.settings)
            try:
                res = client.status(self.client_id)
                self.finished.emit(res, '')
            finally:
                client.close()
        except Exception as exc:
            self.finished.emit(None, str(exc))


def show_feedback_dialog(w, company_id):
    c = w.store.company(company_id)
    if not c:
        return
    d = QDialog(w)
    d.setWindowTitle(f"API Activity & Feedback - {c['name']}")
    d.resize(680, 560)
    layout = QVBoxLayout(d)
    layout.setSpacing(12)
    layout.setContentsMargins(20, 20, 20, 20)

    header = QLabel(f"<b>API Activity & Feedback for {c['name']}</b>")
    header.setStyleSheet("font-size: 16px; color: #1c4d88;")
    layout.addWidget(header)

    # ── Section 1: Latest API Fetch Activity ───────────────────────
    act_box = QFrame()
    act_box.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px;")
    act_layout = QVBoxLayout(act_box)
    act_layout.setContentsMargins(8, 8, 8, 8)
    act_layout.setSpacing(6)

    act_title = QLabel("<b>📥 Latest API Fetch (Diamonds Downloaded by Client)</b>")
    act_title.setStyleSheet("font-size: 13px; color: #0f172a; border: none;")
    act_layout.addWidget(act_title)

    act_status = QLabel("Checking latest fetch activity from cloud...")
    act_status.setStyleSheet("color: #64748b; font-size: 12px; border: none;")
    act_layout.addWidget(act_status)

    act_counts_row = QHBoxLayout()
    act_counts_row.setSpacing(8)
    act_c_nat = QLabel("<b>Natural:</b> -")
    act_c_nat.setStyleSheet("background: #eff6ff; color: #1d4ed8; padding: 6px 10px; border-radius: 4px; font-size: 12px; border: none;")
    act_c_lab = QLabel("<b>Lab Grown:</b> -")
    act_c_lab.setStyleSheet("background: #eff6ff; color: #1d4ed8; padding: 6px 10px; border-radius: 4px; font-size: 12px; border: none;")
    act_c_tot = QLabel("<b>Total:</b> -")
    act_c_tot.setStyleSheet("background: #f1f5f9; color: #334155; padding: 6px 10px; border-radius: 4px; font-size: 12px; border: none;")
    act_counts_row.addWidget(act_c_nat)
    act_counts_row.addWidget(act_c_lab)
    act_counts_row.addWidget(act_c_tot)
    act_counts_row.addStretch()
    act_layout.addLayout(act_counts_row)

    act_meta = QLabel()
    act_meta.setStyleSheet("color: #64748b; font-size: 11px; border: none;")
    act_layout.addWidget(act_meta)
    layout.addWidget(act_box)

    # ── Section 2: Client Import Feedback ──────────────────────────
    fb_title = QLabel("<b>📋 Client Import Feedback (Reported by Client Website)</b>")
    fb_title.setStyleSheet("font-size: 13px; color: #0f172a; margin-top: 4px;")
    layout.addWidget(fb_title)

    status_lbl = QLabel("Fetching latest feedback from cloud...")
    status_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
    layout.addWidget(status_lbl)

    cards_frame = QWidget()
    cards_row = QHBoxLayout(cards_frame)
    cards_row.setContentsMargins(0, 0, 0, 0)
    cards_row.setSpacing(10)

    card_success = QLabel("<b>Success:</b> -")
    card_success.setStyleSheet("background: #e6f4ea; color: #137333; padding: 8px 10px; border-radius: 6px; font-size: 12px;")
    card_failed = QLabel("<b>Failed:</b> -")
    card_failed.setStyleSheet("background: #fdecea; color: #c5221f; padding: 8px 10px; border-radius: 6px; font-size: 12px;")
    card_total = QLabel("<b>Total:</b> -")
    card_total.setStyleSheet("background: #f1f5f9; color: #334155; padding: 8px 10px; border-radius: 6px; font-size: 12px;")

    cards_row.addWidget(card_success)
    cards_row.addWidget(card_failed)
    cards_row.addWidget(card_total)
    cards_frame.setVisible(False)
    layout.addWidget(cards_frame)

    info_lbl = QLabel()
    info_lbl.setWordWrap(True)
    info_lbl.setStyleSheet("color: #475569; font-size: 12px;")
    info_lbl.setVisible(False)
    layout.addWidget(info_lbl)

    table = QTableWidget()
    table.setColumnCount(2)
    table.setHorizontalHeaderLabels(["Stock ID", "Failure / Rejection Reason"])
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setStyleSheet("border: 1px solid #dce3eb; border-radius: 4px; background: white;")
    table.setVisible(False)
    layout.addWidget(table)

    webhook_url = w.store.get('portal_hosting', {}).get('base_url', DEFAULT_BASE).rstrip('/') + '/api/v1/feedback'

    btn_row = QHBoxLayout()
    btn_copy = QPushButton("Copy Webhook cURL")
    btn_copy.setFixedHeight(32)
    btn_copy.setStyleSheet("background: white; border: 1px solid #c5d7ed; color: #1c4d88; padding: 0 12px; border-radius: 4px; font-weight: 600;")
    
    def copy_curl():
        key = portal_secrets.company_key(c['id'], c['key_hash']) or 'YOUR_API_KEY'
        curl_cmd = (
            f'curl -X POST "{webhook_url}" \\\n'
            f'  -H "x-api-key: {key}" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            '  -d \'{"success_count": 680, "failed_count": 2, "failures": [{"stock_id": "NAT-1002", "reason": "Missing cut grade"}]}\''
        )
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(curl_cmd)
        btn_copy.setText("Copied!")
    btn_copy.clicked.connect(copy_curl)
    btn_row.addWidget(btn_copy)
    btn_row.addStretch()

    close_btn = QPushButton("Close")
    close_btn.setFixedHeight(32)
    close_btn.setStyleSheet("background: #2f6ae6; color: white; border: none; border-radius: 4px; padding: 0 16px; font-weight: 600;")
    close_btn.clicked.connect(d.accept)
    btn_row.addWidget(close_btn)
    layout.addLayout(btn_row)

    thread = FeedbackFetchThread(w.store.get('portal_hosting', {}), company_id)
    def on_fetched(res, err):
        if err:
            status_lbl.setText(f"Could not load data from cloud: {err}")
            status_lbl.setStyleSheet("color: #c5221f; font-size: 13px;")
            act_status.setText("Could not load fetch activity.")
            return

        # ── Populate Activity Box ──────────────────────────────
        acc = res.get('latest_access') if res else None
        if acc and acc.get('fetch_count'):
            if not hasattr(w, '_access_cache'): w._access_cache = {}
            w._access_cache[company_id] = acc
            if hasattr(w, 'client_ids') and company_id in w.client_ids:
                row_idx = w.client_ids.index(company_id)
                w.clients_table.setCellWidget(row_idx, _COL_ACTIVITY, _make_activity_cell(acc))
            info = format_access_summary(acc)
            act_status.setText(f"<b>Last Fetched:</b> {info['header']}")
            act_status.setStyleSheet("color: #15803d; font-size: 12px; border: none;")
            act_c_nat.setText(f"<b>Natural:</b> {info['nat']}")
            act_c_lab.setText(f"<b>Lab Grown:</b> {info['lab']}")
            act_c_tot.setText(f"<b>Total Fetched:</b> {info['tot']}")
            act_meta.setText(f"<b>Client IP:</b> {info['ip']} &nbsp;|&nbsp; <b>Tool:</b> {info['agent']}")
        else:
            act_status.setText("No API fetches recorded yet for this company key.")
            act_status.setStyleSheet("color: #94a3b8; font-size: 12px; font-style: italic; border: none;")
            act_c_nat.setText("<b>Natural:</b> 0")
            act_c_lab.setText("<b>Lab Grown:</b> 0")
            act_c_tot.setText("<b>Total:</b> 0")
            act_meta.setText("")

        # ── Populate Feedback Section ──────────────────────────
        fb = res.get('latest_feedback') if res else None
        if not fb:
            status_lbl.setText("No feedback reported yet by this client.\nDevelopers should POST import results to:\n" + webhook_url)
            status_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
            return
        status_lbl.setText(f"Last reported: {fb.get('recorded_at', 'Unknown')}")
        status_lbl.setStyleSheet("color: #137333; font-size: 12px; font-weight: 600;")
        card_success.setText(f"<b>Success:</b> {fb.get('success_count', 0)}")
        card_failed.setText(f"<b>Failed:</b> {fb.get('failed_count', 0)}")
        card_total.setText(f"<b>Total Reported:</b> {fb.get('total_reported', 0)}")
        cards_frame.setVisible(True)

        comment = fb.get('comment')
        if comment:
            info_lbl.setText(f"<b>Client Note:</b> {comment}")
            info_lbl.setVisible(True)

        failures = fb.get('failures') or []
        if failures:
            table.setRowCount(len(failures))
            for idx, item in enumerate(failures):
                s_item = QTableWidgetItem(str(item.get('stock_id', '')))
                r_item = QTableWidgetItem(str(item.get('reason', '')))
                table.setItem(idx, 0, s_item)
                table.setItem(idx, 1, r_item)
            table.setVisible(True)
        else:
            if fb.get('failed_count', 0) == 0:
                info_lbl.setText((info_lbl.text() + "\n" if comment else "") + "All diamonds imported successfully with zero errors!")
                info_lbl.setStyleSheet("color: #137333; font-size: 12px; font-weight: 600;")
                info_lbl.setVisible(True)

    thread.finished.connect(on_fetched)
    thread.start()
    d.finished.connect(thread.wait)
    d.exec()


def _row_actions(w, company_id):
    """Return a compact widget with Publish · Edit · Delete buttons for one row."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(6, 4, 6, 4)
    row.setSpacing(4)

    pub = QPushButton('Publish')
    pub.setFixedHeight(30)
    pub.setStyleSheet(
        'font-size:12px;font-weight:600;padding:0 10px;'
        'background:#2f6ae6;color:white;border:none;border-radius:4px;'
    )

    fb = QPushButton('📋 Feedback')
    fb.setObjectName('quiet')
    fb.setFixedHeight(30)
    fb.setStyleSheet(
        'font-size:12px;padding:0 8px;border-radius:4px;'
        'background:white;color:#1c4d88;border:1px solid #c5d7ed;'
    )
    fb.setToolTip('View import feedback and failed diamond reasons from this client')
    fb.clicked.connect(lambda: show_feedback_dialog(w, company_id))

    edit = QPushButton('✎ Edit')
    edit.setObjectName('quiet')
    edit.setFixedHeight(30)
    edit.setStyleSheet(
        'font-size:12px;padding:0 10px;border-radius:4px;'
        'background:white;color:#475569;border:1px solid #d8e0e9;'
    )

    delete = QPushButton('✕')
    delete.setObjectName('quiet')
    delete.setFixedHeight(30)
    delete.setFixedWidth(30)
    delete.setToolTip('Revoke & delete feed')
    delete.setStyleSheet(
        'font-size:13px;padding:0;border-radius:4px;'
        'background:white;color:#a33b35;border:1px solid #f5c6c3;'
    )

    def do_publish():
        c = w.store.company(company_id)
        if not c:
            return
        text = ('Publish this feed inventory and email its API access?\n\n'
                'Feed Name: ' + c['name'] + '\nRecipient: ' + c['email'] +
                '\nInventory: ' + ', '.join(json.loads(c['kinds'])))
        if QMessageBox.question(w, 'Confirm invitation', text) != QMessageBox.Yes:
            return
        w.worker.request('email', c['id'])
        w.update_status('Preparing feed access for ' + c['name'])

    def do_edit():
        c = w.store.company(company_id)
        if c:
            create_feed_dialog(w, c)

    def do_delete():
        c = w.store.company(company_id)
        if not c:
            return
        if QMessageBox.question(
            w, 'Revoke feed access',
            f"Revoke API access for {c['name']}?\nThis does not delete its history."
        ) == QMessageBox.Yes:
            w.worker.request('revoke', c['id'])

    pub.clicked.connect(do_publish)
    edit.clicked.connect(do_edit)
    delete.clicked.connect(do_delete)

    row.addWidget(pub)
    row.addWidget(fb)
    row.addWidget(edit)
    row.addWidget(delete)
    row.addStretch()
    return holder


def api_page(w):
    from .ui import label
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)

    # ── toolbar ────────────────────────────────────────────────
    toolbar = QHBoxLayout()
    toolbar.setSpacing(8)

    title_lbl = label('Client Feeds', 'pageSection')
    title_lbl.setStyleSheet('font-size:17px;font-weight:600;color:#202b3a;')
    toolbar.addWidget(title_lbl)
    toolbar.addStretch()

    settings_btn = QPushButton('⚙  Settings')
    settings_btn.setObjectName('quiet')
    settings_btn.setFixedHeight(36)
    settings_btn.setStyleSheet('font-size:13px;padding:0 14px;border-radius:5px;')
    settings_btn.clicked.connect(lambda: hosting_settings(w))
    toolbar.addWidget(settings_btn)

    create_btn = QPushButton('+ New Feed')
    create_btn.setFixedHeight(36)
    create_btn.setStyleSheet(
        'font-size:13px;font-weight:700;padding:0 16px;border-radius:5px;'
        'background:#2f6ae6;color:white;border:none;'
    )
    create_btn.clicked.connect(lambda: create_feed_dialog(w))
    toolbar.addWidget(create_btn)

    layout.addLayout(toolbar)

    # ── status bar ─────────────────────────────────────────────
    w.api_setup_status = label('Email sender and private hosting status shown here.', 'muted')
    layout.addWidget(w.api_setup_status)

    # ── table ──────────────────────────────────────────────────
    w.clients_table = QTableWidget(0, _NUM_COLS)
    w.clients_table.setHorizontalHeaderLabels(
        ['Feed Name', 'Scope', 'Recipient Email', 'Latest Client Fetch', 'Status', 'Actions']
    )
    w.clients_table.setShowGrid(False)
    w.clients_table.setAlternatingRowColors(True)
    w.clients_table.setStyleSheet(
        'QTableWidget{alternate-background-color:#f8fafc;background:white;'
        'border:1px solid #e1e6ed;border-radius:6px;}'
        'QHeaderView::section{background:#f4f6f9;color:#253244;border:none;'
        'border-bottom:1px solid #e1e6ed;padding:10px 12px;font-weight:600;font-size:13px;}'
        'QTableWidget::item{padding:6px 10px;border:none;}'
        'QTableWidget::item:selected{background:#e9f0ff;color:#202b3a;}'
    )
    w.clients_table.verticalHeader().hide()
    w.clients_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    w.clients_table.setSelectionMode(QAbstractItemView.SingleSelection)
    w.clients_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    w.clients_table.setFocusPolicy(Qt.StrongFocus)

    hdr = w.clients_table.horizontalHeader()
    hdr.setSectionResizeMode(QHeaderView.Interactive)
    hdr.setMinimumHeight(40)
    w.clients_table.setColumnWidth(_COL_NAME,     160)
    w.clients_table.setColumnWidth(_COL_SCOPE,    105)
    w.clients_table.setColumnWidth(_COL_EMAIL,    180)
    w.clients_table.setColumnWidth(_COL_ACTIVITY, 250)
    w.clients_table.setColumnWidth(_COL_STATUS,   100)
    hdr.setSectionResizeMode(_COL_ACTIONS, QHeaderView.Stretch)

    layout.addWidget(w.clients_table, 1)

    # ── API URL strip ──────────────────────────────────────────
    url_bar = QHBoxLayout()
    url_bar.setSpacing(8)
    w.api_url_label = label(
        w.store.get('portal_hosting', {}).get('base_url', DEFAULT_BASE) + '/api/v1/inventory',
        'muted'
    )
    w.api_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    url_bar.addWidget(QLabel('API URL:'))
    url_bar.addWidget(w.api_url_label, 1)

    copy_btn = QPushButton('Copy')
    copy_btn.setObjectName('quiet')
    copy_btn.setFixedHeight(30)
    copy_btn.setFixedWidth(60)
    copy_btn.setStyleSheet('font-size:12px;padding:0;border-radius:4px;')
    copy_btn.clicked.connect(
        lambda: __import__('PySide6.QtWidgets', fromlist=['QApplication'])
                .QApplication.clipboard().setText(w.api_url_label.text())
    )
    url_bar.addWidget(copy_btn)
    layout.addLayout(url_bar)

    return page

def selected_company(w):
    row=w.clients_table.currentRow()
    if row<0 or row>=len(getattr(w,'client_ids',[])):
        QMessageBox.information(w,'Select feed','Select a feed in API Access first.');return None
    return w.store.company(w.client_ids[row])

def create_feed_dialog(w,company=None):
    from .ui import AdjustmentButtons,PercentageInput
    d=QDialog(w)
    d.setWindowTitle('Edit Feed' if company else 'Create Feed')
    d.resize(520,380)
    layout=dialog_layout(d)

    title_lbl=QLabel('Edit Feed' if company else 'New Client Feed')
    title_lbl.setStyleSheet('font-size:17px;font-weight:700;color:#202b3a;')
    layout.addWidget(title_lbl)

    form=QFormLayout();form.setSpacing(14);form.setLabelAlignment(Qt.AlignRight)
    name=QLineEdit(company['name'] if company else '')
    email=QLineEdit(company['email'] if company else '')
    name.setPlaceholderText('e.g. Acme Gems')
    email.setPlaceholderText('recipient@company.com')
    name.setMaxLength(120);email.setMaxLength(254)
    form.addRow('Feed name',name)
    form.addRow('Recipient email',email)

    checks=QHBoxLayout();checks.setSpacing(16)
    natural=QCheckBox('Natural Diamonds')
    lab=QCheckBox('Lab Grown Diamonds')
    kinds=json.loads(company['kinds']) if company else ['Natural','Lab Grown']
    natural.setChecked('Natural' in kinds);lab.setChecked('Lab Grown' in kinds)
    checks.addWidget(natural);checks.addWidget(lab);checks.addStretch()
    form.addRow('Inventory scope',checks)

    mode=AdjustmentButtons()
    percent=PercentageInput()
    percent.setMinimumWidth(140);percent.setFixedHeight(44)
    if company:mode.setCurrentIndex(1 if company['mode']=='increase' else 0)
    percent.setMaximum(100 if mode.currentIndex() else 99.99)
    percent.setValue(float(company['percent']) if company else 0)
    mode.currentIndexChanged.connect(lambda:percent.setMaximum(100 if mode.currentIndex() else 99.99))
    if not company:
        form.addRow('Price adjustment',mode)
        form.addRow('Percentage (%)',percent)

    layout.addLayout(form)

    hint=QLabel('API keys are stored in Windows Credential Manager. An invitation email is only sent when you click Publish.')
    hint.setWordWrap(True);hint.setObjectName('muted');layout.addWidget(hint)

    error=QLabel();error.setObjectName('error');error.setWordWrap(True);layout.addWidget(error)
    buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Save).setText('Save Feed' if company else 'Create Feed')
    buttons.rejected.connect(d.reject)

    def save():
        try:
            chosen=[k for k,c in [('Natural',natural),('Lab Grown',lab)] if c.isChecked()]
            if not name.text().strip() or not chosen:
                raise ValueError('Enter a feed name and select at least one inventory scope.')
            if not re.fullmatch(r'[^\s@<>\r\n]+@[^\s@<>\r\n]+\.[^\s@<>\r\n]+',email.text().strip()):
                raise ValueError('Enter a valid recipient email address.')
            percent.interpretText()
            if company:
                w.store.db.execute('UPDATE portal_companies SET name=?,email=? WHERE id=?',(name.text().strip(),email.text().strip(),company['id']))
                w.store.db.commit()
                if chosen!=json.loads(company['kinds']):
                    w.worker.request('scope',{'id':company['id'],'kinds':chosen})
            else:
                w.store.add_company(name.text(),email.text(),chosen,'increase' if mode.currentIndex() else 'discount',str(percent.value()))
            d.accept()
        except Exception as exc:
            from .responses import redact_text
            error.setText(redact_text(str(exc)) if isinstance(exc,ValueError) else 'Could not save feed. Check Windows Credential Manager and local storage.')

    buttons.accepted.connect(save)
    layout.addWidget(buttons)
    if d.exec()==QDialog.Accepted:
        w.rebuild_rates()
        w.refresh()

def company_form(w,company=None):
    return create_feed_dialog(w,company)

def add_company(w):
    create_feed_dialog(w)

def edit_access(w):
    c=selected_company(w)
    if c:create_feed_dialog(w,c)

def request_email(w,rotate=False):
    c=selected_company(w)
    if not c:return
    text=('Rotate this feed API key and email the replacement?' if rotate else 'Publish this feed inventory and email its API access?')+'\n\nFeed Name: '+c['name']+'\nRecipient: '+c['email']+'\nInventory: '+', '.join(json.loads(c['kinds']))
    if QMessageBox.question(w,'Confirm invitation',text)!=QMessageBox.Yes:return
    w.worker.request('rotate' if rotate else 'email',c['id'])
    w.update_status('Preparing feed access for '+c['name'])

def revoke(w):
    c=selected_company(w)
    if c and QMessageBox.question(w,'Revoke feed access','Revoke API access for '+c['name']+'? This does not delete its history.')==QMessageBox.Yes:
        w.worker.request('revoke',c['id'])

def hosting_settings(w):
    d=QDialog(w);d.setWindowTitle('Email & Hosting Settings');d.resize(650,560);layout=dialog_layout(d)
    tabs=QTabWidget();layout.addWidget(tabs,1)
    
    # Tab 1: Gmail App Password (SMTP) & OAuth
    mail_tab=QWidget();mail_layout=QVBoxLayout(mail_tab);mail_layout.setContentsMargins(12,12,12,12);mail_layout.setSpacing(14)
    m_info=QLabel('Configure Gmail to send inventory invitation emails. Use a 16-character Google App Password (recommended, does not expire) or Desktop OAuth.')
    m_info.setWordWrap(True);mail_layout.addWidget(m_info)
    
    m_form=QFormLayout();m_form.setSpacing(12)
    smtp_sender,smtp_pw=portal_secrets.get_smtp_credentials()
    gmail_user=QLineEdit(smtp_sender or '');gmail_user.setPlaceholderText('yourname@gmail.com')
    app_pw_input=QLineEdit(smtp_pw or '');app_pw_input.setEchoMode(QLineEdit.Password)
    app_pw_input.setPlaceholderText('16-character Google App Password')
    
    pw_row=QHBoxLayout();pw_row.addWidget(app_pw_input,1)
    toggle_pw=QPushButton('Show');toggle_pw.setObjectName('quiet')
    def toggle_show():
        if app_pw_input.echoMode()==QLineEdit.Password:
            app_pw_input.setEchoMode(QLineEdit.Normal);toggle_pw.setText('Hide')
        else:
            app_pw_input.setEchoMode(QLineEdit.Password);toggle_pw.setText('Show')
    toggle_pw.clicked.connect(toggle_show)
    pw_row.addWidget(toggle_pw)
    
    m_form.addRow('Gmail Sender',gmail_user)
    m_form.addRow('App Password',pw_row)
    mail_layout.addLayout(m_form)
    
    mail_status=QLabel();mail_status.setWordWrap(True);mail_layout.addWidget(mail_status)
    
    m_btns=QHBoxLayout()
    save_smtp=QPushButton('Save App Password')
    test_smtp=QPushButton('Test Connection');test_smtp.setObjectName('quiet')
    oauth_btn=QPushButton('Connect via OAuth JSON');oauth_btn.setObjectName('quiet')
    m_btns.addWidget(save_smtp);m_btns.addWidget(test_smtp);m_btns.addWidget(oauth_btn)
    mail_layout.addLayout(m_btns)
    
    def on_save_smtp():
        u=gmail_user.text().strip();p=app_pw_input.text().strip().replace(' ','')
        if not u or not p:
            mail_status.setText('Enter your Gmail address and 16-character App Password.')
            return
        portal_secrets.put_smtp_credentials(u,p)
        mail_status.setText('Gmail App Password saved securely in Windows Credential Manager.')
    save_smtp.clicked.connect(on_save_smtp)
    
    def on_test_smtp():
        u=gmail_user.text().strip();p=app_pw_input.text().strip().replace(' ','')
        if not u or not p:
            mail_status.setText('Enter your Gmail address and App Password before testing.')
            return
        mail_status.setText('Testing connection to smtp.gmail.com...')
        w.worker.request('smtp-test',{'sender':u,'password':p})
    test_smtp.clicked.connect(on_test_smtp)
    
    def on_oauth_connect():
        path,_=QFileDialog.getOpenFileName(d,'Select Google Desktop OAuth client','','JSON (*.json)')
        if path:
            w.worker.request('google',path)
            mail_status.setText('Complete Google sign-in in your browser.')
    oauth_btn.clicked.connect(on_oauth_connect)
    mail_layout.addStretch()
    tabs.addTab(mail_tab,'Gmail Sender')
    
    # Tab 2: Hosting & Vercel Setup
    host_tab=QWidget();host_layout=QVBoxLayout(host_tab);host_layout.setContentsMargins(12,12,12,12);host_layout.setSpacing(14)
    h_form=QFormLayout();h_form.setSpacing(12)
    base=QLineEdit(w.store.get('portal_hosting',{}).get('base_url',DEFAULT_BASE))
    token=QLineEdit();token.setEchoMode(QLineEdit.Password);token.setPlaceholderText('Leave blank to keep the saved admin credential')
    h_form.addRow('Hosting origin',base);h_form.addRow('Admin credential',token)
    admin_hash=QLineEdit();admin_hash.setReadOnly(True);admin_hash.setPlaceholderText('Generate the local admin credential, then copy this hash to Vercel')
    generate=QPushButton('Generate / show admin hash');generate.setObjectName('quiet')
    h_form.addRow('Vercel admin hash',admin_hash);h_form.addRow('',generate)
    host_layout.addLayout(h_form)
    
    def generate_admin():
        try:
            import secrets,hashlib
            origin=clean_base(base.text());slot=portal_secrets.hosting_slot(origin);raw=portal_secrets.get_secret(slot)
            if not raw:
                raw='cg_admin_'+secrets.token_urlsafe(32);portal_secrets.put_secret(slot,raw)
            admin_hash.setText(hashlib.sha256(raw.encode()).hexdigest())
            h_status.setText('Copy this hash into INVENTORY_ADMIN_TOKEN_SHA256 on Vercel.')
        except Exception:h_status.setText('Could not access Windows Credential Manager.')
    generate.clicked.connect(generate_admin)
    
    h_status=QLabel();h_status.setWordWrap(True);host_layout.addWidget(h_status)
    save_host_btn=QPushButton('Save & test hosting');host_layout.addWidget(save_host_btn)
    def save_host():
        try:
            origin=clean_base(base.text());secret=token.text().strip()
            if secret:
                if not secret.startswith('cg_admin_') or len(secret)<40:raise ValueError('Use the generated cg_admin_ management credential.')
                portal_secrets.put_secret(portal_secrets.hosting_slot(origin),secret);token.clear()
            w.store.set('portal_hosting',{'base_url':origin});w.api_url_label.setText(origin+'/api/v1/inventory')
            w.worker.request('hosting-test');h_status.setText('Checking hosting in the background...')
        except Exception as exc:h_status.setText(str(exc))
    save_host_btn.clicked.connect(save_host)
    host_layout.addStretch()
    tabs.addTab(host_tab,'Private Hosting')
    
    buttons=QDialogButtonBox(QDialogButtonBox.Close);buttons.rejected.connect(d.reject);layout.addWidget(buttons)
    d.exec()

def start_activity_refresh(w, force=False):
    if not hasattr(w, '_access_cache'):
        w._access_cache = {}
    import time
    now = time.monotonic()
    last_run = getattr(w, '_last_activity_refresh', 0)
    if not force and (now - last_run < 60):
        return

    old_worker = getattr(w, '_activity_worker', None)
    if old_worker and old_worker.isRunning():
        return

    hosting = w.store.get('portal_hosting', {})
    if not hosting or not hosting.get('base_url'):
        return
    cids = getattr(w, 'client_ids', [])
    if not cids:
        return

    w._last_activity_refresh = now
    worker = ActivityFetchWorker(hosting, cids)
    def on_access_updated(cid, acc):
        try:
            w._access_cache[cid] = acc
            if hasattr(w, 'client_ids') and cid in w.client_ids and hasattr(w, 'clients_table'):
                row_idx = w.client_ids.index(cid)
                if row_idx < w.clients_table.rowCount():
                    w.clients_table.setCellWidget(row_idx, _COL_ACTIVITY, _make_activity_cell(acc))
            if hasattr(w, 'dashboard_fetch_info'):
                latest_fetch = None
                latest_ts = ""
                for _cid, _acc in w._access_cache.items():
                    if _acc and isinstance(_acc, dict) and _acc.get('last_fetched_at'):
                        if _acc['last_fetched_at'] > latest_ts:
                            latest_ts = _acc['last_fetched_at']
                            latest_fetch = _acc
                if latest_fetch and latest_fetch.get('fetch_count'):
                    try: c = w.store.company(latest_fetch.get('client_id'))
                    except Exception: c = None
                    cname = latest_fetch.get('company_name') or (c.get('name') if c else 'Client')
                    f_info = format_access_summary(latest_fetch)
                    if f_info:
                        w.dashboard_fetch_info.setText(
                            f"<b>Company:</b> {cname} &nbsp;•&nbsp; <b>{f_info['header']}</b><br>"
                            f"<span style='color: #2563eb; font-weight: 600;'>{f_info['diamonds']}</span> &nbsp;•&nbsp; "
                            f"<span style='color: #64748b;'>IP: {f_info['ip']} ({f_info['agent']})</span>"
                        )
        except Exception:
            pass
    worker.access_updated.connect(on_access_updated)
    worker.finished.connect(worker.deleteLater)
    w._activity_worker = worker
    worker.start()


def refresh_companies(w):
    old_ids=getattr(w,'client_ids',[]);row=w.clients_table.currentRow()
    selected=old_ids[row] if 0<=row<len(old_ids) else None
    companies=w.store.companies();w.client_ids=[c['id'] for c in companies]
    w.clients_table.setRowCount(len(companies))
    if not hasattr(w, '_access_cache'):
        w._access_cache = {}
    ROW_H = 50
    for i,c in enumerate(companies):
        w.clients_table.setRowHeight(i, ROW_H)
        # Feed Name
        item=QTableWidgetItem(c['name']);item.setToolTip(c['name'])
        item.setData(Qt.UserRole,c['id']);w.clients_table.setItem(i,_COL_NAME,item)
        # Scope
        scope=', '.join(json.loads(c['kinds']))
        item2=QTableWidgetItem(scope);item2.setToolTip(scope)
        w.clients_table.setItem(i,_COL_SCOPE,item2)
        # Email
        item3=QTableWidgetItem(c['email']);item3.setToolTip(c['email'])
        w.clients_table.setItem(i,_COL_EMAIL,item3)
        # Latest Client Fetch Activity
        acc = w._access_cache.get(c['id'])
        w.clients_table.setCellWidget(i, _COL_ACTIVITY, _make_activity_cell(acc))
        # Status pill (widget)
        pill_holder=QWidget();ph=QHBoxLayout(pill_holder);ph.setContentsMargins(6,6,6,6)
        ph.addWidget(_make_pill(c['status'] or 'Draft'));ph.addStretch()
        w.clients_table.setCellWidget(i,_COL_STATUS,pill_holder)
        # Inline actions
        w.clients_table.setCellWidget(i,_COL_ACTIONS,_row_actions(w,c['id']))
    if selected in w.client_ids:w.clients_table.selectRow(w.client_ids.index(selected))
    elif companies:w.clients_table.selectRow(0)
    start_activity_refresh(w)
