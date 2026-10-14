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
        self.pending = {}                 # guid -> state seen before pairing (retained order varies)
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
                # Pair only with a live ESP, so a stray/offline GUID can't lock us out.
                # Re-pair: stop the service, remove "guid" from state.json, start it.
                if kind == 'state':
                    self.pending[guid] = msg
                    return
                if kind != 'availability' or msg.get('status') != 'online':
                    return
                self.data['guid'] = guid
                self._save()
                self.esp = self.pending.pop(guid, {})
                self.pending.clear()
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


class Handler(server.BaseHTTPRequestHandler):
    app = camera = frames = None          # set by main()
    user = password = ''

    def _authed(self):
        if check_auth(self.headers.get('Authorization'), self.user, self.password):
            return True
        log.warning('auth failed from %s', self.client_address[0])
        time.sleep(FAIL_DELAY_S)
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Irrigation"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), 'application/json')

    def _write(self, routes):
        """POST/PUT: JSON only. A non-simple Content-Type forces a CORS preflight, so
        another website can't drive the valve using the browser's saved password."""
        if not self._authed():
            return
        fn = routes.get(self.path)
        if fn is None:
            return self.send_error(404)
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._json(415, {'error': 'Content-Type must be application/json'})
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n)) if 0 < n <= 4096 else None
        except ValueError:
            body = None
        self._json(*fn(body))

    def do_GET(self):
        if not self._authed():
            return
        if self.path == '/':
            self._send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/api/state':
            self._json(200, self.app.state())
        elif self.path == '/stream.mjpg':
            self._stream()
        else:
            self.send_error(404)

    def do_POST(self):
        self._write({'/api/open': self.app.open, '/api/close': self.app.close})

    def do_PUT(self):
        self._write({'/api/schedule': self.app.set_schedule})

    def _stream(self):
        try:
            self.camera.acquire()
        except Exception:
            log.exception('camera failed to start')
            return self._json(503, {'error': 'camera unavailable'})
        log.info('viewer connected: %s', self.client_address[0])
        try:
            self.send_response(200)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            while True:
                with self.frames.condition:
                    if not self.frames.condition.wait(timeout=5):
                        log.warning('no frame from camera for 5 s')
                        continue
                    frame = self.frames.frame
                self.wfile.write(b'--FRAME\r\n')
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
                self.wfile.write(b'\r\n')
        except (BrokenPipeError, ConnectionResetError):
            log.info('viewer left: %s', self.client_address[0])
        finally:
            self.camera.release()

    def log_message(self, fmt, *args):   # silence per-request stderr noise
        pass


def scheduler_loop(app):
    while True:
        try:
            app.tick()
        except Exception:
            log.exception('tick failed')
        time.sleep(TICK_S)


def main():
    user = os.environ.get('IRRIGATION_USER', '')
    password = os.environ.get('IRRIGATION_PASS', '')
    if not user or len(password) < MIN_PASS_LEN:
        sys.exit(f'set IRRIGATION_USER and IRRIGATION_PASS (>= {MIN_PASS_LEN} chars), see ~/.irrigation.env')
    import paho.mqtt.client as mqtt    # here so tests run without paho
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)   # paho >= 2.0
    except AttributeError:
        client = mqtt.Client()                                   # paho 1.x (Bookworm apt)
    app = App(STATE_PATH, lambda t, p: client.publish(t, p, qos=1, retain=False))
    client.on_connect = lambda c, u, f, rc: c.subscribe(
        [('irrigation/+/availability', 1), ('irrigation/+/state', 1)])
    client.on_message = lambda c, u, m: app.on_message(m.topic, m.payload)
    client.on_disconnect = lambda c, u, rc: app.on_broker_lost()
    client.connect_async(MQTT_HOST, MQTT_PORT)   # retries in the background if mosquitto is late
    client.loop_start()
    threading.Thread(target=scheduler_loop, args=(app,), daemon=True).start()

    frames = FrameBuffer()
    Handler.app, Handler.camera, Handler.frames = app, Camera(*picamera(frames)), frames
    Handler.user, Handler.password = user, password
    log.info('serving on http://0.0.0.0:%d/', PORT)
    server.ThreadingHTTPServer(('', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
