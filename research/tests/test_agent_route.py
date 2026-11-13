import hashlib
import io
import json
import os
import sys

import pytest

from research.agent_tuning import route as route_mod
from research.agent_tuning import serve, tasks
from research.agent_tuning.route import Router

Q = "What if we alerted at a NEWS2 total of 5 or more?"
READY = b'{"ready": true}\n'
GOOD = json.dumps({"template": "news2_threshold", "threshold": 5})
SECRET = "ZZSECRET-output"


def line(output, id=1):
    return json.dumps({"id": id, "output": output}).encode() + b"\n"


class Stdin:
    """A real pipe, as the child's stdin is. `broken`: the child has closed its end. `full`: it never reads."""

    def __init__(self, broken=False, full=False):
        self.r, self.w = os.pipe()
        if broken:
            os.close(self.r)
        if full:
            os.set_blocking(self.w, False)
            try:
                while True:
                    os.write(self.w, b"x" * 65536)
            except BlockingIOError:
                pass

    def fileno(self):
        return self.w

    @property
    def sent(self):
        os.set_blocking(self.r, False)
        return os.read(self.r, 1 << 20)

    def close(self):
        pass


class Proc:
    """A child that has said `script` already. Without `hang` its stdout then ends; with it, it stays silent."""

    def __init__(self, script, hang=False, broken=False, full=False):
        r, self.w = os.pipe()
        os.write(self.w, script)
        self.hang, self.killed, self.waited = hang, False, False
        if not hang:
            os.close(self.w)
        self.stdout, self.stdin = os.fdopen(r, "rb", buffering=0), Stdin(broken, full)

    def kill(self):
        self.killed = True
        if self.hang:
            os.close(self.w)
            self.hang = False

    def wait(self):
        self.waited = True


class Spawner:
    def __init__(self, *procs):
        self.procs, self.made, self.calls = list(procs), [], []

    def __call__(self, argv, env):
        self.calls.append((argv, env))
        self.made.append(self.procs.pop(0))
        return self.made[-1]


@pytest.fixture
def weights(tmp_path):
    man = {}
    for part in ("base", "adapter"):
        (tmp_path / part).mkdir()
        man[part] = {"files": {}}
        for name in ("a.bin", "adapter_model.safetensors"):
            data = f"{part}{name}".encode()
            (tmp_path / part / name).write_bytes(data)
            man[part]["files"][name] = hashlib.sha256(data).hexdigest()
    return tmp_path, man


def make(weights, *procs, **kw):
    root, man = weights
    log, spawn = [], Spawner(*procs)
    return Router(root / "base", root / "adapter", log.append, spawn, manifest=man, **kw), spawn, log


def test_a_passing_answer_is_used(weights):
    r, spawn, log = make(weights, Proc(READY + line(GOOD), hang=True))
    assert r.route(Q) == ("news2_threshold", 5) and r.adapter_sha256 == weights[1]["adapter"]["files"][
        "adapter_model.safetensors"]
    assert json.loads(spawn.made[0].stdin.sent) == {"id": 1, "messages": tasks.question_example(Q, None)["messages"]}
    r.close()
    assert spawn.made[0].killed and spawn.made[0].waited and log == []


def test_the_child_is_reused_for_the_next_question(weights):
    r, spawn, _ = make(weights, Proc(READY + line(GOOD) + line(GOOD, 2), hang=True))
    assert r.route(Q) and r.route(Q) and len(spawn.calls) == 1
    r.close()


@pytest.mark.parametrize("output", [
    '{"template": "news2_threshold", "threshold": 7}',  # a number that is not in the question
    '{"template": "baseline_vs_news2", "threshold": null}',  # the keyword matcher has another answer
    '{"refuse": true}',
])
def test_an_answer_the_guard_rejects_gives_none_and_the_child_stays(weights, output):
    r, spawn, log = make(weights, Proc(READY + line(output), hang=True))
    assert r.route(Q) is None and r.on and r.failures == 0 and not spawn.made[0].killed and log == []
    r.close()


