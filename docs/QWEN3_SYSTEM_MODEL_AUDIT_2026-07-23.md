# Manuscript Qwen 3 System and Model Audit

Date: 2026-07-23  
Scope: Local Windows proofreading, grammar correction, whole-page rewrites, and 12 tone settings.

## Decision

Use `qwen3:14b` with Ollama's `Q4_K_M` quantization.

This is the best production fit for this specific computer. It is a dense 14.8-billion-parameter model, occupies 9.3 GB, fits inside the computer's 16 GB discrete GPU memory with room for context and runtime overhead, is already installed, and is already configured as Manuscript's default.

Do not move Manuscript to 30B, 32B, or 235B on this computer now. Those models do not fully fit the GPU. The 30B and 32B packages also exceed the currently available disk space.

The model choice is final. Release-quality acceptance is not final because the NVIDIA GPU is presently failed with Windows Problem Code 43.

## Audited System

| Component | Measured result | Effect |
|---|---|---|
| Computer | Alienware m18 R2 | Mobile GPU power and memory limits apply |
| Operating system | Windows 11 Pro 64-bit, build 26200 | Supported local Ollama platform |
| CPU | Intel Core i9-14900HX, 32 logical processors | Strong fallback CPU, but too slow for interactive 14B whole-page work |
| RAM | 65,536 MB | Enough for CPU fallback and partial model offload |
| Discrete GPU | NVIDIA GeForce RTX 4090 Laptop GPU | Correct accelerator for the selected model |
| Discrete VRAM | 16 GB GDDR6 per Dell's m18 R2 specification | `qwen3:14b` Q4 fits; larger Q4 models do not fully fit |
| GPU health | Windows Problem Code 43, `CM_PROB_FAILED_POST_START` | Blocks CUDA and GPU inference |
| NVIDIA driver | 32.0.16.1074, package `oem42.inf`, signed | Driver is installed but the device failed to start |
| Integrated GPU | Intel UHD Graphics, started | Not suitable for the selected 14B workload |
| System RAM reported to Ollama | 63.7 GiB | CPU inference remains possible |
| Disk C: | 3,791.42 GiB total; 15.58 GiB free | Not enough free space to pull a 19-20 GB model safely |
| Power plan | High performance | Correct for local inference |
| Ollama | 0.31.1, loopback service | Meets local-only product boundary |
| Installed Qwen 3 model | `qwen3:14b`, digest `bdbd181c33f2`, 9.3 GB | Selected model is present |

## Current Runtime Measurement

The audited request used `qwen3:14b`, `think=false`, temperature 0, a 4,096-token context, and a 64-token output limit.

- Input: `we was ready but teh file wasnt saved`
- Output: `We were ready, but the file wasn't saved.`
- Correctness: passed this measured sentence.
- Total time: 13.043 seconds, including a 10.189-second load.
- Output speed: 6.98 tokens per second.
- Ollama-reported GPU memory: 0 bytes.

The result proves the model works on CPU. It does not prove acceptable interactive or whole-page performance.

## Candidate Review

| Model | Ollama size | Fit and decision |
|---|---:|---|
| `qwen3:8b` Q4 | 5.2 GB | Fastest viable fallback, but less capable than the selected dense 14B model |
| `qwen3:14b` Q4_K_M | 9.3 GB | **Selected.** Best balance of capability, full-GPU fit, context headroom, latency, and disk use |
| `qwen3:14b` Q8 | 16 GB | Too tight for a 16 GB GPU after context and runtime overhead |
| `qwen3:30b-a3b-instruct-2507` Q4_K_M | 19 GB | Stronger candidate for a larger-memory system, but requires CPU/RAM spill here and cannot be downloaded with current free disk |
| `qwen3:32b` Q4 | 20 GB | Does not fit the 16 GB GPU or current free disk |
| `qwen3:235b` Q4 | 142 GB | Not suitable for this computer or interactive HUD use |

The Qwen team describes 30B-A3B as a mixture-of-experts model with 30 billion total and 3 billion active parameters. It is attractive for a later comparison, but full model weights still exceed this computer's VRAM.

## Manuscript Workload Fit

Manuscript needs:

- exact spelling, grammar, punctuation, and formatting repair;
- preservation of actors, facts, timing, obligations, numbers, quotes, URLs, and privacy boundaries;
- 12 distinct tone behaviors;
- local-only loopback inference;
- a response inside the Writing HUD's bounded interaction window;
- enough memory for whole-page chunks and review output.

`qwen3:14b` Q4_K_M is the largest dense Qwen 3 model that meets the hardware-fit requirement without routine CPU offload.

## GPU Idle Policy

Every normal Manuscript Ollama workload now sends `keep_alive: "300s"`. Each new request resets Ollama's idle deadline. If no request uses the model for five minutes, Ollama unloads it from GPU memory until the next request.

The policy covers proofreading, tone rewrites, dictation refinement, document writing, local source chat, and local draft generation. Settings display the fixed 300-second policy, and stored older values migrate to 300 seconds.

## Blocking Defect

Windows detects the RTX 4090 Laptop GPU but reports Problem Code 43. Ollama therefore detects only CPU inference and reports `size_vram: 0`.

An attempted targeted device restart was denied because the current session does not have Administrator rights. This is a system/driver blocker, not a Qwen model defect.

## Required Repair and Acceptance Gate

1. Reboot Windows.
2. If Code 43 remains, repair or clean-install the Dell/NVIDIA graphics driver with Administrator rights.
3. Confirm the NVIDIA device status is `Started`.
4. Confirm `nvidia-smi` succeeds.
5. Restart Ollama and confirm `/api/ps` reports nonzero GPU memory for `qwen3:14b`.
6. Rerun the protected 12-tone whole-page test.
7. Require 12/12 passing tones, no protected-fact loss, all focused tests passing, and completion inside the HUD timeout.

Do not claim 100% Qwen 3 acceptance until these gates pass.

## Sources

- Local evidence: `.test-data/qwen3-system-dxdiag.txt`
- Local evidence: Windows `pnputil`, registry, disk, Ollama tags, runtime response, and `/api/ps` checks from 2026-07-23
- Dell Alienware m18 R2 GPU specification: https://www.dell.com/support/manuals/en-us/alienware-m18-r2-laptop/alienware-m18-r2-owners-manual/gpudiscrete
- Ollama Qwen 3 tags and package sizes: https://ollama.com/library/qwen3/tags
- Qwen 3 architecture and model family: https://qwenlm.github.io/blog/qwen3/
