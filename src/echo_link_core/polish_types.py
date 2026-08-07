from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, runtime_checkable


class PolishMode(str, Enum):
    AUTO = "auto"
    INSTANT = "instant"
    BALANCED = "balanced"
    PROFESSIONAL = "professional"

    @classmethod
    def coerce(cls, value: str | "PolishMode") -> "PolishMode":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().casefold())
        except ValueError:
            return cls.AUTO


class PolishTask(str, Enum):
    PROOFREAD = "proofread"
    REWRITE = "rewrite"
    TONE = "tone"
    DOCUMENT = "document"


class ContextScope(str, Enum):
    NONE = "none"
    SENTENCE = "sentence"
    PARAGRAPH = "paragraph"
    DOCUMENT = "document"


class RuntimeBackend(str, Enum):
    RULES = "rules"
    CPU = "cpu"
    CUDA = "cuda"
    VULKAN = "vulkan"
    HIP = "hip"
    SYCL = "sycl"


class RuntimeFailureKind(str, Enum):
    NONE = "none"
    CONNECTION_FAILURE = "connection_failure"
    MODEL_LOADING = "model_loading"
    LOAD_TIMEOUT = "load_timeout"
    GENERATION_TIMEOUT = "generation_timeout"
    GPU_FAILURE = "gpu_failure"
    INVALID_RESPONSE = "invalid_response"
    MODEL_UNAVAILABLE = "model_unavailable"
    VALIDATION_FAILURE = "validation_failure"


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    cpu_name: str
    cpu_threads: int
    cpu_features: tuple[str, ...]
    total_ram_bytes: int
    available_ram_bytes: int
    gpu_name: str = ""
    gpu_vendor: str = ""
    dedicated_vram_bytes: int = 0
    available_vram_bytes: int = 0
    driver_version: str = ""
    driver_healthy: bool = False
    on_battery: bool = False
    battery_percent: int | None = None
    supported_backends: tuple[RuntimeBackend, ...] = (RuntimeBackend.CPU,)
    runtime_version: str = ""
    loaded_models: tuple[str, ...] = ()
    loaded_model_vram: tuple[tuple[str, int], ...] = ()

    @property
    def total_ram_gb(self) -> float:
        return self.total_ram_bytes / (1024**3)

    @property
    def available_ram_gb(self) -> float:
        return self.available_ram_bytes / (1024**3)

    @property
    def dedicated_vram_gb(self) -> float:
        return self.dedicated_vram_bytes / (1024**3)

    @property
    def available_vram_gb(self) -> float:
        return self.available_vram_bytes / (1024**3)


@dataclass(frozen=True, slots=True)
class PolishModelPack:
    pack_id: str
    display_name: str
    model: str
    family: str
    parameters: str
    quantization: str
    runtime: str
    manifest_digest: str
    size_bytes: int
    min_ram_bytes: int
    min_vram_bytes: int
    max_context_tokens: int
    supported_backends: tuple[RuntimeBackend, ...]
    license_id: str
    source_url: str
    download_ref: str
    version: str = "1"
    available: bool = True
    installed: bool = False
    validated: bool = False


@dataclass(frozen=True, slots=True)
class ClassifierDecision:
    needs_llm: bool
    error_categories: tuple[str, ...]
    severity: float
    context_scope: ContextScope
    english_confidence: float
    backend: str
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    policy_version: int
    mode: PolishMode
    task: PolishTask
    use_llm: bool
    pack_id: str = ""
    model: str = ""
    backend: RuntimeBackend = RuntimeBackend.RULES
    context_window: int = 0
    max_output_tokens: int = 0
    memory_budget_bytes: int = 0
    vram_reserve_bytes: int = 0
    require_full_gpu: bool = False
    retry_limit: int = 0
    warm_latency_limit_seconds: float = 0.0
    absolute_timeout_seconds: float = 30.0
    cold_load_timeout_seconds: float = 90.0
    requires_probe: bool = False
    # A small model kept loaded beside the main one, used only when the main
    # model cannot answer in time - a cold load, or another application taking
    # the GPU mid-document. Correcting a section with a smaller model beats
    # showing the reader nothing, and it is small enough to sit in spare VRAM
    # alongside the primary pack, or on the CPU when there is no room.
    standby_model: str = ""
    standby_timeout_seconds: float = 25.0
    reason: str = ""
    downgrade_reason: str = ""

    @property
    def status_label(self) -> str:
        if not self.use_llm:
            return "Local correction"
        backend = self.backend.value.upper()
        return f"Local AI | {backend}"

    @property
    def diagnostic_label(self) -> str:
        """Internal runtime identity. Never display this label to users."""
        if not self.use_llm:
            return "Rules + MiniLM"
        backend = self.backend.value.upper()
        return f"{backend} | {self.model} | {self.pack_id}"


