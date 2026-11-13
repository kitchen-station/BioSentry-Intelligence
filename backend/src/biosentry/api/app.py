"""The HTTP API: the routes the frontend's http provider calls (frontend/src/api/providers/http.ts).

POST /session turns a clinician id and passphrase into a random bearer token (stored hashed,
expires in 12 hours). An unknown id, a clinician with no passphrase set and a wrong passphrase all
get the same 401, after the same work; five failures for an id within 15 minutes get 429 before
anything is hashed; every attempt is audited (ids and outcome, never the passphrase). Missing,
malformed, unknown or expired credentials get 401 and nothing is served. Identity comes first on
every route, including for a body that fails validation. Everything else is scoped to the
caller's patients (an admin: every patient on the ward, read-only) on the server, on every call, and a patient who does not exist and one who is
not yours get the same 404. A
write whose body fails validation leaves a `rejected` audit row (ids only, no values). Responses
are `Cache-Control: no-store`. The service serves nothing unauthenticated except /healthz and
POST /session: no docs or schema routes (the contract is frontend/contracts/openapi.json).
The ward simulation writes through /sim/*, gated by the X-Ingest-Key header (503 if unset). One
shared ward: a different client cannot start a run while the newest is live, and a batch for a run
that is no longer the newest is refused (both 409, nothing written). The sim reads what clinicians
did (acknowledgements, vitals, notes without their text, admissions) from /sim/runs/{id}/actions,
and what a clinician who may (an md or the charge nurse) asked of it from /sim/runs/{id}/commands.
The Simulation lab stores a clinician's request for a simulated compare run and its result; a lab
worker claims and reports through /sim/lab/* (ingest key). A clinician who may see a patient can
also ask one question about that patient: the API freezes an allow-listed copy of the patient's
data with the question, a worker answers from that copy only, and the clinician reads the answer.
A patient's stay can be read as a short history, rebuilt on read from stored bins: nothing is stored.
The API never runs anything.
"""

import hashlib
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from biosentry.core.audit import AuditLog, Clock, Outcome, utc_now
from biosentry.core.config import Settings, restricted
from biosentry.db.passphrase import Busy, verify
from biosentry.db.seed import ensure
from biosentry.repositories.postgres import Conn, PostgresStore, connect
from biosentry.schemas import (
    AbstainedRow,
    Acknowledgement,
    AdmitPatientInput,
    BaselineResponse,
    ClinicalNote,
    LabPanel,
    LabRunDetail,
    LabRunItem,
    NewAcknowledgement,
    NewLabRun,
    NewNote,
    NewPatientQuestion,
    NurseVitalsInput,
    PartialLabInput,
    PatientDetail,
    PatientHistory,
    PatientQuestionDetail,
    PatientQuestionItem,
    PatientStay,
    PatientSummary,
    SessionRequest,
    SessionRole,
    SessionToken,
    VitalsRecorded,
    WardOverview,
    WardResponse,
)
from biosentry.schemas.episodes import Episodes
from biosentry.schemas.ingest import (
    BIGINT_MAX,
    Actions,
    Batch,
    Commands,
    LiveView,
    LiveWard,
    NewCommand,
    NewRun,
)
from biosentry.schemas.lab import LabJob, LabReport, PatientQuestionJob
from biosentry.services import episodes, ingest, lab
from biosentry.services.baseline import BaselineService
from biosentry.services.history import HistoryService
from biosentry.services.overview import OverviewService
from biosentry.services.patient_ask import PatientAskService
from biosentry.services.patients import PatientService, RejectedError
from biosentry.services.models import load_models
from biosentry.services.ward import WardService

NOT_FOUND = "Not found"
SIGN_IN_FAILURES = 5  # failed sign-ins for one id within the window, then 429
SIGN_IN_WINDOW = timedelta(minutes=15)
# Audit rows cannot be deleted, and these two are written for callers nobody identified. At most one
# of each per minute (per id for a locked sign-in), so a flood cannot fill the disk. Every request
# is still refused; only the row is limited.
ANONYMOUS_ROW_EVERY = timedelta(minutes=1)

