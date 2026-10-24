"""Offline service regression tests; never contact QQ, school or production."""
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SPEC = importlib.util.spec_from_file_location('lejian_service', Path(__file__).parents[1] / 'server/service.py')
svc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(svc)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        svc.DB_PATH = str(root / 'db.sqlite')
        svc.DOWNLOAD_DIR = root / 'downloads'
        svc.TOKEN_FILE = str(root / 'bot-token')
        svc.WEBSITE_TOKEN_FILE = str(root / 'website-token')
        Path(svc.TOKEN_FILE).write_text('bot-secret')
        Path(svc.WEBSITE_TOKEN_FILE).write_text('website-secret')
        self.binding = patch.object(svc, 'current_binding', return_value=True)
        self.binding_check = self.binding.start()
        self.grant, self.bootstrap, self.secret, self.dev = self.make_grant()
        self.assertEqual(self.activate()[0], 200)
        self.device = svc.authorized_device(self.dev, self.secret)
        self.clock = int(time.time() * 1000)
        self.counter = 0

    def tearDown(self):
        if svc._conn:
            svc._conn.close()
            svc._conn = None
        self.binding.stop()
        self.temp.cleanup()

    def make_grant(self, account='12', qq='1234567'):
        gid, bootstrap, secret, dev = (secrets.token_hex(16) for _ in range(4))
        now = time.time()
        svc.db().execute('INSERT INTO grants(id,bootstrap_hash,account_id,qq,auth_version,created,expires,download_expires) VALUES(?,?,?,?,?,?,?,?)',
                         (gid, svc.digest(bootstrap), account, qq, 3, now - 100, now + 86400, now + 3600))
        svc.db().commit()
        return gid, bootstrap, secret, dev

    def activate(self, **changes):
        data = dict(bootstrap=self.bootstrap, dev=self.dev, device_secret=self.secret)
        data.update(changes)
        return svc.activate(data)

    def event(self, kind='signup', **changes):
        self.counter += 1
        payload = dict(dev=self.dev, event=kind, ok=True, activityId='activity-1',
                       activity={'title': '健身跑', 'startTime': self.clock + 600000, 'address': '操场'},
                       at=self.clock + self.counter, event_id=secrets.token_hex(16))
        payload.update(changes)
        return payload

    def ingest(self, kind='signup', **changes):
        return svc.ingest(self.event(kind, **changes), self.device)

    def states(self):
        # Existing assertions cover the scheduled reminder. Confirmation
        # delivery is exercised independently below.
        return [r['state'] for r in svc.db().execute("SELECT * FROM events WHERE kind='signup' ORDER BY id")]

    def test_activation_idempotent_same_device_only(self):
        self.assertEqual(self.activate()[0], 200)
        self.assertEqual(self.activate(dev='other-device-12345')[0], 409)
        self.assertEqual(self.activate(device_secret='x' * 32)[0], 409)

    def test_activation_refuses_missing_or_expired_bootstrap(self):
        self.assertEqual(self.activate(bootstrap='unknown_bootstrap_token')[0], 403)
        svc.db().execute('UPDATE grants SET expires=0')
        svc.db().commit()
        self.assertEqual(self.activate()[0], 403)

    def test_secrets_stored_as_hash_only(self):
        rows = str([tuple(r) for r in svc.db().execute('SELECT * FROM grants')])
        rows += str([tuple(r) for r in svc.db().execute('SELECT * FROM devices')])
        self.assertNotIn(self.bootstrap, rows)
        self.assertNotIn(self.secret, rows)

    def test_missing_wrong_or_revoked_device_secret(self):
        self.assertIsNone(svc.authorized_device(self.dev, 'bad' * 20))
        self.assertIsNone(svc.authorized_device(self.dev, None))
        self.binding_check.return_value = False
        self.assertIsNone(svc.authorized_device(self.dev, self.secret))
        self.assertEqual(self.activate()[0], 403)

    def test_failure_signup_and_failure_cancel_leave_state(self):
        self.ingest(ok=False)
        self.assertEqual(self.states(), [])
        self.ingest()
        self.ingest('cancel', ok=False)
        self.assertEqual(self.states(), ['pending'])

    def test_successful_cancel_removes_pending(self):
        self.ingest()
        self.ingest('cancel', activity={})
        self.assertEqual(self.states(), ['cancelled'])
        self.assertEqual(svc.claim_due(5), [])

    def test_cancelled_claim_cannot_ack_or_retry_resurrect(self):
        self.ingest()
        claim = svc.claim_due(5)[0]
        self.ingest('cancel')
        self.assertIsNone(svc.claim_check(claim['id'], claim['claim_token']))
        self.assertEqual(svc.acknowledge([claim], 'sent'), 0)
        self.assertEqual(svc.acknowledge([claim], 'retry'), 0)
        self.assertEqual(self.states(), ['cancelled'])

    def test_cancel_tombstone_blocks_delayed_signup(self):
        self.ingest('cancel', at=self.clock + 100, activity={})
        code, result = self.ingest(at=self.clock)
        self.assertEqual(code, 200)
        self.assertEqual(result['ignored'], 'stale event')
        self.assertEqual(self.states(), [])

    def test_same_timestamp_cancel_wins(self):
        self.ingest('cancel', at=self.clock)
        self.ingest(at=self.clock)
        self.assertEqual(self.states(), [])

    def test_new_signup_after_cancel_is_new_reminder(self):
        self.ingest()
        self.ingest('cancel')
        self.ingest()
        self.assertEqual(self.states(), ['cancelled', 'pending'])

    def test_duplicate_event_and_repeat_signup_schedule_once(self):
        event = self.event()
        svc.ingest(event, self.device)
        self.assertTrue(svc.ingest(event, self.device)[1]['duplicate'])
        self.ingest()
        self.assertEqual(self.states(), ['pending'])

    def test_older_cancel_cannot_remove_later_signup(self):
        self.ingest(at=self.clock + 100)
        self.ingest('cancel', at=self.clock)
        self.assertEqual(self.states(), ['pending'])

    def test_no_school_credentials_or_identity_stored(self):
        self.ingest(token='school-secret', user={'studentNumber': 'sensitive-student'})
        self.assertIsNone(svc.db().execute('SELECT raw FROM events').fetchone()['raw'])
        self.assertIsNone(svc.db().execute('SELECT student_no FROM devices').fetchone()['student_no'])

    def test_missing_start_or_unknown_event_rejected(self):
        self.assertEqual(self.ingest(activity={})[0], 400)
        self.assertEqual(self.ingest('arbitrary')[0], 400)
        self.assertEqual(self.ingest(event_id='')[0], 400)
        self.assertEqual(self.states(), [])

    def test_past_event_expired_not_sent(self):
        self.ingest(activity={'startTime': self.clock - 1})
        self.assertEqual(svc.claim_due(5), [])
        self.assertEqual(self.states(), ['expired'])

    def test_distant_future_not_claimed(self):
        self.ingest(activity={'startTime': self.clock + 3600000})
        items = svc.claim_due(5)
        self.assertEqual(len(items), 1)
        self.assertIn('预约成功', items[0]['text'])
        self.assertEqual(svc.acknowledge(items, 'sent'), 1)
        self.assertEqual(self.states(), ['pending'])

    def test_registration_watch_confirm_then_two_minutes_before_opening(self):
        activity = {'title':'健身跑', 'registerStartTime':self.clock+3600000,
                    'startTime':self.clock+86400000, 'address':'操场'}
        self.assertEqual(self.ingest('watch_registration',activity=activity)[0],200)
        self.assertTrue(self.ingest('watch_registration',activity=activity)[1]['duplicate'])
        first = svc.claim_due(5)
        self.assertEqual(len(first),1)
        self.assertIn('报名提醒已设置',first[0]['text'])
        self.assertIn('不代表已报名',first[0]['text'])
        self.assertIn('活动开始',first[0]['text'])
        svc.acknowledge(first,'sent')
        with patch.object(svc.time,'time',return_value=self.clock/1000+3479):
            self.assertEqual(svc.claim_due(5),[])
        with patch.object(svc.time,'time',return_value=self.clock/1000+3480):
            due=svc.claim_due(5)
            self.assertEqual(len(due),1)
            self.assertIn('2 分钟后开放报名',due[0]['text'])
        self.assertEqual(svc.db().execute('SELECT count(*) FROM activity_state').fetchone()[0],0)

    def test_registration_watch_late_expired_and_binding_revoked(self):
        self.assertEqual(self.ingest('watch_registration',activity={'startTime':self.clock+86400000})[0],400)
        self.assertEqual(self.ingest('watch_registration',activity={'registerStartTime':self.clock-1,'startTime':self.clock+86400000})[0],400)
        self.ingest('watch_registration',activity={'registerStartTime':self.clock+60000,'startTime':self.clock+86400000})
        due=svc.claim_due(5)
        self.assertEqual(len(due),2)
        self.assertIn('已设置',due[0]['text'])
        self.binding_check.return_value=False
        self.assertIsNone(svc.claim_check(due[0]['id'],due[0]['claim_token']))
        self.binding_check.return_value=True
        with patch.object(svc.time,'time',return_value=self.clock/1000+61):
            self.assertEqual(svc.claim_due(5),[])
            self.assertIsNone(svc.claim_check(due[1]['id'],due[1]['claim_token']))

    def test_watch_limit_is_account_wide_cancel_releases_slot(self):
        a={'startTime':self.clock+86400000,'registerStartTime':self.clock+3600000}
        for i in range(4):
            self.assertEqual(self.ingest('watch_registration',activityId=str(i),activity=a)[0],200)
        _,bootstrap,secret,dev=self.make_grant()
        svc.activate(dict(bootstrap=bootstrap,dev=dev,device_secret=secret))
        other=svc.authorized_device(dev,secret)
        self.assertEqual(svc.ingest(self.event('watch_registration',activityId='5',activity=a),other)[0],409)
        self.assertEqual(len(svc.watch_state(other)['watches']),4)
        claims=svc.claim_due(20)
        self.assertEqual(self.ingest('cancel_registration_watch',activityId='0',activity=a)[0],200)
        cancelled=next(c for c in claims if c['activity_id']=='0')
        self.assertIsNone(svc.claim_check(cancelled['id'],cancelled['claim_token']))
        self.assertEqual(svc.acknowledge([cancelled],'sent'),0)
        self.assertEqual(svc.ingest(self.event('watch_registration',activityId='5',activity=a),other)[0],200)
        self.assertEqual(len(svc.watch_state(self.device)['watches']),4)

    def test_watch_cancellation_blocks_old_callbacks_and_can_be_reenabled(self):
        a={'startTime':self.clock+86400000,'registerStartTime':self.clock+3600000}
        self.ingest('watch_registration',activity=a,at=self.clock+1)
        self.ingest('cancel_registration_watch',activity=a,at=self.clock+3)
        self.ingest('watch_registration',activity=a,at=self.clock+2)
        self.assertEqual(svc.watch_state(self.device)['watches'],[])
        self.assertEqual(svc.claim_due(20),[])
        self.ingest('watch_registration',activity=a,at=self.clock+4)
        self.assertEqual(len(svc.watch_state(self.device)['watches']),1)
        self.assertEqual(len(svc.claim_due(20)),1)
        with patch.object(svc.time,'time',return_value=self.clock/1000+3601):
            self.assertEqual(svc.watch_state(self.device)['watches'],[])

    def test_watch_seed_preserves_old_apk_watches(self):
        a={'startTime':self.clock+86400000,'registerStartTime':self.clock+3600000}
        self.ingest('watch_registration',activity=a)
        svc.db().execute('DELETE FROM watch_subscriptions');svc.db().commit()
        self.assertEqual(len(svc.watch_state(self.device)['watches']),1)

    def test_confirmation_deduplicated_and_later_reminder_preserved(self):
        event = self.event(activity={'title': '健身跑', 'startTime': self.clock + 86400000, 'address': '操场'})
        svc.ingest(event, self.device)
        svc.ingest(event, self.device)
        self.ingest(activity=event['activity'])
        items = svc.claim_due(5)
        self.assertEqual(len(items), 1)
        self.assertIn('操场', items[0]['text'])
        self.assertIn('预约成功', items[0]['text'])
        svc.acknowledge(items, 'sent')
        self.assertEqual(svc.claim_due(5), [])
        with patch.object(svc.time, 'time', return_value=self.clock/1000 + 86400 - 1800):
            later = svc.claim_due(5)
            self.assertEqual(len(later), 1)
            self.assertIn('活动提醒', later[0]['text'])

    def test_cancel_invalidates_confirmation_claim_and_resign_confirms_again(self):
        activity = {'startTime': self.clock + 86400000}
        self.ingest(activity=activity)
        claim = svc.claim_due(5)[0]
        self.ingest('cancel')
        self.assertIsNone(svc.claim_check(claim['id'], claim['claim_token']))
        self.assertEqual(svc.acknowledge([claim], 'sent'), 0)
        self.assertEqual(svc.claim_due(5), [])
        self.assertEqual([r[0] for r in svc.db().execute('SELECT state FROM events')], ['cancelled', 'cancelled'])
        self.ingest(activity=activity)
        fresh = svc.claim_due(5)
        self.assertEqual(len(fresh), 1)
        self.assertIn('预约成功', fresh[0]['text'])
        self.assertNotEqual(fresh[0]['id'], claim['id'])

    def test_website_revoke_blocks_claim_and_recheck(self):
        self.ingest()
        self.binding_check.return_value = None  # temporary website outage
        self.assertEqual(svc.claim_due(5), [])
        self.assertEqual(self.states(), ['pending'])
        self.binding_check.return_value = True
        item = svc.claim_due(5)[0]
        self.binding_check.return_value = False
        self.assertIsNone(svc.claim_check(item['id'], item['claim_token']))

    def test_confirmed_revocation_does_not_starve_next_account(self):
        self.ingest()
        _, bootstrap, secret, dev = self.make_grant(account='24', qq='7654321')
        svc.activate(dict(bootstrap=bootstrap, device_secret=secret, dev=dev))
        other = svc.authorized_device(dev, secret)
        svc.ingest(self.event(activityId='activity-2'), other)
        self.binding_check.side_effect = lambda account, qq, version: account != '12'
        self.assertEqual(svc.claim_due(1), [])
        self.assertEqual(self.states()[0], 'revoked')
        self.assertEqual(svc.claim_due(1)[0]['qq'], '7654321')

    def test_retry_bounded_four_attempts(self):
        self.ingest()
        for _ in range(4):
            item = svc.claim_due(5)[0]
            svc.acknowledge([item], 'retry')
        self.assertEqual(self.states(), ['failed'])
        self.assertEqual(svc.claim_due(5), [])

    def test_old_ack_cannot_ack_new_claim(self):
        self.ingest()
        first = svc.claim_due(5)[0]
        svc.acknowledge([first], 'retry')
        second = svc.claim_due(5)[0]
        self.assertEqual(svc.acknowledge([first], 'sent'), 0)
        self.assertEqual(svc.acknowledge([second], 'sent'), 1)

    def test_timeout_claims_eventually_failed(self):
        self.ingest()
        for _ in range(4):
            svc.claim_due(5)
            svc.db().execute('UPDATE events SET claimed_at=0')
            svc.db().commit()
        self.assertEqual(svc.claim_due(5), [])
        self.assertEqual(self.states(), ['failed'])

    def test_rebinding_same_device_cancels_previous_account(self):
        self.ingest()
        _, bootstrap, _, _ = self.make_grant(account='24', qq='7654321')
        self.assertEqual(self.activate(bootstrap=bootstrap)[0], 200)
        self.assertEqual(self.states(), ['cancelled'])
        self.assertEqual(svc.authorized_device(self.dev, self.secret)['qq'], '7654321')

    def test_issue_builds_without_returning_bootstrap_and_throttles(self):
        captured = []
        def build(bootstrap, output):
            captured.append(bootstrap)
            Path(output).write_bytes(b'fake-signed-apk')
        with patch.dict(sys.modules, {'apk_builder': types.SimpleNamespace(build_apk=build)}):
            code, result = svc.issue({'account_id': '12', 'qq': '1234567', 'auth_version': 3})
            self.assertEqual(code, 200)
            self.assertNotIn(captured[0], str(result))
            self.assertTrue((svc.DOWNLOAD_DIR / (result['download_id'] + '.apk')).is_file())
            self.assertEqual(svc.issue({'account_id': '12', 'qq': '1234567', 'auth_version': 3})[0], 429)

    def test_issue_invalid_binding_or_busy_fails(self):
        self.binding_check.return_value = False
        self.assertEqual(svc.issue({'account_id': '12', 'qq': '1234567'})[0], 403)
        self.binding_check.return_value = True
        with svc._build_lock:
            self.assertEqual(svc.issue({'account_id': '12', 'qq': '1234567'})[0], 429)

    def test_download_cleanup_removes_expired_artifacts(self):
        svc.DOWNLOAD_DIR.mkdir()
        file = svc.DOWNLOAD_DIR / (self.grant + '.apk')
        file.write_bytes(b'fake')
        svc.db().execute('UPDATE grants SET download_expires=0')
        svc.db().commit()
        svc.cleanup_downloads()
        self.assertFalse(file.exists())

    def test_http_auth_boundaries_and_legacy_routes(self):
        server = svc.ThreadingHTTPServer(('127.0.0.1', 0), svc.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(path, data=None, credential=None):
            headers = {'Content-Type': 'application/json'}
            if credential:
                headers['Authorization'] = 'Bearer ' + credential
            req = Request('http://127.0.0.1:%s%s' % (server.server_port, path),
                          data=json.dumps(data).encode() if data is not None else None, headers=headers)
            try:
                with urlopen(req, timeout=3) as response:
                    return response.status, response.read()
            except HTTPError as exc:
                return exc.code, exc.read()
        try:
            self.assertEqual(request('/issue', {}, 'bot-secret')[0], 401)
            self.assertEqual(request('/pending', credential='website-secret')[0], 401)
            self.assertEqual(request('/pending?limit=wrong', credential='bot-secret')[0], 400)
            self.assertEqual(request('/hello', {'dev': self.dev})[0], 403)
            self.assertEqual(request('/hook', self.event())[0], 403)
            self.assertEqual(request('/hook', self.event(), self.secret)[0], 200)
            self.assertEqual(request('/bind', {}, 'bot-secret')[0], 410)
            self.assertEqual(request('/app.apk?qq=1234567&exp=9999999999&sig=old')[0], 410)
            self.assertEqual(request('/link?qq=1234567', credential='bot-secret')[0], 410)
            self.assertEqual(request('/download/' + self.grant + '/app.apk')[0], 401)
            svc.DOWNLOAD_DIR.mkdir()
            (svc.DOWNLOAD_DIR / (self.grant + '.apk')).write_bytes(b'package')
            self.assertEqual(request('/download/' + self.grant + '/app.apk', credential='website-secret'), (200, b'package'))
            self.binding_check.return_value = False
            self.assertEqual(request('/download/' + self.grant + '/app.apk', credential='website-secret')[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
