import csv
from io import StringIO
import json
from pathlib import Path

import httpx
from openpyxl import Workbook
import pytest

from drc_sync.contract import new_profile
from drc_sync.core import WorkbookValidationError
from drc_sync.rapaport import LOTS_URL, TOKEN_URL, RapaportClient, RapaportError, prepare_natural_upload


UPLOAD_ID = '3fa85f64-5717-4562-b3fc-2c963f66afa6'
CREDENTIALS = {'client_id': 'test-client', 'client_secret': 'test-secret'}


@pytest.fixture
def natural(tmp_path):
    path = tmp_path / 'natural.xlsx'
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Natural'
    sheet.append(['Stock #', 'Shape', 'Weight', 'Color', 'Clarity', 'Price', 'Discount', 'Depth', 'Table',
                  'Image', 'Video', 'Type Marker', 'Cost'])
    sheet.append(['0012', 'Round', 1.234, 'E', 'VS2', 2999, -0.65, 0.6194, 0.58,
                  'https://example.com/stone.jpg', 'https://example.com/stone.mp4', 'Natural', 1500])
    for cell in ('G2', 'H2', 'I2'):
        sheet[cell].number_format = '0.00%'
    workbook.save(path)
    profile = new_profile()
    profile.update(path=str(path), sheet='Natural', mapping=dict(zip(
        ('Stock #', 'Shape', 'Weight', 'Color', 'Clarity', 'Price', 'Discount', 'Depth',
         'Table', 'Image', 'Video', 'Cost'),
        ('StockId', 'Shape', 'Weight', 'Color', 'Clarity', 'PricePerCarat', 'Discount', 'Depth %',
         'Table', 'Image', 'Video', 'Cost Price'))))
    return profile


def test_natural_csv_uses_source_prices_and_rapaport_field_formats(natural):
    upload = prepare_natural_upload(natural)
    stone, = csv.DictReader(StringIO(upload.content.decode()))
    assert upload.records == 1
    assert stone['Stock #'] == '0012'
    assert stone['Weight'] == '1.23'
    assert stone['PricePerCarat'] == '2999'
    assert stone['RapnetDiscountPercent'] == '-0.6500'
    assert stone['Depth %'] == '61.9' and stone['Table %'] == '58'
    assert stone['Diamond Image'] == 'https://example.com/stone.jpg'
    assert stone['Video Link'] == 'https://example.com/stone.mp4'
    assert 'Cost Price' not in stone


def test_rapaport_rejects_lab_grown_and_unmapped_type_column(natural):
    from openpyxl import load_workbook
    with pytest.raises(WorkbookValidationError, match='Natural workbook'):
        prepare_natural_upload({**natural, 'kind': 'Lab Grown'})
    book = load_workbook(natural['path'])
    book.active['L1'] = 'Diamond Type'
    book.active['L2'] = 'LAB_GROWN'
    book.save(natural['path'])
    book.close()
    with pytest.raises(WorkbookValidationError, match='does not match the Natural'):
        prepare_natural_upload(natural)


def test_login_add_update_and_status(natural):
    prepared = prepare_natural_upload(natural)
    requests = []

    def handler(request):
        requests.append(request)
        if str(request.url) == TOKEN_URL:
            assert json.loads(request.content) == CREDENTIALS
            return httpx.Response(200, json={'access_token': 'temporary-token', 'expires_in': 7200})
        assert request.headers['Authorization'] == 'Bearer temporary-token'
        if request.method == 'POST':
            assert str(request.url) == LOTS_URL
            assert request.headers['Content-Type'].startswith('multipart/form-data; boundary=')
            assert b'name="ReplaceAll"\r\n\r\nfalse' in request.content
            assert b'name="FirstRowHeaders"\r\n\r\ntrue' in request.content
            assert b'name="File"; filename="Natural-Inventory.csv"' in request.content
            assert prepared.content in request.content
            return httpx.Response(200, json={'uploadId': UPLOAD_ID})
        assert str(request.url) == LOTS_URL + '/status/' + UPLOAD_ID
        return httpx.Response(200, json={'status': 'Finished successfully', 'numValidLots': 1, 'numInvalidLots': 0})

    service = RapaportClient()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        uid = service.upload(client, CREDENTIALS, prepared)
        status = service.status(client, CREDENTIALS, uid)
    assert uid == UPLOAD_ID
    assert status['numValidLots'] == 1
    assert [request.method for request in requests] == ['POST', 'POST', 'GET']


def test_multipart_uses_ordered_presigned_parts_without_credentials(monkeypatch, natural):
    prepared = prepare_natural_upload(natural)
    size = 300
    monkeypatch.setattr('drc_sync.rapaport.SINGLE_UPLOAD_LIMIT', size)
    count = (len(prepared.content) + size - 1) // size
    parts = []

    def handler(request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={'access_token': 'temporary-token'})
        if request.url.host.endswith('.amazonaws.com'):
            assert request.method == 'PUT' and 'Authorization' not in request.headers
            assert request.headers['Content-Type'] == 'text/csv'
            part = int(request.url.params['partNumber'])
            assert part == len(parts) + 1
            parts.append(request.content)
            return httpx.Response(200, headers={'ETag': f'"part-{part}"'})
        assert str(request.url) == LOTS_URL + '/multipart'
        assert request.headers['Authorization'] == 'Bearer temporary-token'
        body = json.loads(request.content)
        if request.method == 'POST':
            assert body == {'fileName': 'Natural-Inventory.csv', 'replaceAll': False, 'sendEmail': False,
                            'diamondFileFormat': 'Rapnet', 'fileSize': len(prepared.content)}
            return httpx.Response(200, json={
                'uploadId': UPLOAD_ID, 'multipartUploadId': 'session',
                'multipartSplitSize': size,
                'putUrls': [f'https://diamondupdate-prod1.s3.amazonaws.com/file?partNumber={n}'
                            for n in range(count, 0, -1)],
            })
        assert request.method == 'PUT'
        assert body == {'uploadId': UPLOAD_ID, 'multipartUploadId': 'session',
                        'eTags': {str(n): f'part-{n}' for n in range(1, count + 1)}}
        return httpx.Response(200, json={'uploadId': UPLOAD_ID})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert RapaportClient().upload(client, CREDENTIALS, prepared) == UPLOAD_ID
    assert b''.join(parts) == prepared.content


