"""A small in-process answer cache with an expiry. `ViewCache` is the whole interface a user of it sees
(get, put, version, clear), so a shared cache such as Redis can replace `TtlCache` without a change in
the services.

What may be cached is the caller's decision: only answers that are a function of their key. A ward view is
keyed by the exact set of patients the reader may see (the scoping is applied BEFORE the key is made),
so an answer is never served to a reader whose scope differs; a public open view is the same for everyone.

An answer is never older than `ttl` seconds, and `clear()` (called after every write this process
handles) drops everything. `version()` moves with every `clear()`: a result computed before a write is
refused by `put` when the write finished first, so a slow read cannot re-fill the cache with old data.
"""

import threading
from collections.abc import Callable, Hashable
from typing import Any, Protocol

MISSING: Any = object()  # what `get` returns for no answer (None is a real answer: "no such bed")


class ViewCache(Protocol):
    def get(self, key: Hashable) -> Any: ...
    def put(self, key: Hashable, value: Any, version: int) -> None: ...
    def version(self) -> int: ...
    def clear(self) -> None: ...


class TtlCache:
    """One dict behind a lock (the API runs its handlers in a thread pool). `ttl` 0 or less: nothing is
    kept. At most `limit` answers: the oldest go first."""

    # shortcut: per process, so a write handled by another process is seen only after `ttl` (and a
    # sim batch at once, since the as_of of a run is in every key); upgrade: a shared cache with the same interface.
    def __init__(self, ttl: float, clock: Callable[[], float], limit: int = 16) -> None:
        self._ttl, self._clock, self._limit = ttl, clock, limit
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._version = 0
        self._lock = threading.Lock()

    def _fresh(self, stored: float, now: float) -> bool:
        return 0 <= now - stored < self._ttl  # a clock that went back expires the answer

    def get(self, key: Hashable) -> Any:
        if self._ttl <= 0:
            return MISSING
        with self._lock:
            item = self._items.get(key)
            if item is not None and self._fresh(item[0], self._clock()):
                return item[1]
            self._items.pop(key, None)
            return MISSING

    def put(self, key: Hashable, value: Any, version: int) -> None:
        if self._ttl <= 0:
            return
        with self._lock:
            if version != self._version:
                return
            now = self._clock()
            self._items = {k: v for k, v in self._items.items() if self._fresh(v[0], now)}
            while len(self._items) >= self._limit:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (now, value)

    def version(self) -> int:
        with self._lock:
            return self._version

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._version += 1
