#!/usr/bin/env python3
"""Website-authorized personal APK delivery and authenticated activity reminders."""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, build_opener, ProxyHandler

BASE = Path(__file__).resolve().parent
DB_PATH = os.environ.get('LEJIAN_DB', str(BASE / 'data/lejian.db'))
HOST = os.environ.get('LEJIAN_HOST', '127.0.0.1')
PORT = int(os.environ.get('LEJIAN_PORT', '18090'))
TOKEN_FILE = os.environ.get('LEJIAN_TOKEN_FILE', str(BASE / 'token.txt'))
WEBSITE_TOKEN_FILE = os.environ.get('LEJIAN_WEBSITE_TOKEN_FILE', str(BASE / 'website-token.txt'))
RESOLVE_URL = os.environ.get('LEJIAN_BINDING_RESOLVE_URL', '')
RESOLVE_HOST = os.environ.get('LEJIAN_BINDING_RESOLVE_HOST', '')
DOWNLOAD_DIR = Path(os.environ.get('LEJIAN_DOWNLOAD_DIR', str(BASE / 'data/downloads')))
LEAD_SECONDS = int(os.environ.get('LEJIAN_LEAD_SECONDS', '1800'))
TZ = timezone(timedelta(hours=8))
_lock = threading.RLock()
_build_lock = threading.Lock()
_conn = None
SAFE_ID = re.compile(r'[A-Za-z0-9_-]{16,128}')


def token(path=None):
    try:
        return Path(path or TOKEN_FILE).read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def db():
    global _conn
    if _conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
        _conn.executescript('''
        CREATE TABLE IF NOT EXISTS devices (
            dev TEXT PRIMARY KEY, created REAL NOT NULL, last_seen REAL NOT NULL,
            qq TEXT, bound_at REAL, name TEXT, student_no TEXT, model TEXT,
            last_code TEXT, last_code_at REAL);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dev TEXT NOT NULL, kind TEXT NOT NULL,
            activity_id TEXT, title TEXT, start_ms INTEGER, end_ms INTEGER, address TEXT,
            school TEXT, state TEXT NOT NULL, created REAL NOT NULL, remind_at REAL,
            sent_at REAL, attempts INTEGER NOT NULL DEFAULT 0, qq TEXT, raw TEXT);
        CREATE INDEX IF NOT EXISTS events_state ON events(state, remind_at);
        CREATE TABLE IF NOT EXISTS grants (
            id TEXT PRIMARY KEY, bootstrap_hash TEXT UNIQUE NOT NULL,
            account_id TEXT NOT NULL, qq TEXT NOT NULL, auth_version INTEGER,
            created REAL NOT NULL, expires REAL NOT NULL, download_expires REAL NOT NULL,
            dev TEXT, secret_hash TEXT);
        CREATE TABLE IF NOT EXISTS activity_state (
            dev TEXT NOT NULL, activity_id TEXT NOT NULL, at INTEGER NOT NULL,
            kind TEXT NOT NULL, PRIMARY KEY(dev,activity_id));
        CREATE TABLE IF NOT EXISTS receipts (
            dev TEXT NOT NULL, event_id TEXT NOT NULL, received REAL NOT NULL,
            PRIMARY KEY(dev,event_id));
        CREATE TABLE IF NOT EXISTS watch_subscriptions (
            account_id TEXT NOT NULL, activity_id TEXT NOT NULL, opening INTEGER NOT NULL,
            at INTEGER NOT NULL, active INTEGER NOT NULL,
            PRIMARY KEY(account_id,activity_id,opening));
        ''')
        for table, definition in [
            ('devices', 'secret_hash TEXT'), ('devices', 'account_id TEXT'),
            ('devices', 'auth_version INTEGER'), ('events', 'claimed_at REAL'),
            ('events', 'claim_token TEXT'), ('events', 'activity_start_ms INTEGER')]:
            column = definition.split()[0]
            if column not in {r[1] for r in _conn.execute('PRAGMA table_info(%s)' % table)}:
                _conn.execute('ALTER TABLE %s ADD COLUMN %s' % (table, definition))
        # The old hook collected unnecessary school credentials and identity.
        _conn.execute('UPDATE events SET raw=NULL WHERE raw IS NOT NULL')
        _conn.execute('UPDATE devices SET name=NULL,student_no=NULL,last_code=NULL,last_code_at=NULL')
        _conn.commit()
    return _conn


