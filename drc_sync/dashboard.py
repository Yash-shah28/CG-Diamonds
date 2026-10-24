"""Read-only presentation of sync outcomes; queue acceptance is not import completion."""
import json
from pathlib import Path
from .responses import queued_acceptance


def result_label(row):
    try:
        body = json.loads(row['response_body'] or '{}')
    except (ValueError, TypeError):
        body = {}
    if isinstance(body, dict):
        if body.get('success') is False or body.get('errors') or body.get('failedItems'):
            return 'Failure'
        if queued_acceptance(row['response_status'], row['response_body']):
            return 'Sent to DRC'
    return {'sent': 'Sent to DRC', 'success': 'Success', 'failed': 'Failure', 'blocked': 'Blocked',
            'review': 'Needs review', 'retry': 'Failure'}.get(row['state'], row['state'].capitalize())


def history_entries(rows, profiles):
    by_id = {p['id']: p for p in profiles}
    entries = []
    for row in rows:
        profile = by_id.get(row['profile'], {})
        entries.append(dict(id=row['id'], created=row['created'],
            kind=row['response_kind'] or row['kind'] or profile.get('kind', row['profile']),
            filename=Path(row['response_filename'] or row['filename'] or profile.get('path', '')).name,
            records=row['records'], result=result_label(row), detail=row['detail']))
    return entries
