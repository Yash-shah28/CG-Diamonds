"""Light desktop shell, sidebar navigation and website-driven rate rows."""
from PySide6.QtCore import Qt, QSize, Signal, QLocale, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QDoubleValidator, QValidator
from PySide6.QtWidgets import (QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QToolButton, QButtonGroup, QStackedWidget, QComboBox, QDoubleSpinBox,
    QLineEdit, QPlainTextEdit, QTableWidget, QAbstractItemView, QSizePolicy, QHeaderView, QAbstractSpinBox, QStyle)
from PySide6.QtSvg import QSvgRenderer
import json
from .brand import brand_pixmap
from .rates import WEBSITES, rate_config


def label(value='', name=None):
    item=QLabel(value); item.setWordWrap(True)
    if name: item.setObjectName(name)
    return item


def card():
    frame=QFrame(); frame.setObjectName('card')
    inner=QVBoxLayout(frame);inner.setContentsMargins(18,18,18,18);inner.setSpacing(12)
    return frame,inner


def nav_icon(index, color='#334155'):
    shapes=[
        '<rect x="3" y="3" width="7" height="7" rx="1.3"/><rect x="14" y="3" width="7" height="7" rx="1.3"/><rect x="3" y="14" width="7" height="7" rx="1.3"/><rect x="14" y="14" width="7" height="7" rx="1.3"/>',
        '<path d="m3 8 4-5h10l4 5-9 13L3 8Z M3 8h18 M7 3l3 5 2 13 2-13 3-5"/>',
        '<path d="m3 17 6-6 4 4 8-10 M15 5h6v6"/>',
        '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
        '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m7-13-4 4 4 4"/>',
        '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18m4-13 4 4-4 4"/>',
        '<circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-2-2 3-3m-6 0 3-3"/>',
    ]
    svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="'+color+'" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'+shapes[index]+'</svg>'
    pixmap=QPixmap(72,72);pixmap.fill(Qt.transparent);painter=QPainter(pixmap);QSvgRenderer(QByteArray(svg.encode())).render(painter);painter.end();pixmap.setDevicePixelRatio(3)
    return QIcon(pixmap)


def configure_table(table,row_height=52,select=True):
    table.setShowGrid(True);table.setAlternatingRowColors(False);table.setWordWrap(False)
    table.verticalHeader().hide();table.verticalHeader().setDefaultSectionSize(row_height)
    table.horizontalHeader().setMinimumHeight(48);table.setSelectionMode(QAbstractItemView.SingleSelection if select else QAbstractItemView.NoSelection)
    table.setFocusPolicy(Qt.StrongFocus if select else Qt.NoFocus);table.setSelectionBehavior(QAbstractItemView.SelectRows);table.setEditTriggers(QAbstractItemView.NoEditTriggers)


def status_badge(result):
    r = str(result or '')
    if r in ('Sent to DRC','Success','Published to API','Sent via Gmail') or 'fetch' in r.lower():
        colors = ('#047857', '#ecfdf5', '#a7f3d0')
    elif r == 'Failure':
        colors = ('#b91c1c', '#fef2f2', '#fecaca')
    elif r in ('Needs review', 'Blocked', 'Awaiting retry'):
        colors = ('#b45309', '#fffbeb', '#fde68a')
    else:
        colors = ('#334155', '#f1f5f9', '#e2e8f0')
    holder = QWidget(); holder.setAttribute(Qt.WA_TransparentForMouseEvents); layout = QHBoxLayout(holder); layout.setContentsMargins(4,4,4,4)
    badge = QLabel(r); badge.setAlignment(Qt.AlignCenter); badge.setFixedHeight(26)
    badge.setStyleSheet(f'background:{colors[1]};color:{colors[0]};border:1px solid {colors[2]};border-radius:13px;padding:2px 10px;font-size:12px;font-weight:600;')
    layout.addWidget(badge)
    return holder


def page():
    widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(0,0,0,0);layout.setSpacing(16)
    return widget,layout


