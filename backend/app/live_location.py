"""Live Location: while a family member has the live map open, the other phone sends its position every few seconds.

Normal sharing is one fix every 10 minutes (battery friendly). A live session asks the phone for a fix every
INTERVAL_S seconds until it ends: when the watcher closes the map, after `minutes` (at most MAX_MIN), or when the
person being watched taps "Stop sharing" in the notification they always see.
"""
import datetime as dt

from .db import now

INTERVAL_S = 15
MAX_MIN = 30
_watch: dict[str, dict[str, dt.datetime]] = {}   # watched user id -> {watcher id: live until}


def start(target_id: str, watcher_id: str, minutes: int) -> dt.datetime:
    until = now() + dt.timedelta(minutes=max(1, min(MAX_MIN, minutes)))
    _watch.setdefault(target_id, {})[watcher_id] = until
    return until


def watchers(target_id: str) -> list[str]:
    """Who is watching right now (sessions past their time are dropped)."""
    w = _watch.get(target_id, {})
    t = now()
    for k in [k for k, until in w.items() if until <= t]:
        del w[k]
    if not w:
        _watch.pop(target_id, None)
    return list(w)


def live_until(target_id: str) -> dt.datetime | None:
    w = _watch.get(target_id)
    return max(w.values()) if w else None


def stop(target_id: str, watcher_id: str) -> bool:
    """True when nobody is watching any more (the phone can go back to normal sharing)."""
    _watch.get(target_id, {}).pop(watcher_id, None)
    return not watchers(target_id)


def end_all(target_id: str) -> list[str]:
    """The watched person stopped sharing: returns the watchers to tell."""
    return list(_watch.pop(target_id, {}))
