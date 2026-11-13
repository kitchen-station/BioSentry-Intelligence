"""Writes: notes, lab entry, admission, nurse-recorded vitals and acknowledgements, all scoped and audited.

Nothing here clamps or fills in a value. A number that cannot be a real reading is rejected
with `RejectedError`; an omitted field stays as it was. A write is stored through the store
(one transaction) and the score, rank and reasons come from the real arbiter on the next read:
`record_vitals` reports the result in its response.
"""

from biosentry.core.audit import AuditLog
from biosentry.repositories.postgres import DEVICE_VITALS, Obs, PostgresStore, iso, whole
from biosentry.schemas import (
    AbstainedRow,
    AdmitPatientInput,
    BloodPressure,
    ClinicalNote,
    LabPanel,
    NewNote,
    NurseVitalsInput,
    PartialLabInput,
    VitalsRecorded,
)

from .ward import WardService

# Bounds on a nurse-typed blood pressure: physical, not clinical. The floor is deliberately
# low: a true critical reading such as 45/20 must be recordable, because refusing it would
# leave the patient silently NOT RANKED, which is worse than accepting a typo (a mistyped low
# value scores 3 and over-alarms; it cannot hide a patient). Zero is "missing" (the scorer's
# zero-fill guard), so it is always rejected.
SYSTOLIC_RANGE = (20, 300)
DIASTOLIC_RANGE = (10, 200)
# judgement: the same physical, not clinical, bounds for the device vitals a nurse types in when
# there is no patch. The floor is low so RR 4 or SpO2 70 can be recorded; zero is always refused.
HEART_RATE_RANGE = (1, 300)
RESPIRATORY_RATE_RANGE = (1, 100)
SPO2_RANGE = (1, 100)
TEMPERATURE_RANGE = (20.0, 45.0)


class RejectedError(ValueError):
    """The input cannot be a real reading. The caller gets a 422; nothing is stored."""


class ConflictError(Exception):
    """The ward's state refuses this write (409). Nothing was written."""


def check_number(name: str, value: float, bounds: tuple[float, float]) -> None:
    low, high = bounds
    if not low <= value <= high:
        raise RejectedError(f"{name} {value} is outside {low}-{high}")


def check_blood_pressure(systolic: int, diastolic: int) -> BloodPressure:
    """The bounds for any recorded blood pressure. Out of range is rejected, never clamped."""
    low, high = SYSTOLIC_RANGE
    if not low <= systolic <= high:
        raise RejectedError(f"systolic {systolic} is outside {low}-{high}")
    low, high = DIASTOLIC_RANGE
    if not low <= diastolic <= high:
        raise RejectedError(f"diastolic {diastolic} is outside {low}-{high}")
    if diastolic >= systolic:
        raise RejectedError("diastolic must be below systolic")
    return BloodPressure(systolic=systolic, diastolic=diastolic)


def split_blood_pressure(text: str) -> BloodPressure:
    """ "120/80" -> {120, 80}, once, at the input boundary. Implausible values are rejected."""
    systolic_text, _, diastolic_text = text.partition("/")
    if not (systolic_text.isdigit() and diastolic_text.isdigit()):
        raise RejectedError("blood pressure must be written systolic/diastolic")
    return check_blood_pressure(int(systolic_text), int(diastolic_text))


