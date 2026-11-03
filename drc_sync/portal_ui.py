"""Company and Feed management widgets; all network work stays on the serialized worker."""
import json
import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QPushButton,QTableWidget,
 QTableWidgetItem,QDialog,QFormLayout,QLineEdit,QCheckBox,QFileDialog,QMessageBox,QDialogButtonBox,QHeaderView,QTabWidget)
from .dialogs import dialog_layout,dialog_buttons
from .portal_client import DEFAULT_BASE,clean_base
from . import portal_secrets

def api_page(w):
    from .ui import label,configure_table
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(14)
    
    # Top header bar matching DRC Feeds toolbar
    top_bar=QHBoxLayout();top_bar.setSpacing(10)
    top_bar.addWidget(label('Configure client feeds and read-only API access.','muted'),1)
    
    create_btn=QPushButton('+ Create Feed')
    create_btn.setStyleSheet('font-weight:600;padding:8px 16px;')
    create_btn.clicked.connect(lambda:create_feed_dialog(w))
    top_bar.addWidget(create_btn)
    
    settings_btn=QPushButton('Email && Hosting Settings')
    settings_btn.setObjectName('quiet')
    settings_btn.clicked.connect(lambda:hosting_settings(w))
    top_bar.addWidget(settings_btn)
    
    layout.addLayout(top_bar)
    
    w.api_setup_status=label('Email sender and private hosting status shown here.','muted')
    layout.addWidget(w.api_setup_status)
    
    w.clients_table=QTableWidget(0,5)
    w.clients_table.setHorizontalHeaderLabels(['Feed Name','Inventory Scope','Recipient Email','Feed Status','Email Delivery'])
    configure_table(w.clients_table)
    for i,width in enumerate([170,150,210,180,210]):w.clients_table.setColumnWidth(i,width)
    w.clients_table.horizontalHeader().setStretchLastSection(True)
    layout.addWidget(w.clients_table,1)
    
    actions=QHBoxLayout();actions.setSpacing(10)
    specs=[('Publish & email',lambda:request_email(w)),
           ('Rotate key & email',lambda:request_email(w,True)),
           ('Edit feed',lambda:edit_access(w)),
           ('Revoke access',lambda:revoke(w))]
    for i,(title,fn) in enumerate(specs):
        b=QPushButton(title.replace('&','&&'));b.clicked.connect(fn)
        if i!=0:b.setObjectName('quiet')
        actions.addWidget(b)
    actions.addStretch()
    layout.addLayout(actions)
    
    bottom=QHBoxLayout()
    w.api_url_label=label(w.store.get('portal_hosting',{}).get('base_url',DEFAULT_BASE)+'/api/v1/inventory','muted')
    w.api_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    bottom.addWidget(w.api_url_label,1)
    copy=QPushButton('Copy API URL')
    copy.setObjectName('quiet')
    copy.clicked.connect(lambda:__import__('PySide6.QtWidgets',fromlist=['QApplication']).QApplication.clipboard().setText(w.api_url_label.text()))
    bottom.addWidget(copy)
    layout.addLayout(bottom)
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
    d.resize(560,420)
    layout=dialog_layout(d)
    
    title_lbl=QLabel('Edit Feed Access' if company else 'Create New Client Feed')
    title_lbl.setStyleSheet('font-size:16px;font-weight:700;')
    layout.addWidget(title_lbl)
    
    form=QFormLayout();form.setSpacing(14)
    name=QLineEdit(company['name'] if company else '')
    email=QLineEdit(company['email'] if company else '')
    name.setPlaceholderText('e.g. Acme Gems / Client Website')
    email.setPlaceholderText('recipient@company.com')
    name.setMaxLength(120);email.setMaxLength(254)
    form.addRow('Feed / Company Name',name)
    form.addRow('Recipient Email',email)
    
    checks=QHBoxLayout()
    natural=QCheckBox('Natural Diamonds')
    lab=QCheckBox('Lab Grown Diamonds')
    kinds=json.loads(company['kinds']) if company else ['Natural','Lab Grown']
    natural.setChecked('Natural' in kinds)
    lab.setChecked('Lab Grown' in kinds)
    checks.addWidget(natural);checks.addWidget(lab)
    form.addRow('Inventory Scope',checks)
    
    mode=AdjustmentButtons()
    percent=PercentageInput()
    percent.setMinimumWidth(140);percent.setFixedHeight(44)
    if company:mode.setCurrentIndex(1 if company['mode']=='increase' else 0)
    percent.setMaximum(100 if mode.currentIndex() else 99.99)
    percent.setValue(float(company['percent']) if company else 0)
    mode.currentIndexChanged.connect(lambda:percent.setMaximum(100 if mode.currentIndex() else 99.99))
    if not company:
        form.addRow('Price Adjustment',mode)
        form.addRow('Percentage (%)',percent)
    
    layout.addLayout(form)
    help_lbl=QLabel('Read-only API access keys are generated and held safely in Windows Credential Manager. An invitation email with integration instructions is sent only when you choose Publish & email.')
    help_lbl.setWordWrap(True);help_lbl.setStyleSheet('color:rgba(255,255,255,0.6);font-size:12px;')
    layout.addWidget(help_lbl)
    
    error=QLabel();error.setObjectName('error');error.setWordWrap(True);layout.addWidget(error)
    buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Save).setText('Save Feed' if company else 'Create Feed')
    buttons.rejected.connect(d.reject)
    
    def save():
        try:
            chosen=[k for k,c in [('Natural',natural),('Lab Grown',lab)] if c.isChecked()]
            if not name.text().strip() or not chosen:
                raise ValueError('Enter a feed/company name and select at least one inventory scope.')
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
            error.setText(redact_text(str(exc)) if isinstance(exc,ValueError) else 'Could not save feed access. Check Windows Credential Manager and local storage.')
            
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
    old_ids=getattr(w,'client_ids',[]);row=w.clients_table.currentRow();selected=old_ids[row] if 0<=row<len(old_ids) else None
    companies=w.store.companies();w.client_ids=[c['id'] for c in companies];w.clients_table.setRowCount(len(companies))
    for i,c in enumerate(companies):
        for j,value in enumerate([c['name'],', '.join(json.loads(c['kinds'])),c['email'],c['status'],c['last_email']]):
            item=QTableWidgetItem(value);item.setToolTip(value);w.clients_table.setItem(i,j,item)
    if selected in w.client_ids:w.clients_table.selectRow(w.client_ids.index(selected))
    elif companies:w.clients_table.selectRow(0)
