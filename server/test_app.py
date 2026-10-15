#!/usr/bin/env python3
"""App logic without HTTP, MQTT or a camera."""
import base64
import json
import os
import tempfile
import unittest
from datetime import datetime

import irrigation_server as srv
import state_store

G = 'a' * 32
CMD = f'irrigation/{G}/command'
TODAY = 2026286     # 2026-10-14


class Rig:
    def __init__(self, now=datetime(2026, 10, 14, 8, 0), synced=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'state.json')
        self.now, self.synced, self.mono = now, synced, 0.0
        self.sent = []
        self.app = srv.App(self.path, lambda t, p: self.sent.append((t, json.loads(p))),
                           clock=lambda: self.now, synced=lambda: self.synced,
                           mono=lambda: self.mono)

    def esp(self, last_day=TODAY - 1, valve='closed', guid=G):
        self.app.on_message(f'irrigation/{guid}/availability', b'{"status":"online"}')
        self.app.on_message(f'irrigation/{guid}/state', json.dumps(
            {'valve': valve, 'remaining_s': 0, 'max_s': 90, 'synced': True,
             'last_sched_day': last_day, 'fallback': False}).encode())

    def commands(self):
        return [p for t, p in self.sent if t == CMD]

    def schedule_on(self, time='08:00', seconds=60):
        self.assertok(self.app.set_schedule({'enabled': True, 'time': time, 'seconds': seconds}))

    @staticmethod
    def assertok(result):
        assert result[0] == 200, result


class TestAuth(unittest.TestCase):
    U, P = 'nilrasp', 'k7#Qv9!xLp2wZr'

    def basic(self, s):
        return 'Basic ' + base64.b64encode(s.encode()).decode()

    def test_check_auth(self):
        self.assertTrue(srv.check_auth(self.basic(f'{self.U}:{self.P}'), self.U, self.P))
        self.assertFalse(srv.check_auth(self.basic(f'{self.U}:nope'), self.U, self.P))
        for h in (None, '', 'Bearer x', 'Basic', 'Basic !!!'):
            self.assertFalse(srv.check_auth(h, self.U, self.P), h)


