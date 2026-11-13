"""A patient's change from their own baseline, with the numbers behind it. Read-only: it stores
nothing. The ward's arbiter reads the same finding (`scoring.drift`) as a tie-break and a reason.
Who may see a patient is the patient route's rule: assigned to the caller and still on the ward
(the ward service's `visible`), and ranked (the same `decide` the patient route ranks with: a
NOT RANKED patient has no detail page, so no baseline either). Every call writes one
`view_baseline` audit row, on both paths.

The baseline window is the first 24 valid bins from admission when it is known, from the first
stored bin when it is not (every patient present before the run), and from the oldest bin still
kept once the server's 7-day prune or the 672-bin cap has dropped earlier ones: a long stay is
baselined on mid-stay data. `baseline_from`, `anchored_at` and `admission_known` say which. `latest`
is the latest 15-minute median bin, not a single reading."""

from datetime import timedelta

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import NotRanked
from biosentry.domain.computed import NotComputed
from biosentry.domain.scoring.tier_b.baseline import DEFAULTS, VITALS, baseline_finding
from biosentry.repositories.postgres import PostgresStore
from biosentry.schemas.baseline import (
    BASELINE_LABEL,
    BaselineComputed,
    BaselineForming,
    BaselineResponse,
    BaselineUnavailable,
    BaselineUsed,
    BaselineVital,
)

from .scoring import BIN, MAX_BINS, counted, decide, grid  # noqa: F401 (re-exported)
from .ward import WardService

ADMISSION_SLACK = timedelta(minutes=30)  # a window starting this close to admission is "at admission"
ORDER: tuple[BaselineVital, ...] = ("heart_rate", "respiratory_rate", "spo2", "temperature")


class BaselineService:
    def __init__(self, store: PostgresStore, audit: AuditLog, wards: WardService) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards

    def baseline(self, clinician_id: str, patient_id: str) -> BaselineResponse | None:
        found = self.compute(clinician_id, patient_id)
        if found is None:
            self._audit.append(clinician_id, "view_baseline", patient_id, "not_found")
            return None
        self._audit.append_view(clinician_id, "view_baseline", patient_id)
        return found

    def compute(self, clinician_id: str, patient_id: str) -> BaselineResponse | None:
        """The baseline, or None when the caller may not see the patient or they are NOT RANKED.
        Writes no audit row: the caller audits."""
        state = (
            self._store.read(patient_id)
            if patient_id in self._wards.visible(clinician_id)
            else None
        )
        if state is None or isinstance(decide(state), NotRanked):
            return None
        now = self._store.now()  # the clock `last_update_s` is measured on
        admitted_at = state.admitted_at
        vitals: list[BaselineComputed | BaselineForming | BaselineUnavailable] = []
        for vital in ORDER:
            # Temperature has no full history in the patient read, so it has no baseline here.
            points = getattr(state.vital_history, vital, None)
            if points is None:
                vitals.append(
                    BaselineUnavailable(vital=vital, status="unavailable", reason="no_history")
                )
                continue
            if points and not counted(state, vital):  # the row's own rule: no flag, no panel
                vitals.append(
                    BaselineUnavailable(vital=vital, status="unavailable", reason="patch_not_live")
                )
                continue
            bins, start = grid(points, admitted_at, vital)
            found = baseline_finding(vital, bins)
            if isinstance(found, NotComputed):
                valid = sum(x is not None for x in bins)
                vitals.append(
                    BaselineForming(
                        vital=vital,
                        status="forming",
                        bins_valid=valid,
                        bins_needed=DEFAULTS.window_bins,
                    )
                    if valid < DEFAULTS.window_bins
                    else BaselineUnavailable(
                        vital=vital, status="unavailable", reason="latest_bin_missing"
                    )
                )
                continue
            finding, dev = found
            assert start is not None  # a computed baseline has at least one bin
            window_start = next(i for i, x in enumerate(bins) if x is not None)
            first = start - BIN  # a stored bin_at is the window's END (model_signals, THE BIN); we serve the START
            baseline_from = first + window_start * BIN
            latest_at = first + (len(bins) - 1) * BIN
            vitals.append(
                BaselineComputed(
                    vital=vital,
                    status="computed",
                    baseline_median=round(dev.baseline_median, 1),
                    latest=dev.value,
                    latest_at=latest_at,
                    latest_age_s=max(0, int((now - (latest_at + BIN)).total_seconds())),
                    baseline_from=baseline_from,
                    anchored_at=(
                        "admission"
                        if admitted_at is not None and baseline_from - admitted_at <= ADMISSION_SLACK
                        else "first_kept_bin"
                    ),
                    delta=round(dev.delta, 1),
                    z=round(dev.z, 1),
                    band_low=round(dev.baseline_median - DEFAULTS.k * dev.spread, 1),
                    band_high=round(dev.baseline_median + DEFAULTS.k * dev.spread, 1),
                    run_bins=dev.run_bins,
                    since=None if dev.since_bin is None else first + dev.since_bin * BIN,
                    significant=finding.significant,
                    direction=finding.direction,
                    pathway=finding.pathway,
                    unit=VITALS[vital].unit,
                )
            )
        return BaselineResponse(
            patient_id=patient_id,
            synthetic=state.synthetic,
            admission_known=admitted_at is not None,
            label=BASELINE_LABEL,
            params=BaselineUsed(window_bins=DEFAULTS.window_bins, k=DEFAULTS.k, p=DEFAULTS.p),
            vitals=vitals,
        )