class PercentageInput(QLineEdit):
    valueChanged = Signal(float)

    def __init__(self):
        super().__init__();self._maximum=100.0;self._value=0.0
        self.numeric_validator=QDoubleValidator(0,100,2,self);self.numeric_validator.setNotation(QDoubleValidator.StandardNotation);numeric_locale=QLocale.c();numeric_locale.setNumberOptions(QLocale.RejectGroupSeparator);self.numeric_validator.setLocale(numeric_locale);self.setValidator(self.numeric_validator)
        self.setText('0.00');self.setAccessibleName('Rate percentage');self.setToolTip('Percentage, up to 100% for an increase');self.textEdited.connect(self._edited)

    def _edited(self,text):
        if self.hasAcceptableInput():
            self._value=float(text);self.valueChanged.emit(self._value)

    def value(self):
        return float(self.text()) if self.hasAcceptableInput() else self._value

    def setValue(self,value):
        self._value=max(0.0,min(float(value),self._maximum));self.setText(format(self._value,'.2f'));self.valueChanged.emit(self._value)

    def setMaximum(self,value):
        changed=float(value)!=self._maximum;self._maximum=float(value);self.numeric_validator.setTop(self._maximum)
        if changed and self._value>self._maximum:self.setValue(self._maximum)

    def maximum(self):return self._maximum
    def lineEdit(self):return self
    def interpretText(self):
        if not self.hasAcceptableInput():raise ValueError('Enter a valid percentage')
        self._value=float(self.text());self.setText(format(self._value,'.2f'))


class AdjustmentButtons(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self):
        super().__init__();self._index=0
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
        self.buttons=[];self.group=QButtonGroup(self)
        for index,caption in enumerate((chr(0x2212), '+')):
            button=QPushButton(caption);button.setObjectName('adjust');button.setCheckable(True);button.setFixedSize(46,42)
            button.setToolTip('Discount' if index==0 else 'Increase');button.setAccessibleName('Decrease price' if index==0 else 'Increase price')
            layout.addWidget(button);self.group.addButton(button,index);self.buttons.append(button)
        self.buttons[0].setChecked(True);self.group.idClicked.connect(self.setCurrentIndex)

    def currentIndex(self):return self._index
    def currentText(self):return 'Increase' if self._index else 'Discount'
    def setCurrentIndex(self,index):
        index=1 if index else 0;changed=index!=self._index;self._index=index;self.buttons[index].setChecked(True)
        if changed:self.currentIndexChanged.emit(index)


