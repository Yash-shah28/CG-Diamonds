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
    store.db.close()
    store = Store(tmp_path/'history.db')
    assert store.db.execute('SELECT filename FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == 'old.xlsx'
    store.db.close()
