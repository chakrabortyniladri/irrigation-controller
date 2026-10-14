"""On-demand Pi camera: opened for the first viewer, closed after the last one leaves,
so rpicam-* commands can use the camera whenever nobody is watching.
ponytail: one shared encoder fanned out to all viewers; fine for a handful of clients.
"""
import io
import threading

SIZE = (640, 480)
FLIP = False          # True if the camera is mounted upside down


class FrameBuffer(io.BufferedIOBase):
    """Holds the latest JPEG frame; wakes every viewer when a new one arrives."""

    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class Camera:
    """Viewer refcount around a start/stop pair."""

    def __init__(self, start, stop):
        self._start, self._stop = start, stop
        self._lock = threading.Lock()
        self.viewers = 0

    def acquire(self):
        with self._lock:
            if self.viewers == 0:
                self._start()          # raises -> viewers stays 0
            self.viewers += 1

    def release(self):
        with self._lock:
            self.viewers -= 1
            if self.viewers == 0:
                self._stop()


def picamera(frames):
    """(start, stop) for the real camera. Imports live here so tests run off-Pi."""
    held = {}

    def start():
        from libcamera import Transform
        from picamera2 import Picamera2
        from picamera2.encoders import MJPEGEncoder
        from picamera2.outputs import FileOutput
        cam = Picamera2()
        try:
            cam.configure(cam.create_video_configuration(
                main={'size': SIZE}, transform=Transform(hflip=FLIP, vflip=FLIP)))
            cam.start_recording(MJPEGEncoder(), FileOutput(frames))
        except Exception:
            cam.close()                # don't keep the camera locked after a failed start
            raise
        held['cam'] = cam

    def stop():
        cam = held.pop('cam')
        cam.stop_recording()
        cam.close()

    return start, stop