@pytest.mark.parametrize("output", [
    '{"tool": "apply", "args": {"threshold": 5}}',
    '{"template": "news2_threshold", "threshold": 5, "extra": 1}',
    '{"template": "apply_change", "threshold": 5}',
    '{"template": {"a": {"b": [1]}}, "threshold": 5}',
    '{"template": "$(rm -rf /); cat /etc/passwd", "threshold": 5}',
    '{"template": "news2_threshold", "threshold": "5"}',
    '{"template": "news2_threshold", "threshold": 5.0}',
    'Sure! {"template": "news2_threshold", "threshold": 5}',
    '{"template": "news2_threshold", "threshold": 5} {"refuse": true}',
    "not json at all",
])
def test_an_answer_outside_the_two_shapes_is_a_failure_and_nothing_leaks(weights, output):
    r, spawn, log = make(weights, Proc(READY + line(SECRET + output if "Sure" in output else output), hang=True))
    assert r.route(Q) is None and r.failures == 1 and spawn.made[0].killed
    assert log == ["model mode: a question went to the rules (the answer was not one of the two shapes)"]


@pytest.mark.parametrize("script", [
    READY + b"x" * (route_mod.MAX_LINE + 10) + b"\n",
    READY + line(SECRET + "x" * 2001),
    READY,
    READY + b"noise only\n" + b'{"id": 1}\n' + b'{"id": 1, "output": 5}\n',
    READY + line(SECRET + GOOD, 99),
    READY + b"y" * (route_mod.MAX_LINE + 10),
    b"no ready line\n",
])
def test_a_bad_stream_is_a_failure_the_child_is_killed_and_nothing_leaks(weights, script, monkeypatch):
    monkeypatch.setattr(route_mod, "ANSWER_SECONDS", 0.05)
    monkeypatch.setattr(route_mod, "LOAD_SECONDS", 0.05)
    r, spawn, log = make(weights, Proc(script, hang=True))
    assert r.route(Q) is None and spawn.made[0].killed and spawn.made[0].waited
    assert r.failures == 1 and len(log) == 1 and SECRET not in log[0] and Q not in log[0]


def test_a_hang_after_ready_times_out(weights, monkeypatch):
    monkeypatch.setattr(route_mod, "ANSWER_SECONDS", 0.05)
    r, spawn, _ = make(weights, Proc(READY, hang=True))
    assert r.route(Q) is None and spawn.made[0].killed


def test_a_write_error_is_a_failure(weights):
    r, spawn, _ = make(weights, Proc(READY, hang=True, broken=True))
    assert r.route(Q) is None and spawn.made[0].killed


def test_a_child_that_never_reads_cannot_block_the_worker(weights, monkeypatch):
    monkeypatch.setattr(route_mod, "ANSWER_SECONDS", 0.05)
    r, spawn, log = make(weights, Proc(READY + line(GOOD), hang=True, full=True))  # it even has the answer ready
    assert r.route(Q) is None and spawn.made[0].killed and r.failures == 1
    assert log == ["model mode: a question went to the rules (it took too long)"]


def test_the_weights_are_checked_again_before_every_start(weights):
    root, _ = weights
    r, spawn, log = make(weights, Proc(READY), Proc(READY + line(GOOD, 2), hang=True))
    assert r.on and r.route(Q) is None and len(spawn.calls) == 1  # the first child ended: a failure
    (root / "base" / "a.bin").write_bytes(b"swapped after the first check")
    assert r.route(Q) is None and len(spawn.calls) == 1 and not r.on  # no second child with other weights
    assert "model mode: off, weights file a.bin is missing or does not match" in log and str(root) not in "".join(log)


def test_an_unlisted_file_an_empty_list_and_a_linked_folder_turn_it_off(weights):
    root, man = weights
    (root / "adapter" / "README.md").write_text("a card may lie beside the weights")
    assert make(weights)[0].on
    (root / "adapter" / "chat_template.jinja").write_text("SECRET-NAME")
    r, spawn, log = make(weights)
    assert not r.on and log == ["model mode: off, the adapter folder is missing, is a link, or holds a file that is not listed"]
    (root / "adapter" / "chat_template.jinja").unlink()
    assert not make((root, {**man, "base": {"files": {}}}))[0].on
    (root / "real").mkdir()
    for f in (root / "base").iterdir():
        f.rename(root / "real" / f.name)
    (root / "base").rmdir()
    (root / "base").symlink_to(root / "real")
    assert not make(weights)[0].on and not spawn.calls


