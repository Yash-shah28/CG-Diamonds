import json
from drc_sync.dashboard import result_label
from drc_sync.core import Store


def test_queued_acceptance_is_not_completed_success():
    row = dict(state='success', response_status=200, response_body=json.dumps(dict(success=True, message='Push queued successfully', uploadId=1671)))
    assert result_label(row) == 'Sent to DRC'
    row['response_body'] = json.dumps(dict(success=False, errors=['Invalid items']))
    assert result_label(row) == 'Failure'
    row['response_body'] = '{}'
    assert result_label(row) == 'Success'
    row['state'] = 'review'
    assert result_label(row) == 'Needs review'


def test_replacement_retains_original_job_file(tmp_path):
    store = Store(tmp_path/'history.db')
    original = dict(id='natural', kind='Natural', path='old.xlsx')
    store.set('profiles', [original])
    job_id = store.enqueue('natural', {'diamonds': [{'StockId': 'A'}]})
    store.set('profiles', [dict(original, path='replacement.xlsx')])
    store = Store(tmp_path/'history.db')
    assert store.db.execute('SELECT filename FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 'old.xlsx'
    store.db.close()


def test_dashboard_diamond_cards_and_history_columns(tmp_path, monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from drc_sync.app import Window, Worker
    from drc_sync.portal_store import PortalStore

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)

    db_path = tmp_path / 'test_dash.db'
    ps = PortalStore(db_path)
    # cache natural and lab grown diamonds
    ps.cache({'kind': 'Natural', 'id': 'nat', 'path': 'nat.xlsx', 'sheet': 'Sheet1', 'mapping': {'StockId': 'StockId', 'PricePerCarat': 'PricePerCarat'}, 'required': [], 'numeric': [], 'decimal_fields': [], 'request_format': 'two-array'},
             {'diamonds': [{'StockId': 'N1', 'PricePerCarat': 100}, {'StockId': 'N2', 'PricePerCarat': 200}]})
    ps.cache({'kind': 'Lab Grown', 'id': 'lab', 'path': 'lab.xlsx', 'sheet': 'Sheet1', 'mapping': {'StockId': 'StockId', 'PricePerCarat': 'PricePerCarat'}, 'required': [], 'numeric': [], 'decimal_fields': [], 'request_format': 'two-array'},
             {'diamonds': [{'StockId': 'L1', 'PricePerCarat': 50}]})
    cid = ps.add_company('Test Jewels', 'test@jewels.com', ['Natural'], secret_writer=lambda *a: None)
    ps.history(cid, 'Publish', 'Published to API', 'Published 2 diamonds', 2)

    w = Window(db_path)
    # Check metric values
    assert 'Natural diamonds' in w.metric_values
    assert 'Lab Grown diamonds' in w.metric_values
    assert w.metric_values['Natural diamonds'].text() == '2'
    assert w.metric_values['Lab Grown diamonds'].text() == '1'
    assert 'Connected files' not in w.metric_values
    assert 'Failure' not in w.metric_values

    # Check history headers
    headers = [w.history.horizontalHeaderItem(i).text() for i in range(w.history.columnCount())]
    assert headers == ['Date & time', 'Diamond Type', 'Status', 'Results (Diamonds)', 'Type', 'Message']

    # Check history rows
    assert w.history.rowCount() >= 1
    # Check Type column
    type_text = w.history.item(0, 4).text()
    assert 'Publish' in type_text and 'Test Jewels' in type_text

    # Check dashboard fetch info widget exists
    assert hasattr(w, 'dashboard_fetch_info')

    w.tray.hide(); w.timer.stop(); w.store.db.close(); w.deleteLater(); app.processEvents()