# The body-bearing write routes and the action their audit rows carry. A body that fails
# validation never reaches the handler, so its audit row is written by the validation handler.
WRITE_ACTIONS = {
    ("POST", "/patients"): "admit_patient",
    ("POST", "/patients/{patient_id}/notes"): "add_note",
    ("POST", "/patients/{patient_id}/labs"): "enter_labs",
    ("POST", "/patients/{patient_id}/vitals"): "record_vitals",
    ("POST", "/patients/{patient_id}/acknowledgement"): "acknowledge",
    ("POST", "/wards/{ward}/sim/commands"): "sim_command",
    ("POST", "/wards/{ward}/lab/runs"): "lab_request",
    ("POST", "/patients/{patient_id}/questions"): "ask_patient",
}
# Every write route that has no body or is not in WRITE_ACTIONS, so an admin is refused on it too.
REFUSED_ACTIONS = {
    **WRITE_ACTIONS,
    ("DELETE", "/patients/{patient_id}/notes/{note_id}"): "delete_note",
    ("POST", "/extract/labs"): "extract_labs",
    ("POST", "/extract/patient"): "extract_patient",
}
# Routes that are not behind a bearer token: sign-in, and the sim's ingest key. All but the sim's
# two reads and the lab claim carry a body.
UNAUTHENTICATED_WRITES = {
    ("POST", "/session"),
    ("POST", "/sim/runs"),
    ("POST", "/sim/runs/{run_id}/batch"),
    ("POST", "/sim/runs/{run_id}/view"),
    ("POST", "/sim/runs/{run_id}/episodes"),
    ("GET", "/sim/runs/{run_id}/actions"),
    ("GET", "/sim/runs/{run_id}/commands"),
    ("POST", "/sim/lab/claim"),
    ("POST", "/sim/lab/runs/{lab_run_id}/report"),
}
LAB_RUN_ID = re.compile(r"[1-9][0-9]{0,18}")


def row_id(text: str) -> int | None:
    """The id a path names, or None when it is not one the database can hold."""
    return int(text) if LAB_RUN_ID.fullmatch(text) and int(text) <= BIGINT_MAX else None


@dataclass(slots=True)
class Context:
    """One request: its connection and the services built on it."""

    conn: Conn
    store: PostgresStore
    audit: AuditLog
    wards: WardService
    patients: PatientService
    baselines: BaselineService
    history: HistoryService
    overview: OverviewService
    questions: PatientAskService

    def close(self) -> None:
        self.conn.close()


class AuditEntry(BaseModel):
    """What a clinician may read back about their own activity: ids and outcomes only."""

    seq: int
    at: str
    action: str
    resource: str
    outcome: str


def context(request: Request) -> Iterator[Context]:
    ctx: Context = request.app.state.open_ctx()
    try:
        yield ctx
    finally:
        ctx.close()


Ctx = Annotated[Context, Depends(context)]


def identify(ctx: Context, authorization: str | None) -> str:
    """The clinician a live session token names. Otherwise 401, audited without an identity."""
    scheme, _, token = (authorization or "").partition(" ")
    who = ctx.store.clinician_for(token.strip()) if scheme.lower() == "bearer" else None
    if who is None:
        since = ctx.store.clock() - ANONYMOUS_ROW_EVERY
        if not ctx.audit.count("authenticate", "-", "denied", since):
            ctx.audit.append(None, "authenticate", "-", "denied")
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return who


