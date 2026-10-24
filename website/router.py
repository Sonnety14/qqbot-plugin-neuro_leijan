"""QQ-bound Lejian downloads. Account identity never comes from the browser."""
import html
import os
import re
import secrets
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


P = '/tools/lejian'
ASSETS = Path(__file__).parent / 'static' / 'lejian'
PRIVATE = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
           'Referrer-Policy': 'no-referrer', 'X-Robots-Tag': 'noindex, nofollow, noarchive'}
HANDLE = re.compile(r'^[A-Za-z0-9_-]{16,128}$')


class Resolve(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_id: str = Field(pattern=r'^[1-9][0-9]{0,18}$')
    qq: str = Field(pattern=r'^[1-9][0-9]{4,11}$')
    auth_version: int = Field(ge=0)


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid')


def service_token():
    """The dedicated secret is mounted on the website host, never in the APK."""
    value = os.getenv('LEJIAN_SERVICE_TOKEN', '').strip()
    if not value and os.getenv('LEJIAN_SERVICE_TOKEN_FILE'):
        try:
            value = Path(os.environ['LEJIAN_SERVICE_TOKEN_FILE']).read_text(encoding='utf8').strip()
        except OSError:
            pass
    if len(value) < 32:
        raise HTTPException(503, '乐健下载暂未开放，请稍后重试')
    return value


def build_router(identity, origin, store, *, connect, render, session_cookie, transport=None):
    """Host supplies authenticated identity, DB context manager, renderer and limiter."""
    router = APIRouter()
    building = set()
    with connect(identity.db) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS lejian_downloads (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            qq TEXT NOT NULL, auth_version INTEGER NOT NULL,
            service_id TEXT NOT NULL, expires REAL NOT NULL)''')

    def session(request, write=False):
        s = identity.load_session(request.cookies.get(session_cookie, ''))
        if not s:
            raise HTTPException(401, '请先登录')
        if not s.get('verified_at'):
            raise HTTPException(403, '请先验证邮箱')
        if write and (request.headers.get('origin') != origin or not secrets.compare_digest(
                request.headers.get('x-csrf-token', ''), s['csrf'])):
            raise HTTPException(403, '请刷新页面重试')
        request.state.user_id = s['id']
        return s

    def bound(db, uid):
        row = db.execute('SELECT qq FROM campus_qq_bindings WHERE user_id=?', (uid,)).fetchone()
        if not row:
            raise HTTPException(403, '请先在账号设置中绑定 QQ')
        return row['qq']

    def valid(db, uid, qq, version):
        return bool(db.execute('''SELECT 1 FROM users u
            JOIN email_credentials e ON e.user_id=u.id
            JOIN campus_qq_bindings b ON b.user_id=u.id
            WHERE u.id=? AND u.status='active' AND e.verified_at IS NOT NULL
            AND b.qq=? AND u.auth_version=?''', (uid, qq, version)).fetchone())

    def client():
        return httpx.AsyncClient(
            base_url=os.getenv('LEJIAN_SERVICE_URL', 'http://127.0.0.1:18090').rstrip('/') + '/',
            headers={'Authorization': 'Bearer ' + service_token()}, trust_env=False,
            follow_redirects=False, timeout=httpx.Timeout(150, connect=5), transport=transport)

    @router.get(P + '/')
    def page(request: Request):
        if not identity.load_session(request.cookies.get(session_cookie, '')):
            return RedirectResponse('/account/login/?return_to=%2Ftools%2Flejian%2F', 303)
        s = session(request)
        body = (ASSETS / 'index.html').read_text(encoding='utf8').replace(
            '__CSRF__', html.escape(s['csrf'], quote=True))
        return render('乐健报名提醒', '报名成功后，在活动开始前通过 QQ 提醒你。', body,
                       is_admin=False, extra_style=(ASSETS / 'style.css').read_text(encoding='utf8'),
                       extra_script='<script src="/tools/lejian/app.js" defer></script>')

    @router.get(P + '/app.js')
    def script(request: Request):
        session(request)
        return FileResponse(ASSETS / 'app.js', media_type='application/javascript', headers=PRIVATE)

    @router.get(P + '/status')
    def status(request: Request):
        s = session(request)
        with connect(identity.db) as db:
            qq = db.execute('SELECT qq FROM campus_qq_bindings WHERE user_id=?', (s['id'],)).fetchone()
        return {'bound': bool(qq), 'qq': qq['qq'] if qq else None}

    @router.post(P + '/downloads')
    async def prepare(payload: Empty, request: Request):
        s = session(request, True)
        with connect(identity.db) as db:
            qq = bound(db, s['id'])
        if s['id'] in building:
            raise HTTPException(409, '安装包正在生成，请稍候')
        if not store.limit('lejian-package:' + str(s['id']), 3):
            raise HTTPException(429, '生成过于频繁，请稍后重试')
        building.add(s['id'])
        try:
            async with client() as service:
                result = await service.post('issue', json={'account_id': str(s['id']), 'qq': qq,
                    'auth_version': s['user_auth_version']})
                if result.status_code == 429:
                    raise HTTPException(429, '安装包生成繁忙，请稍后重试')
                result.raise_for_status()
                data = result.json()
            service_id, lifetime = data['download_id'], data['expires_in']
            if not isinstance(service_id, str) or not HANDLE.fullmatch(service_id):
                raise ValueError('Invalid package handle')
            if type(lifetime) is not int or not 0 < lifetime <= 86400:
                raise ValueError('Invalid package lifetime')
            handle = secrets.token_urlsafe(24)
            expires = time.time() + min(3600, lifetime)
            with connect(identity.db) as db:
                if not valid(db, s['id'], qq, s['user_auth_version']):
                    raise HTTPException(409, '账号绑定已更新，请刷新后重试')
                db.execute('DELETE FROM lejian_downloads WHERE expires<=?', (time.time(),))
                db.execute('INSERT INTO lejian_downloads VALUES (?,?,?,?,?,?)',
                           (handle, s['id'], qq, s['user_auth_version'], service_id, expires))
            return {'url': P + '/downloads/' + handle + '/app.apk', 'expires_in': min(3600, lifetime)}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(503, '安装包暂时无法生成，请稍后重试') from None
        finally:
            building.discard(s['id'])

    @router.get(P + '/downloads/{handle}/app.apk')
    async def download(handle: str, request: Request):
        s = session(request)
        if not HANDLE.fullmatch(handle):
            raise HTTPException(404)
        with connect(identity.db) as db:
            row = db.execute('SELECT * FROM lejian_downloads WHERE id=? AND user_id=?',
                             (handle, s['id'])).fetchone()
            if not row or row['expires'] <= time.time():
                raise HTTPException(404, '下载已过期，请重新生成')
            if not valid(db, s['id'], row['qq'], row['auth_version']):
                raise HTTPException(403, '账号绑定已更新，请重新生成安装包')
        service = client()
        try:
            upstream = await service.send(service.build_request('GET',
                'download/' + row['service_id'] + '/app.apk'), stream=True)
            if upstream.status_code != 200:
                raise ValueError('Package unavailable')
            size = int(upstream.headers['content-length'])
            if not 0 < size <= 256 * 1024 * 1024:
                raise ValueError('Invalid package size')
        except (httpx.HTTPError, KeyError, ValueError):
            if 'upstream' in locals():
                await upstream.aclose()
            await service.aclose()
            raise HTTPException(503, '安装包已失效，请重新生成') from None

        async def chunks():
            try:
                async for part in upstream.aiter_raw():
                    yield part
            finally:
                await upstream.aclose()
                await service.aclose()

        return StreamingResponse(chunks(), media_type='application/vnd.android.package-archive',
            headers={**PRIVATE, 'Content-Length': str(size),
                     'Content-Disposition': 'attachment; filename="neuro-lejian-3.10.3.apk"'})

    @router.post('/comments/bridge/lejian/resolve')
    def resolve(payload: Resolve, request: Request):
        if not secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + service_token()):
            raise HTTPException(401)
        with connect(identity.db) as db:
            return {'valid': valid(db, int(payload.account_id), payload.qq, payload.auth_version)}

    return router