def current_binding(account_id, qq, auth_version=None):
    """Always ask the website; a network failure cannot authorize an account."""
    credential = token(WEBSITE_TOKEN_FILE)
    if not credential or not RESOLVE_URL:
        return None
    payload = {'account_id': str(account_id), 'qq': str(qq)}
    if auth_version is not None:
        payload['auth_version'] = auth_version
    headers = {'Authorization': 'Bearer ' + credential, 'Content-Type': 'application/json'}
    if RESOLVE_HOST:
        headers['Host'] = RESOLVE_HOST
    req = Request(RESOLVE_URL, data=json.dumps(payload).encode(), headers=headers, method='POST')
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=5) as response:
            data = json.loads(response.read(8192))
            return data['valid'] if response.status == 200 and isinstance(data.get('valid'), bool) else None
    except Exception:
        return None


def authorized_device(dev, secret):
    if not isinstance(dev, str) or not isinstance(secret, str) or not SAFE_ID.fullmatch(secret):
        return None
    with _lock:
        row = db().execute('SELECT * FROM devices WHERE dev=?', (dev,)).fetchone()
        item = dict(row) if row else None
    if not item or not item['secret_hash'] or not secrets.compare_digest(item['secret_hash'], digest(secret)):
        return None
    if not current_binding(item['account_id'], item['qq'], item['auth_version']):
        return None
    return item


