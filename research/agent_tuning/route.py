"""Asks the tuned model which fixed template fits a question. The model's text goes only into
`tasks.guard_question`; it is never logged, stored, parsed any other way or run. Standard library only.

The model runs in a child process (serve.py) that gets no BIOSENTRY_* value. The child needs only the optional
MindSpore environment, so it may run under another Python than the worker (`python`). Any failure kills the child
and returns None, so the keyword rules answer. The weights are checked by sha256 against weights.json."""

import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from research.agent import ask

from . import tasks

LOAD_SECONDS = 300
ANSWER_SECONDS = 240
MAX_LINE = 8192
MAX_OUTPUT = 2000
MAX_FAILURES = 3
SERVE = Path(__file__).with_name("serve.py")
PASS_ENV = ("PATH", "HOME", "PYTHONDONTWRITEBYTECODE")
DOCS = ("LICENSE", "README.md", "SHA256SUMS.txt")  # may lie in a weights folder; the model loader reads none of them


class _Fail(Exception):
    """Carries one fixed reason, never text from the child."""


def _spawn(argv, env):
    return subprocess.Popen(argv, shell=False, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=True)


def _kill(proc) -> None:
    if isinstance(proc, subprocess.Popen):  # its own session, so its group id is its pid: end what it started too
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    proc.kill()
    proc.wait()


def _sha256(path: Path) -> str | None:
    """The file's sha256, or None when it is not a regular file (a symlink is not) or cannot be read."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # no link, and no wait on a named pipe
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(fd).st_mode):  # the file that was opened, so it cannot be swapped after
                return None
            h = hashlib.sha256()
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
            return h.hexdigest()
    except OSError:
        return None


class Router:
    def __init__(self, base_dir, adapter_dir, log, spawn=None, clock=None, manifest=None, environ=None, python=None):
        self.python = python or sys.executable
        self.log, self.spawn, self.clock = log, spawn or _spawn, clock or time.monotonic
        self.environ = os.environ if environ is None else environ
        self.dirs = (str(base_dir), str(adapter_dir))
        self.proc, self.buf, self.next_id, self.failures = None, b"", 0, 0
        self.manifest = manifest or json.loads(Path(__file__).with_name("weights.json").read_text())
        self.adapter_sha256 = self.manifest["adapter"]["files"].get("adapter_model.safetensors")
        self.on = self._check()

    def _check(self) -> bool:
        """Each folder is a real folder that holds the listed files, right by sha256, and nothing else but DOCS."""
        for folder, part in zip(self.dirs, ("base", "adapter")):
            files = self.manifest[part]["files"]
            try:
                ok = bool(files) and stat.S_ISDIR(os.lstat(folder).st_mode) \
                    and set(os.listdir(folder)) <= set(files) | set(DOCS)
            except OSError:
                ok = False
            if not ok:  # a name in the folder is outside text: it is never logged
                self.log(f"model mode: off, the {part} folder is missing, is a link, or holds a file that is not listed")
                return False
            for name, want in files.items():
                if name != Path(name).name or _sha256(Path(folder) / name) != want:
                    self.log(f"model mode: off, weights file {name[:80]} is missing or does not match")
                    return False
        return True

    def close(self) -> None:
        proc, self.proc, self.buf = self.proc, None, b""
        if proc:
            _kill(proc)
            for pipe in (proc.stdin, proc.stdout):
                try:
                    pipe.close()
                except Exception:  # noqa: BLE001 - the child is dead; nothing more to do
                    pass

    def _line(self, deadline: float) -> bytes:
        """One line of the child's stdout, read with a deadline."""
        fd = self.proc.stdout.fileno()
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_READ)
            while True:
                head, nl, rest = self.buf.partition(b"\n")
                if nl:
                    self.buf = rest
                    if len(head) > MAX_LINE:
                        raise _Fail("a line was too long")
                    return head
                if len(self.buf) > MAX_LINE:
                    raise _Fail("a line was too long")
                wait = deadline - self.clock()
                if wait <= 0 or not sel.select(wait):
                    raise _Fail("it took too long")
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise _Fail("the child ended")
                self.buf += chunk

    @staticmethod
    def _object(line: bytes):
        try:
            o = json.loads(line.decode("utf-8"))
        except (ValueError, RecursionError):
            return None
        return o if type(o) is dict else None

    def _send(self, data: bytes, deadline: float) -> None:
        """Writes to the child's stdin with a deadline: a child that does not read cannot block the worker."""
        fd = self.proc.stdin.fileno()
        os.set_blocking(fd, False)
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_WRITE)
            while data:
                wait = deadline - self.clock()
                if wait <= 0 or not sel.select(wait):
                    raise _Fail("it took too long")
                try:
                    data = data[os.write(fd, data):]
                except BlockingIOError:
                    pass

    def _start(self) -> None:
        if not self._check():  # again before every start: the files may have changed since the last check
            self.on = False
            raise _Fail("the weights changed")
        env = {k: self.environ[k] for k in PASS_ENV if k in self.environ}
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        self.buf = b""
        self.proc = self.spawn([self.python, str(SERVE), "--model", self.dirs[0], "--adapter", self.dirs[1]], env)
        deadline = self.clock() + LOAD_SECONDS
        while self._object(self._line(deadline)) != {"ready": True}:
            pass

    def _ask(self, question: str) -> str:
        self.next_id += 1
        deadline = self.clock() + ANSWER_SECONDS
        # the prompt is built here, so the child imports nothing of the agent or the backend
        messages = tasks.question_example(question, None)["messages"]
        try:
            self._send((json.dumps({"id": self.next_id, "messages": messages}) + "\n").encode(), deadline)
        except (OSError, ValueError):
            raise _Fail("the child did not take the question") from None
        while True:
            o = self._object(self._line(deadline))
            if o and type(o.get("id")) is int and o["id"] == self.next_id and type(o.get("output")) is str:
                if len(o["output"]) > MAX_OUTPUT:
                    raise _Fail("the output was too long")
                return o["output"]

    def route(self, question: str) -> tuple[str, int | None] | None:
        # refused text never reaches the model, and where the keyword rules answer the model is not asked at all:
        # the guard would make the rules win there anyway, so asking would only cost the wait
        if not self.on or tasks.refuses(question) or ask.match(question) != (None, None):
            return None
        try:
            if self.proc is None:
                self._start()
            output = self._ask(question)
            if tasks.question_shape(output) is None:  # the tuned model wrote no such answer on the held-out questions
                raise _Fail("the answer was not one of the two shapes")
        except (_Fail, OSError, ValueError) as e:
            self.close()
            self.failures += 1
            self.log(f"model mode: a question went to the rules ({e if isinstance(e, _Fail) else 'the child failed'})")
            if self.on and self.failures >= MAX_FAILURES:
                self.on = False
                self.log("model mode: off, 3 failures in a row")
            return None
        self.failures = 0
        template, threshold = tasks.guard_question(output, question)
        return (template, threshold) if template else None
