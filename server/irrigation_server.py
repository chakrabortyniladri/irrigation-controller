#!/usr/bin/env python3
"""Irrigation on the Pi 4: page + JSON API + on-demand camera + scheduler on :8000.

Talks to the ESP-01S valve over MQTT on the local mosquitto. HTTP Basic auth on every
route; creds from env IRRIGATION_USER / IRRIGATION_PASS (systemd loads ~/.irrigation.env).
Plain HTTP is fine over Tailscale (WireGuard encrypts it).
"""
import base64
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http import server
from pathlib import Path

import schedule
import state_store
from camera import Camera, FrameBuffer, picamera

PORT = 8000
MQTT_HOST, MQTT_PORT = 'localhost', 1883
STATE_PATH = os.path.expanduser('~/.local/share/irrigation/state.json')
PAGE = Path(__file__).parent / 'static' / 'index.html'
TOPIC_HEARTBEAT = 'irrigation/server/heartbeat'
TICK_S = 5            # scheduler period; catch-up starts within seconds of NTP sync
RETRY_S = 10          # resend a scheduled open until the ESP confirms it
HEARTBEAT_S = 60      # ESP falls back to its own 08:00 run after 30 min without one
MIN_PASS_LEN = 12
FAIL_DELAY_S = 1.0    # slows password guessing

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [irrigation] %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger(__name__)


def check_auth(header, user, password):
    """True if an Authorization header carries exactly user:password (constant-time)."""
    if not header or not header.startswith('Basic '):
        return False
    try:
        given = base64.b64decode(header[6:], validate=True)
    except ValueError:
        return False
    return hmac.compare_digest(given, f'{user}:{password}'.encode())


def ntp_synced():
    try:
        out = subprocess.run(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return out.strip() == 'yes'


class App:
    """All irrigation logic. HTTP and MQTT are thin shells around it."""

    def __init__(self, store_path, publish, clock=datetime.now, synced=ntp_synced,
                 mono=time.monotonic):
        self.path = store_path
        self.data = state_store.load(store_path)
        self.publish = publish            # publish(topic, payload_str), never retained
        self.now, self.synced, self.mono = clock, synced, mono
        self.is_synced = False
        self.esp = {}                     # latest ESP state message
        self.esp_online = False
        self.last_attempt = self.last_beat = float('-inf')
        self.lock = threading.Lock()

    def _save(self):
        state_store.save(self.path, self.data)

    def _topic(self, kind):
        return f"irrigation/{self.data['guid']}/{kind}"

    # ---- MQTT in ----
    def on_message(self, topic, payload):
        parts = topic.split('/')
        if len(parts) != 3 or parts[0] != 'irrigation' or parts[1] == 'server':
            return
        guid, kind = parts[1], parts[2]
        try:
            msg = json.loads(payload)
        except ValueError:
            log.warning('bad JSON on %s', topic)
            return
        if not isinstance(msg, dict):
            return
        with self.lock:
            if self.data['guid'] is None:
                self.data['guid'] = guid
                self._save()
                log.info('paired with ESP %s', guid)
            if guid != self.data['guid']:
                log.warning('ignoring unknown ESP %s', guid)
                return
            if kind == 'availability':
                self.esp_online = msg.get('status') == 'online'
            elif kind == 'state':
                self.esp = msg
                self._note_run()

    def _note_run(self):
        now = self.now()
        today = now.strftime('%Y-%m-%d')
        if (self.esp.get('last_sched_day') == schedule.day_key(now)
                and not (self.data['last_run'] or '').startswith(today)):
            self.data['last_run'] = now.strftime('%Y-%m-%d %H:%M')
            self._save()

    def on_broker_lost(self):
        with self.lock:
            self.esp_online = False

    # ---- API: each returns (status, body) ----
    def open(self, body):
        try:
            secs = schedule.validate_seconds(body.get('seconds') if isinstance(body, dict) else None)
        except ValueError as e:
            return 400, {'error': str(e)}
        return self._command({'action': 'open', 'seconds': secs})

    def close(self, body=None):
        return self._command({'action': 'close'})

    def _command(self, cmd):
        with self.lock:
            if not self.esp_online or not self.data['guid']:
                return 503, {'error': 'valve offline'}
            self.publish(self._topic('command'), json.dumps(cmd))
        return 200, {'ok': True}

    def set_schedule(self, body):
        try:
            s = schedule.validate_schedule(body)
        except ValueError as e:
            return 400, {'error': str(e)}
        with self.lock:
            self.data['schedule'] = s
            self._save()
        log.info('schedule saved: %s', s)
        return 200, {'ok': True}

    def state(self):
        with self.lock:
            now, s, e = self.now(), self.data['schedule'], self.esp
            ran = e.get('last_sched_day') == schedule.day_key(now)
            return {
                'esp_online': self.esp_online,
                'valve': e.get('valve', 'closed'),
                'remaining_s': e.get('remaining_s', 0),
                'max_s': schedule.MAX_RUN_S,
                'fallback': bool(e.get('fallback', False)),
                'synced': self.is_synced,
                'clock': now.strftime('%H:%M'),
                'schedule': dict(s),
                'last_run': self.data['last_run'],
                'next_run': schedule.next_run(s, now, ran),
            }

    # ---- scheduler, every TICK_S ----
    def tick(self):
        self.is_synced = self.synced()
        mono = self.mono()
        if mono - self.last_beat >= HEARTBEAT_S:
            self.last_beat = mono
            self.publish(TOPIC_HEARTBEAT, '1')
        with self.lock:
            s = self.data['schedule']
            if not self.esp_online or not schedule.due(
                    s, self.now(), self.is_synced, self.esp.get('last_sched_day')):
                return
            if mono - self.last_attempt < RETRY_S:
                return
            self.last_attempt = mono
            log.info('scheduled run: %d s', s['seconds'])
            self.publish(self._topic('command'),
                         json.dumps({'action': 'open', 'seconds': s['seconds'], 'scheduled': True}))


def main():
    user = os.environ.get('IRRIGATION_USER', '')
    password = os.environ.get('IRRIGATION_PASS', '')
    if not user or len(password) < MIN_PASS_LEN:
        sys.exit(f'set IRRIGATION_USER and IRRIGATION_PASS (>= {MIN_PASS_LEN} chars), see ~/.irrigation.env')
    raise NotImplementedError('wired in Task 8')


if __name__ == '__main__':
    main()
