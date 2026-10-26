"""Public change summaries derive only from persisted patch records."""
import json


def change_summary(row):
    result = dict(row)
    try:
        records = json.loads(result.pop('changes_json', '[]'))
    except (ValueError, TypeError):
        records = []
    result['files'] = []
    for item in records if isinstance(records, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            continue
        lines = str(item.get('diff', '')).splitlines()
        added, removed, in_hunk = 0, 0, False
        for line in lines:
            if line.startswith('@@'):
                in_hunk = True
            elif in_hunk:
                added += line.startswith('+')
                removed += line.startswith('-')
        result['files'].append({
            'path': item['path'],
            'additions': added,
            'deletions': removed,
            'before_sha256': item.get('before_sha256', ''),
            'after_sha256': item.get('after_sha256', ''),
        })
    return result
