# The tuned model on the team server: what it needs

Nothing here is done. No image is built for the server, no file is on the server, and no compose
service starts the worker. This page is the list to work from when the owner says go.

The model mode of the question worker is off unless two environment values are set. With them
unset the worker is the rules-only worker (`research/README.md`, "Model mode").

## The server

Ubuntu 24.04, 4 CPUs, 12 GB memory, no GPU.

## The image

- Python 3.11. Ubuntu 24.04's own Python is 3.12, and MindSpore 2.7.0 has wheels for Python 3.9,
  3.10 and 3.11 only. So the worker runs in a container from a Python 3.11 base image.
- The system package `libgomp1`. MindSpore does not import without it.
- The packages of `research/agent_tuning/requirements.txt`, exact pins. The notebook block at the end
  of that file is not needed on the server.
- MindSpore's wheel: `mindspore-2.7.0-cp311-cp311-manylinux1_x86_64.whl` (749 MB) on an x86_64
  server. On Linux aarch64 it is `mindspore-2.7.0-cp311-none-any.whl` (320 MB).
- The repository's `research/` and `backend/src/` folders, as for the rules-only worker.
- Two Python environments in the one image. The worker runs in the backend's environment, as the
  rules-only worker does. The model runs as a child process in its own environment with the pinned
  list above, and `BIOSENTRY_AGENT_TUNED_PYTHON` names that interpreter. The model's environment does
  not hold the backend's packages, and the model's process imports nothing of the agent. Found by
  trying it: the model's environment alone cannot import the agent.

Not checked: the pins were resolved on macOS arm64 and on Linux aarch64 only. On x86_64 the `torch`
wheel from the default index can bring GPU libraries of several gigabytes (seen here once, with a
newer `torch`). Take the CPU build of the same version, and resolve the list once on x86_64 before
the image is built.

## The files, mounted read-only

| What | Size | Where it comes from |
|---|---|---|
| Base weights folder (`Qwen/Qwen2.5-0.5B-Instruct`, revision `7ae557604adf67be50417f59c2c2f167def9a775`) | 955 MB | the publisher's public repository, at that revision |
| Adapter release folder (`adapter_model.safetensors`, `adapter_config.json`) | 2.1 MB | our private storage (see `training/agent/REGISTRY.md`, section 6) |
| Trial root (`ward-v2`, seeds 1 to 10) | as for the rules-only worker | as for the rules-only worker |

    BIOSENTRY_AGENT_TUNED_BASE=/weights/base        (read-only mount)
    BIOSENTRY_AGENT_TUNED_ADAPTER=/weights/adapter  (read-only mount)
    BIOSENTRY_AGENT_TUNED_PYTHON=/opt/tuned/bin/python   (the model's environment; an example path)

A person copies both folders to the server once, at set-up. The worker never downloads anything: it
starts the model with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, and the container needs the
network only to reach the API.

## The check at start

The worker hashes every base file and adapter file and compares each sha256 with
`research/agent_tuning/weights.json`, which is in the repository. A missing file, a symlink or one
wrong hash turns the model mode off for that process. The rules then answer every question, and the
log says so in one line.

## Memory and time, measured

One run, on 2026-11-13: the tuned model on 13 held-out questions in a Linux aarch64 container on a
laptop (Apple M4), limited to 4 CPUs and 7 GB, with no network.

| | |
|---|---|
| Time for one question | 36 to 128 seconds, mean 60 |
| Time to load the model | about 40 seconds |
| Peak memory of the container | 6.0 GB (the kernel's count, with file cache). 4.2 GB was seen mid-run |
| Answers | the same text as the laptop run for all 13 |

Not measured: an x86_64 processor, and the server itself. The numbers above are from another
processor type, so read them as a first look.

The limits in the code: 300 seconds to load, 240 seconds for one model answer, then the experiment's
own 600 seconds. A model answer that is late is dropped and the rules answer. The API waits 20
minutes for a report.

## One question at a time

One worker, one model process, one question. The worker never claims a question while one runs. A
second worker would need its own 6 GB. The rest of the stack shares the other half of the 12 GB.

## Storage of the weights

- Now: the adapter goes to a private Hugging Face model repository, which is free.
  `python -m research.agent_tuning.upload_adapter` prepares and uploads it. The upload was run once, on
  2026-11-13, at the owner's request: the repository is `Aellaecieee/biosentry-agent-adapter`. Nothing is
  on the server yet.
- Final plan: a Huawei Cloud OBS bucket, when a paid Huawei Cloud account exists.
- On the server: read-only local copies, checked by sha256 at every start.
