"""Deterministic synthetic single-lead ECG with exact ground truth.

Stands in for the CP3b generator until it lands. Not a physiological simulator: a sum of
Gaussians per beat on top of baseline wander and white noise, which is enough to hand the
detectors exact R-peak times to be scored against. Everything comes from one seed.
"""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class SynthEcg:
    fs: float
    samples: tuple[float, ...]
    r_times_s: tuple[float, ...]  # truth: every R peak
    ectopic: tuple[bool, ...]  # truth: that beat is a premature ventricular beat


# (offset s, sigma s, amplitude mV) of the P, Q, R, S and T waves of a normal beat
_NORMAL = (
    (-0.16, 0.025, 0.12),
    (-0.025, 0.010, -0.12),
    (0.0, 0.012, 1.0),
    (0.028, 0.012, -0.2),
    (0.26, 0.055, 0.30),
)
_PVC = ((0.0, 0.028, 1.3), (0.30, 0.07, -0.35))  # wide, tall, no P, inverted T


def synth_ecg(
    *,
    fs: float = 250.0,
    duration_s: float = 120.0,
    hr_bpm: float = 70.0,
    rr_sd_s: float = 0.03,
    noise_sd_mv: float = 0.02,
    wander_mv: float = 0.1,
    pvc_after_s: tuple[float, ...] = (),
    irregular: bool = False,
    seed: int = 0,
) -> SynthEcg:
    rng = np.random.default_rng(seed)
    mean_rr = 60.0 / hr_bpm
    times: list[float] = []
    ectopic: list[bool] = []
    pending = sorted(pvc_after_s)
    t = 0.6
    while t < duration_s - 0.6:
        rr = (
            float(rng.uniform(0.4, 1.4))
            if irregular
            else float(np.clip(rng.normal(mean_rr, rr_sd_s), 0.4, 1.6))
        )
        if pending and t >= pending[0]:
            pending.pop(0)
            times += [t + 0.62 * rr, t + 2.0 * rr]  # premature beat, then the compensatory pause
            ectopic += [True, False]
            t += 2.0 * rr
        else:
            times.append(t)
            ectopic.append(False)
            t += rr
    n = round(duration_s * fs)
    axis = np.arange(n) / fs
    x = wander_mv * np.sin(2 * np.pi * 0.3 * axis + 1.0) + rng.normal(0.0, noise_sd_mv, n)
    for tb, is_pvc in zip(times, ectopic, strict=True):
        for off, sigma, amp in _PVC if is_pvc else _NORMAL:
            x += amp * np.exp(-0.5 * ((axis - (tb + off)) / sigma) ** 2)
    return SynthEcg(fs, tuple(float(v) for v in x), tuple(times), tuple(ectopic))


def f1_within(truth: tuple[float, ...], found: tuple[float, ...], tol_s: float = 0.04) -> float:
    """F1 of detections against truth, each truth beat matched at most once within tol_s."""
    used: set[int] = set()
    tp = 0
    for tt in truth:
        best, best_d = None, tol_s
        for j, ft in enumerate(found):
            d = abs(ft - tt)
            if j not in used and d <= best_d:
                best, best_d = j, d
        if best is not None:
            used.add(best)
            tp += 1
    if tp == 0:
        return 0.0
    precision, recall = tp / len(found), tp / len(truth)
    return 2 * precision * recall / (precision + recall)


def ecg_with_premature_beats(
    pvc_after: tuple[int, ...],
    n_beats: int = 130,
    rr: float = 0.85,
    seed: int = 1,
    fs: float = 250.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sinus rhythm with PVCs that arrive 0.6 RR after the previous beat and take the place of the
    next sinus beat (the full compensatory pause): returns the signal (mV), the beat positions
    in samples and the symbols ("N" or "V").

    Unlike `synth_ecg`, whose ectopic beat comes late, these are truly premature: the interval
    before is 0.6 RR and the one after 1.4 RR, so a prematurity rule has something to find."""
    sinus = 0.6 + rr * np.arange(n_beats)
    times, symbols = [], []
    for k, t in enumerate(sinus):
        if k in pvc_after:
            times.append(sinus[k - 1] + 0.6 * rr)
            symbols.append("V")
        else:
            times.append(t)
            symbols.append("N")
    duration = float(sinus[-1] + 1.0)
    axis = np.arange(round(duration * fs)) / fs
    rng = np.random.default_rng(seed)
    x = rng.normal(0.0, 0.02, axis.size) + 0.1 * np.sin(2 * np.pi * 0.3 * axis)
    for tb, symbol in zip(times, symbols, strict=True):
        for off, sigma, amp in _PVC if symbol == "V" else _NORMAL:
            x += amp * np.exp(-0.5 * ((axis - (tb + off)) / sigma) ** 2)
    return x, np.array(times) * fs, np.array(symbols)
