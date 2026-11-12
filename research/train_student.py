"""Train the distilled vitals student on tuning seeds 1 to 10 and write it out.

    python -m research.train_student <trial folder>... [--out <dir>]

Held-out folders (seeds 11 to 30) may be listed; only their manifest.json is opened, to skip
them. Writes model.onnx, model_no_temp.onnx and metadata.json into --out (default
models/tier_c/student_vitals/). Deterministic: two runs write the same bytes. Exit 2 on a refusal.
"""

import json
import os
import sys
from pathlib import Path

from .student import build, load_for_training
from .trials import TrialError

OUT = Path(__file__).parent.parent / "models" / "tier_c" / "student_vitals"
FILES = {"all": "model.onnx", "no_temp": "model_no_temp.onnx"}


def metadata(result: dict, skipped: list[int]) -> dict:
    return {
        "name": "tier_c/student_vitals", "synthetic": True, "validated": False,
        "data": "ward-v2 simulator trials", "trained_on_seeds": "1-7", "selected_on_seeds": "8-10",
        "held_out_seeds_skipped_unread": skipped,
        "models": {tag: {"file": FILES[tag], **r["meta"]} for tag, r in result.items()},
    }


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    out = OUT
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print("refused: --out needs a folder", file=sys.stderr)
            return 2
        out, argv = Path(argv[i + 1]), argv[:i] + argv[i + 2:]
    try:
        train, val, skipped = load_for_training(argv)
        result = build(train, val)
    except TrialError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    meta = metadata(result, skipped)
    out.mkdir(parents=True, exist_ok=True)
    for tag, r in result.items():
        _write(out / FILES[tag], r["onnx"])
    _write(out / "metadata.json", (json.dumps(meta, indent=1, sort_keys=True) + "\n").encode())
    print(f"trained on seeds {', '.join(str(t.seed) for t in train)}; "
          f"selected on {', '.join(str(t.seed) for t in val)}; held-out skipped unread: {len(skipped)}")
    for tag, m in meta["models"].items():
        v = m["validation_seeds_8_10"]
        print(f"{tag}: kept {m['student_kind']} ({m['n_features']} features); validation AP teacher "
              f"{v['teacher']['average_precision']:.3f}, logistic {v['logistic']['average_precision']:.3f}, "
              f"mlp {v['mlp']['average_precision']:.3f}; threshold student {m['threshold_student']}, "
              f"teacher {m['threshold_teacher']}; tuning alerts student {m['tuning_alerts_student_at_threshold']}, "
              f"teacher {m['tuning_alerts_teacher_at_threshold']}, NEWS2 {m['tuning_alerts_news2_chart_trigger']}; "
              f"{m['onnx_bytes']} bytes, ops {', '.join(m['onnx_ops'])}, sha256 {m['onnx_sha256']}")
    print("synthetic, not validated: trained on the same simulator it will be judged on")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
