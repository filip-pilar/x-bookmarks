"""Persistent run budgets and account cooldowns; reading settings never starts work."""
import contextlib
import contextvars
import time
from store import db, setting, config

RUN = contextvars.ContextVar('processing_run', default=None)
DEFAULTS = {'cache_gib': 8, 'local_workers': 0, 'description_workers': 0,
            'video_height': 480, 'x_requests': 100, 'description_requests': 100}
# Status and the pending-only action must select the same bookmarks. Existing
# work belongs to its run, including failed work that needs an explicit retry.
PENDING_ITEMS = """i.active=1 AND (i.processed_hash IS NULL OR i.processed_hash!=i.hash)
    AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.item=i.id AND j.kind='process'
        AND j.state NOT IN ('complete','cancelled'))"""


class BudgetReached(RuntimeError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__('run_limit_' + kind)


class CoolingDown(RuntimeError):
    def __init__(self, until):
        self.until = until
        super().__init__('chatgpt_cooldown')


def migrate(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS run_limits(run TEXT PRIMARY KEY, x_limit INTEGER, description_limit INTEGER,
      x_used INTEGER DEFAULT 0, description_used INTEGER DEFAULT 0, item_limit INTEGER DEFAULT 0,
      reason TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS provider_cooldowns(account TEXT PRIMARY KEY, until REAL, reason TEXT);
    CREATE TABLE IF NOT EXISTS media_reservations(asset TEXT PRIMARY KEY, bytes INTEGER);
    ''')


def preferences():
    result = {**DEFAULTS, **(setting('processing_preferences', {}) or {})}
    # Preserve an explicitly configured private cache budget on existing installations.
    if not setting('processing_preferences') and config().get('media_cache_bytes'):
        result['cache_gib'] = max(1, config()['media_cache_bytes'] / 1024**3)
    return result


def configure(args):
    allowed = {'cache_gib': (4, 128), 'local_workers': (0, 4), 'description_workers': (0, 4),
               'video_height': (240, 1080), 'x_requests': (0, 100000), 'description_requests': (0, 100000)}
    values = preferences()
    for key, value in args.items():
        if key not in allowed or isinstance(value, bool) or not isinstance(value, int) or not allowed[key][0] <= value <= allowed[key][1]:
            raise ValueError('Invalid processing setting: ' + key)
        values[key] = value
    if values['video_height'] not in (240, 360, 480, 720, 1080):
        raise ValueError('Choose an available video resolution.')
    from store import set_setting
    set_setting('processing_preferences', values)
    return {'preferences': values, 'started': False}


def validate_limits(limits=None, processing=False):
    limits = limits or {}
    if not isinstance(limits, dict) or set(limits) - {'x_requests', 'description_requests', 'items'}:
        raise ValueError('Unknown run limit.')
    if processing and 'items' in limits:
        raise ValueError('Item limits apply to imports, not processing saved bookmarks.')
    defaults = preferences()
    values = [limits.get('x_requests', defaults['x_requests']), limits.get('description_requests', defaults['description_requests']), limits.get('items', 0)]
    if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100000 for v in values):
        raise ValueError('Run limits must be whole numbers from 0 to 100000; 0 means no limit.')
    return values


def create(c, run, limits=None):
    values = validate_limits(limits)
    c.execute('INSERT OR IGNORE INTO run_limits(run,x_limit,description_limit,item_limit) VALUES (?,?,?,?)', (run, *values))


@contextlib.contextmanager
def scope(run):
    token = RUN.set(run)
    try:
        yield
    finally:
        RUN.reset(token)


def stop(run, kind):
    with db() as c:
        c.execute("UPDATE runs SET state='paused' WHERE id=?", (run,))
        c.execute('UPDATE run_limits SET reason=? WHERE run=?', (kind, run))
    raise BudgetReached(kind)


def claim(kind):
    run = RUN.get()
    if not run:
        return  # Personal sign-in and explicitly invoked generated-media preflight have no run.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        create(c, run)
        row = c.execute('SELECT * FROM run_limits WHERE run=?', (run,)).fetchone()
        paused = c.execute('SELECT state FROM runs WHERE id=?', (run,)).fetchone()
        if setting('paused', True) or not paused or paused[0] != 'running':
            raise InterruptedError('processing_paused')
        limit, used = row[kind + '_limit'], row[kind + '_used']
        blocked = bool(limit and used >= limit)
        if blocked:
            c.execute("UPDATE runs SET state='paused' WHERE id=?", (run,))
            c.execute('UPDATE run_limits SET reason=? WHERE run=?', (kind, run))
        else:
            # Reserve before sending. An interruption may overcount, never silently buy another request.
            c.execute(f'UPDATE run_limits SET {kind}_used={kind}_used+1 WHERE run=?', (run,))
    if blocked:
        raise BudgetReached(kind)


def remaining_items(run, used):
    with db() as c:
        row = c.execute('SELECT item_limit FROM run_limits WHERE run=?', (run,)).fetchone()
    return None if not row or not row[0] else max(0, row[0] - used)


def extend(args):
    run, kind, amount = args['run'], args['kind'], args['amount']
    if kind not in ('x', 'description', 'items') or isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 100000:
        raise ValueError('Choose a positive budget extension.')
    column = 'item_limit' if kind == 'items' else kind + '_limit'
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM run_limits WHERE run=?', (run,)).fetchone()
        if not row or not row[column]:
            raise ValueError('This run has no matching limit.')
        c.execute(f"UPDATE run_limits SET {column}={column}+?,reason=CASE WHEN reason=? THEN '' ELSE reason END WHERE run=?", (amount, kind, run))
    return {'run': run, 'added': amount, 'kind': kind, 'started': False}


def check_cooldown(account):
    with db() as c:
        row = c.execute('SELECT until FROM provider_cooldowns WHERE account=?', (account or '',)).fetchone()
    if row and row[0] > time.time():
        raise CoolingDown(row[0])


def cool_down(account, until, reason):
    with db() as c:
        c.execute('INSERT INTO provider_cooldowns VALUES (?,?,?) ON CONFLICT(account) DO UPDATE SET until=max(until,excluded.until),reason=excluded.reason', (account or '', max(time.time()+5, until), reason))


def snapshot():
    account = (__import__('subscription').load('accounts', {}) or {}).get('active') or ''
    with db() as c:
        cooldowns = [dict(r) for r in c.execute('SELECT until,reason FROM provider_cooldowns WHERE account=? AND until>?', (account, time.time()))]
        unprocessed = c.execute(f"SELECT count(*) FROM items i WHERE {PENDING_ITEMS}").fetchone()[0]
    return {'preferences': preferences(), 'cooldowns': cooldowns, 'unprocessed': unprocessed}
