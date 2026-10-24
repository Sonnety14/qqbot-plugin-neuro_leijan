"""Contract tests with fake sessions/bindings; no public service or real QQ."""
from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import httpx
import pytest

from website.router import build_router

TOKEN = 'fixture-website-token-' + 'x' * 40
ORIGIN = 'https://example.invalid'
HEADERS = {'origin': ORIGIN, 'x-csrf-token': 'fixture-csrf'}
P = '/tools/lejian'


@contextmanager
def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'fake-apk'


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('LEJIAN_SERVICE_TOKEN', TOKEN)
    database = str(tmp_path / 'host.db')
    with connect(database) as db:
        db.executescript('''CREATE TABLE users(id INTEGER PRIMARY KEY,status TEXT,auth_version INTEGER);
            CREATE TABLE email_credentials(user_id INTEGER, verified_at INTEGER);
            CREATE TABLE campus_qq_bindings(user_id INTEGER PRIMARY KEY,qq TEXT);
            INSERT INTO users VALUES(1,'active',0),(2,'active',0);
            INSERT INTO email_credentials VALUES(1,1),(2,1);
            INSERT INTO campus_qq_bindings VALUES(1,'10001'),(2,'10002');''')
    def session(cookie):
        if cookie not in ('user-one', 'user-two'):
            return None
        return dict(id=1 if cookie == 'user-one' else 2, verified_at=1, csrf='fixture-csrf', user_auth_version=0)
    calls = []
    def dispatch(request):
        calls.append(request)
        assert request.headers['authorization'] == 'Bearer ' + TOKEN
        if request.url.path == '/issue':
            return httpx.Response(200, json={'download_id': 'fixture-service-package-1234', 'expires_in': 3600})
        return httpx.Response(200, headers={'content-length': '8'}, stream=Stream())
    app = FastAPI()
    store = SimpleNamespace(limit=lambda key, n: True)
    app.include_router(build_router(SimpleNamespace(db=database, load_session=session), ORIGIN, store,
        connect=connect, render=lambda title, desc, body, **kw: HTMLResponse(body), session_cookie='session',
        transport=httpx.MockTransport(dispatch)))
    with TestClient(app, base_url=ORIGIN) as client:
        yield SimpleNamespace(client=client, db=database, calls=calls, store=store)


def login(host, user='user-one'):
    host.client.cookies.set('session', user)


def prepare(host):
    login(host)
    response = host.client.post(P + '/downloads', json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()['url']


def test_anonymous_and_csrf(host):
    assert host.client.get(P + '/', follow_redirects=False).status_code == 303
    assert host.client.post(P + '/downloads', json={}).status_code == 401
    login(host)
    assert host.client.post(P + '/downloads', json={}).status_code == 403
    assert host.client.post(P + '/downloads', json={'qq': '10002'}, headers=HEADERS).status_code == 422
    assert not host.calls


def test_identity_download_and_owner(host):
    path = prepare(host)
    assert json.loads(host.calls[0].content) == {'account_id': '1', 'qq': '10001', 'auth_version': 0}
    assert TOKEN not in path and '10001' not in path and 'fixture-service' not in path
    response = host.client.get(path)
    assert response.content == b'fake-apk'
    assert 'no-store' in response.headers['cache-control']
    login(host, 'user-two')
    assert host.client.get(path).status_code == 404


@pytest.mark.parametrize('change', ["UPDATE users SET status='disabled' WHERE id=1",
    'UPDATE users SET auth_version=1 WHERE id=1', "UPDATE campus_qq_bindings SET qq='10003' WHERE user_id=1",
    'DELETE FROM campus_qq_bindings WHERE user_id=1', 'UPDATE email_credentials SET verified_at=NULL WHERE user_id=1'])
def test_binding_changes_block_existing_download(host, change):
    path = prepare(host)
    with connect(host.db) as db:
        db.execute(change)
    assert host.client.get(path).status_code == 403


def test_unbound_and_rate_limit(host):
    login(host)
    host.store.limit = lambda key, n: False
    assert host.client.post(P + '/downloads', json={}, headers=HEADERS).status_code == 429
    with connect(host.db) as db:
        db.execute('DELETE FROM campus_qq_bindings WHERE user_id=1')
    assert host.client.post(P + '/downloads', json={}, headers=HEADERS).status_code == 403


def test_resolve_requires_secret_and_current_identity(host):
    data = {'account_id': '1', 'qq': '10001', 'auth_version': 0}
    path = '/comments/bridge/lejian/resolve'
    assert host.client.post(path, json=data).status_code == 401
    headers = {'authorization': 'Bearer ' + TOKEN}
    assert host.client.post(path, json=data, headers=headers).json() == {'valid': True}
    data['qq'] = '10002'
    assert host.client.post(path, json=data, headers=headers).json() == {'valid': False}


def test_expired_download(host):
    path = prepare(host)
    with connect(host.db) as db:
        db.execute('UPDATE lejian_downloads SET expires=0')
    assert host.client.get(path).status_code == 404


def test_missing_secret(host, monkeypatch):
    monkeypatch.delenv('LEJIAN_SERVICE_TOKEN')
    monkeypatch.delenv('LEJIAN_SERVICE_TOKEN_FILE', raising=False)
    login(host)
    assert host.client.post(P + '/downloads', json={}, headers=HEADERS).status_code == 503
    assert not host.calls