@dataclass(frozen=True, slots=True)
class CorrectionRequest:
    text: str
    task: PolishTask = PolishTask.PROOFREAD
    mode: PolishMode = PolishMode.AUTO
    command: str = "Proofread"
    profile: str = "General"
    instruction: str = ""
    language: str = ""
    protected_terms: tuple[str, ...] = ()
    manual: bool = False
    download_consent: bool = False
    preverified_fallback_available: bool = False
    # The whole document the text was taken from, when the caller has it. A
    # section alone cannot answer "how often does this word occur", and that
    # count is what separates a place name from an ordinary word the writer
    # happened to capitalise at a clause opening.
    document: str = ""
    plan_callback: Callable[[ExecutionPlan], None] | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    text: str
    command: str
    profile: str
    instruction: str
    language: str
    protected_terms: tuple[str, ...]
    plan: ExecutionPlan


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model: str
    backend: RuntimeBackend
    elapsed_seconds: float
    cold_load_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    semantic_similarity: float
    failures: tuple[str, ...] = ()
    # Observations that describe the candidate without refusing it. They steer
    # the retry prompt and appear in diagnostics, but a candidate carrying only
    # advisories is still accepted. Meaning changes that would mislead a reader
    # stay in `failures`; drift that only reads as rephrasing lands here.
    advisories: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolishEdit:
    operation: str
    original: str
    replacement: str
    original_start: int
    original_end: int


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    original_text: str
    normalized_text: str
    deterministic_text: str
    text: str
    classifier: ClassifierDecision
    plan: ExecutionPlan
    validation: ValidationResult
    edits: tuple[PolishEdit, ...] = ()
    used_llm: bool = False
    rejected_llm: bool = False
    retries: int = 0
    message: str = ""
    quality_complete: bool = False
    residual_errors: tuple[str, ...] = ()
    tone_verified: bool = False
    quality_score: float = 0.0
    runtime_failure_kind: RuntimeFailureKind = RuntimeFailureKind.NONE
    cold_load_seconds: float = 0.0
    generation_seconds: float = 0.0
    review_ready: bool = False
    metrics: dict[str, float | int | str | bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    model: str
    loaded: bool
    backend: RuntimeBackend
    model_bytes: int = 0
    gpu_bytes: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AcceptanceProbeResult:
    accepted: bool
    elapsed_seconds: float
    correctness_passed: bool
    protected_passed: bool
    backend: RuntimeBackend
    reason: str = ""
    model_identity: str = ""


@runtime_checkable
class InferenceRuntime(Protocol):
    @property
    def runtime_id(self) -> str: ...

    def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def status(self, model: str, *, timeout: float = 1.0) -> RuntimeStatus: ...

    def acceptance_probe(self, plan: ExecutionPlan) -> AcceptanceProbeResult: ...


@runtime_checkable
class PolishSettingsStore(Protocol):
    def get_meta(self, key: str, default=None): ...

    def set_meta(self, key: str, value) -> None: ...


__all__ = [
    "AcceptanceProbeResult",
    "ClassifierDecision",
    "ContextScope",
    "CorrectionRequest",
    "CorrectionResult",
    "ExecutionPlan",
    "GenerationRequest",
    "GenerationResult",
    "HardwareSnapshot",
    "InferenceRuntime",
    "PolishEdit",
    "PolishMode",
    "PolishModelPack",
    "PolishSettingsStore",
    "PolishTask",
    "RuntimeBackend",
    "RuntimeFailureKind",
    "RuntimeStatus",
    "ValidationResult",
]