class PatientService:
    def __init__(self, store: PostgresStore, audit: AuditLog, wards: WardService) -> None:
        self._store = store
        self._audit = audit
        self._wards = wards

    def _visible(self, clinician_id: str, patient_id: str, action: str) -> bool:
        if patient_id in self._store.assigned(clinician_id):
            return True
        self._audit.append(clinician_id, action, patient_id, "not_found")
        return False

    # --- notes -----------------------------------------------------------------------------

    def notes(self, clinician_id: str, patient_id: str) -> list[ClinicalNote] | None:
        if patient_id not in self._wards.visible(clinician_id):  # an admin reads, never writes
            self._audit.append(clinician_id, "view_notes", patient_id, "not_found")
            return None
        self._audit.append(clinician_id, "view_notes", patient_id, "ok")
        return self._store.notes(patient_id)

    def add_note(self, clinician_id: str, patient_id: str, body: NewNote) -> ClinicalNote | None:
        if not self._visible(clinician_id, patient_id, "add_note"):
            return None
        with self._store.tx():
            self._store.add_action("add_note", clinician_id, patient_id, {})  # never the text
            note = self._store.add_note(patient_id, body.author, body.role, body.content)
        self._audit.append(clinician_id, "add_note", patient_id, "ok")
        return note

    def delete_note(self, clinician_id: str, patient_id: str, note_id: str) -> bool:
        if not self._visible(clinician_id, patient_id, "delete_note"):
            return False
        self._store.delete_note(patient_id, note_id)
        self._audit.append(clinician_id, "delete_note", patient_id, "ok")
        return True

    # --- acknowledgements ------------------------------------------------------------------

    def acknowledgements(self, clinician_id: str) -> list[dict[str, object]]:
        self._audit.append(clinician_id, "list_acknowledgements", "-", "ok")
        return self._store.acknowledgements(clinician_id)

    def acknowledge(self, clinician_id: str, patient_id: str, score: int) -> bool:
        if not self._visible(clinician_id, patient_id, "acknowledge"):
            return False
        with self._store.tx():
            self._store.add_action("acknowledge", clinician_id, patient_id, {"score": score})
            self._store.acknowledge(clinician_id, patient_id, score)
        self._audit.append(clinician_id, "acknowledge", patient_id, "ok")
        return True

    # --- labs ------------------------------------------------------------------------------

    def enter_labs(
        self, clinician_id: str, patient_id: str, labs: PartialLabInput
    ) -> LabPanel | None:
        if not self._visible(clinician_id, patient_id, "enter_labs"):
            return None
        entered = {k: v for k, v in labs.model_dump().items() if v is not None}
        for name, value in entered.items():
            if value < 0:
                self._audit.append(clinician_id, "enter_labs", patient_id, "rejected")
                raise RejectedError(f"{name} cannot be negative")
        now = self._store.run_now
        self._store.record(Obs(patient_id, k, now, "lab", num=v) for k, v in entered.items())
        self._audit.append(clinician_id, "enter_labs", patient_id, "ok")
        state = self._store.read(patient_id)
        assert state is not None
        return state.labs

    # --- admission -------------------------------------------------------------------------

    def admit(self, clinician_id: str, form: AdmitPatientInput) -> AbstainedRow:
        """Admit a patient: NOT RANKED until every NEWS2 input is recorded, by a patch or a nurse.

        Kept: name, age, sex, bed, admitting context, and the nurse-entered blood pressure
        (split once, structured) and ACVPU. Accepted but NOT stored in this build: date of
        birth, civil status, contact number, PhilHealth PIN, route, condition on admission,
        chief complaint, attending physician, allergies, weight and height. The PIN is
        sensitive personal information (RA 10173): when it is persisted it must be stored
        ciphertext-only and never logged (W5).
        """
        bp: BloodPressure | None = None
        if form.initial_bp is not None:
            try:
                bp = split_blood_pressure(form.initial_bp)
            except RejectedError:
                self._audit.append(clinician_id, "admit_patient", "-", "rejected")
                raise
        store = self._store
        now = store.run_now
        try:
            with store.tx():
                patient_id = store.next_patient_id()  # takes the run lock: the check below holds
                if store.bed_occupied(form.bed):
                    raise ConflictError(f"Bed {form.bed} is occupied")
                store.add_patient(
                    patient_id,
                    clinician_id,
                    {
                        "name": f"{form.last_name}, {form.first_name}",
                        "age": form.age,
                        "sex": form.sex.value,
                        "bed": form.bed,
                        "admitting_context": form.admitting_context,
                        "mechanism": "Sorted on NEWS2 (Tier A) once every input is recorded, by a patch or a nurse.",
                        "supporting": "",
                        "trend": "insufficient",
                        "admission": {"admitted_at": iso(now), "admitted_by": clinician_id},
                        "admitted_at": whole(now),
                    },
                )

                # No patch yet, so no device data. Each missing input says why (I1).
                def missing(code: str, source: str, why: str) -> Obs:
                    return Obs(patient_id, code, now, source, "missing", absent_reason=why)

                obs = [missing(c, "device", "no_patch") for c in DEVICE_VITALS]
                obs += [
                    missing(c, "nurse", "not_recorded") for c in ("supplemental_o2", "spo2_scale")
                ]
                obs.append(
                    missing("blood_pressure", "nurse", "not_measured")
                    if bp is None
                    else Obs(
                        patient_id,
                        "blood_pressure",
                        now,
                        "nurse",
                        num=bp.systolic,
                        num2=bp.diastolic,
                    )
                )
                obs.append(
                    missing("consciousness", "nurse", "not_measured")
                    if form.initial_avpu is None
                    else Obs(patient_id, "consciousness", now, "nurse", txt=form.initial_avpu.value)
                )
                store.record(obs)
                action: dict[str, object] = {
                    "name": f"{form.last_name}, {form.first_name}",
                    "age": form.age,
                    "sex": form.sex.value,
                    "bed": form.bed,
                    "context": form.admitting_context,
                }
                if bp is not None:
                    action |= {"systolic": bp.systolic, "diastolic": bp.diastolic}
                if form.initial_avpu is not None:
                    action["acvpu"] = form.initial_avpu.value
                store.add_action("admit_patient", clinician_id, patient_id, action)
        except ConflictError:  # audited after the rollback, or the row would go with it
            self._audit.append(clinician_id, "admit_patient", "-", "rejected")
            raise
        self._audit.append(clinician_id, "admit_patient", patient_id, "ok")
        _, abstained = self._wards.compute(frozenset({patient_id}))
        return next(a for a in abstained if a.id == patient_id)

    # --- nurse-recorded vitals ---------------------------------------------------------------

    def record_vitals(
        self, clinician_id: str, patient_id: str, body: NurseVitalsInput
    ) -> VitalsRecorded | None:
        """Record nurse-entered NEWS2 inputs and report where the patient now stands.

        Bounds are physical, not clinical, and are never clamped. The recorded measurement is
        echoed with its provenance; the rank is relative to the caller's own set.
        """
        if not self._visible(clinician_id, patient_id, "record_vitals"):
            return None
        device = {
            "heart_rate": (body.heart_rate, HEART_RATE_RANGE),
            "respiratory_rate": (body.respiratory_rate, RESPIRATORY_RATE_RANGE),
            "spo2": (body.spo2, SPO2_RANGE),
            "temperature": (body.temperature, TEMPERATURE_RANGE),
        }
        try:
            bp = (
                check_blood_pressure(body.systolic, body.diastolic)
                if body.systolic is not None and body.diastolic is not None
                else None
            )
            for name, (value, bounds) in device.items():
                if value is not None:
                    check_number(name, value, bounds)
            if body.temperature is not None and round(body.temperature, 1) != body.temperature:
                raise RejectedError("temperature takes one decimal place")
        except RejectedError:
            self._audit.append(clinician_id, "record_vitals", patient_id, "rejected")
            raise

        now = self._store.run_now

        obs: dict[str, Obs] = {
            name: Obs(patient_id, name, now, "nurse", num=float(value))
            for name, (value, _) in device.items()
            if value is not None
        }
        if bp is not None:
            obs["blood_pressure"] = Obs(
                patient_id, "blood_pressure", now, "nurse", num=bp.systolic, num2=bp.diastolic
            )
        if body.acvpu is not None:
            obs["consciousness"] = Obs(
                patient_id, "consciousness", now, "nurse", txt=body.acvpu.value
            )
        if body.on_oxygen is not None:
            obs["supplemental_o2"] = Obs(
                patient_id, "supplemental_o2", now, "nurse", num=float(body.on_oxygen)
            )
        if body.spo2_scale is not None:
            obs["spo2_scale"] = Obs(
                patient_id, "spo2_scale", now, "nurse", num=float(body.spo2_scale)
            )
        with self._store.tx():
            self._store.add_action(
                "record_vitals",
                clinician_id,
                patient_id,
                body.model_dump(mode="json", exclude_none=True),  # only what was entered
            )
            self._store.record(obs.values())
        self._audit.append(clinician_id, "record_vitals", patient_id, "ok")

        state = self._store.read(patient_id)
        assert state is not None
        echo = {
            code: getattr(state.vitals, code) if code in obs else None
            for code in (
                *DEVICE_VITALS,
                "blood_pressure",
                "consciousness",
                "supplemental_o2",
                "spo2_scale",
            )
        }
        ranked, abstained = self._wards.compute(self._store.assigned(clinician_id))
        row = next((r for r in ranked if r.id == patient_id), None)
        not_ranked = next((a for a in abstained if a.id == patient_id), None)
        return VitalsRecorded(
            patient_id=patient_id,
            **echo,
            ranked=row is not None,
            rank=row.rank if row is not None else None,
            score=row.score if row is not None else None,
            not_ranked_reason=not_ranked.reason if not_ranked is not None else None,
            not_ranked_instruction=not_ranked.instruction if not_ranked is not None else None,
        )