def build_ui(w):
    shell=QWidget();root=QHBoxLayout(shell);root.setContentsMargins(0,0,0,0);root.setSpacing(0);w.setCentralWidget(shell)
    w.sidebar=QFrame();w.sidebar.setObjectName('sidebar');side=QVBoxLayout(w.sidebar);side.setContentsMargins(12,20,12,14);side.setSpacing(9)
    identity=QHBoxLayout();identity.setSpacing(9)
    mark=QLabel();mark.setFixedSize(32,32);mark.setPixmap(brand_pixmap().scaled(32,32,Qt.KeepAspectRatio,Qt.SmoothTransformation));identity.addWidget(mark)
    w.sidebar_brand=label('Chintamani Gems','brandName');identity.addWidget(w.sidebar_brand);side.addLayout(identity);side.addSpacing(28)
    w.page_names=['Dashboard','Excel Mapping','API Access','Change Rates','History'];w.nav_buttons=[];w.nav_group=QButtonGroup(w)
    for index,name in enumerate(w.page_names):
        button=QToolButton();button.setObjectName('nav');button.setText(name);button.setToolTip(name);button.setAccessibleName(name);button.setCheckable(True);button.setIconSize(QSize(22,22));button.setMinimumHeight(44);button.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        side.addWidget(button);w.nav_buttons.append(button);w.nav_group.addButton(button,index)
    side.addStretch()
    from .version import VERSION
    w.update_button=QToolButton();w.update_button.setText('Check for updates');w.update_button.setIcon(w.style().standardIcon(QStyle.SP_BrowserReload));w.update_button.setMinimumHeight(38);w.update_button.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed);w.update_button.setObjectName('quiet');w.update_button.setToolTip('Chintamani Gems '+VERSION);side.addWidget(w.update_button)
    w.collapse_button=QPushButton('Collapse sidebar');w.collapse_button.setObjectName('quiet');w.collapse_button.clicked.connect(lambda:w.set_sidebar_collapsed(not w.sidebar_collapsed));side.addWidget(w.collapse_button)
    root.addWidget(w.sidebar)
    content=QWidget();main=QVBoxLayout(content);main.setContentsMargins(24,24,24,24);main.setSpacing(22);root.addWidget(content,1)
    header=QHBoxLayout();w.page_title=label('Dashboard','pageTitle');header.addWidget(w.page_title);header.addStretch();w.monitor_badge=label('Monitoring','badge');header.addWidget(w.monitor_badge);main.addLayout(header)
    w.pages=QStackedWidget();main.addWidget(w.pages,1)
    dashboard,layout=page();w.dashboard_page=dashboard
    row=QHBoxLayout();w.status=label('Active - Waiting for configured Excel files','muted');row.addWidget(w.status,1);w.pause_button=QPushButton('Pause monitoring');w.pause_button.setObjectName('quiet');w.pause_button.clicked.connect(w.pause);row.addWidget(w.pause_button);publish=QPushButton('Publish inventory');publish.clicked.connect(w.sync);row.addWidget(publish);layout.addLayout(row)
    w.rate_warning=label('','error');w.rate_warning.hide();layout.addWidget(w.rate_warning)
    metrics=QHBoxLayout();metrics.setSpacing(12);w.metric_values={}
    for key,title in [('Natural diamonds','Natural Diamonds'),('Lab Grown diamonds','Lab Grown Diamonds'),('Sent to DRC','Published Feeds'),('Needs review','Requires Review')]:
        frame,inner=card()
        lbl_title=label(title,'muted')
        lbl_title.setStyleSheet('font-size:12px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;')
        inner.addWidget(lbl_title)
        number=label('0','stat')
        number.setStyleSheet('font-size:28px;font-weight:700;color:#0f172a;')
        inner.addWidget(number)
        metrics.addWidget(frame)
        w.metric_values[key]=number
    layout.addLayout(metrics)

    w.latest_fetch_frame, inner_fetch = card()
    inner_fetch.setSpacing(6)
    fetch_title = label('Latest Client API Fetch', 'cardTitle')
    fetch_title.setStyleSheet('font-size: 15px; font-weight: 700; color: #0f172a;')
    inner_fetch.addWidget(fetch_title)
    w.dashboard_fetch_info = label('No client API fetches recorded yet.', 'muted')
    w.dashboard_fetch_info.setStyleSheet('font-size: 13px; color: #475569; line-height: 1.4;')
    inner_fetch.addWidget(w.dashboard_fetch_info)
    layout.addWidget(w.latest_fetch_frame)

    frame,inner=card()
    sync_title=label('Sync Summary','cardTitle')
    sync_title.setStyleSheet('font-size: 15px; font-weight: 700; color: #0f172a;')
    inner.addWidget(sync_title)
    w.summary=label('','muted')
    inner.addWidget(w.summary)
    layout.addWidget(frame)
    layout.addStretch()
    w.pages.addWidget(dashboard)
    w.inspect_button=QPushButton('Send once & view response',w);w.inspect_button.hide();w.inspect_button.clicked.connect(w.begin_inspection)
    w.editor=QPlainTextEdit(json.dumps(w.store.get('profiles',[]),indent=2),w);w.editor.hide()
    config,layout=page();layout.addWidget(label('Connect one Excel workbook per inventory type.','muted'));connections=QHBoxLayout();connections.setSpacing(14);w.connection_labels={}
    for kind in ('Natural','Lab Grown'):
        frame,inner=card();title=label(kind+' Diamonds');title.setStyleSheet('font-size:16px;font-weight:600;');inner.addWidget(title)
        info=label('','fileInfo');info.setMinimumHeight(130);info.setTextInteractionFlags(Qt.TextSelectableByMouse);inner.addWidget(info);w.connection_labels[kind]=info
        button=QPushButton('Choose Excel file');button.clicked.connect(lambda checked=False,k=kind:w.add_file(k,persist=True));inner.addWidget(button)
        button=QPushButton('Edit mapping');button.setObjectName('quiet');button.clicked.connect(lambda checked=False,k=kind:w.add_file(k,persist=True,edit=True));inner.addWidget(button);disconnect_btn=QPushButton('Disconnect');disconnect_btn.setObjectName('quiet');disconnect_btn.setStyleSheet('color:#a33b35;border-color:#f5c6c3;');disconnect_btn.clicked.connect(lambda checked=False,k=kind:w.remove_profile(k));inner.addWidget(disconnect_btn);connections.addWidget(frame)
    layout.addLayout(connections);preview=QHBoxLayout();preview.addStretch();button=QPushButton('Preview inventory');button.setObjectName('quiet');button.clicked.connect(w.preview_payload);preview.addWidget(button);layout.addLayout(preview);layout.addStretch();w.pages.addWidget(config)
    from .portal_ui import api_page
    w.pages.addWidget(api_page(w))
    rates,layout=page();layout.addWidget(label('Each company receives its own prices. Saving a rate immediately republishes its last validated inventory.','muted'))
    w.rate_table=QTableWidget(0,5);w.rate_table.setHorizontalHeaderLabels(['Company','Rate change','Percentage (%)','Status','Action'])
    for index,width in enumerate([140,145,170,220,90]):w.rate_table.setColumnWidth(index,width)
    w.rate_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);w.rate_table.setMinimumHeight(160);w.rate_table.setMaximumHeight(560)
    w.rate_rows={};rebuild_rate_rows(w);layout.addWidget(w.rate_table);layout.addWidget(label('Add a company in API Access to configure its rate. Previous DRC settings remain stored as legacy data.','muted'));layout.addStretch();w.pages.addWidget(rates)
    history,layout=page();w.history_page=history
    top=QHBoxLayout();caption=label('Upload history and server responses','muted');caption.setWordWrap(False);top.addWidget(caption);top.addStretch();w.history_filter=QComboBox();w.history_filter.addItems(['All results','Published to API','Sent via Gmail','Failure','Awaiting retry','Needs review','Revoked','Suspended','Sent to DRC','Blocked']);w.history_filter.currentIndexChanged.connect(w.refresh);top.addWidget(w.history_filter);layout.addLayout(top)
    w.history=QTableWidget(0,6);w.history.setHorizontalHeaderLabels(['Date & time','Diamond Type','Status','Results (Diamonds)','Type','Message']);w.history.setEditTriggers(QAbstractItemView.NoEditTriggers);w.history.setSelectionBehavior(QAbstractItemView.SelectRows);w.history.setAlternatingRowColors(True);w.history.setShowGrid(False);w.history.verticalHeader().hide();w.history.verticalHeader().setDefaultSectionSize(44)
    for index,width in enumerate([160,130,140,140,160]):w.history.setColumnWidth(index,width)
    w.history.horizontalHeader().setStretchLastSection(True);w.history.cellDoubleClicked.connect(lambda row,column:w.open_history_response(row));layout.addWidget(w.history,1)
    bottom=QHBoxLayout();bottom.addStretch();button=QPushButton('View response');button.clicked.connect(lambda:w.open_history_response(w.history.currentRow()));bottom.addWidget(button);layout.addLayout(bottom);w.pages.addWidget(history)
    configure_table(w.rate_table,88,False);configure_table(w.history,52);w.nav_group.idClicked.connect(w.navigate);w.navigate(0);w.set_sidebar_collapsed(w.store.get('ui.sidebar_collapsed',False),persist=False)


