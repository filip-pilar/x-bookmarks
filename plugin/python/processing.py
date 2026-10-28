"""Paged recovery views over saved work; reading never resumes processing."""
from store import db

ISSUES = """WITH entries AS (
 SELECT s.item,s.run,s.kind,s.state,COALESCE(s.error,'') error,s.next_at,s.updated,
        i.title,i.author,'stage' AS origin FROM stages s JOIN items i ON i.id=s.item
 WHERE i.active=1 AND s.state IN ('failed','blocked','browser','unavailable','waiting')
 UNION ALL
 SELECT j.item,j.run,j.kind,j.state,COALESCE(j.error,'') error,j.next_at,j.updated,
        COALESCE(i.title,'Bookmark sync'),COALESCE(i.author,''),'job'
 FROM jobs j LEFT JOIN items i ON i.id=j.item
 WHERE (j.item='' OR i.active=1) AND j.state IN ('failed','waiting')
), keyed AS (
 SELECT *,CASE WHEN item!='' THEN 'item:'||item ELSE 'run:'||run END AS key,
        CASE state WHEN 'failed' THEN 0 WHEN 'blocked' THEN 0 WHEN 'browser' THEN 1 WHEN 'waiting' THEN 2 ELSE 3 END AS priority
 FROM entries
) """
RUNS = "EXISTS(SELECT 1 FROM jobs WHERE jobs.run=runs.id) OR EXISTS(SELECT 1 FROM stages WHERE stages.run=runs.id)"


def counts(c):
    return {
        'processing_issue_count': c.execute(ISSUES + 'SELECT count(DISTINCT key) FROM keyed').fetchone()[0],
        'run_count': c.execute('SELECT count(*) FROM runs WHERE ' + RUNS).fetchone()[0],
        'running_run_count': c.execute('SELECT count(*) FROM runs WHERE state=\'running\' AND (' + RUNS + ')').fetchone()[0],
    }


def run_details(c, run):
    run = dict(run)
    limit = c.execute('SELECT * FROM run_limits WHERE run=?', (run['id'],)).fetchone()
    run['limits'] = dict(limit) if limit else None
    work = c.execute("SELECT kind,json_extract(payload,'$.mode') AS mode,json_extract(payload,'$.process') AS process FROM jobs WHERE run=? ORDER BY CASE kind WHEN 'sync' THEN 0 ELSE 1 END,created LIMIT 1", (run['id'],)).fetchone()
    run['media_processing'] = bool(not work or work['kind'] != 'sync' or work['process'])
    run['label'] = ('Retry unfinished work' if not work else
        {'new': 'Sync new bookmarks', 'full': 'Full bookmark sync', 'folders': 'Folder sync', 'sample': 'Import 10 bookmarks'}.get(work['mode'], 'Bookmark sync') if work['kind'] == 'sync' else
        {'rebuild': 'Rebuild saved content', 'refresh': 'Refresh sources'}.get(work['mode'], 'Process bookmarks'))
    run['items'] = c.execute("SELECT count(DISTINCT item) FROM (SELECT item FROM jobs WHERE run=? UNION SELECT item FROM stages WHERE run=?) WHERE item!=''", (run['id'], run['id'])).fetchone()[0]
    imported = c.execute("SELECT max(json_extract(payload,'$.returned')) FROM jobs WHERE run=? AND kind='sync'", (run['id'],)).fetchone()[0]
    run['items'] = max(run['items'], imported or 0)
    run['retryable_steps'] = c.execute("SELECT count(*) FROM (SELECT id FROM stages WHERE run=? AND state IN ('failed','blocked','waiting') UNION ALL SELECT id FROM jobs WHERE run=? AND state='failed')", (run['id'], run['id'])).fetchone()[0]
    run['pending_steps'] = c.execute("SELECT count(*) FROM (SELECT id FROM stages WHERE run=? AND state IN ('queued','running','waiting') UNION ALL SELECT id FROM jobs WHERE run=? AND state IN ('queued','running','waiting'))", (run['id'], run['id'])).fetchone()[0]
    return run


def page(args):
    view = args.get('view', 'issues')
    if view not in ('issues', 'runs'):
        raise ValueError('Choose issues or runs.')
    limit = max(1, min(50, int(args.get('limit', 20))))
    offset = max(0, int(args.get('offset', 0)))
    with db() as c:
        c.execute('BEGIN')
        totals = counts(c)
        total = totals['processing_issue_count' if view == 'issues' else 'run_count']
        offset = min(offset, max(0, (total - 1) // limit * limit))
        if view == 'runs':
            rows = c.execute('SELECT * FROM runs WHERE ' + RUNS + " ORDER BY CASE WHEN state='complete' THEN 1 ELSE 0 END,created DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            items = [run_details(c, r) for r in rows]
        else:
            groups = c.execute(ISSUES + 'SELECT key,count(*) AS steps FROM keyed GROUP BY key ORDER BY min(priority),max(updated) DESC,key LIMIT ? OFFSET ?', (limit, offset)).fetchall()
            items = []
            for group in groups:
                # One summary per bookmark. Its reader exposes individual source stages.
                entries = [dict(r) for r in c.execute(ISSUES + 'SELECT * FROM keyed WHERE key=? ORDER BY priority,updated DESC,kind', (group['key'],))]
                first = entries[0]
                items.append({**{k: first[k] for k in ('key','item','run','title','author','error','state')},
                    'steps': group['steps'], 'kinds': sorted({r['kind'] for r in entries}),
                    'retryable': sum(r['state'] == 'failed' or (r['origin'] == 'stage' and r['state'] in ('blocked','waiting')) for r in entries),
                    'browser': any(r['state'] == 'browser' for r in entries),
                    'account': any(r['error'].startswith('chatgpt_') for r in entries),
                    'next_at': min((r['next_at'] for r in entries if r['state'] == 'waiting'), default=None)})
    return {'view': view, 'items': items, 'total': total, 'offset': offset,
            'next_offset': offset + limit if offset + limit < total else None}
