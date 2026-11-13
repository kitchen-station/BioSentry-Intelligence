"""python -m research.agent worker [--once]

Claims one clinician's question from the API, runs the agent's ask path on it, posts the answer back.
The question is data: it reaches only `ask.answer`, as one str. The worker writes no file of its own
(the ledger is written by the agent's code), calls no tool itself and never prints a question, an
answer, an exception text or the key. Contract: contract-ask v1 and contract-d7 v3.1."""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import ask as asking
from .config import AgentError, Config
from .ledger import LedgerError
from .tools import ToolError

TIMEOUT = 30
MAX_RESPONSE = 64 * 1024
MAX_ID = 2**53 - 1
MAX_QUESTION = 500
MAX_ANSWER = 4000
MAX_RECORD = 131072
IDLE, BACKOFF_MAX, DENIED = 30, 300, 300
RETRY_WAITS = (15, 30, 60, 120, 240)  # 5 retries, 465 s in all
RETRY_BUDGET = 600
FINAL = (204, 404, 409, 422)

R_KIND = "the worker does not accept this job: kind"
R_QUESTION = "the worker does not accept this job: question"
R_NO_TEMPLATE = "Refused: no template fits this question."
R_UNGROUNDED = "Refused: a number in the answer is not in the tool result."
R_TOOL = "the agent's tool failed"
R_LEDGER = "the agent's ledger could not be written"
R_SIZE = "the agent's answer is over the size cap"
R_ERROR = "the agent stopped with an error"

