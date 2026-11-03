import json
import pytest
from openpyxl import Workbook
from drc_sync.contract import ENDPOINT, OLD_ENDPOINT, FIELDS, ALIASES, new_profile, suggested_field
from drc_sync.core import parse_excel, Store, deliver
import httpx

@pytest.fixture
def drc_profile(tmp_path):
    p = new_profile(); p['path'] = str(tmp_path/'drc.xlsx')
    wb=Workbook(); ws=wb.active; ws.title='Stock'
    ws.append(['Stock #','Cut Shape','Cts','Colour','Clr','PPC','Certificate #','Video Link','Image 2','Disc %','Girdle','Girdle %'])
    ws.append(['ABC123','Round','1.01','D','VVS1','5000','001234','https://example.com/video','https://example.com/image',-10,'THIN',3.5])
    wb.save(p['path']); p['sheet']='Stock'
    p['mapping']={h:suggested_field(h) for h in next(ws.values)}
    return p


def test_all_import_header_aliases():
    for field, aliases in ALIASES.items():
        for alias in aliases:
            assert suggested_field(alias) == field, (alias, field)
    assert suggested_field('Internal supplier account') == ''


def test_drc_full_payload(drc_profile):
    record = parse_excel(drc_profile)['diamonds'][0]
    assert {k:record[k] for k in ('StockId','Shape','Weight','Color','Clarity','PricePerCarat')} == {
        'StockId':'ABC123','Shape':'Round','Weight':'1.01','Color':'D','Clarity':'VVS1','PricePerCarat':'5000'}
    assert record['Diamond Type'] == 'NATURAL'
    assert record['Certificate #'] == '001234'
    assert record['Video'] == 'https://example.com/video'
    assert record['Image 2'] == 'https://example.com/image'
    assert record['Discount'] == '-10'
    assert record['Girdle'] == 'THIN'
    assert record['Girdle %'] == '3.5'
    drc_profile['kind']='Lab Grown'
    assert parse_excel(drc_profile)['diamonds'][0]['Diamond Type']=='LAB_GROWN'


def test_numeric_excel_cells_emit_numeric_strings(drc_profile):
    from openpyxl import load_workbook
    wb=load_workbook(drc_profile['path']); ws=wb['Stock']; ws['C2']=1.01; ws['F2']=5000
    wb.save(drc_profile['path']); wb.close()
    record=parse_excel(drc_profile)['diamonds'][0]
    assert record['Weight']=='1.01' and record['PricePerCarat']=='5000'


def test_conflicting_diamond_type_rejected(drc_profile):
    from openpyxl import load_workbook
    wb=load_workbook(drc_profile['path']); ws=wb['Stock']; ws['M1']='Diamond Type'; ws['M2']='Lab Grown'
    wb.save(drc_profile['path']); wb.close(); drc_profile['mapping']['Diamond Type']='Diamond Type'
    with pytest.raises(ValueError, match='Diamond Type'): parse_excel(drc_profile)


def test_omit_blank_optional_fields(drc_profile):
    from openpyxl import load_workbook
    wb=load_workbook(drc_profile['path']); wb['Stock']['G2']=None; wb['Stock']['J2']=None
    wb.save(drc_profile['path']); wb.close()
    record=parse_excel(drc_profile)['diamonds'][0]
    assert 'Certificate #' not in record and 'Discount' not in record


def test_migrate_old_profiles_preserves_read_only_legacy_payloads(tmp_path):
    path=tmp_path/'migration.db'; store=Store(path)
    old={'id':'natural','kind':'Natural','path':'unchanged.xlsx','sheet':'Sheet1','endpoint':OLD_ENDPOINT,
         'mapping':{'No':'stock_id','Carat':'carat'},'required':['stock_id'],'numeric':['carat'],'identity':'stock_id','confirmed':True}
    store.set('profiles',[old]); store.enqueue('natural',{'diamonds':[{'stock_id':'old'}]}); store.db.close()
    store=Store(path); p=store.get('profiles',[])[0]
    assert p['path']=='unchanged.xlsx' and p['endpoint']==ENDPOINT
    assert p['mapping']=={'No':'StockId','Carat':'Weight'} and not p['confirmed']
    row=store.db.execute('SELECT state,payload FROM jobs').fetchone()
    assert row['state']=='pending' and 'old' in row['payload']
    store.db.close(); store=Store(path)
    assert store.get('profiles',[])[0]==p


def test_exact_endpoint_and_sample_wire_payload(tmp_path,drc_profile):
    store=Store(tmp_path/'wire.db'); drc_profile['confirmed']=True; drc_profile['success_field']='success'
    payload=parse_excel(drc_profile); n=store.enqueue('natural',payload)
    job=store.db.execute('SELECT * FROM jobs WHERE id=?',(n,)).fetchone()
    def handle(request):
        assert str(request.url)=='https://connect.drc.diamonds/v1/inventory/push'
        assert request.headers['content-type']=='application/json'
        assert request.headers['x-api-key']=='mock-key'
        assert json.loads(request.content)==payload
        return httpx.Response(200,json={'success':True})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        deliver(store,job,drc_profile,'mock-key',client)
    assert store.db.execute('SELECT state FROM jobs').fetchone()[0]=='success'

def test_every_listed_field_can_be_sent(tmp_path):
    from drc_sync.contract import DECIMAL_FIELDS
    p=new_profile(); p['path']=str(tmp_path/'all-fields.xlsx'); p['sheet']='Stock'
    wb=Workbook(); ws=wb.active; ws.title='Stock'; ws.append(list(FIELDS))
    values={field:'value' for field in FIELDS}
    values.update({field:'1' for field in DECIMAL_FIELDS})
    values.update({'StockId':'000123','Shape':'Round','Weight':'1.01','Color':'D','Clarity':'VVS1','PricePerCarat':'5000','Diamond Type':'Natural'})
    ws.append([values[f] for f in FIELDS]); wb.save(p['path'])
    p['mapping']={field:field for field in FIELDS}
    record=parse_excel(p)['diamonds'][0]
    assert set(record)==set(FIELDS)
    assert record['StockId']=='000123'
    assert all(isinstance(value,str) for value in record.values())

def test_existing_curl_style_profile_also_receives_type_and_string_preset():
    from drc_sync.contract import migrate_profile, FORMAT
    old={'id':'lab','kind':'Lab Grown','endpoint':ENDPOINT,'mapping':{'Stock':'StockId','Cts':'Weight'},'confirmed':True}
    updated=migrate_profile(old)
    assert updated['request_format']==FORMAT
    assert updated['mapping']==old['mapping']
    assert 'Weight' in updated['string_fields']
    assert updated['confirmed'] is False
    updated['confirmed']=True
    assert migrate_profile(updated)['confirmed'] is True

def test_blank_color_and_clarity_are_optional_in_new_profiles(drc_profile):
    from openpyxl import load_workbook
    wb=load_workbook(drc_profile['path']); ws=wb['Stock']; ws['D2']=None; ws['E2']=None
    wb.save(drc_profile['path']); wb.close()
    record=parse_excel(drc_profile)['diamonds'][0]
    assert 'Color' not in record and 'Clarity' not in record
    assert record['StockId']=='ABC123'
