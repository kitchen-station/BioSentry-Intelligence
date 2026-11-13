# Registry: the research agent's language model (models, weights, data, sources)

Everything that was considered, chosen, downloaded or generated for the optional language model
of `research/agent/`. Researched on 2026-11-13 by opening the pages listed. **[verified]** means
the page was opened that day and says it. **[unverified]** means a search snippet, a third-party
page, or our own estimate; the reason is given. The model card is `models/agent/card.md`; the
code is `research/agent_tuning/`; the run-log tool's notes are in `training/agent/README.md`. The run log is `training/agent/runs/run1.json` and the notebook is `training/agent/notebooks/finetune.ipynb`.

## 1. What was chosen, in plain words

- **Was DeepSeek chosen?** No DeepSeek model was trained, downloaded or called. DeepSeek is
  named in one place only: `deepseek-v4-flash` (open weights, MIT) is the hosted model the agent's
  optional model planner is meant to call on Huawei Cloud, because it is one of the six models the
  international site's API lists. That call has never been made: there is no Huawei Cloud account
  yet. DeepSeek was not chosen as the model to fine-tune. Its small open models (the R1
  distillations) always write their reasoning before the answer and have no switch to turn that
  off, which breaks an answer that must be one JSON object. Its current open model,
  DeepSeek-V4-Flash, has 284 billion parameters and cannot be tuned on a laptop.
- **Why not Huawei's own open-weight model?** openPangu-Embedded-1B is published with open
  weights in the `ascend-tribe` repository on GitCode, under a licence that names Huawei
  Technologies Co., Ltd. as the licensor (that the repository is Huawei's official one was not
  confirmed). It was considered and not chosen. Its licence is Huawei's own (not Apache or MIT):
  it allows use, changes and redistribution at no charge, asks for a "Powered by openPangu"
  notice, forbids use "within the European Union", and does not say whether commercial use is
  allowed. A third-party copy of its card says it is built for Ascend hardware and needs
  `trust_remote_code` (unverified: the card itself could not be opened). MindNLP does not list
  it, and no laptop CPU run of it was found. The licence was read on 2026-11-13; the model
  itself was not downloaded or run. openPangu-2.0-Pro and openPangu-2.0-Flash are served on
  the China site's MaaS only (not on the international list); whether their weights are open was
  not checked.
- **Fine-tuned here:** `Qwen/Qwen2.5-0.5B-Instruct` (Alibaba Cloud, Apache-2.0, 0.49 billion
  parameters). It is not a reasoning model, it is not gated, it is the smallest strong instruct
  model found, and MindSpore tooling lists its family as supported.
- **Fallback:** `Qwen/Qwen3-0.6B` (Apache-2.0), with thinking switched off. Not downloaded.
- **Framework:** MindSpore 2.7.0 as the backend, through MindNLP (pinned as `mindnlp==0.5.1`; the installed
  library reports itself as 0.5.0rc2), which makes Hugging Face `transformers` and `peft` run on
  MindSpore operators. No other framework did any training.

## 2. Every open-weight model considered

