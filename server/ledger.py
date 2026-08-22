"""What to do with a scan we have seen before.

Three different situations hide behind "this UUID again", and collapsing them
into one silent `return` gets two of them wrong:

    within the cooldown   the same phone is simply still in frame        -> SILENT
    already passed        they are done; say so, but do not re-card them -> REPEAT
    previously failed     a TIMEOUT card says "try again", so let them   -> SHOW

That third case matters most: blocking it means a student who times out can
never actually retry, however many times they scan.

No Tk in here, so the rules can be tested without a display.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .verify import MUOP, OK, TO, Result

SILENT = "silent"
SHOW = "show"
REPEAT = "repeat"

DEFAULT_COOLDOWN = 3.0


@dataclass
class ScanLedger:
    """Per-device history for one session."""

    cooldown_seconds: float = DEFAULT_COOLDOWN
    results: dict[str, Result] = field(default_factory=dict)
    scans: dict[str, int] = field(default_factory=dict)
    repeats: int = 0
    _last_at: dict[str, float] = field(default_factory=dict)

    def submit(self, key: str, result: Result, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now

        last = self._last_at.get(key)
        if last is not None and now - last < self.cooldown_seconds:
            self._last_at[key] = now          # still in frame; stay quiet
            return SILENT
        self._last_at[key] = now
        self.scans[key] = self.scans.get(key, 0) + 1

        previous = self.results.get(key)
        if previous is not None and previous.verdict == OK:
            # Already marked present. Keep the pass -- a later stale QR must not
            # downgrade it -- but tell the operator rather than dropping it.
            self.repeats += 1
            return REPEAT

        self.results[key] = result
        return SHOW

    def record_for(self, key: str) -> Result | None:
        return self.results.get(key)

    def count(self, key: str) -> int:
        return self.scans.get(key, 0)

    @property
    def present(self) -> int:
        return sum(1 for r in self.results.values() if r.verdict == OK)

    @property
    def flagged(self) -> int:
        return sum(1 for r in self.results.values() if r.verdict in (MUOP, TO))

    @property
    def devices(self) -> int:
        return len(self.results)

    def clear(self) -> None:
        """Used after a roster reload, so a newly-registered student can rescan."""
        self.results.clear()
        self.scans.clear()
        self._last_at.clear()
        self.repeats = 0