_TAIL = "\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff"
_BAD_ONE_LINE = re.compile(f"[\x00-\x1f{_TAIL}]")  # the C0 range holds LF
_BAD_TEXT = re.compile(f"[\x00-\x09\x0b-\x1f{_TAIL}]")  # LF allowed in an answer
_COMMIT = re.compile(r"[0-9a-f]{7,40}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 3xx becomes an HTTPError: the key never goes to another host


def urllib_transport(method: str, url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    """(status, at most 64 KiB + 1 byte of body). Network errors propagate; the caller never prints them."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as e:
        return e.code, b""


def read_env(env) -> tuple[str, str, str | None]:
    """(base URL, key, commit or None). Raises AgentError, one line, never holding the key."""
    env = os.environ if env is None else env
    if any(k.startswith("BIOSENTRY_AGENT_MODEL_") for k in env):
        raise AgentError("the worker does not run with a BIOSENTRY_AGENT_MODEL_* variable set")
    url, key = env.get("BIOSENTRY_API_URL") or "", env.get("BIOSENTRY_INGEST_KEY") or ""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a bad port
        ok = (parts.scheme in ("http", "https") and parts.hostname and "@" not in parts.netloc
              and not parts.query and not parts.fragment and "?" not in url and "#" not in url
              and not re.search(r"[\x00-\x20\x7f-\x9f]", url))
    except ValueError:
        ok = False
    if not ok:
        raise AgentError("BIOSENTRY_API_URL must be an http:// or https:// URL with a host, "
                         "without user-info, query or fragment")
    if not key:
        raise AgentError("BIOSENTRY_INGEST_KEY is not set")
    commit = env.get("BIOSENTRY_AGENT_COMMIT") or ""
    return url.rstrip("/"), key, commit if _COMMIT.fullmatch(commit) else None


def check_job(job: dict) -> str | None:
    """None when the job may run, else the `refused` reason. The id is checked by the caller."""
    if job.get("kind") != "question":
        return R_KIND
    q = job.get("question")
    if type(q) is not str or not 1 <= len(q) <= MAX_QUESTION or _BAD_ONE_LINE.search(q):
        return R_QUESTION
    return None


def _record(out: dict, commit: str | None) -> str:
    return json.dumps({
        "question_format": 1, "synthetic": True, "grounding": "ok",
        "agent": {"ask": "keywords", "step": out["step"], "commit": commit},
        "experiment": {"template": out["template"], "tool": "evaluate_rules", "args": out["args"],
                       "result_sha256": out["result_sha256"], "result": out["result"]},
        "numbers": out["numbers"]})


def report_for(cfg: Config, job: dict, commit: str | None) -> dict:
    """The report body for a checked job. No exception text, no outside text, goes into it."""
    try:
        out = asking.answer(cfg, job["question"])
    except ToolError:
        return {"state": "failed", "reason": R_TOOL}
    except LedgerError:
        return {"state": "failed", "reason": R_LEDGER}
    except Exception:  # noqa: BLE001 - mapped to a fixed reason
        return {"state": "failed", "reason": R_ERROR}
    if out["status"] == "no_template":
        return {"state": "refused", "reason": R_NO_TEMPLATE}
    if out["status"] == "ungrounded":
        return {"state": "refused", "reason": R_UNGROUNDED}
    try:
        record = _record(out, commit)
    except Exception:  # noqa: BLE001
        return {"state": "failed", "reason": R_ERROR}
    text = out["text"]
    if not 1 <= len(text) <= MAX_ANSWER or _BAD_TEXT.search(text) or len(record) > MAX_RECORD:
        return {"state": "failed", "reason": R_SIZE}
    return {"state": "done", "record_json": record, "answer": text}


class Worker:
    def __init__(self, cfg, base, key, commit, transport, sleep, clock, log):
        self.cfg, self.base, self.commit = cfg, base, commit
        self.headers = {"X-Ingest-Key": key}
        self.transport, self.sleep, self.clock, self.log = transport, sleep, clock, log
        self.conn = None

    def _send(self, url: str, body: bytes, headers: dict) -> tuple[int | None, bytes]:
        """(status, body), or (None, b"") on any transport failure. Failure text is dropped."""
        try:
            status, data = self.transport("POST", url, {**self.headers, **headers}, body, TIMEOUT)
            return (status, data) if type(status) is int else (None, b"")
        except Exception:  # noqa: BLE001 - its text may hold the URL or the key
            return None, b""

    def _state(self, name: str) -> None:
        if name != self.conn:
            self.conn = name
            self.log(f"connection: {name}")

    def claim(self):
        """('job', dict) | ('idle',) | ('unreachable',) | ('denied',)"""
        status, data = self._send(f"{self.base}/sim/lab/claim?kind=question", b"", {})
        if status in (403, 503):
            self._state("denied")
            return ("denied",)
        if status == 204:
            self._state("connected")
            return ("idle",)
        if status == 200 and len(data) <= MAX_RESPONSE:
            try:
                job = json.loads(data.decode("utf-8"))
            except (ValueError, RecursionError):
                job = None
            if type(job) is dict:
                self._state("connected")
                return ("job", job)
        self._state("unreachable")
        return ("unreachable",)

    def deliver(self, jid: int, body: dict) -> bool:
        """True when the API gave a final answer. The same bytes are sent each time."""
        url = f"{self.base}/sim/lab/runs/{str(int(jid))}/report"
        raw = json.dumps(body).encode("utf-8")
        start = self.clock()
        for attempt in range(len(RETRY_WAITS) + 1):
            status, _ = self._send(url, raw, {"Content-Type": "application/json"})
            if status in FINAL:
                return True
            if attempt == len(RETRY_WAITS) or self.clock() - start + RETRY_WAITS[attempt] > RETRY_BUDGET:
                return False
            self.sleep(RETRY_WAITS[attempt])
        return False

    def handle(self, job: dict) -> bool:
        """Run one claimed job and report it. False when the job had no usable id."""
        jid = job.get("id")
        if type(jid) is not int or not 1 <= jid <= MAX_ID:
            self.log("ignored a job with no usable id")
            return False
        reason = check_job(job)
        body = {"state": "refused", "reason": reason} if reason else report_for(self.cfg, job, self.commit)
        state = body["state"] if self.deliver(jid, body) else "report not delivered"
        self.log(f"job {jid}: {state}")
        return True

    def loop(self, once: bool = False, max_polls: int | None = None) -> None:
        delay, polls = None, 0
        while max_polls is None or polls < max_polls:
            polls += 1
            got = self.claim()
            if got[0] == "job":
                delay = None
                if self.handle(got[1]):
                    if once:
                        return
                    continue  # claim again at once
                wait = IDLE
            elif got[0] == "idle":
                delay, wait = None, IDLE
            elif got[0] == "denied":
                wait = DENIED
            else:
                delay = IDLE if delay is None else min(delay * 2, BACKOFF_MAX)
                wait = delay
            if once:
                return
            self.sleep(wait)


def run(cfg: Config, args: list[str], env=None, transport=None, sleep=None, clock=None, log=None,
        max_polls: int | None = None) -> str:
    if args not in ([], ["--once"]):
        raise AgentError("usage: worker [--once]")
    base, key, commit = read_env(env)
    log = log or (lambda line: print(line, file=sys.stderr, flush=True))
    parts = urlsplit(base)
    log(f"worker started: {parts.scheme}://{parts.hostname}")
    Worker(cfg, base, key, commit, transport or urllib_transport, sleep or time.sleep, clock or time.monotonic,
           log).loop(once=bool(args), max_polls=max_polls)
    return ""
