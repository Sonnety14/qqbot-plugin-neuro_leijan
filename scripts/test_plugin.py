"""Offline mocked QQ delivery checks; does not send a real message."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
import os
import httpx

SPEC = importlib.util.spec_from_file_location('lejian_plugin', Path(__file__).parents[1] / 'main.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def response(payload, code=200):
    return httpx.Response(code, json=payload, request=httpx.Request('POST', 'http://test/'))


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = module.Lejian.__new__(module.Lejian)
        self.plugin.sent = {}
        self.bot = AsyncMock()
        self.bot.call_action.return_value = {'message_id': 123}
        self.plugin.platform = lambda: self.bot
        self.plugin.client = AsyncMock()
        self.item = dict(id=1, qq='1234567', text='健身跑提醒', claim_token='lease-one')
        self.check = response(dict(ok=True, valid=True, item=self.item))
        self.ack = response(dict(ok=True, updated=1))

    async def test_cancelled_or_revoked_skips_send(self):
        self.plugin.client.post.return_value = response(dict(ok=True, valid=False, item=None))
        await self.plugin.deliver([self.item])
        self.bot.call_action.assert_not_awaited()
        self.assertEqual(self.plugin.client.post.await_count, 1)

    async def test_recheck_outage_never_sends(self):
        self.plugin.client.post.side_effect = httpx.ConnectError('offline')
        await self.plugin.deliver([self.item])
        self.bot.call_action.assert_not_awaited()

    async def test_uses_current_recheck_data_and_claim_receipt(self):
        old = dict(self.item, qq='7654321', text='stale')
        self.plugin.client.post.side_effect = [self.check, self.ack]
        await self.plugin.deliver([old])
        self.assertEqual(self.bot.call_action.await_args.kwargs['user_id'], 1234567)
        self.assertEqual(self.bot.call_action.await_args.kwargs['message'][0]['data']['text'], '健身跑提醒')
        self.assertEqual(self.plugin.client.post.await_args.kwargs['json'],
                         {'items': [{'id': 1, 'claim_token': 'lease-one'}], 'state': 'sent'})

    async def test_ack_failure_does_not_duplicate_same_process_send(self):
        self.plugin.client.post.side_effect = [self.check, httpx.ConnectError('ack failed')]
        await self.plugin.deliver([self.item])
        self.plugin.client.post.side_effect = [self.check, self.ack]
        await self.plugin.deliver([self.item])
        self.assertEqual(self.bot.call_action.await_count, 1)

    async def test_qq_failure_not_acknowledged_as_sent(self):
        self.plugin.client.post.return_value = self.check
        self.bot.call_action.return_value = {'status': 'failed', 'retcode': 100}
        await self.plugin.deliver([self.item])
        self.assertEqual(self.plugin.client.post.await_count, 1)
        self.assertEqual(self.plugin.sent, {})

    async def test_uncertain_send_does_not_immediately_retry(self):
        self.plugin.client.post.return_value = self.check
        self.bot.call_action.side_effect = TimeoutError()
        await self.plugin.deliver([self.item])
        self.assertEqual(self.bot.call_action.await_count, 1)
        self.assertEqual(self.plugin.client.post.await_count, 1)

    async def test_unconfigured_template_stays_disabled(self):
        self.plugin.config = {}
        with patch.object(module.httpx, 'AsyncClient') as factory:
            await self.plugin.initialize()
            factory.assert_not_called()

    async def test_enabled_missing_connection_rejected(self):
        self.plugin.config = {'enabled': True}
        with self.assertRaises(ValueError):
            await self.plugin.initialize()

    async def test_environment_token_and_configured_origin(self):
        self.plugin.config = {'enabled': True, 'service_url': 'http://127.0.0.1:18090',
                              'token': 'fixture-fallback', 'platform_id': 'fixture-platform'}
        client = AsyncMock()
        self.plugin.poll = AsyncMock()
        with patch.dict(os.environ, {'LEJIAN_BOT_TOKEN': 'fixture-env'}), patch.object(module.httpx, 'AsyncClient', return_value=client) as factory:
            await self.plugin.initialize()
            self.assertEqual(factory.call_args.kwargs['headers']['Authorization'], 'Bearer fixture-env')
            await self.plugin.terminate()
        client.aclose.assert_awaited_once()

    def test_download_command_uses_configured_site(self):
        self.plugin.config = {'download_url': 'https://example.invalid/tools/lejian/'}
        self.assertIn('https://example.invalid/tools/lejian/', self.plugin.download_help())


if __name__ == '__main__':
    unittest.main()
