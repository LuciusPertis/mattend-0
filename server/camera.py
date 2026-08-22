"""Webcam capture and QR decode, off the Tk thread.

Tk's mainloop must never block, and detectAndDecodeMulti on a 720p frame costs
tens of milliseconds. So capture runs in its own thread and hands decoded
strings back through a queue that the GUI drains on a timer.

Nothing here touches Tk -- that would crash, Tk is single-threaded.
"""

from __future__ import annotations

import collections
import contextlib
import os
import queue
import threading
import time

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


def preview_ppm(frame, height: int, mirror: bool = True) -> bytes | None:
    """Scale a BGR frame for the aiming thumbnail and encode it as PPM.

    Mirrored by default: the camera faces the person standing at it, so an
    unmirrored preview makes them step the wrong way when lining up their phone.
    This is display only -- the frame handed to the QR detector is never
    transformed.

    PPM because Tk reads it natively, so there is no PIL round-trip.
    """
    if frame is None or height < 1:
        return None
    source_height, source_width = frame.shape[:2]
    if source_height < 1 or source_width < 1:
        return None
    scale = height / source_height
    small = cv2.resize(frame, (max(1, int(source_width * scale)), height))
    if mirror:
        small = cv2.flip(small, 1)
    ok, buf = cv2.imencode(".ppm", small)
    return buf.tobytes() if ok else None


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
        self.drops = 0
        self.decodes = 0
        self.last_decode_at: float | None = None
        self.actual_size = (0, 0)
        self._ticks: collections.deque[float] = collections.deque(maxlen=45)
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
        self.actual_size = (
            int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._thread.start()
        return True

    def latest_frame(self):
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    @property
    def fps(self) -> float:
        """Rolling capture rate over the last ~45 frames."""
        with self._lock:
            ticks = list(self._ticks)
        if len(ticks) < 2:
            return 0.0
        span = ticks[-1] - ticks[0]
        return (len(ticks) - 1) / span if span > 0 else 0.0

    @property
    def seconds_since_decode(self) -> float | None:
        return None if self.last_decode_at is None else time.monotonic() - self.last_decode_at

    def _run(self) -> None:
        detector = cv2.QRCodeDetector()
        misses = 0
        while not self._stop.is_set():
            ok, frame = self._cap.read()
            if not ok or frame is None:
                misses += 1
                self.drops += 1
                if misses > 30:
                    self.error = "camera stopped returning frames"
                    break
                continue
            misses = 0
            self.frames_seen += 1
            with self._lock:
                self._latest = frame
                self._ticks.append(time.monotonic())

            try:
                found, decoded, _points, _ = detector.detectAndDecodeMulti(frame)
            except cv2.error:
                continue
            if not found or decoded is None:
                continue
            for text in decoded:
                if not text:
                    continue
                self.decodes += 1
                self.last_decode_at = time.monotonic()
                with contextlib.suppress(queue.Full):
                    self.payloads.put_nowait(text)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with contextlib.suppress(Exception):
            self._cap.release()