| Model | Publisher | Licence | Size | Decision | Why |
|---|---|---|---|---|---|
| [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) | Alibaba Cloud (Qwen) | Apache-2.0 | 0.49B, 988 MB | **chosen, fine-tuned** | smallest strong instruct model; no reasoning text; MindNLP lists the Qwen2 family |
| [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | Alibaba Cloud (Qwen) | Apache-2.0 | 0.6B, 1.5 GB | fallback, not downloaded | thinking is on by default and must be switched off everywhere |
| [Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) | Alibaba Cloud (Qwen) | Apache-2.0 | 1.54B, 3.1 GB | not chosen | about three times slower to tune; a candidate for a ModelArts job |
| [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) | Alibaba Cloud (Qwen) | Apache-2.0 | 1.7B, 4.1 GB | not chosen | too slow here; thinking on by default |
| [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) | Alibaba Cloud (Qwen) | Apache-2.0 | 4.0B | not chosen | too large for this machine |
| [Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) | Alibaba Cloud (Qwen) | Apache-2.0 | 2B, 4.5 GB | not chosen | its card asks for "the latest transformers"; whether MindNLP 0.5.1 works with that was not checked; has a vision encoder; four times slower to tune |
| [DeepSeek-R1-Distill-Qwen-1.5B](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B) (and 7B) | DeepSeek | MIT | 1.5B | **not chosen** | always a reasoning model; no off switch; reasoning text before the JSON |
| [DeepSeek-V4-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash) | DeepSeek | MIT | 284B (13B active) | hosted target only | served by Huawei Cloud MaaS; far too large to tune or run here |
| [SmolLM3-3B](https://huggingface.co/HuggingFaceTB/SmolLM3-3B) | Hugging Face | Apache-2.0 | 3B | not chosen | too large for this machine; MindNLP does not name it |
| [SmolLM2-1.7B-Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct) | Hugging Face | Apache-2.0 | 1.7B | not chosen | its own card reports 27% on a function-calling benchmark |
| [Phi-4-mini-instruct](https://huggingface.co/microsoft/Phi-4-mini-instruct) | Microsoft | MIT | 3.8B | not chosen | too large for this machine |
| [Llama-3.2-1B-Instruct](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) (and 3B) | Meta | Llama 3.2 Community License | 1.23B | not chosen | gated, naming and notice duties: too much licence friction for a competition entry |
| [gemma-3-1b-it](https://huggingface.co/google/gemma-3-1b-it) | Google | Gemma terms | 1.0B | not chosen | click-through terms and a use policy |
| [granite-4.0-1b](https://huggingface.co/ibm-granite/granite-4.0-1b) | IBM | Apache-2.0 | 1.6B | not chosen | larger than the pick; MindNLP does not name it |
| [MiniCPM4-0.5B](https://huggingface.co/openbmb/MiniCPM4-0.5B) | OpenBMB | Apache-2.0 | 0.5B | not chosen | no structured-output claim; MindNLP does not name it |
| openPangu-Embedded-1B ([licence text](https://raw.gitcode.com/ascend-tribe/openpangu-embedded-1b-model/raw/main/LICENSE), read 2026-11-13; card facts from a [third-party mirror](https://hf-mirror.492719920.workers.dev/FreedomIntelligence/openPangu-Embedded-1B/raw/main/README.md), unverified) | Huawei | OPENPANGU MODEL LICENSE AGREEMENT VERSION 1.0 (Huawei's own; no use within the European Union; "Powered by openPangu" notice; commercial use not stated) | 1B | not chosen | custom licence with a territorial limit; built for Ascend hardware; needs `trust_remote_code`; MindNLP does not name it |
| [GLM-5.1](https://huggingface.co/zai-org/GLM-5.1) (hosted) | Z.ai | MIT | 754B | not chosen | served by MaaS; an alternative hosted target (evidence in section 4) |

Sizes are the full-precision weight files where one was read. The evidence for each row, with
its link and mark, is in section 3; for the two hosted models it is in section 4.

## 3. Evidence for each model considered

Licence and size facts come from the official Hugging Face model card, except the openPangu row: its licence comes from the licence file in the `ascend-tribe` repository on GitCode, and its size and card facts come from a third-party mirror and are marked unverified. The JSON column quotes what a source says. "None on card" means no published figure was found on that page.

| Model | Licence, gating | Params, context | Thinking? | Tool-call / JSON figure | Mark, URL |
|---|---|---|---|---|---|
| Qwen/Qwen2.5-0.5B-Instruct | Apache-2.0, ungated | 0.49B (0.36B non-embed); 32,768 ctx, 8,192 gen | No | Card claims "Significant improvements" in "generating structured outputs especially JSON"; no number | [verified] https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct |
| Qwen/Qwen2.5-1.5B-Instruct | Apache-2.0, ungated | 1.54B (1.31B non-embed); 32,768 ctx, 8,192 gen | No | Same JSON claim; no tool-call claim, no number | [verified] https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct |
| Qwen/Qwen3-0.6B | Apache-2.0 | 0.6B (0.44B non-embed); 32,768 | Yes, on by default. `enable_thinking=False` is a "hard switch" that removes `<think>` | "excels in tool calling"; no number on card | [verified] https://huggingface.co/Qwen/Qwen3-0.6B |
| Qwen/Qwen3-1.7B | Apache-2.0 | 1.7B (1.4B non-embed); 32,768 | Same as 0.6B | No number on its card. SmolLM3's card reports Qwen3-1.7B BFCL 89.5 (no think) and 88.8 (think), publisher-run | [verified] https://huggingface.co/Qwen/Qwen3-1.7B , https://huggingface.co/HuggingFaceTB/SmolLM3-3B |
| Qwen/Qwen3-4B-Instruct-2507 | Apache-2.0 | 4.0B; 262,144 | Non-thinking only | BFCL-v3 61.9 | [verified] https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 |
| Qwen/Qwen3.5-2B (newest small Qwen, released 2026-03-02 per press) | Apache-2.0 | 2B; 262,144; hybrid Gated DeltaNet + attention, with a vision encoder | Non-thinking by default | BFCL-V4 43.6 (thinking); Qwen3.5-0.8B 25.3. Needs "the latest transformers" | [verified] https://huggingface.co/Qwen/Qwen3.5-2B ; release date [unverified] (search snippets) |
| deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B (7B is the same story) | MIT for repo and weights; derived from Qwen2.5 (Apache-2.0) | 1.5B (widget 2B) | Yes, always a reasoning model. Card tells you to force a `<think>\n` start and use no system prompt. No off switch | No tool-calling mention | [verified] https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B . Ruled out: emits reasoning before the JSON |
| HuggingFaceTB/SmolLM3-3B | Apache-2.0 | 3B; 64k trained, 128k with YaRN | Yes, on by default. `/no_think` or `enable_thinking=False` | BFCL 92.3 (no think), 88.8 (think), self-reported | [verified] https://huggingface.co/HuggingFaceTB/SmolLM3-3B |
| HuggingFaceTB/SmolLM2-1.7B-Instruct | Apache-2.0 | 1.7B; ctx not stated | No | "scores 27% on the BFCL Leaderboard" | [verified] https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct |
| microsoft/Phi-4-mini-instruct | MIT | 3.8B; 128K | No (separate reasoning model exists) | Function calling supported via `<\|tool\|>` tags; warns it "could sometimes hallucinate function names"; no BFCL number | [verified] https://huggingface.co/microsoft/Phi-4-mini-instruct |
| meta-llama/Llama-3.2-1B-Instruct (and 3B) | Llama 3.2 Community License; gated (share HF info); "Built with Llama" and a "Llama" name prefix on derivatives; EU exclusion for the multimodal models; 700M MAU clause | 1.23B; 128k | No | BFCL V2: 25.7 (1B), 67.0 (3B) | [verified] https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct . Licence too fussy for the entry |
| google/gemma-3-1b-it | Gemma terms; click-through; Prohibited Use Policy | 1.0B; 32K in, 8,192 out | No | No function-calling mention | [verified] https://huggingface.co/google/gemma-3-1b-it |
| ibm-granite/granite-4.0-1b | Apache-2.0 | 1.6B per table (widget 2B); 128K | Not mentioned | BFCL v3 54.82 (1B), 50.21 (H 1B), 39.32 (350M) | [verified] https://huggingface.co/ibm-granite/granite-4.0-1b |
| openbmb/MiniCPM4-0.5B | Apache-2.0, no registration mentioned | 0.5B (widget 0.4B); 32K pretrain | Not mentioned | No tool-calling claim | [verified] https://huggingface.co/openbmb/MiniCPM4-0.5B |
| Huawei openPangu-Embedded-1B (ascend-tribe, GitCode) | "OPENPANGU MODEL LICENSE AGREEMENT VERSION 1.0" (custom, not OSI). [licence text](https://raw.gitcode.com/ascend-tribe/openpangu-embedded-1b-model/raw/main/LICENSE) opened 2026-11-13 [verified that the text says this; the page does not itself prove the repository is Huawei's official one]: licensor Huawei Technologies Co., Ltd.; use, modify and distribute are granted at no charge; a product offered to others must say "Powered by openPangu"; the model must not be used "within the European Union"; commercial use is not stated | 1B non-embed, 26 layers, 32k | "fast-thinking" variant | None found; `trust_remote_code`; tuned for Ascend Atlas 200I A2 | size, layers and hardware facts [unverified]: they come from a third-party HF mirror https://hf-mirror.492719920.workers.dev/FreedomIntelligence/openPangu-Embedded-1B/raw/main/README.md |

Notes on the licence question for a competition entry. Apache-2.0 and MIT allow fine-tuning, redistributing adapters and any use, provided the licence and notice are kept [verified via the licence names on the cards above; the general meaning of Apache-2.0/MIT is standard and not re-checked today]. GLM edge small models were not checked.

## 4. Hosted models on Huawei Cloud MaaS (OpenAI-compatible API)

**International site** [verified] https://support.huaweicloud.com/intl/en-us/model-call-maas/model-call-021.html (updated 2026-11-11). The page says the function is "only supported in CN-Hong Kong". Base URL `https://api-ap-southeast-1.modelarts-maas.com/openai/v1`.

| Name shown | `model` value |
|---|---|
| GLM-5.3 | glm-5.3 |
| GLM-5.2 | glm-5.2 |
| GLM-5.1 | glm-5.1 |
| DeepSeek-V4-Flash | deepseek-v4-flash |
| DeepSeek-V4.1-Flash | deepseek-v4.1-flash |
| DeepSeek-V4.1-Flash-1017 | deepseek-v4.1-flash-1017 |

No Qwen and no small model is on the international list. The page gives no context lengths and no free quota. Default `max_tokens` is 64K for the V4.1 models and 4K for the others.

Open-weight status of the hosted targets:
- DeepSeek-V4-Flash: MIT, 284B total / 13B active MoE, 1M context, modes "Non-think, Think High, Think Max". [verified] https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash . The card does not say which mode is the default. How to choose Non-think through the MaaS API was not checked [unverified].
- GLM-5.1: MIT, 754B. [verified] https://huggingface.co/zai-org/GLM-5.1 . GLM-5.2 and 5.3 open-weight status not checked [unverified].

**China site** [verified] https://support.huaweicloud.com/model-call-maas/model-call-021.html (updated 2026-11-13). Region Southwest-Guiyang1. Models: openPangu-2.0-Pro (`openpangu-2.0-pro`), openPangu-2.0-Flash (`openpangu-2.0-flash`), GLM-5.3, GLM-5.2, GLM-5.2-ArkTS-SPARK, GLM-5.1, Kimi-K2.6 (`kimi-k2.6`), DeepSeek-V4.1-Flash-1017, DeepSeek-V4.1-Flash, DeepSeek-V4-Flash, **Qwen3-32B (`qwen3-32b`)**, **Qwen3-30B-A3B (`qwen3-30b-a3b`)**.
- China-site notice: DeepSeek-V3, DeepSeek-V3.2, DeepSeek-R1-0528 and GLM5 stopped sale 2026-08-03 and went offline 2026-08-15 in Guiyang1. [verified] https://www.huaweicloud.com/notice/20260803180226403.html . Models on MaaS get retired within months, so the code should keep the model name in config.

**Free quota, fine-tuning and custom deployment on MaaS:** not verified. Every MaaS user-guide and pricing page I tried returned 404 (intl `model-call-050`, `maas-modelarts-0002/0006`, `price-modelarts-0047`, `productdesc-modelarts_01_0057/0063`). Search snippets say MaaS has "free models and paid models", that built-in open models can be fine-tuned, and that self-deployed services are billed by compute [unverified, snippets only]. A China-site "Token Plan" with paid monthly bundles (e.g. 149 yuan/month) exists per snippet https://support.huaweicloud.com/Token-plan-maas/tokenplan-maas-0001.html [unverified].

## 5. The base weights that were downloaded

| | |
|---|---|
| Repository | https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct (the publisher's own) |
| Revision | `7ae557604adf67be50417f59c2c2f167def9a775` |
| Licence | Apache-2.0 (the `LICENSE` file is kept beside the weights) |
| Downloaded | 2026-11-13, each file from `https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/resolve/<revision>/<file>` |
| Where | `~/Desktop/Projects/biosentry-data/models/Qwen2.5-0.5B-Instruct/` (outside the repository), with `SHA256SUMS.txt` |
| Check | the sha256 of `model.safetensors` equals the one the publisher's repository lists for that file |

| File | Bytes | sha256 |
|---|---|---|
| `LICENSE` | 11,343 | `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e` |
| `README.md` | 4,917 | `b19c806a904db6dc878a0462e70b551f6b7ac78dfbb88c2eb966ca2b9109ae15` |
| `config.json` | 659 | `18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45` |
| `generation_config.json` | 242 | `e558847a8b4402616f1273797b015104dc266fe4b520056fca88823ba8f8ebe6` |
| `merges.txt` | 1,671,839 | `599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3` |
| `model.safetensors` | 988,097,824 | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| `tokenizer.json` | 7,031,645 | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| `tokenizer_config.json` | 7,305 | `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583` |
| `vocab.json` | 2,776,833 | `ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910` |

## 6. The adapter

| | |
|---|---|
| What it is | a LoRA adapter for `Qwen/Qwen2.5-0.5B-Instruct` at the revision in section 5 |
| Settings | rank 8, alpha 16, dropout 0.05, on `q_proj` and `v_proj`; 540,672 trainable parameters |
| Training | batch size 1, one pass planned over 476 examples, learning rate 2e-4, seed 20261113, loss on the answer tokens only |
| Optimizer | Adam with decoupled weight decay 0.01 (betas 0.9 and 0.999, eps 1e-8), written out from basic operators |
| Gradients | `mindtorch.autograd.value_and_grad` (on this backend `loss.backward()` is an empty function) |
| Framework | MindSpore 2.7.0 (CPU) through MindNLP, `transformers` 4.57.1, `peft` 0.17.1. The pin is `mindnlp==0.5.1`; the installed library reports its own version as 0.5.0rc2 |
| Stopped | by plan after step 200 of 476 (159 question examples, 41 planner examples; 127 minutes). Fixed for time before any held-out answer existed; not chosen on a score |
| File | `adapter_model.safetensors`, 2,175,168 bytes, sha256 `6f9ceb056616416e512df99542b6b85bc543bb9c6a4a18d6d48029dd48801670` |
| Where | `~/Desktop/Projects/biosentry-data/agent-tuning/run1/adapter-step200/` (outside the repository; not uploaded anywhere) |
| Release copy | `adapter-step200-release/` beside it, made by `python -m research.agent_tuning.upload_adapter prepare`: the same weights, and `adapter_config.json` with the base model's public name in place of a local path (899 bytes, sha256 `d117ed827f99cee837c9cea76c8b6ce5847e481dcaa08b89f8acb8a289a3aaaf`). `research/agent_tuning/weights.json` holds the sha256 of every base file and adapter file |
| Storage now | a private Hugging Face model repository (free). Prepared and not run: `upload_adapter upload` was never called, no repository exists, nothing is uploaded. Only the adapter, its config, its sha256 file and a card go there (2.1 MB). The base model is public at its publisher and is not stored again |
| Storage, final plan | a Huawei Cloud OBS bucket, when a paid Huawei Cloud account exists |
| On the server | the question worker reads read-only local copies and checks each by sha256 at start (`research/agent_tuning/SERVER.md`). Nothing is on the server yet |
| Weights, measured from the file | all 48 `lora_B` tensors are non-zero, mean absolute value 0.00224 (they start at zero) |
| Loss against the frozen control | below it on 199 of the 199 steps after the first, same examples in the same order. Mean question loss in steps 151 to 200: 0.060 trained, 0.499 frozen |
| Held-out evaluation | blind subsample, one run each, greedy decoding. Questions, answerable ones right after the guard: untuned 2 of 30, tuned 7 of 30, keyword matcher 13 of 30, ceiling 20 of 30. Refusal questions acted on after the guard: 0 of 48 for both models, 2 of 48 for the matcher. Planner, exactly the rule planner's pick: untuned 0 of 9, tuned 9 of 9. The tuned model alone is worse than the matcher. Every answer is in `training/agent/runs/run1.json` |

The first full run (476 steps) is kept as the frozen control in
`~/Desktop/Projects/biosentry-data/agent-tuning/run0-learned-nothing/`. Its adapter (sha256
`e273f4f76fd89e66114c73fd198320144f955230ef034c2d335998aa3de95a57`) has all 48 `lora_B` tensors
at zero: it learned nothing, and no held-out answer was ever taken from it.

## 7. The generated data

Generated by `python -m research.agent_tuning.generate --out <dir>` with the generator, the held-out
frames, the matcher and the guard as they are in this pull request (the branch commit that last
changed them was `0a8ff07` before the branch was rebased; a squash merge gives it another name, so
the check that counts is the sha256 below: the files were made again after the rebase and are the
same bytes). Seed 2611.
Same inputs, same bytes. No patient data of any kind: the planner examples are made-up states of
the agent's own ledger, and the questions are sentence frames written for this project.

| File | Lines | sha256 |
|---|---|---|
| `train.jsonl` | 1,500 | `5e9321f64deebc2b9835e6af6027c9723b51a2521c7c0faea26fa544bc6d7f48` |
| `heldout.jsonl` | 400 | `d16d2686e51beb85700d20592e3e652426316e8bbe7b364936c7a2ca9cf1dcdf` |

Kept outside the repository in `~/Desktop/Projects/biosentry-data/agent-tuning/data/` with its
`manifest.json`. Both files can be made again from the commit above.

| | Training | Held out |
|---|---|---|
| Planner examples (one per walk structure) | 300 | 100 |
| Questions | 1200 | 300 |
| of which answerable | 720 | 180 |
| of which must be refused | 480 | 120 |
| Question frames | 148 | 88 |

- Answerable questions by group, training: alerts_per_nurse/baseline 95, alerts_per_nurse/plain 113, alerts_per_nurse/threshold 44, baseline_vs_news2 212, news2_threshold 256. Held out: alerts_per_nurse/baseline 18, alerts_per_nurse/plain 36, alerts_per_nurse/threshold 20, baseline_vs_news2 42, news2_threshold 64.
- Refusals by category, training: ambiguous_threshold 67, approve_apply_write 59, change_product 58, held_out_or_real_data 54, off_topic 67, one_patient 61, prompt_injection 56, treatment_advice 58. Held out: ambiguous_threshold 16, approve_apply_write 15, change_product 15, held_out_or_real_data 15, off_topic 14, one_patient 15, prompt_injection 15, treatment_advice 15.
- Split rule: planner: sha256 of the walk's structure (experiment, ok, chosen per step, numbers ignored), one in four structures is held out; question: by frame, every frame is in one file only; the held-out frames are a separate file written without sight of the guard, and a training question whose words overlap a held-out question by 0.8 or more (Jaccard, numbers as one word) is dropped.
- The held-out question frames (`research/agent_tuning/heldout_frames.json`, 88 frames) were
  written by a separate writer who was given only a description of the three experiments and the
  refusal categories, not the matcher, the guard or the training frames. They are never edited to
  pass a check.
- 22 training questions were dropped because they were near copies of a held-out
  question. So the held-out text decides which training questions are kept; nothing else of it
  reaches training.
- Not independent: five phrases in the refusal list ("from tomorrow", "on all wards", "live
  system", "bay", "man") also appear in held-out refusal frames. They came from example sentences
  that were given both to the frame writer and to the list.
- The planner label is always the first open experiment shown in the message, and about three in
  four planner examples show only one. That task is close to copying.

## 8. MindSpore tooling: what was read before choosing the route

| Item | Fact | Mark, URL |
|---|---|---|
| MindSpore latest | 2.11.0, released 2026-10-29. requires-python `<3.13,>=3.9`; wheels for cp310/311/312 | [verified] https://pypi.org/project/mindspore/ |
| macOS arm64 wheels | PyPI has `macosx_11_0_arm64` for 2.11.0 (cp310 to 312) and 2.7.0 (cp39 to 311) | [verified] https://pypi.org/project/mindspore/ , https://pypi.org/pypi/mindspore/2.7.0/json |
| Linux aarch64 wheels | Not on PyPI. On repo.mindspore.cn: `linux_aarch64` for 2.7.0 and 2.7.1 (cp39 to 311), and 2.10.0 and 2.11.0 (cp310 to 312) | [verified] https://repo.mindspore.cn/pypi/simple/mindspore/ |
| MindSpore install selector | JS page, the options did not render | [unverified] https://www.mindspore.cn/install/en |
| MindNLP latest | 0.5.1, uploaded 2025-11-05. requires_dist `mindspore<=2.7.0,>=2.5.0`, `transformers>=4.55.0`, `peft>=0.15.2`, `accelerate>=1.6.0` | [verified] https://pypi.org/pypi/mindnlp/json |
| MindNLP status | GitHub releases: latest v0.5.1, no 0.6.x tag. The main-branch README now describes "MindAct", "a complete pivot to embodied AI with PyTorch", and says the repo "was originally MindNLP". The MindSpore code lives on the `legacy` branch | [verified] https://github.com/mindspore-lab/mindnlp/releases , https://github.com/mindspore-lab/mindnlp , https://github.com/mindspore-lab/mindnlp/tree/legacy |
| MindNLP version table | 0.6.x: MindSpore >=2.7.1; 0.5.x: 2.5.0 to 2.7.0; Python 3.10 to 3.11 for both. 0.6.x is not on PyPI | [verified] https://raw.githubusercontent.com/mindspore-lab/mindnlp/legacy/README.md |
| How MindNLP works | "When you import `mindnlp`, it automatically patches `transformers` and `diffusers` to use MindSpore as the backend." README: "PyTorch-compatible API via mindtorch", "Zero code changes - Just `import mindnlp`" | [verified] https://mindnlp.readthedocs.io/notes/faq/ , legacy README above |
| macOS / CPU | FAQ: "macOS: Intel and Apple Silicon (CPU only)"; "CPU: All platforms" | [verified] https://mindnlp.readthedocs.io/notes/faq/ |
| Training caveat | FAQ: "Using transformers directly may have gradient issues"; use MindNLP's native APIs for training | [verified] same FAQ |
| PEFT/LoRA | README lists "PEFT/LoRA - Parameter-efficient fine-tuning". The PEFT tutorial imports `from mindnlp.peft import LoraConfig, get_peft_model` (mt0-large example; no version or device given). 0.5.1 depends on HF `peft`, so the 0.5 path is probably HF peft via the patch | [verified] tutorial https://mindnlp.readthedocs.io/tutorials/peft/ ; "probably HF peft" is my inference [unverified] |
| Architectures | Supported-models page: "Qwen/Qwen2/Qwen3 ✅ ✅ Full series support" (inference, training); Llama/Llama2/Llama3; Phi-2/Phi-3; ChatGLM/GLM; Gemma. Qwen2.5, SmolLM3, Granite, MiniCPM not named (Qwen2.5 uses the Qwen2 class) | [verified] https://mindnlp.readthedocs.io/supported_models/ |
| Known problem | mindspore 2.7.0 + mindnlp 0.5.0: "Select CPU operator[Gather] fail! Unsupported data type!" when bf16 tensors hit the CPU Gather kernel. Means: load weights as float32 on CPU | [verified] https://discuss.mindspore.cn/t/topic/1383 |
| CPU LoRA of 0.5 to 2B documented? | Not found. No published MindNLP CPU or macOS LoRA run for Qwen was found | [unverified]: absence after search |
| MindFormers (MindSpore Transformers) latest | 2.1.0, uploaded 2026-10-19; `mindspore~=2.11.0`, `transformers>=5.3.0`, `mindpet==1.0.4`; summary "platform: linux, cpu: x86_64" | [verified] https://pypi.org/pypi/mindformers/json |
| MindFormers hardware | "当前支持的硬件为 Atlas 800T A2、Atlas 800I A2、Atlas 900 A3 SuperPoD" (Ascend only). Lists Qwen2.5 0.5B to 72B and Qwen3 0.6B to 32B (in development) | [verified] https://gitee.com/mindspore/mindformers (GitHub URL 404s) |
| MindFormers weights | Unified HF to MindSpore conversion tool, both directions (needs torch >=2.10.0); LoRA merge into base weights; conversion table lists GLM4, Qwen2.5, Mixtral | [verified] https://www.mindspore.cn/mindformers/docs/en/r1.9.0/feature/ckpt.html |

What "trained with MindSpore" means on the MindNLP route: you write ordinary Hugging Face `transformers`/`peft` code. `import mindnlp` swaps the torch layer under it for mindtorch, which runs MindSpore ops. So the tensors and gradients really are computed by MindSpore, but the model classes are Hugging Face's. Saved adapters are in HF format. That is a fair claim if it is worded as "MindSpore backend via MindNLP" [verified for the patching statement; the exact mechanism wording is my reading of the README and FAQ].

## 9. Huawei Cloud free packages

| Item | Fact | Mark, URL |
|---|---|---|
| Free packages page | "This page is no longer available." | [verified] https://activity.huaweicloud.com/intl/en-us/free_packages/index.html |
| Free trial page | 404 | [verified that it 404s] https://www.huaweicloud.com/intl/en-us/free-trial/ |
| ModelArts free notebook | Two free flavours: "CPU type, 2 vCPUs \| 4 GiB" and "GPU: 1 x P100 ... 8 vCPUs \| 64 GiB"; one of each per account; stop after one hour; released if not restarted in 72 h; "only in the CN North-Beijing4 region"; no OBS/EVS beyond 5 GB EVS. No date on the page, likely old | [verified] https://support.huaweicloud.com/en-us/engineers-modelarts/modelarts_23_0228.html |
| ModelArts free training flavours, free model import | Mentioned in search snippets | [unverified] snippet |
| MaaS free tokens | None found on the international site | [unverified] absence |
| OBS free tier | Snippet: "creating an OBS bucket is free"; storage billed | [unverified] snippet |
| ICT Competition / ICT Academy credits | No credit or voucher programme found. Snippet: Practice Competition Cloud tracks run on Huawei Cloud | [unverified] https://engineering.tiu.edu.iq/computer/wp-content/uploads/2026/09/Guidelines-Iraq-2026-2027.pdf (not opened) |
| Payment method needed? | Not stated on any page opened | [unverified] |

## 10. Export and serving a tuned model on Huawei

- MindFormers can merge LoRA weights into the base model and convert HF weights both ways for Qwen2.5. [verified] https://www.mindspore.cn/mindformers/docs/en/r1.9.0/feature/ckpt.html . This runs on Ascend.
- Export of a merged small transformer to MindIR: not found.
- MaaS custom-model deployment of user weights: not found (all relevant MaaS pages 404). A Huawei "quick deploy Qwen3" solution (0.6B to 14B fp16) dated 2025-04-30 appears in search https://support.huaweicloud.com/qwen3-aislt/qwen3_05.html [unverified, not opened].

## 11. Not verified, or could not be opened

- All MaaS user-guide, pricing, model-square and product-description pages on support.huaweicloud.com intl (HTTP 404): free tokens, fine-tuning base models, custom deployment, deep-thinking toggle via API.
- The Huawei Cloud international free packages page (retired) and free trial page (404).
- https://www.mindspore.cn/install/en and /versions/en (JS, no content rendered).
- openPangu-Embedded-1B: its card on GitCode did not render (card facts come from a third-party mirror); whether the `ascend-tribe` repository is Huawei's official one; whether its licence allows commercial use (the text does not say); whether openPangu-2.0-Pro and -Flash have open weights.
- GLM-5.2 / 5.3 licence; GLM edge small models; any newer small DeepSeek model (none found).
- Qwen3.5 release date (press snippets only).
- Any published MindNLP CPU or macOS LoRA run, and whether mindnlp 0.5.1 is pinned to a specific transformers version (requires_dist gives only a lower bound).
- Whether `import mindnlp` conflicts with a real `torch` installed in the same environment.
- Competition credits, payment-method requirement for Huawei Cloud trials.
