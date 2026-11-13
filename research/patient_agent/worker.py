"""python -m research.patient_agent worker [--once]

Claims one patient question from the API and posts the answer back. The patient's data lives in memory for
one job only: nothing is written, and a log line holds a job id and a state, never data, a question or an answer."""

import hashlib
import json
import sys
import time
from functools import partial
from urllib.parse import urlsplit

from research.agent import worker as base
from research.agent.config import AgentError

from . import ask as asking

MAX_CLAIM = 524288
KIND = "patient_question"
R_KIND, R_QUESTION = base.R_KIND, base.R_QUESTION
R_PATIENT = "the worker does not accept this job: patient"
R_REFUSED = "Refused: this agent answers only fixed kinds of question about this patient's own data."
R_HISTORY = "Refused: the alert history of the stay is not available to this agent yet."
R_UNGROUNDED = "Refused: a number in the answer is not in this patient's data."
R_SIZE, R_ERROR = base.R_SIZE, base.R_ERROR


def data_sha256(patient: dict) -> str:
    # The contract's canonical form (contract-patient-ask section 5): sha256 of the UTF-8 of
    # json.dumps(patient, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False), lowercase hex.
    # A NaN raises ValueError: the caller reports `failed`.
    return hashlib.sha256(json.dumps(patient, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def check_job(job: dict) -> str | None:
    if job.get("kind") != KIND:
        return R_KIND
    q = job.get("question")
    if type(q) is not str or not 1 <= len(q) <= base.MAX_QUESTION or base._BAD_ONE_LINE.search(q):
        return R_QUESTION
    p = job.get("patient")
    if type(p) is not dict or type(p.get("format")) is not int or p["format"] != 1 \
            or p.get("synthetic") is not True or (p.get("detail") is None) == (p.get("abstained") is None) \
            or type(p["detail"] if p.get("detail") is not None else p["abstained"]) is not dict:
        return R_PATIENT
    return None


def report_for(job: dict, commit: str | None) -> dict:
    """The report body for a checked job. No exception text and no patient data goes into it."""
    patient = job["patient"]
    try:
        out = asking.answer(patient, job["question"])
    except Exception:  # noqa: BLE001 - mapped to a fixed reason
        return {"state": "failed", "reason": R_ERROR}
    if out["status"] == "refused":
        if out["reason"] == "history":
            return {"state": "refused", "reason": R_HISTORY}
        return {"state": "refused", "reason": R_REFUSED}
    if out["status"] == "ungrounded":
        return {"state": "refused", "reason": R_UNGROUNDED}
    if out["status"] != "answered":  # bad_data and anything else: `done` is never the default
        return {"state": "failed", "reason": R_ERROR}
    try:
        record = json.dumps({
            "patient_question_format": 1, "synthetic": True, "grounding": "ok",
            "agent": {"ask": out["planner"], "commit": commit},
            "kind": out["kind"], "args": out["args"], "numbers": out["numbers"],
            "data_sha256": data_sha256(patient)}, allow_nan=False)
    except Exception:  # noqa: BLE001
        return {"state": "failed", "reason": R_ERROR}
    text = out["text"]
    if base._BAD_TEXT.search(text):
        return {"state": "failed", "reason": R_ERROR}
    if not 1 <= len(text) <= base.MAX_ANSWER or len(record) > base.MAX_RECORD:
        return {"state": "failed", "reason": R_SIZE}
    return {"state": "done", "record_json": record, "answer": text}


class PatientWorker(base.Worker):
    KIND = KIND
    MAX_CLAIM = MAX_CLAIM

    def check(self, job: dict) -> str | None:
        return check_job(job)

    def report(self, job: dict) -> dict:
        return report_for(job, self.commit)


def run(args: list[str], env=None, transport=None, sleep=None, clock=None, log=None, max_polls: int | None = None) -> str:
    if args not in ([], ["--once"]):
        raise AgentError("usage: worker [--once]")
    url, key, commit = base.read_env(env)
    log = log or (lambda line: print(line, file=sys.stderr, flush=True))
    parts = urlsplit(url)
    log(f"worker started: {parts.scheme}://{parts.hostname}")
    PatientWorker(None, url, key, commit, transport or partial(base.urllib_transport, cap=MAX_CLAIM),
                  sleep or time.sleep, clock or time.monotonic, log).loop(once=bool(args), max_polls=max_polls)
    return ""
