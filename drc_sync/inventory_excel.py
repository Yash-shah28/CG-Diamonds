"""Edit one row of an XLSX without resaving other sheets or dropping cached formulas."""
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import posixpath
import re
import shutil
from tempfile import NamedTemporaryFile
from xml.dom import Node, minidom
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils.cell import coordinate_from_string, column_index_from_string, get_column_letter, range_boundaries

from .contract import DECIMAL_FIELDS, REQUIRED_FIELDS, suggested_field
from .core import WorkbookValidationError


@dataclass
class InventoryColumn:
    index: int
    header: str
    field: str
    text: str
    percent: bool
    numeric: bool
    formula: bool


def workbook_stamp(path):
    info = Path(path).stat()
    return info.st_size, info.st_mtime_ns, info.st_ino


def _percent_format(fmt):
    return '%' in re.sub(r'"[^"]*"|\[[^\]]*\]|\\.|_.|\*.', '', fmt or '')


def read_inventory_row(profile, row_number=None, expected_stamp=None):
    path = Path(profile['path'])
    stamp = workbook_stamp(path)
    if expected_stamp is not None and stamp != expected_stamp:
        raise WorkbookValidationError('Excel changed. Refresh the inventory table before editing the selected row.')
    mapping = {header.casefold().strip(): field for header, field in profile.get('mapping', {}).items()}
    workbook = load_workbook(path, read_only=True)
    try:
        sheet = workbook[profile['sheet']]
        sheet.reset_dimensions()
        rows = sheet.iter_rows()
        headers = next(rows, ())
        selected = ()
        previous_styles = {}
        stock_ids = set()
        stock_index = next((i for i, cell in enumerate(headers)
                            if mapping.get(str(cell.value).strip().casefold(), suggested_field(cell.value)) == 'StockId'), None)
        for number, cells in enumerate(rows, 2):
            if number == row_number:
                selected = cells
            elif stock_index is not None and stock_index < len(cells) and cells[stock_index].value is not None:
                stock_ids.add(str(cells[stock_index].value).strip())
            if row_number is None:
                for index, cell in enumerate(cells):
                    if cell.value is not None:
                        previous_styles[index] = cell
        columns = []
        for index, header_cell in enumerate(headers):
            if header_cell.value is None:
                continue
            header = str(header_cell.value).strip()
            if not header:
                continue
            field = mapping.get(header.casefold(), suggested_field(header))
            cell = selected[index] if index < len(selected) else None
            style_cell = cell if cell is not None and cell.value is not None else previous_styles.get(index, cell)
            formatting = style_cell.number_format if style_cell else None
            percent = _percent_format(formatting)
            value = cell.value if cell is not None else None
            if percent and isinstance(value, (int, float)) and not isinstance(value, bool):
                value = format(Decimal(str(value)).scaleb(2).normalize(), 'f')
            numeric = (percent or field in DECIMAL_FIELDS or field in ('Weight', 'PricePerCarat')
                       or field in profile.get('numeric', ()))
            columns.append(InventoryColumn(
                index + 1, header, field, '' if value is None else str(value), percent, numeric,
                bool(cell is not None and cell.data_type == 'f'),
            ))
    finally:
        workbook.close()
    if workbook_stamp(path) != stamp:
        raise WorkbookValidationError('Excel changed while reading. Reopen the inventory form and try again.')
    return stamp, columns, stock_ids


def row_values(columns, texts, stock_ids, *, adding):
    fields = {column.field: texts[column.index].strip() for column in columns}
    for field in REQUIRED_FIELDS:
        if not fields.get(field):
            raise WorkbookValidationError(f'Enter {field} before saving.')
    if fields['StockId'] in stock_ids:
        raise WorkbookValidationError('That Stock ID is already in this worksheet.')
    values = {}
    for column in columns:
        value = texts[column.index].strip()
        if column.formula or (not adding and value == column.text.strip()):
            continue
        if value.startswith('='):
            raise WorkbookValidationError(f'{column.header}: enter a value instead of a formula.')
        if ILLEGAL_CHARACTERS_RE.search(value):
            raise WorkbookValidationError(f'{column.header}: remove the invalid control characters before saving.')
        if column.numeric and value:
            try:
                number = Decimal(value.removesuffix('%').strip() if column.percent else value)
                if not number.is_finite() or (column.field in ('Weight', 'PricePerCarat') and number <= 0):
                    raise InvalidOperation()
                value = number.scaleb(-2) if column.percent else number
            except InvalidOperation:
                raise WorkbookValidationError(f'{column.header}: enter a valid numeric value.') from None
        if value or not adding:
            values[column.index] = value
    return values


def _elements(node, name):
    return [child for child in node.childNodes if child.nodeType == Node.ELEMENT_NODE and child.localName == name]


def _entry(archive, path):
    return minidom.parseString(archive.read(path))


def _related_entries(archive, package_path):
    part_dir, basename = posixpath.split(package_path)
    doc = _entry(archive, posixpath.join(part_dir, '_rels', basename + '.rels'))
    return {
        item.getAttribute('Id'): (item.getAttribute('Target').lstrip('/') if item.getAttribute('Target').startswith('/')
                                  else posixpath.normpath(posixpath.join(part_dir, item.getAttribute('Target'))))
        for item in _elements(doc.documentElement, 'Relationship')
        if item.getAttribute('TargetMode') != 'External'
    }


def _sheet_path(archive, name):
    workbook_path = 'xl/workbook.xml'
    workbook = _entry(archive, workbook_path).documentElement
    sheet, = (sheet for sheet in _elements(_elements(workbook, 'sheets')[0], 'sheet')
              if sheet.getAttribute('name') == name)
    return _related_entries(archive, workbook_path)[sheet.getAttribute('r:id')]