def activate(data):
    bootstrap, dev, secret = (data.get(k) for k in ('bootstrap', 'dev', 'device_secret'))
    if not all(isinstance(v, str) and SAFE_ID.fullmatch(v) for v in (bootstrap, secret)):
        return 400, {'ok': False, 'error': 'activation'}
    if not isinstance(dev, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', dev):
        return 400, {'ok': False, 'error': 'device'}
    with _lock:
        row = db().execute('SELECT * FROM grants WHERE bootstrap_hash=?', (digest(bootstrap),)).fetchone()
        grant = dict(row) if row else None
    if not grant or grant['expires'] < time.time():
        return 403, {'ok': False, 'error': 'activation expired; download again from website'}
    if not current_binding(grant['account_id'], grant['qq'], grant['auth_version']):
        return 403, {'ok': False, 'error': 'website binding unavailable'}
    hashed = digest(secret)
    with _lock:
        d = db()
        grant = d.execute('SELECT * FROM grants WHERE id=?', (grant['id'],)).fetchone()
        if grant['dev'] and (grant['dev'] != dev or not secrets.compare_digest(grant['secret_hash'], hashed)):
            return 409, {'ok': False, 'error': 'package already activated on another device'}
        old = d.execute('SELECT * FROM devices WHERE dev=?', (dev,)).fetchone()
        if old and old['secret_hash'] and not secrets.compare_digest(old['secret_hash'], hashed):
            return 409, {'ok': False, 'error': 'device credential mismatch'}
        if old and (old['account_id'] != grant['account_id'] or old['qq'] != grant['qq']):
            d.execute("UPDATE events SET state='cancelled' WHERE dev=? AND state IN ('pending','claimed')", (dev,))
        now = time.time()
        d.execute('''INSERT INTO devices(dev,created,last_seen,qq,bound_at,secret_hash,account_id,auth_version)
                     VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(dev) DO UPDATE SET
                     last_seen=excluded.last_seen,qq=excluded.qq,bound_at=excluded.bound_at,
                     secret_hash=excluded.secret_hash,account_id=excluded.account_id,auth_version=excluded.auth_version''',
                  (dev, now, now, grant['qq'], now, hashed, grant['account_id'], grant['auth_version']))
        d.execute('UPDATE grants SET dev=?,secret_hash=? WHERE id=?', (dev, hashed, grant['id']))
        d.commit()
    return 200, {'ok': True, 'bound': True, 'need_bind': False}


def parse_ms(value):
    try:
        n = int(float(str(value)))
        if n < 10**11:
            n *= 1000
        return n if 10**12 <= n <= 10**14 else None
    except (TypeError, ValueError, OverflowError):
        return None


def s(value, limit=200):
    return ('' if value is None else str(value))[:limit]


def seed_watches(d):
    # Preserve watches set by the previous APK; repeated calls never overwrite
    # an explicit cancellation or a newer account-level subscription.
    d.execute("""INSERT OR IGNORE INTO watch_subscriptions
        SELECT d.account_id,e.activity_id,e.start_ms,CAST(e.created*1000 AS INTEGER),
               CASE WHEN e.state IN ('cancelled','revoked','expired') THEN 0 ELSE 1 END
        FROM events e JOIN devices d ON d.dev=e.dev
        WHERE e.kind='registration_reminder' AND d.account_id IS NOT NULL
        AND e.id=(SELECT MAX(e2.id) FROM events e2 JOIN devices d2 ON d2.dev=e2.dev
                  WHERE e2.kind='registration_reminder' AND d2.account_id=d.account_id
                  AND e2.activity_id=e.activity_id AND e2.start_ms=e.start_ms)""")


def watch_state(device):
    with _lock:
        d=db();seed_watches(d)
        rows=[dict(r) for r in d.execute('SELECT activity_id,opening FROM watch_subscriptions WHERE account_id=? AND active=1 AND opening>?',
                                        (device['account_id'],int(time.time()*1000)))]
        d.commit()
    return {'ok':True,'watches':rows,'limit':4}


def ingest(data, device):
    kind = data.get('event')
    if kind not in ('signup', 'cancel', 'watch_registration', 'cancel_registration_watch'):
        return 400, {'ok': False, 'error': 'event'}
    # Failed signup AND failed cancellation leave an existing signup intact.
    if data.get('ok') is not True:
        return 200, {'ok': True, 'ignored': 'unsuccessful request'}
    activity = data.get('activity') if isinstance(data.get('activity'), dict) else {}
    aid = s(data.get('activityId') or activity.get('id'), 64)
    event_id = data.get('event_id')
    at = parse_ms(data.get('at'))
    start = parse_ms(activity.get('startTime'))
    if not aid or not isinstance(event_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', event_id):
        return 400, {'ok': False, 'error': 'activity or event id'}
    if at is None or at > time.time() * 1000 + 300000:
        return 400, {'ok': False, 'error': 'event time'}
    if kind == 'signup' and not start:
        return 400, {'ok': False, 'error': 'activity start time missing'}
    opening = parse_ms(activity.get('registerStartTime'))
    if kind in ('watch_registration','cancel_registration_watch') and (not opening or opening <= time.time()*1000 or not start):
        return 400, {'ok': False, 'error': 'registration opening time missing or past'}
    dev = device['dev']
    with _lock:
        d = db()
        if d.execute('SELECT 1 FROM receipts WHERE dev=? AND event_id=?', (dev, event_id)).fetchone():
            return 200, {'ok': True, 'duplicate': True}
        old = d.execute('SELECT * FROM activity_state WHERE dev=? AND activity_id=?', (dev, aid)).fetchone()
        d.execute('INSERT INTO receipts VALUES(?,?,?)', (dev, event_id, time.time()))
        if kind in ('watch_registration','cancel_registration_watch'):
            seed_watches(d)
            account = device['account_id']
            previous = d.execute('SELECT * FROM watch_subscriptions WHERE account_id=? AND activity_id=? AND opening=?',
                                 (account,aid,opening)).fetchone()
            if previous and (previous['at'] > at or (previous['at']==at and not previous['active'])):
                d.commit()
                return 200, {'ok':True, 'ignored':'stale event'}
            active = bool(previous and previous['active'])
            if kind=='watch_registration' and not active:
                count=d.execute('SELECT count(*) FROM watch_subscriptions WHERE account_id=? AND active=1 AND opening>?',
                                (account,int(time.time()*1000))).fetchone()[0]
                if count>=4:
                    d.commit()
                    return 409, {'ok':False, 'error':'watch_limit', 'limit':4}
            enabled = kind=='watch_registration'
            d.execute('INSERT INTO watch_subscriptions VALUES(?,?,?,?,?) ON CONFLICT(account_id,activity_id,opening) DO UPDATE SET at=excluded.at,active=excluded.active',
                      (account,aid,opening,at,int(enabled)))
            if not enabled:
                d.execute("""UPDATE events SET state='cancelled',claim_token=NULL WHERE activity_id=? AND start_ms=?
                    AND kind IN ('registration_confirmation','registration_reminder') AND state IN ('pending','claimed')
                    AND dev IN (SELECT dev FROM devices WHERE account_id=?)""",(aid,opening,account))
            elif not active:
                for delivery_kind, due in [('registration_confirmation', time.time()),
                                           ('registration_reminder', opening/1000-120)]:
                    d.execute('''INSERT INTO events(dev,kind,activity_id,title,start_ms,activity_start_ms,address,remind_at,qq,state,created)
                                 VALUES(?,?,?,?,?,?,?,?,?,'pending',?)''',
                              (dev, delivery_kind, aid, s(activity.get('title') or activity.get('name'),120),
                               opening, start, s(activity.get('address'),200), due, device['qq'], time.time()))
            d.execute('UPDATE devices SET last_seen=? WHERE dev=?', (time.time(),dev))
            d.commit()
            return 200, {'ok':True, 'bound':True, 'duplicate':active and enabled, 'watch_active':enabled}
        if old and (old['at'] > at or (old['at'] == at and old['kind'] == 'cancel')):
            d.commit()
            return 200, {'ok': True, 'ignored': 'stale event'}
        d.execute('INSERT INTO activity_state VALUES(?,?,?,?) ON CONFLICT(dev,activity_id) DO UPDATE SET at=excluded.at,kind=excluded.kind',
                  (dev, aid, at, kind))
        if kind == 'cancel':
            d.execute("UPDATE events SET state='cancelled',claim_token=NULL WHERE dev=? AND activity_id=? AND state IN ('pending','claimed')", (dev, aid))
        else:
            exists = d.execute("SELECT id,state FROM events WHERE dev=? AND activity_id=? AND kind='signup' AND state IN ('pending','claimed','sent') ORDER BY id DESC LIMIT 1", (dev, aid)).fetchone()
            if old and old['kind'] == 'cancel':
                exists = None
            values = (s(activity.get('title') or activity.get('name'), 120), start,
                      parse_ms(activity.get('endTime')), s(activity.get('address'), 200),
                      start / 1000 - LEAD_SECONDS, device['qq'])
            if exists:
                d.execute('UPDATE events SET title=?,start_ms=?,end_ms=?,address=?,remind_at=?,qq=? WHERE id=?', values + (exists['id'],))
            else:
                d.execute('''INSERT INTO events(dev,kind,activity_id,title,start_ms,end_ms,address,remind_at,qq,state,created)
                             VALUES(?,'signup',?,?,?,?,?,?,?,'pending',?)''', (dev, aid) + values + (time.time(),))
                # A separate delivery preserves the 30-minute reminder after
                # confirmation is acknowledged. Retries/repeated callbacks do
                # not create another confirmation for the same registration.
                confirmation = values[:4] + (time.time(), device['qq'])
                d.execute('''INSERT INTO events(dev,kind,activity_id,title,start_ms,end_ms,address,remind_at,qq,state,created)
                             VALUES(?,'signup_confirmation',?,?,?,?,?,?,?,'pending',?)''',
                          (dev, aid) + confirmation + (time.time(),))
        d.execute('UPDATE devices SET last_seen=? WHERE dev=?', (time.time(), dev))
        d.commit()
    return 200, {'ok': True, 'bound': True}


def reminder(row):
    start = datetime.fromtimestamp(row['start_ms'] / 1000, TZ).strftime('%m月%d日 %H:%M')
    minutes = max(0, int((row['start_ms'] / 1000 - time.time() + 59) // 60))
    if row['kind'] in ('registration_confirmation', 'registration_reminder'):
        confirmation = row['kind'] == 'registration_confirmation'
        lines = ['🔔 neuro乐健 · 报名提醒已设置' if confirmation else '⏰ neuro乐健 · 即将开放报名',
                 '', '「%s」' % (row['title'] or '活动'), '报名开始：'+start]
        if row.get('activity_start_ms'):
            lines.append('活动开始：'+datetime.fromtimestamp(row['activity_start_ms']/1000,TZ).strftime('%m月%d日 %H:%M'))
        if row['address']:
            lines.append('活动地点：'+row['address'])
        lines += ['', ('将在报名开始前 2 分钟提醒你。' if minutes > 2 else '距报名开放不足 2 分钟，请准备报名。')
                  if confirmation else '约 %d 分钟后开放报名，请打开 neuro乐健 手动报名。' % minutes,
                  '仅设置提醒，不代表已报名，不会自动报名。']
        return {'id':row['id'], 'qq':row['qq'], 'text':'\n'.join(lines),
                'activity_id':row['activity_id'], 'claim_token':row['claim_token']}
    confirmation = row['kind'] == 'signup_confirmation'
    lines = ['✅ neuro乐健 · 预约成功' if confirmation else '🏃 neuro乐健 · 活动提醒',
             '', '「%s」' % (row['title'] or '活动'), '开始时间：' + start]
    if row['address']:
        lines.append('活动地点：' + row['address'])
    lines += ['', '预约已同步，活动开始前 30 分钟会再次提醒。取消预约后将停止提醒。'
              if confirmation and minutes > 30 else
              ('预约已同步。约 %d 分钟后开始，记得提前到场并按时签到。' % minutes
               if confirmation else '约 %d 分钟后开始，记得提前到场并按时签到。' % minutes)]
    return {'id': row['id'], 'qq': row['qq'], 'text': '\n'.join(lines),
            'activity_id': row['activity_id'], 'claim_token': row['claim_token']}


def claim_due(limit):
    now = time.time()
    with _lock:
        d = db()
        d.execute("UPDATE events SET state='expired',claim_token=NULL WHERE state IN ('pending','claimed') AND start_ms<=?", (int(now * 1000),))
        d.execute("UPDATE events SET state=CASE WHEN attempts>=4 THEN 'failed' ELSE 'pending' END,claim_token=NULL WHERE state='claimed' AND COALESCE(claimed_at,0)<?", (now - 600,))
        rows = [dict(r) for r in d.execute("""SELECT e.*,d.account_id,d.auth_version,d.secret_hash FROM events e
                    JOIN devices d ON d.dev=e.dev WHERE e.state='pending' AND e.remind_at<=?
                    AND e.qq=d.qq AND d.secret_hash IS NOT NULL ORDER BY CASE WHEN e.kind='registration_confirmation' THEN 0 ELSE 1 END,e.remind_at,e.id LIMIT ?""", (now, limit))]
        d.commit()
    items = []
    for row in rows:
        valid = current_binding(row['account_id'], row['qq'], row['auth_version'])
        if valid is False:
            with _lock:
                d = db()
                d.execute("UPDATE events SET state='revoked',claim_token=NULL WHERE dev=? AND state IN ('pending','claimed')", (row['dev'],))
                d.commit()
            continue
        if valid is not True:
            continue
        lease = secrets.token_urlsafe(24)
        with _lock:
            d = db()
            changed = d.execute("UPDATE events SET state='claimed',attempts=attempts+1,claimed_at=?,claim_token=? WHERE id=? AND state='pending' AND start_ms>?", (now, lease, row['id'], int(time.time() * 1000))).rowcount
            d.commit()
        if changed:
            row['claim_token'] = lease
            items.append(reminder(row))
    return items


def claim_check(eid, lease):
    with _lock:
        row = db().execute('''SELECT e.*,d.account_id,d.auth_version FROM events e JOIN devices d ON d.dev=e.dev
                             WHERE e.id=? AND e.state='claimed' AND e.claim_token=? AND e.qq=d.qq''', (eid, lease)).fetchone()
        item = dict(row) if row else None
    if not item or item['start_ms'] <= time.time() * 1000 or not current_binding(item['account_id'], item['qq'], item['auth_version']):
        return None
    with _lock:
        if not db().execute("SELECT 1 FROM events WHERE id=? AND state='claimed' AND claim_token=?", (eid, lease)).fetchone():
            return None
    return reminder(item)


def acknowledge(items, state):
    done = 0
    with _lock:
        d = db()
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            eid, lease = item.get('id'), item.get('claim_token')
            if state == 'sent':
                done += d.execute("UPDATE events SET state='sent',sent_at=?,claim_token=NULL WHERE id=? AND state='claimed' AND claim_token=?", (time.time(), eid, lease)).rowcount
            else:
                done += d.execute("UPDATE events SET state=CASE WHEN attempts>=4 THEN 'failed' ELSE 'pending' END,claim_token=NULL WHERE id=? AND state='claimed' AND claim_token=?", (eid, lease)).rowcount
        d.commit()
    return done


def cleanup_downloads():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    now = time.time()
    with _lock:
        expired = [r['id'] for r in db().execute('SELECT id FROM grants WHERE download_expires<?', (now,))]
    for gid in expired:
        if SAFE_ID.fullmatch(gid):
            (DOWNLOAD_DIR / (gid + '.apk')).unlink(missing_ok=True)
    with _lock:
        d = db()
        d.execute('DELETE FROM grants WHERE expires<?', (now,))
        d.execute('DELETE FROM receipts WHERE received<?', (now - 86400 * 30,))
        d.commit()


def issue(data):
    account, qq = data.get('account_id'), data.get('qq')
    version = data.get('auth_version')
    if not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', account) or not isinstance(qq, str) or not re.fullmatch(r'[1-9][0-9]{4,11}', qq):
        return 400, {'ok': False, 'error': 'account'}
    if version is not None and (not isinstance(version, int) or isinstance(version, bool)):
        return 400, {'ok': False, 'error': 'auth version'}
    if not current_binding(account, qq, version):
        return 403, {'ok': False, 'error': 'website binding unavailable'}
    if not _build_lock.acquire(blocking=False):
        return 429, {'ok': False, 'error': 'package builder busy'}
    output = None
    try:
        cleanup_downloads()
        with _lock:
            recent = db().execute('SELECT 1 FROM grants WHERE account_id=? AND created>?', (account, time.time() - 60)).fetchone()
        if recent:
            return 429, {'ok': False, 'error': 'please wait one minute before downloading again'}
        from apk_builder import build_apk
        gid, bootstrap = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        output = DOWNLOAD_DIR / (gid + '.apk')
        build_apk(bootstrap, str(output))
        os.chmod(output, 0o600)
        now = time.time()
        with _lock:
            d = db()
            d.execute('INSERT INTO grants(id,bootstrap_hash,account_id,qq,auth_version,created,expires,download_expires) VALUES(?,?,?,?,?,?,?,?)',
                      (gid, digest(bootstrap), account, qq, version, now, now + 86400 * 7, now + 3600))
            d.commit()
        return 200, {'ok': True, 'download_id': gid, 'expires_in': 3600}
    except Exception as exc:
        if output:
            output.unlink(missing_ok=True)
        sys.stderr.write('package build failed: %s\n' % type(exc).__name__)
        return 503, {'ok': False, 'error': 'package temporarily unavailable'}
    finally:
        _build_lock.release()


class ServiceServer(ThreadingHTTPServer):
    def service_actions(self):
        if time.monotonic() - getattr(self, '_last_cleanup', 0) >= 60:
            self._last_cleanup = time.monotonic()
            try:
                cleanup_downloads()
            except OSError:
                sys.stderr.write('download cleanup temporarily unavailable\n')


class Handler(BaseHTTPRequestHandler):
    server_version = 'lejian/2.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        pass  # URLs, bearer credentials and package handles are not logged.

    def send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        try:
            length = int(self.headers.get('content-length') or 0)
            if not 0 < length <= 32768:
                self.close_connection = True
                return {}
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
            return payload if isinstance(payload, dict) else {}
        except (ValueError, UnicodeError):
            self.close_connection = True
            return {}

    def bearer(self):
        value = self.headers.get('authorization', '')
        return value[7:] if value.startswith('Bearer ') else ''

    def auth_ok(self, website=False):
        want = token(WEBSITE_TOKEN_FILE if website else TOKEN_FILE)
        return bool(want) and secrets.compare_digest(self.bearer(), want)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.removeprefix('/lejian')
        if path == '/health':
            return self.send_json(200, {'ok': True, 'version': 2})
        if path in ('/app.apk', '/link'):
            return self.send_json(410, {'ok': False, 'error': 'download from website after binding QQ'})
        if path.startswith('/download/'):
            if not self.auth_ok(website=True):
                return self.send_json(401, {'ok': False, 'error': 'auth'})
            match = re.fullmatch(r'/download/([A-Za-z0-9_-]{16,128})/app.apk', path)
            if not match:
                return self.send_json(404, {'ok': False})
            gid = match.group(1)
            with _lock:
                row = db().execute('SELECT * FROM grants WHERE id=? AND download_expires>?', (gid, time.time())).fetchone()
                grant = dict(row) if row else None
            if not grant or not current_binding(grant['account_id'], grant['qq'], grant['auth_version']):
                return self.send_json(403, {'ok': False, 'error': 'download expired'})
            file = DOWNLOAD_DIR / (gid + '.apk')
            if not file.is_file():
                return self.send_json(404, {'ok': False, 'error': 'package missing'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.android.package-archive')
            self.send_header('Content-Disposition', 'attachment; filename="lejian-reminder-3.10.3.apk"')
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                with file.open('rb') as stream:
                    while chunk := stream.read(262144):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if path == '/pending':
            if not self.auth_ok():
                return self.send_json(401, {'ok': False, 'error': 'auth'})
            try:
                limit = min(20, max(1, int(parse_qs(parsed.query).get('limit', ['10'])[0])))
            except ValueError:
                return self.send_json(400, {'ok': False, 'error': 'limit'})
            return self.send_json(200, {'ok': True, 'items': claim_due(limit)})
        return self.send_json(404, {'ok': False, 'error': 'not found'})

    def do_POST(self):
        path = urlparse(self.path).path.removeprefix('/lejian')
        data = self.body()
        if path == '/bind':
            return self.send_json(410, {'ok': False, 'error': 'website binding required'})
        if path == '/issue':
            if not self.auth_ok(website=True):
                return self.send_json(401, {'ok': False, 'error': 'auth'})
            return self.send_json(*issue(data))
        if path == '/activate':
            return self.send_json(*activate(data))
        if path in ('/hello', '/hook', '/watch-state'):
            device = authorized_device(data.get('dev'), self.bearer())
            if not device:
                return self.send_json(403, {'ok': False, 'error': 'device or website binding unavailable'})
            if path == '/hello':
                return self.send_json(200, {'ok': True, 'bound': True, 'need_bind': False})
            if path == '/watch-state':
                return self.send_json(200, watch_state(device))
            return self.send_json(*ingest(data, device))
        if path in ('/ack', '/claim-check'):
            if not self.auth_ok():
                return self.send_json(401, {'ok': False, 'error': 'auth'})
            if path == '/claim-check':
                item = claim_check(data.get('id'), data.get('claim_token'))
                return self.send_json(200, {'ok': True, 'valid': item is not None, 'item': item})
            items = data.get('items')
            if not isinstance(items, list) or data.get('state') not in ('sent', 'retry'):
                return self.send_json(400, {'ok': False, 'error': 'ack'})
            return self.send_json(200, {'ok': True, 'updated': acknowledge(items, data['state'])})
        return self.send_json(404, {'ok': False, 'error': 'not found'})


def main():
    db()
    cleanup_downloads()
    server = ServiceServer((HOST, PORT), Handler)
    sys.stderr.write('lejian service v2 on %s:%s\n' % (HOST, PORT))
    server.serve_forever()


if __name__ == '__main__':
    main()
