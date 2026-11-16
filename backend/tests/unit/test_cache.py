"""The in-process answer cache: an answer lives at most `ttl` seconds, a clear drops all and refuses what
was being built before it, and a size limit drops the oldest."""

from biosentry.core.cache import MISSING, TtlCache


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def cache(ttl: float = 3.0, limit: int = 32) -> tuple[TtlCache, Clock]:
    clock = Clock()
    return TtlCache(ttl, clock, limit), clock


def test_an_answer_is_served_until_ttl_and_never_after() -> None:
    c, clock = cache(3.0)
    c.put("k", [1], c.version())
    assert c.get("k") == [1]
    clock.t += 2.999
    assert c.get("k") == [1]
    clock.t += 0.001  # exactly ttl old: gone
    assert c.get("k") is MISSING


def test_none_is_an_answer_and_an_unknown_key_is_missing() -> None:
    c, _ = cache()
    c.put("no bed", None, c.version())
    assert c.get("no bed") is None and c.get("other") is MISSING


def test_ttl_zero_keeps_nothing() -> None:
    c, _ = cache(0.0)
    c.put("k", 1, c.version())
    assert c.get("k") is MISSING


def test_a_clock_that_goes_back_expires_the_answer() -> None:
    c, clock = cache(3.0)
    c.put("k", 1, c.version())
    clock.t -= 1
    assert c.get("k") is MISSING


def test_clear_drops_everything_and_refuses_an_answer_built_before_it() -> None:
    c, _ = cache()
    c.put("a", 1, c.version())
    started = c.version()  # a read starts
    c.clear()  # a write finishes
    assert c.get("a") is MISSING and c.version() == started + 1
    c.put("late", 2, started)  # the read ends: its data may be older than the write
    assert c.get("late") is MISSING
    c.put("fresh", 3, c.version())
    assert c.get("fresh") == 3


def test_the_oldest_go_at_the_limit_and_the_expired_go_first() -> None:
    c, clock = cache(10.0, limit=2)
    c.put("a", 1, c.version())
    clock.t += 1
    c.put("b", 2, c.version())
    clock.t += 1
    c.put("c", 3, c.version())  # full: a goes
    assert [c.get(k) for k in "abc"] == [MISSING, 2, 3]
    clock.t += 9.5  # b is 10.5 old, c 9.5
    c.put("d", 4, c.version())
    assert [c.get(k) for k in "bcd"] == [MISSING, 3, 4]
