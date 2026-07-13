"""Market hours for the markets the strategy watches.

Each market is a :class:`MarketHours` with its local timezone and one or two
trading sessions (two for Asian markets with a lunch break).

Works with Nautilus's :meth:`Actor.clock.utc_now` and plain pandas timestamps.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time

import pandas as pd


# =============================================================================
# Core model
# =============================================================================


@dataclass(frozen=True)
class MarketHours:
    """Trading session(s) for a single market in its local timezone.

    ``sessions`` is one tuple ``(open, close)`` for continuous markets (NYSE,
    LSE, Xetra) or two tuples for markets with a lunch break (TSE, SSE, HKEX).
    """

    tz: str
    sessions: tuple[tuple[time, time], ...]
    name: str = ""
    use_weekends: bool = False

    # ----- factories ---------------------------------------------------------

    @classmethod
    def continuous(
        cls,
        tz: str,
        open_h: int,
        open_m: int,
        close_h: int,
        close_m: int,
        name: str = "",
        use_weekends: bool = False,
    ) -> MarketHours:
        return cls(
            tz=tz,
            sessions=((time(open_h, open_m), time(close_h, close_m)),),
            name=name,
            use_weekends=use_weekends,
        )

    @classmethod
    def with_lunch(
        cls,
        tz: str,
        morning: tuple[int, int, int, int],
        afternoon: tuple[int, int, int, int],
        name: str = "",
        use_weekends: bool = False,
    ) -> MarketHours:
        """Two-session market. Args: ``morning=(oh, om, ch, cm)`` etc."""
        return cls(
            tz=tz,
            sessions=(
                (time(morning[0], morning[1]), time(morning[2], morning[3])),
                (time(afternoon[0], afternoon[1]), time(afternoon[2], afternoon[3])),
            ),
            name=name,
            use_weekends=use_weekends,
        )

    # ----- helpers -----------------------------------------------------------

    def _localize(self, ts: pd.Timestamp | None) -> pd.Timestamp:
        if ts is None:
            ts = pd.Timestamp.now(tz="UTC")
        elif ts.tz is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(self.tz)

    def _session_endpoints(self, local_ts: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        base = local_ts.normalize()
        return [
            (
                base + pd.Timedelta(hours=s.hour, minutes=s.minute),
                base + pd.Timedelta(hours=e.hour, minutes=e.minute),
            )
            for s, e in self.sessions
        ]

    # ----- public API --------------------------------------------------------

    def is_open(self, ts: pd.Timestamp | None = None) -> bool:
        local = self._localize(ts)
        if local.dayofweek >= 5 and not self.use_weekends:  # Sat/Sun
            return False
        now = local.time()
        return any(s <= now < e for s, e in self.sessions)

    def next_open(self, ts: pd.Timestamp | None = None) -> pd.Timestamp:
        """Next session start (handles lunch breaks and weekends)."""
        local = self._localize(ts)
        for _ in range(14):  # search up to 2 weeks ahead
            if local.dayofweek < 5 or self.use_weekends:
                for s, _ in self.sessions:
                    candidate = local.normalize() + pd.Timedelta(hours=s.hour, minutes=s.minute)
                    if candidate > local:
                        return candidate
            local = (local + pd.Timedelta(days=1)).normalize()
        raise RuntimeError(f"no opening found within 2 weeks for {self.name or self.tz}")

    def next_close(self, ts: pd.Timestamp | None = None) -> pd.Timestamp:
        """Next session end (current session if open, else next session's end)."""
        local = self._localize(ts)
        if local.dayofweek < 5 or self.use_weekends:
            for _, end_ts in self._session_endpoints(local):
                if end_ts > local:
                    return end_ts
        # Past today's last close → next trading day's matching session end
        no = self.next_open(local).tz_convert(self.tz)
        for _, end_ts in self._session_endpoints(no):
            if end_ts > no:
                return end_ts
        raise RuntimeError(f"no close found for {self.name or self.tz}")

    def status(self, ts: pd.Timestamp | None = None) -> str:
        """Human-readable: ``"OPEN (closes 16:00 EST)"`` / ``"CLOSED (opens 09:30 EST in 2h 14m)"``."""
        ts = self._localize(ts)
        if self.is_open(ts):
            close = self.next_close(ts)
            delta = close - ts
            return f"OPEN — closes {close.strftime('%H:%M %Z')} (in {format_delta(delta)})"
        no = self.next_open(ts)
        delta = no - ts
        return f"CLOSED — opens {no.strftime('%a %d %b %H:%M %Z')} (in {format_delta(delta)})"


# =============================================================================
# Helpers
# =============================================================================


def format_delta(td: pd.Timedelta) -> str:
    """Compact ``"2h 14m"`` / ``"3d 5h"`` formatting for small durations."""
    secs = int(td.total_seconds())
    if secs < 0:
        return "0m"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def upcoming(
    markets: Iterable[MarketHours],
    ts: pd.Timestamp | None = None,
) -> list[tuple[MarketHours, pd.Timestamp]]:
    """``[(market, next_open), ...]`` sorted ascending by next open time."""
    return sorted(
        ((m, m.next_open(ts)) for m in markets),
        key=lambda pair: pair[1],
    )


def open_now(
    markets: Iterable[MarketHours],
    ts: pd.Timestamp | None = None,
) -> list[MarketHours]:
    """Subset of markets currently open."""
    return [m for m in markets if m.is_open(ts)]


def next_to_open(
    markets: Iterable[MarketHours],
    ts: pd.Timestamp | None = None,
) -> tuple[MarketHours, pd.Timestamp] | None:
    """The single market opening soonest. ``None`` if all are open now."""
    items = upcoming(markets, ts)
    return items[0] if items else None


# =============================================================================
# Pre-built sessions — adjust if your exchange changes its hours.
# =============================================================================


NYSE = MarketHours.continuous("America/New_York", 9, 30, 16, 0, name="NYSE")
LSE = MarketHours.continuous("Europe/London", 8, 0, 16, 30, name="LSE")
XETRA = MarketHours.continuous("Europe/Berlin", 9, 0, 17, 30, name="XETRA")
BME = MarketHours.continuous("Europe/Madrid", 9, 0, 17, 30, name="BME")

TSE = MarketHours.with_lunch(
    "Asia/Tokyo",
    morning=(9, 0, 11, 30),
    afternoon=(12, 30, 15, 0),
    name="TSE",
)
SSE = MarketHours.with_lunch(
    "Asia/Shanghai",
    morning=(9, 30, 11, 30),
    afternoon=(13, 0, 15, 0),
    name="SSE",
)
HKEX = MarketHours.with_lunch(
    "Asia/Hong_Kong",
    morning=(9, 30, 12, 0),
    afternoon=(13, 0, 16, 0),
    name="HKEX",
)


WATCHED = [NYSE, LSE, TSE, SSE, HKEX, XETRA, BME]

# =============================================================================
# CLI demo
# =============================================================================


if __name__ == "__main__":
    now = pd.Timestamp.now(tz="UTC")
    print(f"now: {now} ({now.tz_convert('Europe/Madrid').strftime('%H:%M Madrid')})")
    print()

    print(f"open now: {[m.name for m in open_now(WATCHED, now)]}")
    print()

    print("next openings (sorted):")
    for m, when in upcoming(WATCHED, now):
        delta = when - now
        print(f"  {m.name:5s}  {when.strftime('%a %d %b %H:%M %Z')}  (in {format_delta(delta)})")

    print()
    for m in WATCHED:
        print(f"  {m.status(now)}")
