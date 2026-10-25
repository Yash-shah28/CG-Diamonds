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
    colors=('#187344','#eaf7ee') if result in ('Sent to DRC','Success') else ('#b42318','#fff0ee') if result=='Failure' else ('#8a5b12','#fff7e5') if result in ('Needs review','Blocked') else ('#526172','#f1f4f7')
    holder=QWidget();holder.setAttribute(Qt.WA_TransparentForMouseEvents);layout=QHBoxLayout(holder);layout.setContentsMargins(0,0,0,0)
    badge=QLabel(result);badge.setAlignment(Qt.AlignCenter);badge.setFixedHeight(28);badge.setStyleSheet('background:'+colors[1]+';color:'+colors[0]+';border-radius:13px;padding:3px 8px;font-size:13px;');layout.addWidget(badge)
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
    w.page_names=['Dashboard','Excel Mapping','Change Rates','History'];w.nav_buttons=[];w.nav_group=QButtonGroup(w)
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
    row=QHBoxLayout();w.status=label('Active - Waiting for configured Excel files','muted');row.addWidget(w.status,1);w.pause_button=QPushButton('Pause monitoring');w.pause_button.setObjectName('quiet');w.pause_button.clicked.connect(w.pause);row.addWidget(w.pause_button);layout.addLayout(row)
    w.rate_warning=label('','error');w.rate_warning.hide();layout.addWidget(w.rate_warning)
    metrics=QHBoxLayout();metrics.setSpacing(12);w.metric_values={}
    for key,title in [('Sent to DRC','Uploads sent'),('Connected files','Excel connections'),('Needs review','Requires review'),('Failure','Failed uploads')]:
        frame,inner=card();inner.addWidget(label(title,'muted'));number=label('0','stat');inner.addWidget(number);metrics.addWidget(frame);w.metric_values[key]=number
    layout.addLayout(metrics);frame,inner=card();inner.addWidget(label('Sync summary'));w.summary=label('','muted');inner.addWidget(w.summary);layout.addWidget(frame);layout.addStretch();w.pages.addWidget(dashboard)
    w.inspect_button=QPushButton('Send once & view response',w);w.inspect_button.hide();w.inspect_button.clicked.connect(w.begin_inspection)
    w.editor=QPlainTextEdit(json.dumps(w.store.get('profiles',[]),indent=2),w);w.editor.hide()
    config,layout=page();layout.addWidget(label('Connect one Excel workbook per inventory type.','muted'));connections=QHBoxLayout();connections.setSpacing(14);w.connection_labels={}
    for kind in ('Natural','Lab Grown'):
        frame,inner=card();title=label(kind+' Diamonds');title.setStyleSheet('font-size:16px;font-weight:600;');inner.addWidget(title)
        info=label('','fileInfo');info.setMinimumHeight(130);info.setTextInteractionFlags(Qt.TextSelectableByMouse);inner.addWidget(info);w.connection_labels[kind]=info
        button=QPushButton('Choose Excel file');button.clicked.connect(lambda checked=False,k=kind:w.add_file(k,persist=True));inner.addWidget(button)
        button=QPushButton('Edit mapping');button.setObjectName('quiet');button.clicked.connect(lambda checked=False,k=kind:w.add_file(k,persist=True,edit=True));inner.addWidget(button);connections.addWidget(frame)
    layout.addLayout(connections);preview=QHBoxLayout();preview.addStretch();button=QPushButton('Preview upload');button.setObjectName('quiet');button.clicked.connect(w.preview_payload);preview.addWidget(button);layout.addLayout(preview);layout.addStretch();w.pages.addWidget(config)
    rates,layout=page();layout.addWidget(label('Adjust Price Per Carat by website. Changes apply on the next Excel save.','muted'))
    w.rate_table=QTableWidget(len(WEBSITES),5);w.rate_table.setHorizontalHeaderLabels(['Website','Rate change','Percentage (%)','Status','Action']);w.rate_table.verticalHeader().hide();w.rate_table.setEditTriggers(QAbstractItemView.NoEditTriggers);w.rate_table.setSelectionMode(QAbstractItemView.NoSelection);w.rate_table.setShowGrid(False)
    for index,width in enumerate([110,148,180,225,100]):w.rate_table.setColumnWidth(index,width)
    w.rate_table.horizontalHeader().setSectionResizeMode(3,QHeaderView.Stretch);w.rate_table.setFixedHeight(min(500,48+len(WEBSITES)*104));w.rate_rows={}
    settings=w.store.get('website_rates',{})
    for index,website in enumerate(WEBSITES):
        website_id=website['id'];mode=AdjustmentButtons();percent=PercentageInput();percent.setObjectName('percentage');percent.setMinimumWidth(140);percent.setFixedHeight(44);percent.setAlignment(Qt.AlignCenter)
        raw=settings.get(website_id,{});invalid=False
        try:saved_mode,saved_percent=rate_config(settings,website_id)
        except ValueError:saved_mode=raw.get('mode','discount');saved_percent=0;invalid=True
        mode.setCurrentIndex(1 if saved_mode=='increase' else 0);percent.setMaximum(100 if saved_mode=='increase' else 99.99);percent.setValue(float(saved_percent))
        status=label('Saved: '+format(saved_percent,'.2f')+'% '+saved_mode,'muted');save=QPushButton('Save');save.setAccessibleName('Save '+website['name']+' rate');save.setToolTip('Save '+website['name']+' rate');save.setEnabled(not invalid)
        row={'mode':mode,'percent':percent,'status':status,'save':save,'invalid':invalid,'dirty':False};w.rate_rows[website_id]=row
        for column,widget in enumerate([label(website['name']),mode,percent,status,save]):
            holder=QWidget();cell=QHBoxLayout(holder);cell.setContentsMargins(8,8,8,8);cell.addWidget(widget);
            w.rate_table.setCellWidget(index,column,holder)
        w.rate_table.setRowHeight(index,104)
        mode.currentIndexChanged.connect(lambda value,k=website_id:w.update_rate_draft(k));percent.valueChanged.connect(lambda value,k=website_id:w.update_rate_draft(k));percent.lineEdit().textEdited.connect(lambda value,k=website_id:w.update_rate_draft(k));save.clicked.connect(lambda checked=False,k=website_id:w.save_rate(k))
        w.update_rate_draft(website_id,initial=True)
        if invalid:status.setObjectName('error');status.setText('Saved value '+str(raw.get('percent'))+'% is invalid. Enter a valid rate and save.')
    layout.addWidget(w.rate_table);layout.addStretch();w.pages.addWidget(rates)
    # DRC aliases keep existing diagnostic/test callers compatible with the row-based editor.
    drc=w.rate_rows['drc'];w.rate_mode=drc['mode'];w.rate_percent=drc['percent'];w.rate_saved=drc['status']
    history,layout=page();w.history_page=history
    top=QHBoxLayout();caption=label('Upload history and server responses','muted');caption.setWordWrap(False);top.addWidget(caption);top.addStretch();w.history_filter=QComboBox();w.history_filter.addItems(['All results','Success','Failure','Sent to DRC','Needs review','Blocked']);w.history_filter.currentIndexChanged.connect(w.refresh);top.addWidget(w.history_filter);layout.addLayout(top)
    w.history=QTableWidget(0,6);w.history.setHorizontalHeaderLabels(['Date & time','Inventory type','Excel file','Diamonds','Result','Message']);w.history.setEditTriggers(QAbstractItemView.NoEditTriggers);w.history.setSelectionBehavior(QAbstractItemView.SelectRows);w.history.setAlternatingRowColors(True);w.history.setShowGrid(False);w.history.verticalHeader().hide();w.history.verticalHeader().setDefaultSectionSize(42)
    for index,width in enumerate([185,120,200,90,150]):w.history.setColumnWidth(index,width)
    w.history.horizontalHeader().setStretchLastSection(True);w.history.cellDoubleClicked.connect(lambda row,column:w.open_history_response(row));layout.addWidget(w.history,1)
    bottom=QHBoxLayout();bottom.addStretch();button=QPushButton('View response');button.clicked.connect(lambda:w.open_history_response(w.history.currentRow()));bottom.addWidget(button);layout.addLayout(bottom);w.pages.addWidget(history)
    configure_table(w.rate_table,104,False);configure_table(w.history,52);w.nav_group.idClicked.connect(w.navigate);w.navigate(0);w.set_sidebar_collapsed(w.store.get('ui.sidebar_collapsed',False),persist=False)
