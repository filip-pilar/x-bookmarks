"""Read-only, bookmark-level activity and bounded queue pages."""
import json
import time
from store import db
from processing import run_details
from controls import PENDING_ITEMS

# Aggregate before pagination so counts and rows use the same state definition.
# A running stage takes precedence; its unresolved source issues remain in details.
WORK = """WITH work AS (
 SELECT s.item,s.id,s.run,s.kind,s.state,s.error,s.next_at,s.updated,r.state AS run_state
 FROM stages s LEFT JOIN runs r ON r.id=s.run WHERE s.state!='cancelled'
 UNION ALL
 SELECT j.item,j.id,j.run,j.kind,j.state,j.error,j.next_at,j.updated,r.state
 FROM jobs j LEFT JOIN runs r ON r.id=j.run WHERE j.kind='process' AND j.state!='cancelled'
), totals AS (
 SELECT item,sum(state='running') AS active,
 sum(state='blocked' OR (state='browser' AND error IN ('private_page_access','paywalled_page','login_or_paywall','authentication_or_challenge'))) AS attention,
 sum(state='failed' OR (state='browser' AND error NOT IN ('private_page_access','paywalled_page','login_or_paywall','authentication_or_challenge'))) AS failed,
 sum(state IN ('queued','waiting')) AS pending,
 sum(state='waiting' AND run_state='running') AS retrying,
 sum(state IN ('unavailable','skipped')) AS unavailable
 FROM work GROUP BY item
), classified AS (
 SELECT i.id,i.title,i.author,i.url,
 CASE WHEN t.active>0 THEN 'active' WHEN t.attention>0 THEN 'attention'
 WHEN t.retrying>0 AND :paused=0 THEN 'retrying' WHEN t.pending>0 THEN 'waiting'
 WHEN t.failed>0 THEN 'partial'
 WHEN EXISTS(SELECT 1 FROM work_available a WHERE a.item=i.id AND NOT EXISTS(SELECT 1 FROM work_plan p WHERE p.item=i.id AND p.category=a.category AND p.revision=i.hash)) THEN 'waiting'
 WHEN t.unavailable>0 OR EXISTS(SELECT 1 FROM x_dependencies d WHERE d.item=i.id AND d.state!='complete') THEN 'partial' ELSE 'complete' END AS status,
 coalesce(t.active,0) AS active_steps,coalesce(t.attention,0) AS issues,
 CASE WHEN """ + PENDING_ITEMS + """ THEN 1 ELSE 0 END AS eligible
 FROM items i LEFT JOIN totals t ON t.item=i.id
 WHERE i.active=1
) """


def run_state(c, run, paused):
    result = run_details(c, run)
    problems = c.execute("SELECT sum(state IN ('failed','blocked','browser')),sum(state IN ('unavailable','skipped')),sum(state IN ('failed','blocked')) FROM stages WHERE run=?", (run['id'],)).fetchone()
    failed_jobs = c.execute("SELECT count(*) FROM jobs WHERE run=? AND state='failed'", (run['id'],)).fetchone()[0]
    result['retryable_steps'] = (problems[2] or 0)+failed_jobs
    result['source_issues'] = (problems[0] or 0)+failed_jobs
    running = c.execute("SELECT count(*) FROM (SELECT id FROM jobs WHERE run=? AND state='running' UNION ALL SELECT id FROM stages WHERE run=? AND state='running')", (run['id'], run['id'])).fetchone()[0]
    permitted = not paused and run['state'] == 'running'
    result['activity_state'] = ('pausing' if running and not permitted else
        'running' if permitted and (running or result['pending_steps']) else
        'limited' if (result.get('limits') or {}).get('reason') else
        'paused' if result['pending_steps'] else
        'attention' if result['source_issues'] else 'partial' if problems[1] else 'complete')
    return result


