"""Consistent selection dialogs and mapping editors."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QDialogButtonBox,QWidget,QStyledItemDelegate,QStyleOptionViewItem


def dialog_layout(dialog):
    layout=QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(16)
    return layout


def dialog_buttons(dialog,accept='Continue'):
    buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText(accept)
    buttons.button(QDialogButtonBox.Cancel).setObjectName('quiet')
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    return buttons


def choose_item(parent,title,prompt,items,current=0,editable=False):
    dialog=QDialog(parent);dialog.setWindowTitle(title);dialog.setMinimumWidth(440)
    layout=dialog_layout(dialog);heading=QLabel(title);heading.setObjectName('dialogTitle');layout.addWidget(heading)
    caption=QLabel(prompt);caption.setWordWrap(True);caption.setObjectName('muted');layout.addWidget(caption)
    combo=QComboBox();combo.setEditable(editable);combo.addItems(items);combo.setCurrentIndex(current);combo.setMinimumHeight(44);layout.addWidget(combo)
    layout.addSpacing(4);layout.addWidget(dialog_buttons(dialog))
    accepted=dialog.exec()==QDialog.Accepted
    return combo.currentText(),accepted


class MappingField(QWidget):
    def __init__(self,combo):
        super().__init__();self.combo=combo
        layout=QHBoxLayout(self);layout.setContentsMargins(10,6,10,6);layout.setSpacing(0)
        combo.setFixedHeight(42);layout.addWidget(combo);self.setFocusProxy(combo)
    def currentText(self):return self.combo.currentText()


class ColumnLabelDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        adjusted=QStyleOptionViewItem(option);adjusted.rect.adjust(12,0,-8,0)
        super().paint(painter,adjusted,index)
