"""Webcam capture and QR decode, off the Tk thread.

Tk's mainloop must never block, and detectAndDecodeMulti on a 720p frame costs
tens of milliseconds. So capture runs in its own thread and hands decoded
strings back through a queue that the GUI drains on a timer.

Nothing here touches Tk -- that would crash, Tk is single-threaded.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading

import cv2


@contextlib.contextmanager
def muted_stderr():
    """Silence OpenCV's native V4L chatter, which bypasses sys.stderr."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


def list_cameras(limit: int = 6) -> list[tuple[int, int, int]]:
    """Probe the first few indices. Returns [(index, width, height), ...]."""
    found = []
    for index in range(limit):
        with muted_stderr():
            cap = cv2.VideoCapture(index)
            opened = cap.isOpened()
            ok, frame = cap.read() if opened else (False, None)
            cap.release()
        if opened and ok and frame is not None:
            height, width = frame.shape[:2]
            found.append((index, width, height))
    return found


class CameraWorker:
    """Reads frames, decodes any QRs, posts payload strings to `.payloads`.

    Also keeps the most recent frame in `.latest_frame` for the aiming preview,
    guarded by a lock because the GUI thread reads it.
    """

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720):
        self.index = index
        self.width = width
        self.height = height
        self.payloads: queue.Queue[str] = queue.Queue(maxsize=256)
        self.error: str | None = None
        self.frames_seen = 0
        self._latest = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        with muted_stderr():
            self._cap = cv2.VideoCapture(self.index)
            if self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if not self._cap.isOpened():
            self.error = f"cannot open camera {self.index}"
            return False
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()
        return True

    def latest_frame(self):
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def _run(self) -> None:
        detector = cv2.QRCodeDetector()
        misses = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                misses += 1
                if misses > 30:
                    self.error = "camera stopped returning frames"
                    break
                continue
            misses = 0
            self.frames_seen += 1
            with self._lock:
                self._latest = frame

            try:
                found, decoded, _points, _ = detector.detectAndDecodeMulti(frame)
            except cv2.error:
                continue
            if not found or decoded is None:
                continue
            for text in decoded:
                if not text:
                    continue
                with contextlib.suppress(queue.Full):
                    self.payloads.put_nowait(text)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with contextlib.suppress(Exception):
            self._cap.release()