def clinician(
    ctx: Ctx,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    who = identify(ctx, authorization)
    # Identity first. Then a path parameter with a NUL names nothing (and PostgreSQL text could
    # not hold it): the same 404 as any other missing resource, audited with the NUL dropped.
    for value in request.path_params.values():
        if "\x00" in str(value):
            ctx.audit.append(who, "request", str(value), "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
    return who


Who = Annotated[str, Depends(clinician)]


def refuse_admin(ctx: Context, who: str, request: Request) -> None:
    """An admin writes nothing: 403 and one `denied` row, before any handler or body check."""
    if ctx.store.is_admin(who):
        route = request.scope["route"]
        params = request.path_params
        resource = str(params.get("patient_id") or params.get("ward") or "-")
        ctx.audit.append(who, REFUSED_ACTIONS[(request.method, route.path)], resource, "denied")
        raise HTTPException(status_code=403, detail="Forbidden")


def writer(ctx: Ctx, request: Request, who: Who) -> str:
    refuse_admin(ctx, who, request)
    return who


# One known order exception: a path with a NUL byte is refused as 404 `request not_found` in
# `clinician`, before this admin check, and writes nothing.
Writer = Annotated[str, Depends(writer)]


def unprocessable(exc: RequestValidationError) -> JSONResponse:
    """FastAPI's 422 body without `input` and `ctx`: a rejected value (a PIN) is never echoed."""
    errors = [{k: v for k, v in e.items() if k not in ("input", "ctx")} for e in exc.errors()]
    for e in errors:
        if e["type"] == "union_tag_invalid":  # pydantic's message quotes the tag the caller sent
            e["msg"] = "Input tag is not one of the expected tags"
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def create_app(settings: Settings | None = None, clock: Clock = utc_now) -> FastAPI:
    """Build the app. `clock` is the wall clock (tests inject one)."""
    settings = settings or Settings.from_env()
    production = settings.env == "production" or restricted()
    ensure(settings.database_url, settings.dataset, production)
    models = load_models(settings.models_dir)  # once; a refused file is "not computed" and one warning

    def open_ctx() -> Context:
        conn = connect(settings.database_url, production)
        try:  # a connection that cannot become a Context (no run, say) is not left open
            store = PostgresStore(conn, clock)
            audit = AuditLog(conn, clock, store.role_of)
            wards = WardService(store, audit, models)
            baselines = BaselineService(store, audit, wards)
            return Context(
                conn,
                store,
                audit,
                wards,
                PatientService(store, audit, wards),
                baselines,
                HistoryService(store, audit, wards),
                OverviewService(store, audit, wards),
                PatientAskService(store, audit, wards, baselines),
            )
        except BaseException:
            conn.close()
            raise

    def ingest_gate(ctx: Ctx, x_ingest_key: Annotated[str | None, Header()] = None) -> None:
        """Fails closed: no key configured, no ingest."""
        # ponytail: one shared static key. Upgrade: a key per sim, rotated, or mTLS.
        if settings.ingest_key is None:
            ctx.audit.append(None, "ingest", "-", "denied")
            raise HTTPException(status_code=503, detail="Ingest is not configured")
        given = (x_ingest_key or "").encode()
        if not secrets.compare_digest(given, settings.ingest_key.encode()):
            ctx.audit.append(None, "ingest", "-", "denied")
            raise HTTPException(status_code=403, detail="Forbidden")

    def lab_kind(request: Request, kind: lab.Kind = "compare") -> lab.Kind:
        """At most one `kind`: FastAPI would take the last of two without a word."""
        if len(request.query_params.getlist("kind")) > 1:
            raise RequestValidationError(
                [{"type": "too_long", "loc": ("query", "kind"), "msg": "Give kind once"}]
            )
        return kind

    app = FastAPI(
        title="BioSentry Intelligence API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.open_ctx = open_ctx
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Ingest-Key"],
    )

    @app.middleware("http")
    async def no_store(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ingest.ConflictError)
    async def conflict(_: Request, exc: ingest.ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(Busy)
    async def busy(_: Request, __: Busy) -> JSONResponse:
        # Raised inside the sign-in transaction: no audit row, and the thread is free again.
        return JSONResponse(
            status_code=503,
            content={"detail": "Sign-in is busy. Try again."},
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(RejectedError)
    async def rejected(_: Request, exc: RejectedError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> Response:
        route = request.scope.get("route")
        key = (request.method, getattr(route, "path", ""))
        if key in UNAUTHENTICATED_WRITES:
            return unprocessable(exc)
        # Identity first, as on every route: a body that is not JSON fails before the
        # dependencies run, and an unidentified caller must be told nothing about it.
        ctx = open_ctx()
        try:
            who = identify(ctx, request.headers.get("authorization"))
            if key in REFUSED_ACTIONS:  # an admin's bad body is refused, not explained
                refuse_admin(ctx, who, request)
            action = WRITE_ACTIONS.get(key)
            if action is not None:  # ids and outcome only: the rejected values are never logged
                resource = str(request.path_params.get("patient_id", "-"))
                ctx.audit.append(who, action, resource, "rejected")
        except HTTPException as denied:
            return JSONResponse(
                status_code=denied.status_code,
                content={"detail": denied.detail},
                headers=denied.headers,
            )
        finally:
            ctx.close()
        return unprocessable(exc)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/session", response_model=SessionToken)
    def sign_in(body: SessionRequest, ctx: Ctx) -> SessionToken:
        known, stored = ctx.store.passphrase_hash(body.clinician_id)
        # The audit resource: the id when it is a clinician, else a fixed-length digest of what was
        # sent (bounded, never an attacker's text). The limit counts by it, so an unknown id is
        # throttled exactly like a known one and a 429 does not say which is which.
        resource = (
            body.clinician_id
            if known
            else "unknown:" + hashlib.sha256(body.clinician_id.encode()).hexdigest()[:16]
        )
        with ctx.store.tx():
            # One attempt at a time per id, so a burst cannot all pass the count below before any
            # of it is written. Released at commit, after the audit row is in.
            ctx.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("sign_in:" + resource,)
            )
            failures = ctx.audit.count(
                "sign_in", resource, "denied", ctx.store.clock() - SIGN_IN_WINDOW
            )
            if failures >= SIGN_IN_FAILURES:
                outcome: Outcome = "limited"  # before any hashing
            else:
                outcome = "ok" if verify(body.passphrase, stored) else "denied"
            since = ctx.store.clock() - ANONYMOUS_ROW_EVERY
            if outcome != "limited" or not ctx.audit.count("sign_in", resource, "limited", since):
                ctx.audit.append(body.clinician_id if known else None, "sign_in", resource, outcome)
            token = ctx.store.open_session(body.clinician_id) if outcome == "ok" else None
        if outcome == "limited":  # raised outside the transaction: the audit row must be kept
            raise HTTPException(
                status_code=429,
                detail="Too many failed sign-in attempts. Try again in 15 minutes.",
                headers={"Retry-After": str(int(SIGN_IN_WINDOW.total_seconds()))},
            )
        if token is None:
            raise HTTPException(status_code=401, detail="Sign-in failed")
        role = ctx.store.role_of(body.clinician_id)
        assert role is not None  # a token exists only for a known clinician
        return SessionToken(token=token, role=SessionRole(role))

    @app.get("/wards/{ward}/rows", response_model=WardResponse)
    def ward_rows(ward: str, ctx: Ctx, who: Who) -> WardResponse:
        if ward != ctx.store.ward:
            ctx.audit.append(who, "view_ward", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return ctx.wards.ward(who)

    @app.get("/wards/{ward}/stays", response_model=list[PatientStay])
    def ward_stays(ward: str, ctx: Ctx, who: Who) -> list[PatientStay]:
        if ward != ctx.store.ward:
            ctx.audit.append(who, "view_stays", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return ctx.wards.stays(who)

    @app.get("/wards/{ward}/overview", response_model=WardOverview)
    def ward_overview(ward: str, ctx: Ctx, who: Who) -> WardOverview:
        if not ctx.store.is_admin(who):  # on every call: the role is read from the database
            ctx.audit.append(who, "view_overview", ward, "denied")
            raise HTTPException(status_code=403, detail="Forbidden")
        if ward != ctx.store.ward:
            ctx.audit.append(who, "view_overview", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return ctx.overview.overview(who)

    @app.get("/wards/{ward}/live", response_model=LiveWard)
    def ward_live(ward: str, ctx: Ctx, who: Who) -> LiveWard:
        # The sim's whole shared view is for the ward's clinicians. An admin reads the ward's
        # patients through /rows and the patient pages, and gets this view's status keys through
        # the overview, nothing more.
        if ctx.store.is_admin(who):
            ctx.audit.append(who, "view_live", ward, "denied")
            raise HTTPException(status_code=403, detail="Forbidden")
        if ward != ctx.store.ward:
            ctx.audit.append(who, "view_live", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return ctx.wards.live(who)

    @app.post("/wards/{ward}/sim/commands", status_code=204)
    def send_sim_command(ward: str, body: NewCommand, ctx: Ctx, who: Writer) -> Response:
        if ward != ctx.store.ward:
            ctx.audit.append(who, "sim_command", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        if not ctx.store.can_control(who):
            ctx.audit.append(who, "sim_command", ward, "denied")
            raise HTTPException(status_code=403, detail="Forbidden")
        if not ingest.add_command(ctx.store, ctx.audit, who, body):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Response(status_code=204)

    @app.post("/wards/{ward}/lab/runs", response_model=LabRunItem, status_code=201)
    def request_lab_run(ward: str, body: NewLabRun, ctx: Ctx, who: Writer) -> LabRunItem:
        if ward != ctx.store.ward:
            ctx.audit.append(who, "lab_request", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        if not ctx.store.can_control(who):
            ctx.audit.append(who, "lab_request", ward, "denied")
            raise HTTPException(status_code=403, detail="Forbidden")
        made = lab.create(ctx.store, ctx.audit, who, body)
        if made is None:
            raise HTTPException(status_code=429, detail="Lab run limit reached")
        return made

    def lab_reader(ctx: Context, who: str, action: str, ward: str) -> None:
        """An admin reads no lab run (a row says who asked what): the same refusal as /live."""
        if ctx.store.is_admin(who):
            ctx.audit.append(who, action, ward, "denied")
            raise HTTPException(status_code=403, detail="Forbidden")
        if ward != ctx.store.ward:
            ctx.audit.append(who, action, ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)

    @app.get("/wards/{ward}/lab/runs", response_model=list[LabRunItem])
    def lab_runs(ward: str, ctx: Ctx, who: Who) -> list[LabRunItem]:
        lab_reader(ctx, who, "view_lab_runs", ward)
        ctx.audit.append_view(who, "view_lab_runs", ward)
        return lab.list_runs(ctx.store)

    @app.get("/wards/{ward}/lab/runs/{lab_run_id}", response_model=LabRunDetail)
    def lab_run(ward: str, lab_run_id: str, ctx: Ctx, who: Who) -> LabRunDetail:
        lab_reader(ctx, who, "view_lab_run", ward)
        found = None
        if (run_id := row_id(lab_run_id)) is not None:
            found = lab.get_run(ctx.store, run_id)
        if found is None:  # malformed and unknown are one answer, and audited
            ctx.audit.append(who, "view_lab_run", ward, "not_found")
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        ctx.audit.append(who, "view_lab_run", f"lab_run:{lab_run_id}", "ok")
        return found

    @app.get("/patients", response_model=list[PatientSummary])
    def list_patients(ctx: Ctx, who: Who) -> list[PatientSummary]:
        return ctx.wards.summaries(who)

    @app.get("/patients/{patient_id}", response_model=PatientDetail)
    def get_patient(patient_id: str, ctx: Ctx, who: Who) -> PatientDetail:
        found = ctx.wards.patient(who, patient_id)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return found

    @app.get("/patients/{patient_id}/baseline", response_model=BaselineResponse)
    def get_baseline(patient_id: str, ctx: Ctx, who: Who) -> BaselineResponse:
        found = ctx.baselines.baseline(who, patient_id)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return found

    @app.get("/patients/{patient_id}/history", response_model=PatientHistory)
    def get_history(patient_id: str, ctx: Ctx, who: Who) -> PatientHistory:
        found = ctx.history.history(who, patient_id)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return found

    @app.post(
        "/patients/{patient_id}/questions", response_model=PatientQuestionItem, status_code=201
    )
    def ask_patient(
        patient_id: str, body: NewPatientQuestion, ctx: Ctx, who: Writer
    ) -> PatientQuestionItem:
        made = ctx.questions.create(who, patient_id, body)
        if made == "not_found":
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        if made == "limited":
            raise HTTPException(status_code=429, detail="Question limit reached")
        return made

    def not_for_admin(ctx: Context, who: str, action: str, patient_id: str) -> None:
        """An admin does not see what a clinician asked about a patient: the lab's rule."""
        if ctx.store.is_admin(who):
            ctx.audit.append(who, action, patient_id, "denied")
            raise HTTPException(status_code=403, detail="Forbidden")

    @app.get("/patients/{patient_id}/questions", response_model=list[PatientQuestionItem])
    def list_patient_questions(patient_id: str, ctx: Ctx, who: Who) -> list[PatientQuestionItem]:
        not_for_admin(ctx, who, "view_patient_questions", patient_id)
        found = ctx.questions.recent(who, patient_id)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return found

    @app.get(
        "/patients/{patient_id}/questions/{question_id}", response_model=PatientQuestionDetail
    )
    def get_patient_question(
        patient_id: str, question_id: str, ctx: Ctx, who: Who
    ) -> PatientQuestionDetail:
        not_for_admin(ctx, who, "view_patient_question", patient_id)
        found = ctx.questions.detail(who, patient_id, row_id(question_id))
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return found

    @app.post("/patients", response_model=AbstainedRow, status_code=201)
    def admit_patient(form: AdmitPatientInput, ctx: Ctx, who: Writer) -> AbstainedRow:
        return ctx.patients.admit(who, form)

    @app.get("/patients/{patient_id}/notes", response_model=list[ClinicalNote])
    def get_notes(patient_id: str, ctx: Ctx, who: Who) -> list[ClinicalNote]:
        notes = ctx.patients.notes(who, patient_id)
        if notes is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return notes

    @app.post("/patients/{patient_id}/notes", response_model=ClinicalNote, status_code=201)
    def add_note(patient_id: str, body: NewNote, ctx: Ctx, who: Writer) -> ClinicalNote:
        note = ctx.patients.add_note(who, patient_id, body)
        if note is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return note

    @app.delete("/patients/{patient_id}/notes/{note_id}", status_code=204)
    def delete_note(patient_id: str, note_id: str, ctx: Ctx, who: Writer) -> Response:
        if not ctx.patients.delete_note(who, patient_id, note_id):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Response(status_code=204)

    @app.post("/patients/{patient_id}/labs", response_model=LabPanel)
    def enter_labs(patient_id: str, labs: PartialLabInput, ctx: Ctx, who: Writer) -> LabPanel:
        panel = ctx.patients.enter_labs(who, patient_id, labs)
        if panel is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return panel

    @app.post("/patients/{patient_id}/vitals", response_model=VitalsRecorded)
    def record_vitals(
        patient_id: str, body: NurseVitalsInput, ctx: Ctx, who: Writer
    ) -> VitalsRecorded:
        recorded = ctx.patients.record_vitals(who, patient_id, body)
        if recorded is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return recorded

    @app.get("/acknowledgements", response_model=list[Acknowledgement])
    def get_acknowledgements(ctx: Ctx, who: Who) -> list[dict[str, object]]:
        return ctx.patients.acknowledgements(who)

    @app.post("/patients/{patient_id}/acknowledgement", status_code=204)
    def acknowledge(patient_id: str, body: NewAcknowledgement, ctx: Ctx, who: Writer) -> Response:
        if not ctx.patients.acknowledge(who, patient_id, body.score):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Response(status_code=204)

    @app.post("/extract/labs", status_code=501)
    def extract_labs(who: Writer) -> JSONResponse:
        return JSONResponse(
            status_code=501, content={"detail": "Document extraction is not implemented (v2)"}
        )

    @app.post("/extract/patient", status_code=501)
    def extract_patient(who: Writer) -> JSONResponse:
        return JSONResponse(
            status_code=501, content={"detail": "Document extraction is not implemented (v2)"}
        )

    @app.get("/audit", response_model=list[AuditEntry])
    def my_audit(ctx: Ctx, who: Who) -> list[AuditEntry]:
        """The caller's own audit trail: not a window onto anyone else's activity."""
        return [
            AuditEntry(
                seq=r.seq,
                at=r.at.isoformat().replace("+00:00", "Z"),
                action=r.action,
                resource=r.resource,
                outcome=r.outcome,
            )
            for r in ctx.audit.for_clinician(who)
        ]

    @app.post("/sim/runs", status_code=201)
    def start_sim_run(
        body: NewRun, ctx: Ctx, _: Annotated[None, Depends(ingest_gate)]
    ) -> dict[str, int]:
        return {"run_id": ingest.start_run(ctx.conn, body, clock)}

    @app.post("/sim/runs/{run_id}/batch", status_code=204)
    def add_sim_batch(
        run_id: int, body: Batch, ctx: Ctx, _: Annotated[None, Depends(ingest_gate)]
    ) -> Response:
        if not ingest.add_batch(ctx.conn, run_id, body, clock, settings.retention_days):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Response(status_code=204)

    @app.post("/sim/runs/{run_id}/view", status_code=204)
    def set_sim_view(
        run_id: int, body: LiveView, ctx: Ctx, _: Annotated[None, Depends(ingest_gate)]
    ) -> Response:
        if not ingest.set_view(ctx.conn, run_id, body):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Response(status_code=204)

    @app.post("/sim/runs/{run_id}/episodes", status_code=204)
    def add_episodes(
        run_id: int, body: Episodes, ctx: Ctx, _: Annotated[None, Depends(ingest_gate)]
    ) -> Response:
        if not episodes.store_episodes(ctx.conn, run_id, body, clock):
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        # Truth entered the database: one row for each accepted post, the run's id only (contract-truth 7).
        # The batch post writes none; this one is rare (the runner posts an episode once).
        ctx.audit.append(None, "ingest_episodes", f"run:{run_id}", "ok")
        return Response(status_code=204)

    @app.get("/sim/runs/{run_id}/actions", response_model=Actions)
    def sim_actions(
        run_id: int,
        ctx: Ctx,
        _: Annotated[None, Depends(ingest_gate)],
        after: Annotated[int, Query(ge=0, le=BIGINT_MAX)] = 0,
    ) -> Actions:
        found = ingest.actions(ctx.store, run_id, after)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Actions.model_validate({"actions": found})

    @app.get("/sim/runs/{run_id}/commands", response_model=Commands)
    def sim_commands(
        run_id: int,
        ctx: Ctx,
        _: Annotated[None, Depends(ingest_gate)],
        after: Annotated[int, Query(ge=0, le=BIGINT_MAX)] = 0,
    ) -> Commands:
        found = ingest.commands(ctx.store, run_id, after)
        if found is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        return Commands.model_validate({"commands": found})

    @app.post("/sim/lab/claim", response_model=LabJob | PatientQuestionJob)
    def claim_lab_run(
        ctx: Ctx,
        _: Annotated[None, Depends(ingest_gate)],
        kind: Annotated[lab.Kind, Depends(lab_kind)],
    ) -> LabJob | PatientQuestionJob | Response:
        job = lab.claim(ctx.conn, clock(), kind)
        if job is None:  # an empty poll writes no audit row: it records nothing
            return Response(status_code=204)
        ctx.audit.append(None, "lab_claim", f"lab_run:{job.id}", "ok")
        return job

    @app.post("/sim/lab/runs/{lab_run_id}/report", status_code=204)
    def report_lab_run(
        lab_run_id: Annotated[int, Path(ge=1, le=BIGINT_MAX)],
        body: LabReport,
        ctx: Ctx,
        _: Annotated[None, Depends(ingest_gate)],
    ) -> Response:
        # An id of 0 or above the database's range answers 422, not 404. A worker reports only ids
        # it claimed, so this never happens in normal use.
        # shortcut: Starlette reads the whole body before the 512 KiB cap is checked. Only the
        # ingest key reaches it, inside compose. Upgrade: a body size limit at the proxy.
        result = lab.report(ctx.conn, lab_run_id, body, clock())
        outcome: Outcome = "rejected" if result in ("not_running", "mismatch") else result
        ctx.audit.append(None, "lab_report", f"lab_run:{lab_run_id}", outcome)
        if result == "not_found":
            raise HTTPException(status_code=404, detail=NOT_FOUND)
        if result == "not_running":
            raise ingest.ConflictError("This lab run is not running")
        if result == "mismatch":
            raise RejectedError("The report does not match the request")
        return Response(status_code=204)

    return app
