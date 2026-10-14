#!/usr/bin/env python3
"""Camera runs only while someone watches. No picamera2 needed."""
import unittest

from camera import Camera, FrameBuffer


class TestCamera(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.cam = Camera(lambda: self.calls.append('start'), lambda: self.calls.append('stop'))

    def test_first_viewer_starts_last_viewer_stops(self):
        self.cam.acquire()
        self.cam.acquire()
        self.assertEqual(self.calls, ['start'])
        self.cam.release()
        self.assertEqual(self.calls, ['start'])
        self.cam.release()
        self.assertEqual(self.calls, ['start', 'stop'])
        self.assertEqual(self.cam.viewers, 0)

    def test_restart_after_stop(self):
        self.cam.acquire(); self.cam.release(); self.cam.acquire()
        self.assertEqual(self.calls, ['start', 'stop', 'start'])

    def test_failed_start_leaves_no_viewer(self):
        def boom():
            raise RuntimeError('camera busy')
        cam = Camera(boom, lambda: self.calls.append('stop'))
        with self.assertRaises(RuntimeError):
            cam.acquire()
        self.assertEqual(cam.viewers, 0)


class TestFrameBuffer(unittest.TestCase):
    def test_write_keeps_latest(self):
        fb = FrameBuffer()
        fb.write(b'a'); fb.write(b'b')
        self.assertEqual(fb.frame, b'b')


if __name__ == '__main__':
    unittest.main()
