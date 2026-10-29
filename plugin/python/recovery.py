"""Grouped website recovery and explicit bulk retry/skip controls."""
import json
import time
import uuid
from urllib.parse import urlsplit
from store import db

ACCESS = ('private_page_access', 'paywalled_page', 'login_or_paywall', 'authentication_or_challenge')


def groups(args):
    with db() as c:
        rows = c.execute("SELECT s.* FROM stages s JOIN items i ON i.id=s.item WHERE i.active=1 AND s.kind='page' AND s.state IN ('browser','blocked','failed','unavailable','skipped') ORDER BY s.id").fetchall()
    grouped = {}
    for r in rows:
        needs = r['state'] == 'browser' and r['error'] in ACCESS
        if args.get('view', 'attention') == 'attention' and not needs:
            continue
        if args.get('view') == 'partial' and needs:
            continue
        url = json.loads(r['payload'])['url']
        domain = urlsplit(url).hostname or 'Unknown website'
        kind = 'service' if r['state']=='blocked' else 'access' if needs else 'skipped' if r['state'] == 'skipped' else 'unavailable' if r['state'] == 'unavailable' else 'extraction'
        key = domain + ':' + kind
        g = grouped.setdefault(key, {'id': key, 'domain': domain, 'kind': kind, 'sources': set(), 'items': set(), 'errors': set()})
        g['sources'].add(r['source']); g['items'].add(r['item']); g['errors'].add(r['error'])
    values = [{**g, 'pages': len(g['sources']), 'bookmarks': len(g['items']), 'errors': sorted(e for e in g['errors'] if e)} for g in grouped.values()]
    for g in values:
        del g['sources']; del g['items']
    values.sort(key=lambda g: g['id'])
    limit = min(50, max(1, int(args.get('limit', 20))))
    offset = min(max(0, int(args.get('offset', 0))), max(0, (len(values)-1)//limit*limit))
    return {'offset': offset, 'groups': values[offset:offset+limit], 'total': len(values), 'next_offset': offset+limit if offset+limit < len(values) else None}


def control(args):
    action = args.get('action')
    if action not in ('retry', 'skip', 'restore'):
        raise ValueError('Choose retry, skip or restore.')
    ids = args.get('items')
    if ids is not None and (not isinstance(ids, list) or not ids or len(ids) > 500 or any(not str(i).isdigit() for i in ids)):
        raise ValueError('Choose up to 500 bookmarks.')
    domain = args.get('domain')
    if action in ('skip', 'restore') and not (ids or domain):
        raise ValueError('Select bookmarks or a website to skip or restore.')
    run = uuid.uuid4().hex; affected = set(); changed = 0
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows = c.execute("SELECT s.* FROM stages s JOIN items i ON i.id=s.item WHERE i.active=1 AND s.state IN ('failed','blocked','browser','unavailable','skipped')").fetchall()
        for row in rows:
            if args.get('category') and row['category'] != args['category']:
                continue
            if ids and row['item'] not in ids:
                continue
            p = json.loads(row['payload'])
            if domain and (row['kind'] != 'page' or urlsplit(p.get('url', '')).hostname != domain):
                continue
            kind = 'service' if row['state']=='blocked' else 'access' if row['state'] == 'browser' and row['error'] in ACCESS else 'skipped' if row['state'] == 'skipped' else 'unavailable' if row['state'] == 'unavailable' else 'extraction'
            if args.get('kind') and kind != args['kind']:
                continue
            if action == 'retry' and (row['state'] in ('skipped', 'unavailable') or (row['state'] == 'browser' and row['error'] in ACCESS)):
                continue
            if action == 'restore' and row['state'] != 'skipped':
                continue
            if action == 'skip' and row['state'] == 'skipped':
                continue
            if action == 'skip':
                c.execute("UPDATE stages SET state='skipped',error='source_skipped',updated=? WHERE id=?", (time.time(), row['id']))
            else:
                c.execute("UPDATE stages SET state='queued',attempts=0,error=NULL,next_at=0,run=?,updated=? WHERE id=?", (run, time.time(), row['id']))
                if row['kind'] == 'page':
                    target = __import__('page_providers').public_target(p['url'])
                    if target:
                        c.execute('DELETE FROM page_attempts WHERE source=?', (__import__('store').fingerprint(target),))
            changed += 1; affected.add(row['item'])
    if changed and action != 'skip':
        __import__('pipeline').authorize(run)
    for item in affected:
        __import__('pipeline').refresh_item(item)
    return {'changed': changed, 'bookmarks': len(affected), 'started': bool(changed and action != 'skip'), 'run': run if changed and action != 'skip' else None}
