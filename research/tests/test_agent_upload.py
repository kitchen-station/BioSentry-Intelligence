"""The adapter's release folder and the upload to a private repository. No network: the API is a fake."""

import hashlib
import json
from pathlib import Path

import pytest

from research.agent_tuning import upload_adapter as up

SOURCE = Path(up.__file__).read_text()


class Info:
    def __init__(self, private):
        self.private = private


class FakeApi:
    """Records every call. `private` is what the repository reports, whatever create_repo was asked."""

    def __init__(self, private=True, after=None):
        self.private, self.after, self.calls = private, after, []

    def create_repo(self, **kw):
        self.calls.append(("create_repo", kw))

    def repo_info(self, **kw):
        self.calls.append(("repo_info", kw))
        uploaded = any(c[0] == "upload_file" for c in self.calls)
        return Info(self.after if uploaded and self.after is not None else self.private)

    def upload_file(self, **kw):
        self.calls.append(("upload_file", kw))


@pytest.fixture
def made(tmp_path):
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / up.WEIGHTS).write_bytes(b"weights for the test")
    (adapter / up.CONFIG).write_text(json.dumps({
        "base_model_name_or_path": "/Users/someone/models/base", "revision": None, "r": 8, "lora_alpha": 16,
        "target_modules": ["v_proj", "q_proj"]}))
    m = {"base": {"name": "pub/base-model", "revision": "a" * 40, "licence": "Apache-2.0"},
         "adapter": {"run": "run1", "step": 200, "files": {up.WEIGHTS: hashlib.sha256(b"weights for the test").hexdigest()}}}
    sums = up.prepare(adapter, tmp_path / "release", m)
    m["adapter"]["files"][up.CONFIG] = sums[up.CONFIG]
    return adapter, tmp_path / "release", m


def scored(tuned, matcher):
    row = lambda right: {"complete": True, "scores": {"answerable_acted_right": right, "n": 78, "positives": 30}}  # noqa: E731
    return {"evaluations": {"question": {"untuned": row(2), "tuned": row(tuned), "matcher": row(matcher)}}}


def test_prepare_writes_four_files_with_no_local_path(made):
    adapter, release, m = made
    assert sorted(p.name for p in release.iterdir()) == sorted(up.FILES)
    config = json.loads((release / up.CONFIG).read_text())
    assert (config["base_model_name_or_path"], config["revision"]) == ("pub/base-model", "a" * 40)
    assert (release / up.WEIGHTS).read_bytes() == (adapter / up.WEIGHTS).read_bytes()
    sums = dict(reversed(line.split()) for line in (release / up.SUMS).read_text().splitlines())
    assert sums == {n: hashlib.sha256((release / n).read_bytes()).hexdigest() for n in (up.CONFIG, up.WEIGHTS)}
    card = (release / up.CARD).read_text()
    for want in ("license: apache-2.0", "base_model: pub/base-model", "a" * 40, "Not validated", "synthetic",
                 "Held-out result: not scored yet.", sums[up.WEIGHTS]):
        assert want in card, want
    for name in (up.CONFIG, up.SUMS, up.CARD):
        assert "/Users/" not in (release / name).read_text()
    up.check_release(release, m)


def test_the_card_takes_the_held_out_counts_from_the_run_log_and_says_which_is_better():
    assert up.held_out(None) == "Held-out result: not scored yet."
    assert up.held_out({"evaluations": {"question": {"tuned": {"complete": False}}}}) == "Held-out result: not scored yet."
    worse, better = up.held_out(scored(7, 13)), up.held_out(scored(15, 13))
    assert "for 7 of the 30 answerable" in worse and "untuned model: 2" in worse and "no model: 13" in worse
    assert "alone is below the keyword matcher" in worse and "alone is not below the keyword matcher" in better