def test_upload_timeout_is_not_retried(natural):
    attempts = 0

    def handler(request):
        nonlocal attempts
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={'access_token': 'temporary-token'})
        attempts += 1
        raise httpx.ReadTimeout('timeout', request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RapaportError, match='result is unknown'):
            RapaportClient().upload(client, CREDENTIALS, prepare_natural_upload(natural))
    assert attempts == 1


def test_rapaport_page_uses_saved_credentials_and_natural_only(natural, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QLineEdit
    from drc_sync.app import Window, Worker
    from drc_sync.rapaport_ui import CREDENTIAL_SLOT

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    saved = {}
    monkeypatch.setattr('drc_sync.rapaport_ui.portal_secrets.get_secret', lambda slot: saved.get(slot))
    monkeypatch.setattr('drc_sync.rapaport_ui.portal_secrets.put_secret', lambda slot, value: saved.update({slot: value}))
    window = Window(tmp_path / 'rapaport.db')
    page = window.rapaport_page
    next(button for button in window.nav_buttons if button.text() == 'Rapaport').click()
    assert window.pages.currentWidget() is page
    window.store.set('profiles', [{**natural, 'kind': 'Lab Grown'}])
    page.refresh_source()
    page.client_id.setText(CREDENTIALS['client_id'])
    page.client_secret.setText(CREDENTIALS['client_secret'])
    assert page.client_secret.echoMode() == QLineEdit.Password
    page.save_button.click()
    assert json.loads(saved[CREDENTIAL_SLOT]) == CREDENTIALS
    assert not page.client_secret.text()
    assert not page.upload_button.isEnabled()
    window.store.set('profiles', [natural])
    page.refresh_source()
    assert page.upload_button.isEnabled()
    assert Path(natural['path']).name in page.source.text()
    page.client_id.setText('different account')
    assert not page.upload_button.isEnabled()
    assert window.store.get('rapaport-api', {}) == {}
    window.tray.hide()
    window.timer.stop()
    window.store.db.close()
    window.deleteLater()
    app.processEvents()



def test_rapaport_500_identifies_login_and_redacts_credentials():
    def handler(request):
        assert str(request.url) == TOKEN_URL
        return httpx.Response(500, json={'message': 'Unavailable for test-secret'},
                              headers={'X-Request-ID': 'request-001'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RapaportError, match='login failed.*HTTP 500.*request-001') as error:
            RapaportClient().login(client, CREDENTIALS)
    assert 'test-secret' not in str(error.value)
    assert not error.value.uncertain


def test_automatic_rapaport_upload_stays_natural_and_deduplicates(natural, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from drc_sync.app import Window, Worker
    from drc_sync.rapaport_ui import UPLOAD_SETTING

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Worker, 'start', lambda self: None)
    monkeypatch.setattr('drc_sync.rapaport_ui.portal_secrets.get_secret', lambda slot: json.dumps(CREDENTIALS))
    window = Window(tmp_path / 'rapaport-auto.db')
    try:
        page = window.rapaport_page
        window.store.set('profiles', [natural])
        page.refresh_source()
        prepared = prepare_natural_upload(natural)
        preparation = []
        dispatch = []
        monkeypatch.setattr(page, '_start', lambda action, message, operation: preparation.append(action))
        monkeypatch.setattr(page, '_start_network', lambda action, message, operation: dispatch.append(action))

        window.worker.natural_validated.emit(natural)
        assert preparation == []
        page.auto_upload.click()
        assert preparation == ['auto-prepare']
        window.worker.natural_validated.emit({**natural, 'kind': 'Lab Grown'})
        assert preparation == ['auto-prepare']

        page.begin_upload(prepared, automatic=True)
        assert dispatch == ['upload']
        assert page.pending_dispatch()['digest']
        page.begin_upload(prepared, automatic=True)
        assert dispatch == ['upload']

        page._task = SimpleNamespace(action='upload', error='', result=UPLOAD_ID, deleteLater=lambda: None)
        page.finish()
        assert not page.pending_dispatch()
        page._auto_digest = None
        window.worker.natural_validated.emit(natural)
        assert preparation == ['auto-prepare']
        page.auto_tick()
        assert dispatch == ['upload', 'status']
        page._task = SimpleNamespace(action='status', error='', result={'status': 'Finished successfully'},
                                     deleteLater=lambda: None)
        page.finish()
        assert preparation == ['auto-prepare', 'auto-prepare']
        assert window.store.get(UPLOAD_SETTING, {})['finished']
        page.begin_upload(prepared, automatic=True)
        assert dispatch == ['upload', 'status']
    finally:
        window.tray.hide()
        window.timer.stop()
        window.rapaport_page.auto_timer.stop()
        window.store.db.close()
        window.deleteLater()
        app.processEvents()
