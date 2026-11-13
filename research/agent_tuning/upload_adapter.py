"""Makes the tuned adapter ready for storage, and uploads it to a PRIVATE Hugging Face model repository.

    python -m research.agent_tuning.upload_adapter prepare --adapter <adapter dir> --out <release dir>
    python -m research.agent_tuning.upload_adapter upload --release <release dir> --repo <owner/name>

`prepare` is local only. It writes four files into a new folder outside the repository: the adapter
weights (same bytes), `adapter_config.json` with the base model's public name in place of the local
path, `SHA256SUMS.txt`, and a card (`README.md`) built from `weights.json` and the run log.
`upload` needs the network and the login the owner makes on this machine themselves
(`hf auth login`). It takes no token and reads none. It creates the repository as private, and it
uploads nothing unless the repository reports itself private. The base model is not uploaded: it is
public at its publisher. `prepare` uses the standard library only; `upload` needs `huggingface_hub`
(in the optional environment).
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUN_LOG = REPO / "training" / "agent" / "runs" / "run1.json"
WEIGHTS, CONFIG, SUMS, CARD = "adapter_model.safetensors", "adapter_config.json", "SHA256SUMS.txt", "README.md"
FILES = (CONFIG, WEIGHTS, SUMS, CARD)
REPO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}")
LOCAL = ("/Users/", "/home/", "/private/", "/var/", "/tmp/", "/Volumes/", "~/", ":\\")  # no local path leaves this machine


class Refused(Exception):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest() -> dict:
    return json.loads((HERE / "weights.json").read_text())


def held_out(log: dict | None) -> str:
    """One line for the card, from the run log's counts. No log, or an unfinished run: not scored yet."""
    q = (log or {}).get("evaluations", {}).get("question", {})
    if not all(q.get(k, {}).get("complete") for k in ("untuned", "tuned", "matcher")):
        return "Held-out result: not scored yet."
    right = {k: q[k]["scores"]["answerable_acted_right"] for k in ("untuned", "tuned", "matcher")}
    n, of = q["tuned"]["scores"]["n"], q["tuned"]["scores"]["positives"]
    verdict = "below" if right["tuned"] < right["matcher"] else "not below"
    return (f"Held-out result ({n} blind questions, one run, no error bars): after the guard the tuned model ran the "
            f"right experiment for {right['tuned']} of the {of} answerable questions. The untuned model: {right['untuned']}. "
            f"The keyword matcher, with no model: {right['matcher']}. The tuned model alone is {verdict} the keyword matcher.")


def card(m: dict, config: dict, sums: dict, log: dict | None) -> str:
    base = m["base"]
    return "\n".join([
        "---", "license: apache-2.0", f"base_model: {base['name']}", "library_name: peft", "---", "",
        f"# BioSentry research agent: LoRA adapter ({m['adapter']['run']}, step {m['adapter']['step']})", "",
        "Not validated. Research only. Generated data, simulated ward. No patient data of any kind.", "",
        f"- Base model: `{base['name']}`, revision `{base['revision']}`, licence {base['licence']}. "
        "The base weights are not in this repository. Get them from the publisher.",
        f"- This adapter: licence Apache-2.0. LoRA rank {config['r']}, alpha {config['lora_alpha']}, "
        f"on {' and '.join(f'`{t}`' for t in sorted(config['target_modules']))}.",
        "- Training data: synthetic. Generated from the project's own rule planner and question frames.",
        "- Its one job: pick one of three fixed experiments for a question, or refuse. Code reads every answer "
        "and decides. The model changes no setting.",
        f"- {held_out(log)}",
        "- The full record is the model card and the registry in the project repository.", "",
        "| File | sha256 |", "|---|---|", *[f"| `{name}` | `{sums[name]}` |" for name in (CONFIG, WEIGHTS)], ""])


def prepare(adapter: Path, out: Path, m: dict | None = None, log: dict | None = None) -> dict:
    """Writes the release folder. Returns {file name: sha256} for the two adapter files."""
    m = manifest() if m is None else m
    out = out.resolve()
    if out == REPO or REPO in out.parents:
        raise Refused("the release folder must be outside the repository")
    if out.exists():
        raise Refused("the release folder exists already")
    if (adapter / WEIGHTS).is_symlink() or sha256(adapter / WEIGHTS) != m["adapter"]["files"][WEIGHTS]:
        raise Refused(f"{WEIGHTS} is not the file weights.json names")
    config = json.loads((adapter / CONFIG).read_text())
    config["base_model_name_or_path"], config["revision"] = m["base"]["name"], m["base"]["revision"]
    out.mkdir(parents=True)
    (out / WEIGHTS).write_bytes((adapter / WEIGHTS).read_bytes())
    (out / CONFIG).write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    sums = {name: sha256(out / name) for name in (CONFIG, WEIGHTS)}
    (out / SUMS).write_text("".join(f"{sums[name]}  {name}\n" for name in (CONFIG, WEIGHTS)))
    (out / CARD).write_text(card(m, config, sums, log))
    return sums


def check_release(release: Path, m: dict) -> None:
    """The folder holds exactly the four files, the two adapter files are the ones weights.json names,
    and no text in it holds a local path."""
    found = sorted(p.name for p in release.iterdir())
    if found != sorted(FILES) or any(p.is_symlink() or not p.is_file() for p in release.iterdir()):
        raise Refused("the release folder must hold exactly " + ", ".join(FILES))
    for name in (CONFIG, WEIGHTS):
        if sha256(release / name) != m["adapter"]["files"][name]:
            raise Refused(f"{name} is not the file weights.json names")
    for name in (CONFIG, SUMS, CARD):
        text = (release / name).read_text()
        if any(mark in text for mark in LOCAL):
            raise Refused(f"{name} holds a local path")


def upload(release: Path, repo: str, api=None, m: dict | None = None) -> list[str]:
    """Uploads the four files. Refuses, with nothing uploaded, unless the repository reports itself private."""
    m = manifest() if m is None else m
    if not REPO_ID.fullmatch(repo):
        raise Refused("the repository name must be <owner>/<name>")
    check_release(release, m)
    if api is None:
        from huggingface_hub import HfApi  # the optional environment; the login is the owner's own
        api = HfApi()
    api.create_repo(repo_id=repo, repo_type="model", private=True, exist_ok=True)
    # exist_ok keeps an existing repository as it is, so ask what it is before any file goes up
    if api.repo_info(repo_id=repo, repo_type="model").private is not True:
        raise Refused("the repository is not private; nothing was uploaded")
    for name in FILES:
        api.upload_file(path_or_fileobj=str(release / name), path_in_repo=name, repo_id=repo, repo_type="model")
    if api.repo_info(repo_id=repo, repo_type="model").private is not True:
        raise Refused("the repository is not private after the upload; make it private or delete it now")
    return list(FILES)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--adapter", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    u = sub.add_parser("upload")
    u.add_argument("--release", required=True, type=Path)
    u.add_argument("--repo", required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == "prepare":
            log = json.loads(RUN_LOG.read_text()) if RUN_LOG.is_file() else None
            for name, digest in prepare(a.adapter, a.out, log=log).items():
                print(f"{digest}  {name}")
        else:
            print("uploaded to the private repository:", ", ".join(upload(a.release, a.repo)))
    except Refused as e:
        print("refused: " + str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