def test_prepare_refuses_a_folder_in_the_repository_an_existing_folder_and_other_weights(made, tmp_path):
    adapter, release, m = made
    for out, why in ((up.REPO / "training" / "agent" / "release", "outside the repository"), (release, "exists already")):
        with pytest.raises(up.Refused, match=why):
            up.prepare(adapter, out, m)
    (adapter / up.WEIGHTS).write_bytes(b"other weights")
    with pytest.raises(up.Refused, match="not the file weights.json names"):
        up.prepare(adapter, tmp_path / "release2", m)
    assert not (tmp_path / "release2").exists() and not (up.REPO / "training" / "agent" / "release").exists()


def test_upload_goes_only_to_a_repository_that_reports_itself_private(made):
    _, release, m = made
    api = FakeApi(private=True)
    assert up.upload(release, "owner/name", api, m) == list(up.FILES)
    kinds = [c[0] for c in api.calls]
    assert kinds == ["create_repo", "repo_info", *["upload_file"] * 4, "repo_info"]
    assert api.calls[0][1] == {"repo_id": "owner/name", "repo_type": "model", "private": True, "exist_ok": True}
    assert [c[1]["path_in_repo"] for c in api.calls if c[0] == "upload_file"] == list(up.FILES)
    assert all(set(c[1]) <= {"repo_id", "repo_type", "private", "exist_ok", "path_or_fileobj", "path_in_repo"} for c in api.calls)


def test_an_existing_public_repository_gets_nothing(made):
    _, release, m = made
    api = FakeApi(private=False)
    with pytest.raises(up.Refused, match="not private; nothing was uploaded"):
        up.upload(release, "owner/name", api, m)
    assert [c[0] for c in api.calls] == ["create_repo", "repo_info"]
    for unknown in (None, "yes", 1):  # only the boolean True counts as private
        with pytest.raises(up.Refused):
            up.upload(release, "owner/name", FakeApi(private=unknown), m)


def test_a_repository_that_turns_public_during_the_upload_is_said(made):
    _, release, m = made
    with pytest.raises(up.Refused, match="not private after the upload"):
        up.upload(release, "owner/name", FakeApi(private=True, after=False), m)


@pytest.mark.parametrize("repo", ["name", "owner/name/extra", "../name", "owner/na me", "owner/", "https://host/owner/name", ""])
def test_a_bad_repository_name_is_refused_before_any_call(made, repo):
    _, release, m = made
    api = FakeApi()
    with pytest.raises(up.Refused, match="<owner>/<name>"):
        up.upload(release, repo, api, m)
    assert api.calls == []


def test_a_changed_release_folder_is_refused_before_any_call(made, tmp_path):
    _, release, m = made
    changes = [lambda: (release / "extra.txt").write_text("x"),
               lambda: (release / up.WEIGHTS).write_bytes(b"other weights"),
               lambda: (release / up.CARD).write_text("kept in /Users/someone/models"),
               lambda: (release / up.SUMS).unlink()]
    for change in changes:
        saved = {p.name: p.read_bytes() for p in release.iterdir()}
        change()
        api = FakeApi()
        with pytest.raises(up.Refused):
            up.upload(release, "owner/name", api, m)
        assert api.calls == []
        for p in release.iterdir():
            p.unlink()
        for name, data in saved.items():
            (release / name).write_bytes(data)
    up.upload(release, "owner/name", FakeApi(), m)


def test_the_command_refuses_in_one_line_and_never_takes_a_token(made, capsys):
    _, release, _ = made
    assert up.main(["upload", "--release", str(release), "--repo", "not a name"]) == 2
    assert capsys.readouterr().err.strip() == "refused: the repository name must be <owner>/<name>"
    with pytest.raises(SystemExit):
        up.main(["upload", "--release", str(release), "--repo", "owner/name", "--token", "x"])
    assert "token=" not in SOURCE and "HF_TOKEN" not in SOURCE and "private=False" not in SOURCE


def test_the_manifest_names_the_base_model_and_both_adapter_files():
    m = up.manifest()
    assert m["base"]["name"] == "Qwen/Qwen2.5-0.5B-Instruct" and len(m["base"]["revision"]) == 40
    assert set(m["adapter"]["files"]) == {up.CONFIG, up.WEIGHTS}
    assert all(len(v) == 64 for part in ("base", "adapter") for v in m[part]["files"].values())
