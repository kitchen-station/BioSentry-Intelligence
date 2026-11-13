"""One loaded model that answers question prompts over stdin and stdout, for route.py.

    python research/agent_tuning/serve.py --model <base dir> --adapter <adapter dir>

Prints {"ready": true}, then for each stdin line {"id": <int>, "messages": [<chat messages>]} prints one line
{"id": <int>, "output": "<text>"}. Greedy decoding, the question task's token cap. It imports nothing of the agent. Reads nothing else and
writes no file. A line that is not such an object is skipped without an answer.
"""

import argparse
import json
import sys


def serve(stdin, stdout, generate) -> None:
    for line in stdin:
        try:
            o = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if type(o) is dict and type(o.get("id")) is int and type(o.get("messages")) is list:
            stdout.write(json.dumps({"id": o["id"], "output": generate(o["messages"])}) + "\n")
            stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", required=True)
    a = ap.parse_args()
    from common import backend, load
    from run_model import MAX_NEW

    _, torch = backend()
    tok, model = load(a.model, torch, a.adapter)
    model.eval()

    def generate(messages: list) -> str:
        text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        ids = tok(text, return_tensors="pt")
        with torch.no_grad():
            got = model.generate(**ids, max_new_tokens=MAX_NEW["question"], do_sample=False)
        return tok.decode(got[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)

    print(json.dumps({"ready": True}), flush=True)
    serve(sys.stdin, sys.stdout, generate)


if __name__ == "__main__":
    main()