class TestEsp(unittest.TestCase):
    def test_learns_guid_from_availability(self):
        r = Rig(); r.esp()
        self.assertEqual(r.app.data['guid'], G)
        self.assertTrue(r.app.esp_online)
        self.assertEqual(state_store.load(r.path)['guid'], G)

    def test_learns_guid_from_state_first(self):
        r = Rig()
        r.app.on_message(f'irrigation/{G}/state', b'{"valve":"closed","last_sched_day":5}')
        self.assertIsNone(r.app.data['guid'])                  # pairing needs "online"
        r.app.on_message(f'irrigation/{G}/availability', b'{"status":"online"}')
        self.assertEqual(r.app.data['guid'], G)
        self.assertEqual(r.app.esp['last_sched_day'], 5)       # earlier retained state kept

    def test_pairs_only_on_online_availability(self):
        r = Rig()
        other = 'b' * 32
        r.app.on_message(f'irrigation/{other}/availability', b'{"status":"offline"}')
        r.app.on_message(f'irrigation/{other}/command', b'{}')
        self.assertIsNone(r.app.data['guid'])
        r.esp()
        self.assertEqual(r.app.data['guid'], G)

    def test_ignores_second_esp(self):
        r = Rig(); r.esp()
        r.app.on_message(f'irrigation/{"b" * 32}/availability', b'{"status":"offline"}')
        self.assertTrue(r.app.esp_online)

    def test_offline_and_broker_lost(self):
        r = Rig(); r.esp()
        r.app.on_message(f'irrigation/{G}/availability', b'{"status":"offline"}')
        self.assertFalse(r.app.esp_online)
        r.esp(); r.app.on_broker_lost()
        self.assertFalse(r.app.esp_online)

    def test_retained_online_is_not_live(self):
        # After a power cut the broker replays the old retained "online", but the dead ESP's LWT never fired.
        r = Rig()
        r.app.on_message(f'irrigation/{G}/availability', b'{"status":"online"}', retained=True)
        r.app.on_message(f'irrigation/{G}/state', b'{"valve":"closed"}', retained=True)
        self.assertEqual(r.app.data['guid'], G)
        self.assertFalse(r.app.esp_online)
        self.assertEqual(r.app.open({'seconds': 30})[0], 503)
        r.app.on_message(f'irrigation/{G}/state', b'{"valve":"closed"}')   # live heartbeat reply
        self.assertTrue(r.app.esp_online)

    def test_offline_when_esp_goes_silent(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.mono = srv.STALE_S - 1
        self.assertTrue(r.app.esp_online)
        r.mono = srv.STALE_S
        self.assertFalse(r.app.esp_online)
        self.assertFalse(r.app.state()['esp_online'])
        r.app.tick()
        self.assertEqual(r.commands(), [])

    def test_bad_payloads_ignored(self):
        r = Rig()
        for topic, payload in ((f'irrigation/{G}/state', b'not json'),
                               (f'irrigation/{G}/state', b'[1]'),
                               ('foo', b'{}'),
                               ('irrigation/server/heartbeat', b'1')):
            r.app.on_message(topic, payload)
        self.assertIsNone(r.app.data['guid'])


class TestApi(unittest.TestCase):
    def test_open_validates(self):
        r = Rig(); r.esp()
        for body in ({'seconds': 91}, {'seconds': 0}, {'seconds': True}, {'seconds': '30'},
                     {'seconds': None}, {}, None, [45]):
            status, out = r.app.open(body)
            self.assertEqual(status, 400, body)
            self.assertIn('error', out)
        self.assertEqual(r.commands(), [])
        self.assertEqual(r.app.open({'seconds': 45})[0], 200)
        self.assertEqual(r.commands(), [{'action': 'open', 'seconds': 45}])   # never 'scheduled'

    def test_open_close_offline_503(self):
        r = Rig()
        self.assertEqual(r.app.open({'seconds': 30})[0], 503)
        self.assertEqual(r.app.close()[0], 503)
        self.assertEqual(r.sent, [])

    def test_close(self):
        r = Rig(); r.esp()
        self.assertEqual(r.app.close({})[0], 200)
        self.assertEqual(r.commands(), [{'action': 'close'}])

    def test_set_schedule(self):
        r = Rig()
        self.assertEqual(r.app.set_schedule({'enabled': True, 'time': '22:00', 'seconds': 60})[0], 400)
        self.assertEqual(r.app.set_schedule({'enabled': True, 'time': '07:15', 'seconds': 45})[0], 200)
        self.assertEqual(state_store.load(r.path)['schedule'],
                         {'enabled': True, 'time': '07:15', 'seconds': 45})

    def test_state_view(self):
        r = Rig(now=datetime(2026, 10, 14, 7, 0)); r.esp(); r.schedule_on()
        r.app.tick()
        v = r.app.state()
        self.assertEqual(v['esp_online'], True)
        self.assertEqual(v['valve'], 'closed')
        self.assertEqual(v['max_s'], 90)
        self.assertEqual(v['synced'], True)
        self.assertEqual(v['clock'], '07:00')
        self.assertEqual(v['next_run'], 'Wed 14 Oct 08:00')
        self.assertEqual(v['schedule'], {'enabled': True, 'time': '08:00', 'seconds': 60})
        self.assertIsNone(v['last_run'])
        self.assertFalse(v['fallback'])


class TestScheduler(unittest.TestCase):
    SCHED = {'action': 'open', 'seconds': 60, 'scheduled': True}

    def test_fires_at_time(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [self.SCHED])

    def test_catch_up_after_power_cut(self):
        r = Rig(now=datetime(2026, 10, 14, 15, 30)); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [self.SCHED])

    def test_dropped_after_cutoff(self):
        r = Rig(now=datetime(2026, 10, 14, 22, 0)); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [])

    def test_skips_when_esp_already_ran_today(self):
        r = Rig(); r.esp(last_day=TODAY); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [])

    def test_waits_for_ntp_and_esp(self):
        r = Rig(synced=False); r.esp(); r.schedule_on()
        r.app.tick()
        self.assertEqual(r.commands(), [])
        r2 = Rig(); r2.schedule_on()
        r2.app.on_message(f'irrigation/{G}/availability', b'{"status":"online"}')   # no state yet
        r2.app.tick()
        self.assertEqual(r2.commands(), [])

    def test_retry_spacing(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.app.tick()                          # mono 0: send
        r.mono = 5; r.app.tick()              # too soon
        r.mono = 10; r.app.tick()             # ESP didn't confirm: resend (ESP dedupes)
        self.assertEqual(len(r.commands()), 2)
        r.esp(last_day=TODAY, valve='open')   # confirmed
        r.mono = 20; r.app.tick()
        self.assertEqual(len(r.commands()), 2)

    def test_last_run_recorded(self):
        r = Rig(); r.esp(); r.schedule_on()
        r.esp(last_day=TODAY, valve='open')
        self.assertEqual(r.app.data['last_run'], '2026-10-14 08:00')
        self.assertEqual(state_store.load(r.path)['last_run'], '2026-10-14 08:00')
        r.now = datetime(2026, 10, 14, 8, 1)
        r.esp(last_day=TODAY, valve='closed')
        self.assertEqual(r.app.data['last_run'], '2026-10-14 08:00')   # not bumped by later states

    def test_heartbeat(self):
        r = Rig()
        r.app.tick()
        r.mono = 30; r.app.tick()
        r.mono = 60; r.app.tick()
        beats = [t for t, _ in r.sent if t == srv.TOPIC_HEARTBEAT]
        self.assertEqual(len(beats), 2)


class TestStartupGuard(unittest.TestCase):
    def run_main(self, env):
        old = {k: os.environ.pop(k, None) for k in ('IRRIGATION_USER', 'IRRIGATION_PASS')}
        os.environ.update(env)
        try:
            with self.assertRaises(SystemExit):
                srv.main()
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_refuses_unset_short_or_missing_user(self):
        self.run_main({})
        self.run_main({'IRRIGATION_USER': 'nilrasp', 'IRRIGATION_PASS': 'short'})
        self.run_main({'IRRIGATION_PASS': 'a-long-enough-password'})


if __name__ == '__main__':
    unittest.main()
