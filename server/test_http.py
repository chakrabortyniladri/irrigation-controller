#!/usr/bin/env python3
"""HTTP shell over a fake App: auth, routing, JSON-only writes, camera failure."""
import base64
import http.client
import json
import threading
import unittest
from http import server

import irrigation_server as srv

U, P = 'nilrasp', 'k7#Qv9!xLp2wZr'
AUTH = 'Basic ' + base64.b64encode(f'{U}:{P}'.encode()).decode()


class FakeApp:
    def __init__(self):
        self.calls = []

    def state(self):
        return {'valve': 'closed'}

    def open(self, body):
        self.calls.append(('open', body)); return 200, {'ok': True}

    def close(self, body=None):
        self.calls.append(('close', body)); return 503, {'error': 'valve offline'}

    def set_schedule(self, body):
        self.calls.append(('schedule', body)); return 400, {'error': 'bad'}


class BrokenCamera:
    def acquire(self):
        raise RuntimeError('no camera')

    def release(self):
        raise AssertionError('release without acquire')


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv.FAIL_DELAY_S = 0
        srv.Handler.user, srv.Handler.password = U, P
        srv.Handler.camera = BrokenCamera()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def setUp(self):
        srv.Handler.app = self.app = FakeApp()

    def req(self, method, path, body=None, auth=AUTH, ctype='application/json'):
        c = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=5)
        headers = {'Authorization': auth} if auth else {}
        if body is not None:
            headers['Content-Type'] = ctype
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_auth_required_everywhere(self):
        for m, p in (('GET', '/'), ('GET', '/api/state'), ('GET', '/stream.mjpg'),
                     ('POST', '/api/open'), ('PUT', '/api/schedule')):
            r, _ = self.req(m, p, auth=None)
            self.assertEqual(r.status, 401, p)
            self.assertIn('Basic', r.getheader('WWW-Authenticate'))
        r, _ = self.req('GET', '/', auth='Basic ' + base64.b64encode(b'nilrasp:nope').decode())
        self.assertEqual(r.status, 401)
        self.assertEqual(self.app.calls, [])

    def test_page_and_state(self):
        r, data = self.req('GET', '/')
        self.assertEqual(r.status, 200)
        self.assertIn(b'<!DOCTYPE html>', data)
        r, data = self.req('GET', '/api/state')
        self.assertEqual(json.loads(data), {'valve': 'closed'})

    def test_writes_pass_body_and_status(self):
        r, _ = self.req('POST', '/api/open', b'{"seconds": 45}')
        self.assertEqual(r.status, 200)
        r, _ = self.req('POST', '/api/close', b'{}')
        self.assertEqual(r.status, 503)
        r, data = self.req('PUT', '/api/schedule', b'{"time": "x"}')
        self.assertEqual(r.status, 400)
        self.assertEqual(json.loads(data), {'error': 'bad'})
        self.assertEqual(self.app.calls, [('open', {'seconds': 45}), ('close', {}),
                                          ('schedule', {'time': 'x'})])

    def test_bad_json_becomes_none(self):
        self.req('POST', '/api/open', b'{not json')
        self.assertEqual(self.app.calls, [('open', None)])

    def test_non_json_content_type_rejected(self):
        r, _ = self.req('POST', '/api/open', b'{"seconds": 45}', ctype='text/plain')
        self.assertEqual(r.status, 415)
        self.assertEqual(self.app.calls, [])

    def test_camera_failure_is_503(self):
        r, _ = self.req('GET', '/stream.mjpg')
        self.assertEqual(r.status, 503)

    def test_unknown_404(self):
        self.assertEqual(self.req('GET', '/nope')[0].status, 404)
        self.assertEqual(self.req('POST', '/nope', b'{}')[0].status, 404)


if __name__ == '__main__':
    unittest.main()