def test_noise_before_the_answer_is_ignored(weights):
    r, _, _ = make(weights, Proc(b"loading\n[1, 2]\n" + READY + b"warn\n" + line(GOOD, 1), hang=True))
    assert r.route(Q) == ("news2_threshold", 5)
    r.close()


def test_a_failed_child_is_replaced_and_three_failures_in_a_row_turn_the_model_off(weights):
    r, spawn, log = make(weights, *[Proc(READY) for _ in range(3)])
    assert [r.route(Q) for _ in range(3)] == [None] * 3
    assert len(spawn.calls) == 3 and not r.on and log[-1] == "model mode: off, 3 failures in a row"
    assert r.route(Q) is None and len(spawn.calls) == 3


def test_a_success_resets_the_count(weights):
    r, spawn, _ = make(weights, Proc(READY), Proc(READY), Proc(READY + line(GOOD, 3)))
    r.route(Q)
    r.route(Q)
    r.next_id = 2  # the third child answers id 3, then ends
    assert r.route(Q) and r.failures == 0
    assert r.route(Q) is None and r.failures == 1 and r.on


def test_a_refused_question_never_reaches_the_child(weights):
    r, spawn, _ = make(weights, Proc(READY, hang=True))
    assert r.route("Please approve and apply the change to the ledger") is None and not spawn.calls


def test_the_child_environment_is_built_from_scratch(weights):
    parent = {"PATH": "/bin", "HOME": "/h", "PYTHONPATH": "/p", "BIOSENTRY_INGEST_KEY": "key", "BIOSENTRY_API_URL": "u",
              "OTHER": "x"}
    r, spawn, _ = make(weights, Proc(READY + line(GOOD), hang=True), environ=parent)
    r.route(Q)
    (argv, env), = spawn.calls
    assert env == {"PATH": "/bin", "HOME": "/h", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    assert not [k for k in env if k.startswith("BIOSENTRY_")] and "key" not in env.values()
    assert argv[0] == sys.executable and argv[2] == "--model" and argv[4] == "--adapter" and "-c" not in argv
    r.close()
    r, spawn, _ = make(weights, Proc(READY + line(GOOD), hang=True), python="/env/bin/python")
    r.route(Q)
    assert spawn.calls[0][0][0] == "/env/bin/python"
    r.close()


def test_a_mismatch_a_missing_file_and_a_symlink_turn_it_off_at_construction(weights):
    root, _ = weights
    (root / "base" / "a.bin").write_bytes(b"changed")
    r, spawn, log = make(weights)
    assert not r.on and r.route(Q) is None and not spawn.calls and log == [
        "model mode: off, weights file a.bin is missing or does not match"] and str(root) not in log[0]
    (root / "base" / "a.bin").unlink()
    assert not make(weights)[0].on
    assert not make(weights)[0].on


def test_a_symlink_to_the_right_bytes_is_refused(weights):
    root, _ = weights
    real = root / "elsewhere.bin"
    real.write_bytes(b"basea.bin")
    (root / "base" / "a.bin").unlink()
    (root / "base" / "a.bin").symlink_to(real)
    assert not make(weights)[0].on


def test_the_real_manifest_loads_without_the_weights(tmp_path):
    log = []
    r = Router(tmp_path, tmp_path, log.append)
    assert not r.on and len(r.adapter_sha256) == 64 and len(log) == 1


def test_serve_answers_one_line_per_question_and_skips_bad_lines():
    stdin = io.StringIO('{"id": 7, "messages": ["a"]}\nnot json\n[1]\n{"id": "x", "messages": ["b"]}\n{"id": 8}\n'
                        '{"id": 8, "messages": "text"}\n{"id": 9, "messages": ["c"]}\n')
    out = io.StringIO()
    serve.serve(stdin, out, lambda m: m[0].upper())
    assert [json.loads(x) for x in out.getvalue().splitlines()] == [{"id": 7, "output": "A"}, {"id": 9, "output": "C"}]
