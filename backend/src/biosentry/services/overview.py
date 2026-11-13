"""The admin's read-only ward overview. The caller's role is checked by the route on every call;
nothing here reads a name, age, sex, vital, lab or note into the response."""

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from biosentry.core.audit import AuditLog
from biosentry.repositories.postgres import PostgresStore
from biosentry.schemas import (
    OverviewActivity,
    OverviewClinician,
    OverviewPatient,
    OverviewSimStatus,
    WardOverview,
)

from .ward import WardService

ACTIVITY_WINDOW = timedelta(hours=24)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sim_status(view: dict[str, Any] | None) -> OverviewSimStatus | None:
    """The four things the status line reads, copied by key; None if any is missing or wrong."""
    shared = view.get("shared") if view else None
    if not isinstance(shared, dict):
        return None
    try:
        return OverviewSimStatus.model_validate(
            {
                **{k: shared.get(k) for k in ("clockS", "speed", "ended")},
                "counts": _counts(shared.get("counts")),
            }
        )
    except ValidationError:
        return None


def _counts(counts: Any) -> Any:
    if not isinstance(counts, dict):
        return None
    return {k: counts.get(k) for k in ("received", "rejected", "abstained")}


class OverviewService:
    def __init__(self, store: PostgresStore, audit: AuditLog, wards: WardService) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards

    def overview(self, admin_id: str) -> WardOverview:
        store = self._store
        ids = store.on_ward()
        ranked, abstained = self._wards.compute(ids)
        care = store.care(ids)
        now = store.clock()
        patients = [
            *(
                OverviewPatient(
                    bed=r.bed, state="ranked", score=r.score, reason=None,
                    care=care[r.id], synthetic=r.synthetic,
                )
                for r in ranked
            ),
            *(
                OverviewPatient(
                    bed=a.bed, state="not_ranked", score=None, reason=a.reason,
                    care=care[a.id], synthetic=a.synthetic,
                )
                for a in abstained
            ),
        ]
        counts = [c for p in patients for c in p.care]
        view = store.live_view()
        activity = self._audit.recent(now - ACTIVITY_WINDOW)
        self._audit.append(admin_id, "view_overview", store.ward, "ok")
        return WardOverview(
            ward=store.ward,
            as_of=_iso(now),
            receiving_data=store.receiving_data(),
            synthetic=store.synthetic,
            sim=_sim_status(view),
            patients=patients,
            clinicians=[
                OverviewClinician(id=i, role=role, patients=counts.count(i))
                for i, role in store.clinicians()
            ],
            activity=[OverviewActivity(**r) for r in activity],
        )
