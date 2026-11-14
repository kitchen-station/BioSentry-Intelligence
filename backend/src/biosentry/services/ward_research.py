"""The ward as one research table for a physician or charge nurse. Every number comes from code that
already serves it (the ward's own ranking, the baseline, the stay history's bins); nothing is
derived here. Scope is decided here: every patient of the ward is a row, and a patient who is not
the reader's is a bed label and numbers only. Each row is built by naming its fields. Writes one
audit row, with no patient in it."""

from collections.abc import Callable

from biosentry.core.audit import AuditLog
from biosentry.domain.arbiter import Ranked
from biosentry.repositories.postgres import PostgresStore
from biosentry.schemas import BaselineComputed, BaselineFlagged, UnscoredPatient
from biosentry.schemas.research_view import (
    RESEARCH_LABEL,
    AlertCounted,
    AlertUnavailable,
    ResearchAbstained,
    ResearchRow,
    WardResearch,
)

from .baseline import BaselineService
from .scoring import BIN, decide
from .ward import WardService


def flag_only(v: BaselineComputed) -> BaselineFlagged:
    """What a reader may see of a patient who is not theirs: the flag and its direction, no value."""
    return BaselineFlagged(vital=v.vital, status="flag_only", significant=v.significant, direction=v.direction)


class WardResearchService:
    def __init__(
        self,
        store: PostgresStore,
        audit: AuditLog,
        wards: WardService,
        baselines: BaselineService,
        alert: Callable[[UnscoredPatient], tuple[int, bool] | str],  # the stay history's alert bins
    ) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards
        self._baselines = baselines
        self._alert = alert

    def research(self, clinician_id: str) -> WardResearch:
        mine = self._wards.visible(clinician_id)
        ranked, abstained, states = self._wards.view(self._store.on_ward())  # the ward's own rank, whole ward
        rows = []
        for r in ranked:
            state = states[r.id]
            decision = decide(state)
            if not isinstance(decision, Ranked):  # the row was ranked from this same state
                raise RuntimeError("a ranked row has no NEWS2 decision")
            found = self._alert(state)
            alert = (
                AlertUnavailable(status="unavailable", reason=found)  # type: ignore[arg-type]
                if isinstance(found, str)
                else AlertCounted(
                    status="counted", bins=found[0], minutes=found[0] * int(BIN.total_seconds() // 60), ongoing=found[1]
                )
            )
            shown = r.id in mine
            vitals = [
                v if shown or not isinstance(v, BaselineComputed) else flag_only(v)  # the numbers are for the reader's own
                for v in self._baselines.of(state).vitals
            ]
            rows.append(
                ResearchRow(
                    bed=r.bed, assigned=shown, id=r.id if shown else None, name=r.name if shown else None,
                    rank=r.rank, score=r.score, level=decision.news2.risk.value,  # type: ignore[arg-type]
                    baseline=vitals, models=r.models, alert=alert,
                )
            )  # fmt: skip
        self._audit.append(clinician_id, "view_ward_research", self._store.ward, "ok")
        return WardResearch(
            ward=self._store.ward, now=self._store.now(), synthetic=True, label=RESEARCH_LABEL, ranked=rows,
            abstained=[
                ResearchAbstained(
                    bed=a.bed, assigned=a.id in mine, id=a.id if a.id in mine else None,
                    name=a.name if a.id in mine else None, reason=a.reason, instruction=a.instruction,
                )
                for a in abstained
            ],
        )