def rebuild_rate_rows(w):
    companies=w.store.companies();w.rate_rows={};w.rate_table.setRowCount(len(companies))
    for index,c in enumerate(companies):
        key=c['id'];mode=AdjustmentButtons();percent=PercentageInput();percent.setObjectName('percentage');percent.setFixedHeight(44);percent.setMinimumWidth(140)
        mode.setCurrentIndex(1 if c['mode']=='increase' else 0);percent.setMaximum(100 if mode.currentIndex() else 99.99);percent.setValue(float(c['percent']))
        status=label(c['percent']+'% '+c['mode'],'muted');save=QPushButton('Save');save.setEnabled(False)
        row={'mode':mode,'percent':percent,'status':status,'save':save,'invalid':False,'dirty':False};w.rate_rows[key]=row
        for column,widget in enumerate([label(c['name']),mode,percent,status,save]):
            holder=QWidget();cell=QHBoxLayout(holder);cell.setContentsMargins(8,8,8,8);cell.addWidget(widget);w.rate_table.setCellWidget(index,column,holder)
        w.rate_table.setRowHeight(index,88)
        mode.currentIndexChanged.connect(lambda value,k=key:w.update_rate_draft(k));percent.valueChanged.connect(lambda value,k=key:w.update_rate_draft(k));percent.textEdited.connect(lambda value,k=key:w.update_rate_draft(k));save.clicked.connect(lambda checked=False,k=key:w.save_rate(k))
    configure_table(w.rate_table,88,False)