def page(args):
    view = args.get('view', 'all')
    if view not in ('all', 'active', 'waiting', 'attention', 'complete', 'partial', 'history'):
        raise ValueError('Choose an Activity view.')
    limit = max(1, min(50, int(args.get('limit', 20))))
    offset = max(0, int(args.get('offset', 0)))
    query = str(args.get('query', ''))[:300].strip()
    with db() as c:
        c.execute('BEGIN')
        saved = {r['key']: json.loads(r['value']) for r in c.execute("SELECT key,value FROM settings WHERE key IN ('paused','folder_inventory','last_inventory','full_inventory_at')")}
        paused = bool(saved.get('paused', True))
        params = {'paused': int(paused), 'query': query, 'offset': offset, 'limit': limit}
        # One snapshot avoids rescanning every stage for counts, pagination and live rows.
        c.execute('CREATE TEMP TABLE activity_items AS '+WORK+'SELECT * FROM classified', params)
        counts = {key: 0 for key in ('active', 'waiting', 'retrying', 'attention', 'complete', 'partial')}
        counts.update({r['status']: r['n'] for r in c.execute('SELECT status,count(*) n FROM activity_items GROUP BY status')})
        counts['total'] = sum(counts.values())
        counts['eligible'] = c.execute(f'SELECT count(*) FROM items i WHERE {PENDING_ITEMS}').fetchone()[0]
        sync = c.execute("SELECT j.*,r.state AS run_state,r.created AS run_created FROM jobs j JOIN runs r ON r.id=j.run WHERE j.kind='sync' ORDER BY CASE WHEN j.state IN ('complete','cancelled') THEN 1 ELSE 0 END,j.created DESC,j.id LIMIT 1").fetchone()
        importing = None
        if sync:
            payload = json.loads(sync['payload'])
            importing = {**run_state(c, {'id': sync['run'], 'state': sync['run_state'], 'created': sync['run_created']}, paused),
                'job_state': sync['state'], 'mode': payload.get('mode', 'full'), 'phase': payload.get('phase', 'bookmarks'),
                'returned': payload.get('returned', 0), 'error': sync['error'], 'next_at': sync['next_at'],
                'process': bool(payload.get('process')),
                'missing_details': c.execute("SELECT count(DISTINCT item) FROM x_dependencies d JOIN items i ON i.id=d.item WHERE i.active=1 AND d.state IN ('pending','missing')").fetchone()[0], 'unfinished': sync['state'] not in ('complete','cancelled')}
        process_where = """
            EXISTS(SELECT 1 FROM jobs j WHERE j.run=r.id AND j.kind='process' AND j.state NOT IN ('complete','cancelled'))
            OR EXISTS(SELECT 1 FROM stages s WHERE s.run=r.id AND s.state IN ('queued','running','waiting','failed','blocked','browser'))
            """
        processing_running = not paused and bool(c.execute("SELECT 1 FROM runs r WHERE r.state='running' AND ("+process_where+") LIMIT 1").fetchone())
        processing_pausing = bool(c.execute("SELECT 1 FROM stages s LEFT JOIN runs r ON r.id=s.run WHERE s.state='running' AND (? OR r.state!='running') UNION ALL SELECT 1 FROM jobs j LEFT JOIN runs r ON r.id=j.run WHERE j.kind='process' AND j.state='running' AND (? OR r.state!='running') LIMIT 1", (paused, paused)).fetchone())
        processing_run_count = c.execute('SELECT count(*) FROM runs r WHERE '+process_where).fetchone()[0]
        processing_runs = [run_state(c, r, paused) for r in c.execute('SELECT * FROM runs r WHERE '+process_where+' ORDER BY created DESC,id LIMIT 2')]
        if view == 'history':
            total = c.execute("SELECT count(*) FROM runs r WHERE EXISTS(SELECT 1 FROM jobs j WHERE j.run=r.id) OR EXISTS(SELECT 1 FROM stages s WHERE s.run=r.id)").fetchone()[0]
            offset = min(offset, max(0, (total-1)//limit*limit))
            items = [run_state(c, r, paused) for r in c.execute("SELECT * FROM runs r WHERE EXISTS(SELECT 1 FROM jobs j WHERE j.run=r.id) OR EXISTS(SELECT 1 FROM stages s WHERE s.run=r.id) ORDER BY created DESC,id LIMIT ? OFFSET ?", (limit, offset))]
        else:
            where = "(:query='' OR instr(lower(title),lower(:query)) OR instr(lower(author),lower(:query)))"
            if view == 'waiting':
                where += " AND status IN ('waiting','retrying')"
            elif view != 'all':
                where += ' AND status=:view'
                params['view'] = view
            total = c.execute('SELECT count(*) FROM activity_items WHERE '+where, params).fetchone()[0]
            offset = min(offset, max(0, (total-1)//limit*limit))
            params['offset'] = offset
            # Stable ID order: completing a stage never moves its row under the pointer.
            rows = c.execute('SELECT * FROM activity_items WHERE '+where+' ORDER BY id DESC LIMIT :limit OFFSET :offset', params).fetchall()
            items = present(c, rows)
        visible = list(dict.fromkeys(str(i) for i in args.get('visible_ids', [])))[:50]
        visible_items = []
        if visible and view != 'history':
            for index, item in enumerate(visible):
                params['visible'+str(index)] = item
            visible_items = present(c, c.execute('SELECT * FROM activity_items WHERE id IN ('+','.join(':visible'+str(i) for i in range(len(visible)))+')', params))
        return {'view': view, 'items': items, 'total': total, 'offset': offset,
            'next_offset': offset+limit if offset+limit < total else None,
            'counts': counts, 'import': importing,
            'missing_details': c.execute("SELECT count(DISTINCT item) FROM x_dependencies d JOIN items i ON i.id=d.item WHERE i.active=1 AND d.state IN ('pending','missing')").fetchone()[0], 'processing_runs': processing_runs,
            'processing_run_count': processing_run_count, 'processing_running': processing_running, 'processing_pausing': processing_pausing, 'visible_items': visible_items,
            'paused': paused, 'coverage': saved, 'at': time.time()}


def present(c, rows):
    from library import row_presentation, content_types
    result = []
    for row in rows:
        raw = json.loads(c.execute('SELECT raw FROM items WHERE id=?', (row['id'],)).fetchone()[0])
        work = c.execute('''SELECT w.*,r.state AS run_state FROM (
            SELECT kind,state AS stage_state,error,next_at,run,updated FROM stages WHERE item=? AND state!='cancelled'
            UNION ALL SELECT kind,state,error,next_at,run,updated FROM jobs WHERE item=? AND kind='process' AND state!='cancelled'
            ) w LEFT JOIN runs r ON r.id=w.run ORDER BY CASE stage_state
            WHEN 'running' THEN 0 WHEN 'blocked' THEN 1 WHEN 'failed' THEN 2 WHEN 'browser' THEN 3
            WHEN 'waiting' THEN 4 WHEN 'queued' THEN 5 WHEN 'unavailable' THEN 6 ELSE 7 END,updated DESC LIMIT 1''', (row['id'],row['id'])).fetchone()
        deferred = __import__('workplan').missing(c, c.execute('SELECT * FROM items WHERE id=?', (row['id'],)).fetchone())
        result.append({'deferred': deferred, **dict(row), **(dict(work) if work else {}), 'presentation': row_presentation(raw), 'content_types': content_types(raw)})
    return result


def details(args):
    item = str(args['item'])
    offset = max(0, int(args.get('offset', 0)))
    with db() as c:
        c.execute('BEGIN')
        if not c.execute('SELECT 1 FROM items WHERE id=? AND active=1', (item,)).fetchone():
            raise ValueError('Bookmark is not in this library.')
        rows = c.execute("SELECT * FROM (SELECT kind,state,error,next_at,run,source,id FROM stages WHERE item=? AND state!='cancelled' UNION ALL SELECT 'process',state,error,next_at,run,item,id FROM jobs WHERE item=? AND kind='process' AND state NOT IN ('cancelled','complete')) ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'blocked' THEN 1 WHEN 'browser' THEN 1 ELSE 2 END,kind,source,id LIMIT 31 OFFSET ?", (item, item, offset)).fetchall()
        deferred = __import__('workplan').missing(c, c.execute('SELECT * FROM items WHERE id=?', (item,)).fetchone())
        dependencies = [dict(r) for r in c.execute("SELECT relation,state,error FROM x_dependencies WHERE item=? AND state!='complete'", (item,))]
        return {'item': item, 'deferred': deferred, 'missing_sources': dependencies, 'stages': [dict(r) for r in rows[:30]], 'next_offset': offset+30 if len(rows)>30 else None}
