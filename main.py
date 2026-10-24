"""Deliver website-bound Lejian reminders, rechecking cancellation before sending."""
import asyncio
import time
import os
from urllib.parse import urlsplit

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register('astrbot_plugin_lejian', 'Sonnety14', 'neuro乐健（预约确认、活动及报名开放提醒）', '0.9.0')
class Lejian(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.task = None
        self.client = None
        self.sent = {}

    async def initialize(self):
        if not self.config.get('enabled', False):
            return
        url = str(self.config.get('service_url', '')).strip()
        token = os.environ.get('LEJIAN_BOT_TOKEN', '').strip() or str(self.config.get('token', '')).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Configure service_url as an HTTP(S) origin')
        if parsed.path not in ('', '/') or not token or not self.config.get('platform_id'):
            raise ValueError('Configure service origin, bot token and platform_id')
        self.client = httpx.AsyncClient(
            base_url=url.rstrip('/'),
            headers={'Authorization': 'Bearer ' + token},
            timeout=35, trust_env=False)
        self.task = asyncio.create_task(self.poll())

    def platform(self):
        platform = self.context.get_platform_inst(self.config.get('platform_id', ''))
        if not platform or not getattr(platform, 'bot', None):
            raise RuntimeError('QQ unavailable')
        return platform.bot

    async def poll(self):
        interval = max(5, int(self.config.get('interval', 20)))
        while True:
            await asyncio.sleep(interval)
            try:
                resp = await self.client.get('/pending', params={'limit': 5})
                resp.raise_for_status()
                items = (resp.json() or {}).get('items') or []
                if items:
                    await self.deliver(items)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('[lejian] poll failed: %s' % type(exc).__name__)

    async def ack(self, item, state):
        response = await self.client.post('/ack', json={
            'items': [{'id': item['id'], 'claim_token': item['claim_token']}], 'state': state})
        response.raise_for_status()

    async def deliver(self, items):
        try:
            bot = self.platform()
        except Exception as exc:
            logger.warning('[lejian] platform unavailable: %s' % type(exc).__name__)
            return
        now = time.time()
        self.sent = {key: timestamp for key, timestamp in self.sent.items() if now - timestamp < 86400}
        for claimed in items:
            try:
                response = await self.client.post('/claim-check', json={
                    'id': claimed.get('id'), 'claim_token': claimed.get('claim_token')})
                response.raise_for_status()
                payload = response.json()
                if payload.get('valid') is not True:
                    continue
                item = payload['item']
                qq, text = str(item.get('qq') or ''), item.get('text') or ''
                if not qq.isdigit() or not text:
                    await self.ack(item, 'retry')
                    continue
                # A successful send whose acknowledgement failed must not be sent again
                # during this plugin lifetime. Cross-restart delivery is at least once.
                if item['id'] not in self.sent:
                    result = await asyncio.wait_for(bot.call_action(
                        'send_private_msg', user_id=int(qq),
                        message=[{'type': 'text', 'data': {'text': text}}]), 25)
                    if isinstance(result, dict) and (result.get('status') == 'failed' or result.get('retcode', 0) != 0):
                        raise RuntimeError('QQ rejected message')
                    self.sent[item['id']] = time.time()
                await self.ack(item, 'sent')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Uncertain sends are not retried immediately. The server lease expires
                # after ten minutes; a cancellation meanwhile prevents another attempt.
                logger.warning('[lejian] delivery failed: %s' % type(exc).__name__)

    @filter.command('乐健绑定')
    async def bind(self, event: AstrMessageEvent):
        yield event.plain_result(self.download_help())

    @filter.command('乐健下载')
    async def download(self, event: AstrMessageEvent):
        yield event.plain_result(self.download_help())

    def download_help(self):
        url = str(self.config.get('download_url', '')).strip()
        if not url:
            return '管理员尚未配置乐健下载页。'
        return '乐健报名提醒：' + url + '\n登录并验证绑定 QQ 后下载专属安装包，安装后自动关联，无需绑定码。'

    async def terminate(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.client:
            await self.client.aclose()
