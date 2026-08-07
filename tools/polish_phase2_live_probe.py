from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from echo_link_core.polish_model_packs import ModelPackManager
from echo_link_core.polish_hardware import POLISH_POLICY_VERSION
from echo_link_core.polish_runtime import AdaptiveInferenceRuntime
from echo_link_core.polish_runtime_packs import RuntimePackManager
from echo_link_core.polish_types import (
    ExecutionPlan,
    GenerationRequest,
    PolishMode,
    PolishTask,
    RuntimeBackend,
)


def main() -> int:
    model_packs = ModelPackManager()
    runtime_packs = RuntimePackManager()
    runtime = AdaptiveInferenceRuntime(
        model_packs=model_packs,
        runtime_packs=runtime_packs,
        host_path=(
            ROOT
            / "native"
            / "polish-host"
            / "bin"
            / "Release"
            / "net8.0"
            / "win-x64"
            / "Manuscript.PolishHost.dll"
        ),
    )
    pack = model_packs.get("gemma4-12b-q4-k-m-v1")
    plan = ExecutionPlan(
        policy_version=POLISH_POLICY_VERSION,
        mode=PolishMode.BALANCED,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-12b-q4-k-m-v1",
        model=pack.model,
        backend=RuntimeBackend.CUDA,
        context_window=2048,
        max_output_tokens=128,
        memory_budget_bytes=32 * 1024**3,
        vram_reserve_bytes=3 * 1024**3,
        require_full_gpu=True,
        retry_limit=1,
        warm_latency_limit_seconds=5.0,
        absolute_timeout_seconds=30.0,
        requires_probe=True,
    )
    evidence: dict[str, object] = {
        "schema_version": 1,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "runtime_id": runtime.runtime_id,
        "plan": {
            "pack_id": plan.pack_id,
            "model": plan.model,
            "backend": plan.backend.value,
            "context_window": plan.context_window,
            "require_full_gpu": plan.require_full_gpu,
        },
    }
    try:
        prepared = runtime.prepare(plan, consent=False)
        evidence["prepared"] = prepared
        evidence["downgrade_reason"] = runtime.last_downgrade_reason
        evidence["runtime_id"] = runtime.runtime_id
        probe = runtime.acceptance_probe(plan)
        evidence["acceptance_probe"] = {
            "accepted": probe.accepted,
            "elapsed_seconds": probe.elapsed_seconds,
            "correctness_passed": probe.correctness_passed,
            "protected_passed": probe.protected_passed,
            "backend": probe.backend.value,
            "reason": probe.reason,
        }
        source = (
            "Every day, the report are not ready, and it fail. Keep build 47 at "
            "http://127.0.0.1:7777/protected until July 23, 2026."
        )
        generation = runtime.generate(
            GenerationRequest(
                text=source,
                command="Proofread",
                profile="General",
                instruction=(
                    "Correct every grammar error. Keep the date, build number, URL, "
                    "and negative statement exact."
                ),
                language="",
                protected_terms=(),
                plan=plan,
            )
        )
        status = runtime.status(plan.model, timeout=2.0)
        evidence["generation"] = {
            "text": generation.text,
            "elapsed_seconds": generation.elapsed_seconds,
            "input_tokens": generation.input_tokens,
            "output_tokens": generation.output_tokens,
            "backend": generation.backend.value,
        }
        evidence["status"] = {
            "loaded": status.loaded,
            "backend": status.backend.value,
            "model_bytes": status.model_bytes,
            "gpu_bytes": status.gpu_bytes,
            "detail": status.detail,
        }
        evidence["invariants"] = {
            "url_preserved": "http://127.0.0.1:7777/protected" in generation.text,
            "date_preserved": "July 23, 2026" in generation.text,
            "build_preserved": "47" in generation.text,
            "negation_preserved": "not ready" in generation.text.casefold(),
            "agreement_fixed": "report is" in generation.text.casefold(),
            "verb_fixed": "it fails" in generation.text.casefold(),
            "full_gpu_resident": (
                status.backend is RuntimeBackend.CUDA and status.gpu_bytes > 0
            ),
        }
        evidence["passed"] = bool(
            probe.accepted
            and all(evidence["invariants"].values())
            and generation.elapsed_seconds <= 5.0
        )
    except Exception as exc:
        evidence["passed"] = False
        evidence["error"] = str(exc)[:500]
    finally:
        runtime.close()
    output = ROOT / "build" / "phase2-live-probe.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
