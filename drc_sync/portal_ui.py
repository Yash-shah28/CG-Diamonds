"""Company and Feed management widgets; all network work stays on the serialized worker."""
import json
import re
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,
 QTableWidgetItem,QDialog,QFormLayout,QLineEdit,QCheckBox,QFileDialog,QMessageBox,QDialogButtonBox,QHeaderView,QTabWidget,QFrame,QAbstractItemView)
from .dialogs import dialog_layout,dialog_buttons
from .portal_client import DEFAULT_BASE,clean_base
from . import portal_secrets


# ── column indices ──────────────────────────────────────────────
_COL_NAME    = 0
_COL_SCOPE   = 1
_COL_EMAIL   = 2
_COL_STATUS  = 3
_COL_ACTIONS = 4
_NUM_COLS    = 5


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
        ['Feed Name', 'Scope', 'Recipient Email', 'Status', 'Actions']
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
    w.clients_table.setColumnWidth(_COL_NAME,   180)
    w.clients_table.setColumnWidth(_COL_SCOPE,  120)
    w.clients_table.setColumnWidth(_COL_EMAIL,  210)
    w.clients_table.setColumnWidth(_COL_STATUS, 110)
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

def refresh_companies(w):
    old_ids=getattr(w,'client_ids',[]);row=w.clients_table.currentRow()
    selected=old_ids[row] if 0<=row<len(old_ids) else None
    companies=w.store.companies();w.client_ids=[c['id'] for c in companies]
    w.clients_table.setRowCount(len(companies))
    ROW_H = 46
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
        # Status pill (widget)
        pill_holder=QWidget();ph=QHBoxLayout(pill_holder);ph.setContentsMargins(6,6,6,6)
        ph.addWidget(_make_pill(c['status'] or 'Draft'));ph.addStretch()
        w.clients_table.setCellWidget(i,_COL_STATUS,pill_holder)
        # Inline actions
        w.clients_table.setCellWidget(i,_COL_ACTIONS,_row_actions(w,c['id']))
    if selected in w.client_ids:w.clients_table.selectRow(w.client_ids.index(selected))
    elif companies:w.clients_table.selectRow(0)
