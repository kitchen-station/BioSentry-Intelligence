"""A patient-level bootstrap with a small fixed generator: no `random`, same numbers every run."""

from collections.abc import Callable, Sequence

SEED = 20261112
RESAMPLES = 1000
_MASK = (1 << 64) - 1


class SplitMix64:
    """Steele, Lea and Flood (2014): a 64-bit state, one addition and a mixer per draw."""

    def __init__(self, seed: int) -> None:
        self.state = seed & _MASK

    def next(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)


def intervals(
    units: dict[str, Sequence[tuple]],
    stats: dict[str, Callable[[list[float]], float | None]],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[tuple[str, str], tuple[float, float] | None]:
    """95% percentile intervals. `units[name]` holds one tuple of numbers per patient (the same
    patients in the same order for every name); each resample draws patients with replacement and
    sums every column; `stats[stat]` turns the column sums into a number (None: undefined, the
    resample is skipped). Interval: sorted values at positions int(.025 n) and int(.975 n) - 1."""
    first = next(iter(units.values()))
    n = len(first)
    if n == 0:
        return {(u, s): None for u in units for s in stats}
    columns = {u: list(zip(*rows)) for u, rows in units.items()}
    rng = SplitMix64(seed)
    values: dict[tuple[str, str], list[float]] = {(u, s): [] for u in units for s in stats}
    for _ in range(resamples):
        idx = [rng.next() % n for _ in range(n)]
        for u, cols in columns.items():
            sums = [sum(map(c.__getitem__, idx)) for c in cols]
            for s, fn in stats.items():
                v = fn(sums)
                if v is not None:
                    values[(u, s)].append(v)
    out = {}
    for key, vs in values.items():
        vs.sort()
        out[key] = (vs[int(0.025 * len(vs))], vs[int(0.975 * len(vs)) - 1]) if vs else None
    return out
