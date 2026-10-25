"""Hybrid logical clock for research map writes.

A stamp is text that sorts in time order: `<ms, 13 digits>.<counter, 4 hex>.<device>`. The milliseconds
follow the wall clock but never go backwards on one device, and never fall behind a stamp this device
has already seen (in the map being written, or its own last write); the counter orders writes in the
same millisecond; the device name breaks ties between Macs, so two writes never compare equal.
"""
from __future__ import annotations

import fcntl
import re
import time
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR, DEVICE

STATE = DATA_DIR / "rmap" / "clock"   # this device's last stamp, so a clock set back cannot reorder writes
HLC = re.compile(r"^(\d{13})\.([0-9a-f]{4})\.([A-Za-z0-9_-]*)$")


def wall_ms() -> int:
    return int(time.time() * 1000)


def make(ms: int, counter: int = 0, device: str = "") -> str:
    return f"{ms:013d}.{counter:04x}.{device}"


def parse(stamp: str) -> tuple[int, int, str] | None:
    m = HLC.match(stamp or "")
    return (int(m.group(1)), int(m.group(2), 16), m.group(3)) if m else None


def from_iso(text: str | None) -> str:
    """A stamp for a local ISO time (records written before clocks existed): it sorts before any real write then."""
    if not text:
        return ""
    try:
        return make(int(datetime.fromisoformat(str(text)).timestamp() * 1000))
    except ValueError:
        return ""


class Clock:
    """Stamps for one batch of writes; observe() everything the batch reads first."""

    def __init__(self, device: str = DEVICE):
        self.device = device
        self.ms, self.counter = 0, 0
        self._load()

    def _load(self) -> None:
        try:
            self.observe(Path(STATE).read_text(encoding="utf-8").strip())
        except OSError:
            pass

    def observe(self, stamp: str | None) -> None:
        p = parse(stamp or "")
        if p and (p[0], p[1]) > (self.ms, self.counter):
            self.ms, self.counter = p[0], p[1]

    def tick(self) -> str:
        now = wall_ms()
        if now > self.ms:
            self.ms, self.counter = now, 0
        else:
            self.counter += 1
            if self.counter > 0xFFFF:
                self.ms, self.counter = self.ms + 1, 0
        return make(self.ms, self.counter, self.device)

    def save(self) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        with (STATE.parent / "clock.lock").open("w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            other = Clock.__new__(Clock)
            other.ms, other.counter, other.device = 0, 0, self.device
            other._load()
            other.observe(make(self.ms, self.counter))
            STATE.write_text(make(other.ms, other.counter, self.device), encoding="utf-8")
