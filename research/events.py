"""True deterioration episodes as evaluation events (research-models.md sections 0 and 4).

The event is the generator's episode and nothing else. `outcome` / "higher care" is decided by
NEWS2 inside the sim, so it is never an event here.
"""

from dataclasses import dataclass

from .trials import BIN_MINUTES, Trial

TAIL_MINUTES = 120  # an alert counts until two hours after the episode is fully in

STARTING = "starting patient (admit minute 0)"
AFTER_TRIAL = "start after the trial ended"
OUTSIDE_STAY = "start outside the patient's stay"


@dataclass(frozen=True, slots=True)
class Window:
    patient: str
    kind: str
    admit: int
    start: float
    full_in: float  # start + ramp: the offset is fully in
    end: float  # last minute at which an alert counts
    stay_end: int  # leave minute, or the trial end, whichever is first

    @property
    def cut(self) -> bool:
        return self.end > self.stay_end


def windows(trial: Trial) -> tuple[list[Window], dict[str, int]]:
    """Evaluated episodes, and the dropped ones by reason."""
    trial_end = trial.days * 1440
    kept: list[Window] = []
    dropped = {STARTING: 0, AFTER_TRIAL: 0, OUTSIDE_STAY: 0}
    for e in trial.episodes:
        if e.kind is None:
            continue
        if e.admit_minute == 0:  # true arrival unknown, and one has a scripted worsening
            dropped[STARTING] += 1
            continue
        stay_end = trial_end if e.leave_minute is None else min(e.leave_minute, trial_end)
        if e.start >= trial_end:
            dropped[AFTER_TRIAL] += 1
        elif not e.admit_minute <= e.start < stay_end:
            dropped[OUTSIDE_STAY] += 1
        else:
            full_in = e.start + e.ramp_minutes
            kept.append(Window(e.patient, e.kind, e.admit_minute, e.start, full_in,
                               full_in + TAIL_MINUTES, stay_end))
    return kept, dropped


def control_windows(trial: Trial, wins: list[Window]) -> list[tuple[str, float, float]]:
    """The chance control: no random draws.

    Every evaluated episode's window (same offset from admission, same length) is laid on every
    admitted, non-starting patient of the same trial whose stay covers the whole window and in
    whose stay no episode begins: no episode at all, or one that starts only after the stay or
    the trial has ended. Returns (patient, start, end).
    """
    trial_end = trial.days * 1440
    out = []
    for e in trial.episodes:
        stay_end = trial_end if e.leave_minute is None else min(e.leave_minute, trial_end)
        if e.admit_minute == 0 or (e.kind is not None and e.start < stay_end):
            continue
        for w in wins:
            start = e.admit_minute + (w.start - w.admit)
            end = start + (w.end - w.start)
            if end <= stay_end:
                out.append((e.patient, start, end))
    return out


def expected_bins(admit: int, leave: int | None, days: int) -> int:
    """Bins the sim should have scored in the stay: from the first boundary at or after admission
    to the last boundary before leaving (no bin at the leave minute). Checked on four real trials:
    exact for 372 of 382 stays, one bin too many for the other 10 (a leave just after a boundary)."""
    first = max(BIN_MINUTES, -(-admit // BIN_MINUTES) * BIN_MINUTES)
    stop = days * 1440 - BIN_MINUTES if leave is None else min(leave - 1, days * 1440 - BIN_MINUTES)
    return max(0, (stop // BIN_MINUTES * BIN_MINUTES - first) // BIN_MINUTES + 1)


def stay_days(admit: int, leave: int | None, days: int) -> float:
    return ((days * 1440 if leave is None else min(leave, days * 1440)) - admit) / 1440