def _new(parent, name):
    doc = parent.ownerDocument
    tag = f'{parent.prefix}:{name}' if parent.prefix else name
    return doc.createElementNS(parent.namespaceURI, tag)


def _extend_range(node, row_number):
    reference = node.getAttribute('ref')
    if not reference:
        return
    _, _, _, last = range_boundaries(reference)
    if last == row_number - 1:
        end = re.sub(r'\d+$', str(row_number), reference)
        node.setAttribute('ref', end if ':' in reference else f'{reference}:{end}')


def _write_cells(row, row_number, values, styles):
    cells = {column_index_from_string(coordinate_from_string(cell.getAttribute('r'))[0]): cell
             for cell in _elements(row, 'c')}
    for column, value in sorted(values.items()):
        cell = cells.get(column)
        if cell is None:
            cell = _new(row, 'c')
            cell.setAttribute('r', f'{get_column_letter(column)}{row_number}')
            style = styles.get(column)
            if style:
                cell.setAttribute('s', style)
            follower = next((cells[index] for index in sorted(cells) if index > column), None)
            row.insertBefore(cell, follower)
            cells[column] = cell
        if _elements(cell, 'f'):
            raise WorkbookValidationError('A formula cell cannot be replaced from this editor.')
        for child in _elements(cell, 'v') + _elements(cell, 'is'):
            cell.removeChild(child)
        if isinstance(value, Decimal):
            cell.setAttribute('t', 'n')
            data = _new(cell, 'v')
            data.appendChild(cell.ownerDocument.createTextNode(format(value, 'f')))
            cell.appendChild(data)
        elif value:
            cell.setAttribute('t', 'inlineStr')
            inline = _new(cell, 'is')
            data = _new(inline, 't')
            data.appendChild(cell.ownerDocument.createTextNode(value))
            inline.appendChild(data)
            cell.appendChild(inline)
        elif cell.hasAttribute('t'):
            cell.removeAttribute('t')


@contextmanager
def _stage_file(destination):
    with NamedTemporaryFile(dir=destination.parent, prefix='~$chintamani-', suffix='.xlsx', delete=False) as temp:
        file = Path(temp.name)
    try:
        yield file
    finally:
        file.unlink(missing_ok=True)


def save_inventory_row(profile, row_number, values, stamp):
    path = Path(profile['path']).resolve()
    changed = 'Excel changed after the form opened. Reopen the form before saving.'
    if workbook_stamp(path) != stamp:
        raise WorkbookValidationError(changed)
    if not values:
        return
    with _stage_file(path) as stage:
        with ZipFile(path) as source:
            sheet_path = _sheet_path(source, profile['sheet'])
            document = _entry(source, sheet_path)
            sheet = document.documentElement
            sheet_data, = _elements(sheet, 'sheetData')
            rows = {int(row.getAttribute('r')): row for row in _elements(sheet_data, 'row')}
            last = max(rows, default=1)
            index = row_number if row_number is not None else last + 1
            row = rows.get(index)
            styles = {}
            if row is None:
                row = _new(sheet_data, 'row')
                row.setAttribute('r', str(index))
                follower = next((rows[number] for number in sorted(rows) if number > index), None)
                sheet_data.insertBefore(row, follower)
                if row_number is None:
                    for previous in reversed(sorted(rows)):
                        if previous == 1:
                            break
                        for cell in _elements(rows[previous], 'c'):
                            if cell.hasChildNodes():
                                column = column_index_from_string(coordinate_from_string(cell.getAttribute('r'))[0])
                                styles.setdefault(column, cell.getAttribute('s'))
            _write_cells(row, index, values, styles)
            replacements = {}
            if row_number is None:
                for node in _elements(sheet, 'autoFilter') + _elements(sheet, 'dimension'):
                    _extend_range(node, index)
                for collection in _elements(sheet, 'tableParts'):
                    paths = _related_entries(source, sheet_path)
                    for table_part in _elements(collection, 'tablePart'):
                        table_path = paths[table_part.getAttribute('r:id')]
                        table = _entry(source, table_path)
                        _extend_range(table.documentElement, index)
                        for node in _elements(table.documentElement, 'autoFilter'):
                            _extend_range(node, index)
                        replacements[table_path] = table.toxml(encoding='utf-8')
            replacements[sheet_path] = document.toxml(encoding='utf-8')
            workbook_doc = _entry(source, 'xl/workbook.xml')
            for calculation in _elements(workbook_doc.documentElement, 'calcPr'):
                if calculation.getAttribute('fullCalcOnLoad') != '1':
                    calculation.setAttribute('fullCalcOnLoad', '1')
                    replacements['xl/workbook.xml'] = workbook_doc.toxml(encoding='utf-8')
            with ZipFile(stage, 'w') as target:
                for entry in source.infolist():
                    content = replacements.get(entry.filename)
                    target.writestr(entry, source.read(entry) if content is None else content)
        if workbook_stamp(path) != stamp:
            raise WorkbookValidationError(changed)
        os.replace(stage, path)


def download_inventory(profile, destination):
    source = Path(profile['path']).resolve()
    target = Path(destination).resolve()
    if source == target:
        raise WorkbookValidationError('Choose a different location for the downloaded Excel copy.')
    if target.suffix.lower() != '.xlsx':
        raise WorkbookValidationError('Save the downloaded workbook with the .xlsx extension.')
    stamp = workbook_stamp(source)
    with _stage_file(target) as stage:
        shutil.copyfile(source, stage)
        if workbook_stamp(source) != stamp:
            raise WorkbookValidationError('Excel changed during the download. Try again.')
        os.replace(stage, target)

