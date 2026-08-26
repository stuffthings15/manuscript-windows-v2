from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Callable, Iterable

from .polish_types import (
    GPU_BACKENDS,
    ExecutionPlan,
    GenerationRequest,
    GenerationResult,
    RuntimeBackend,
    RuntimeFailureKind,
)


_RUNTIME_FAILURES = frozenset(
    {
        RuntimeFailureKind.CONNECTION_FAILURE.value,
        RuntimeFailureKind.MODEL_LOADING.value,
        RuntimeFailureKind.LOAD_TIMEOUT.value,
        RuntimeFailureKind.GENERATION_TIMEOUT.value,
        RuntimeFailureKind.GPU_FAILURE.value,
        RuntimeFailureKind.INVALID_RESPONSE.value,
        RuntimeFailureKind.MODEL_UNAVAILABLE.value,
    }
)



@dataclass(frozen=True, slots=True)
class AcceptedInferenceLease:
    """One exact, locally verified runtime plan shared by every AI consumer."""

    plan: ExecutionPlan
    runtime_id: str
    epoch: int


class AcceptedInferenceCoordinator:
    """Proxy one runtime and publish accepted plans with stale-result protection."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self._lock = threading.RLock()
        self._lease: AcceptedInferenceLease | None = None
        self._epoch = 0
        self._request_epoch = 0
        self._latest_request_epoch = 0
        self._model_pack_ids: dict[str, str] = {}
        self._listeners: list[Callable[[AcceptedInferenceLease | None], None]] = []
        self._thread_result = threading.local()
        self._thread_update = threading.local()
        self._thread_operation = threading.local()
        self._notification_lock = threading.RLock()
        self._generation_condition = threading.Condition()
        self._next_generation_ticket = 0
        self._serving_generation_ticket = 0
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._runtime, name)

    @property
    def runtime_id(self) -> str:
        return str(self._runtime.runtime_id)

    @property
    def parallel_slots(self) -> int:
        # The bundled host currently owns one generation semaphore. Keep every
        # app consumer behind the same FIFO until the host reports and proves
        # more than one real slot.
        return 1

    def register_model_pack(self, pack_id: str, model: str) -> None:
        pack = str(pack_id or "").strip()
        name = str(model or "").strip()
        if not pack or not name:
            raise ValueError("Model-pack registration requires exact identifiers.")
        with self._lock:
            self._model_pack_ids[name] = pack

    def add_listener(
        self,
        listener: Callable[[AcceptedInferenceLease | None], None],
    ) -> None:
        if not callable(listener):
            raise TypeError("Accepted-plan listener must be callable.")
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def begin_update(self) -> int:
        with self._lock:
            self._request_epoch += 1
            token = self._request_epoch
            self._latest_request_epoch = token
        self._thread_result.value = None
        self._thread_update.value = token
        return token

    def finish_update(self, token: int) -> None:
        if getattr(self._thread_update, "value", None) == int(token):
            self._thread_update.value = None

    def lease(self) -> AcceptedInferenceLease | None:
        with self._lock:
            return self._lease

    def accept_verified(
        self,
        plan: ExecutionPlan,
        *,
        token: int | None = None,
        actual_model: str = "",
        actual_backend: RuntimeBackend | None = None,
    ) -> bool:
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("A full execution plan is required.")
        if (
            not plan.use_llm
            or not plan.pack_id
            or not plan.model
            or plan.backend is RuntimeBackend.RULES
        ):
            raise ValueError("Only a complete AI execution plan can be accepted.")
        resolved = self._resolve_actual_plan(
            plan,
            actual_model=actual_model,
            actual_backend=actual_backend,
        )
        runtime_id = self.runtime_id
        with self._notification_lock:
            with self._lock:
                if token is not None and token != self._latest_request_epoch:
                    return False
                self._epoch += 1
                lease = AcceptedInferenceLease(resolved, runtime_id, self._epoch)
                self._lease = lease
                self._model_pack_ids[resolved.model] = resolved.pack_id
                listeners = tuple(self._listeners)
            self._notify(listeners, lease)
            return True

    def clear(self, *, token: int | None = None) -> bool:
        with self._notification_lock:
            with self._lock:
                if token is not None and token != self._latest_request_epoch:
                    return False
                if self._lease is None:
                    return True
                self._epoch += 1
                self._lease = None
                listeners = tuple(self._listeners)
            self._notify(listeners, None)
            return True

    def publish_correction_result(self, result, token: int) -> bool:
        plan = getattr(result, "plan", None)
        if not isinstance(plan, ExecutionPlan):
            return False
        failure = getattr(result, "runtime_failure_kind", RuntimeFailureKind.NONE)
        failure_value = str(getattr(failure, "value", failure or "none"))
        if (
            bool(getattr(result, "used_llm", False))
            and bool(getattr(result, "review_ready", False))
            and plan.use_llm
        ):
            generation = self._generation_for_plan(plan)
            if generation is None:
                return False
            return self.accept_verified(
                plan,
                token=token,
                actual_model=str(getattr(generation, "model", "") or ""),
                actual_backend=getattr(generation, "backend", None),
            )
        lease = self.lease()
        if lease is not None and lease.runtime_id != self.runtime_id:
            self.clear(token=token)
            return False
        if (
            lease is not None
            and failure_value in _RUNTIME_FAILURES
            and self._same_execution_identity(lease.plan, plan)
        ):
            self.clear(token=token)
        return False

    def is_current(
        self,
        pack_id: str,
        model: str,
        policy_version: int,
        require_loaded: bool = False,
    ) -> bool:
        del require_loaded
        lease = self.lease()
        if lease is None:
            return False
        return bool(
            lease.plan.pack_id == str(pack_id or "").strip()
            and lease.plan.model == str(model or "").strip()
            and lease.plan.policy_version == int(policy_version)
            and lease.runtime_id == self.runtime_id
        )

    def preload(self, *, model: str, timeout: float) -> bool:
        lease = self._require_lease(model)
        if lease.runtime_id != self.runtime_id:
            return False
        bounded_timeout = max(2.0, min(90.0, float(timeout)))
        try:
            status = self.status(lease.plan.model, timeout=min(2.0, bounded_timeout))
            if (
                status.loaded
                and status.model == lease.plan.model
                and status.backend is lease.plan.backend
            ):
                return True
        except (OSError, RuntimeError, TypeError, ValueError):
            if lease.runtime_id != self.runtime_id:
                return False
        self.generate_task(
            "Warm start.",
            command="Proofread",
            instruction="Return the text unchanged.",
            model=lease.plan.model,
            timeout=bounded_timeout,
            max_output_tokens=32,
        )
        return True

    def available_models(self, timeout: float = 1.5) -> set[str]:
        del timeout
        lease = self.lease()
        return {lease.plan.model} if lease is not None else set()

    def refine(
        self,
        text: str,
        *,
        model: str,
        protected_terms: Iterable[str],
        timeout: float,
    ):
        from .correction import (
            CorrectionResult,
            _PROTECTED_PATTERN,
            _meaning_guard,
        )

        source = str(text or "").strip()
        if not source:
            return CorrectionResult(text, "deterministic", "Local AI received no text.")
        protected = tuple(str(value) for value in protected_terms if str(value))
        generation = self.generate_task(
            source,
            command="Proofread",
            instruction=(
                "Correct every grammar, spelling, punctuation, capitalization, "
                "and sentence-structure error. Preserve meaning and every protected value."
            ),
            profile="General",
            protected_terms=protected,
            timeout=timeout,
            model=model,
        )
        candidate = str(generation.text or "").strip().strip('"')
        if not candidate or "```" in candidate:
            return CorrectionResult(
                text,
                "deterministic",
                "Local AI returned an invalid response.",
            )
        required = list(protected)
        required.extend(_PROTECTED_PATTERN.findall(source))
        for value in dict.fromkeys(required):
            if source.count(value) > candidate.count(value):
                return CorrectionResult(
                    text,
                    "deterministic",
                    "Local AI changed protected content.",
                )
        if not _meaning_guard(source, candidate):
            return CorrectionResult(
                text,
                "deterministic",
                "Local AI output was rejected to protect meaning.",
            )
        return CorrectionResult(candidate, "ai")

    def generate_task(
        self,
        text: str,
        *,
        command: str,
        instruction: str = "",
        profile: str = "General",
        language: str = "",
        protected_terms: Iterable[str] = (),
        timeout: float = 30.0,
        max_output_tokens: int | None = None,
        model: str = "",
    ) -> GenerationResult:
        lease = self._require_lease(model)
        if lease.runtime_id != self.runtime_id:
            raise RuntimeError("The accepted local AI runtime changed. Retry the request.")
        bounded_timeout = max(2.0, min(300.0, float(timeout)))
        plan = replace(
            lease.plan,
            absolute_timeout_seconds=bounded_timeout,
            max_output_tokens=(
                lease.plan.max_output_tokens
                if max_output_tokens is None
                else max(16, min(4096, int(max_output_tokens)))
            ),
        )
        return self._generate_serialized(
            GenerationRequest(
                text=str(text),
                command=str(command),
                profile=str(profile),
                instruction=str(instruction),
                language=str(language),
                protected_terms=tuple(str(value) for value in protected_terms),
                plan=plan,
            ),
            expected_lease=lease,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        # PolishEngine uses this path while it is proving a new plan, before a
        # lease exists. User-facing consumers use generate_task(), which binds
        # their queued request to the exact accepted lease they observed.
        return self._generate_serialized(request, expected_lease=None)

    def _generate_serialized(
        self,
        request: GenerationRequest,
        *,
        expected_lease: AcceptedInferenceLease | None,
    ) -> GenerationResult:
        ticket = self._enter_operation()
        try:
            if expected_lease is not None:
                self._require_exact_lease(expected_lease)
            else:
                self._require_current_update()
            result = self._runtime.generate(request)
        finally:
            self._leave_operation(ticket)
        if expected_lease is None:
            self._require_current_update()
        self._thread_result.value = (request.plan, result)
        self._adopt_actual_model_for_current_lease(request.plan, result)
        if expected_lease is not None:
            resolved = self._resolve_actual_plan(
                expected_lease.plan,
                actual_model=result.model,
                actual_backend=result.backend,
            )
            self._require_exact_lease(expected_lease, resolved_plan=resolved)
        return result

    def status(self, model: str, *, timeout: float = 1.0):
        return self._runtime.status(model, timeout=timeout)

    def acceptance_probe(self, plan: ExecutionPlan):
        ticket = self._enter_operation()
        try:
            self._require_current_update()
            result = self._runtime.acceptance_probe(plan)
            self._require_current_update()
            return result
        finally:
            self._leave_operation(ticket)

    def prepare(self, plan: ExecutionPlan, *, consent: bool) -> bool:
        ticket = self._enter_operation()
        try:
            self._require_current_update()
            before = self.runtime_id
            prepared = bool(self._runtime.prepare(plan, consent=consent))
            self._require_current_update()
            after = self.runtime_id
            if before != after:
                self._clear_for_runtime_change(after)
            return prepared
        finally:
            self._leave_operation(ticket)

    def close(self) -> None:
        with self._generation_condition:
            self._closed = True
            self._generation_condition.notify_all()
        self._runtime.close()
        self.clear()

    def _require_lease(self, model: str = "") -> AcceptedInferenceLease:
        lease = self.lease()
        if lease is None:
            raise RuntimeError("Local AI is not ready. Try again shortly.")
        requested = str(model or "").strip()
        if requested and requested != lease.plan.model:
            raise RuntimeError("The requested model is not the accepted local AI plan.")
        return lease

    def _require_current_update(self) -> None:
        token = getattr(self._thread_update, "value", None)
        if token is None:
            return
        with self._lock:
            current = self._latest_request_epoch
        if int(token) != current:
            raise RuntimeError("A newer local AI request replaced this work.")

    def _enter_operation(self) -> int:
        if bool(getattr(self._thread_operation, "active", False)):
            raise RuntimeError("Nested local AI work is not allowed.")
        with self._generation_condition:
            if self._closed:
                raise RuntimeError("Local AI is shutting down.")
            ticket = self._next_generation_ticket
            self._next_generation_ticket += 1
            while ticket != self._serving_generation_ticket and not self._closed:
                self._generation_condition.wait()
            if self._closed:
                raise RuntimeError("Local AI is shutting down.")
            self._thread_operation.active = True
            return ticket

    def _leave_operation(self, ticket: int) -> None:
        self._thread_operation.active = False
        with self._generation_condition:
            if ticket == self._serving_generation_ticket:
                self._serving_generation_ticket += 1
            self._generation_condition.notify_all()

    def _require_exact_lease(
        self,
        expected: AcceptedInferenceLease,
        *,
        resolved_plan: ExecutionPlan | None = None,
    ) -> AcceptedInferenceLease:
        runtime_id = self.runtime_id
        with self._lock:
            current = self._lease
        if current is None or current.runtime_id != runtime_id:
            raise RuntimeError("The accepted local AI runtime changed. Retry the request.")
        if resolved_plan is None:
            if current.epoch != expected.epoch:
                raise RuntimeError("The accepted local AI plan changed. Retry the request.")
            wanted = expected.plan
        else:
            wanted = resolved_plan
        if not self._same_execution_identity(current.plan, wanted):
            raise RuntimeError("The accepted local AI plan changed. Retry the request.")
        return current

    def _generation_for_plan(self, plan: ExecutionPlan) -> GenerationResult | None:
        value = getattr(self._thread_result, "value", None)
        if not isinstance(value, tuple) or len(value) != 2:
            return None
        generated_plan, result = value
        if not self._same_plan_identity(generated_plan, plan):
            return None
        return result if isinstance(result, GenerationResult) else None

    def _resolve_actual_plan(
        self,
        plan: ExecutionPlan,
        *,
        actual_model: str,
        actual_backend: RuntimeBackend | None,
    ) -> ExecutionPlan:
        model = str(actual_model or plan.model).strip()
        backend = RuntimeBackend(actual_backend or plan.backend)
        if plan.require_full_gpu and backend not in GPU_BACKENDS:
            raise RuntimeError("Local AI spilled to the CPU and was not accepted.")
        if model == plan.model:
            return replace(plan, backend=backend)
        if model != plan.standby_model:
            raise RuntimeError("Local AI returned an unapproved model identity.")
        with self._lock:
            pack_id = self._model_pack_ids.get(model, "")
        if not pack_id:
            raise RuntimeError("The accepted standby model pack is unknown.")
        # The standby served THIS request. The accepted plan stays the primary.
        #
        # polish_runtime already treats a standby answer as valid for the
        # request it served - it sets expected_model to plan.standby_model
        # when used_standby is set, so the generation is not a mismatch. The
        # only thing that made a standby answer permanent was this function
        # returning it as the whole plan with standby_model cleared, which
        # left nothing to fall back FROM next time.
        #
        # Measured 2026-08-25: a run pinned to qwen3:14b prewarmed and
        # accepted it, the standby answered while the 9.3 GB primary was cold,
        # and all 23 later acceptances were gemma4-e2b. The bigger the
        # primary, the more likely it loses that first race, and losing it
        # once was final - which is why nothing above gemma4-e4b could be
        # measured through the app.
        return plan

    def _adopt_actual_model_for_current_lease(
        self,
        request_plan: ExecutionPlan,
        result: GenerationResult,
    ) -> None:
        with self._lock:
            lease = self._lease
            if lease is None or not self._same_plan_identity(lease.plan, request_plan):
                return
            expected_epoch = lease.epoch
        if result.model == lease.plan.model and result.backend is lease.plan.backend:
            return
        resolved = self._resolve_actual_plan(
            lease.plan,
            actual_model=result.model,
            actual_backend=result.backend,
        )
        runtime_id = self.runtime_id
        with self._notification_lock:
            with self._lock:
                current = self._lease
                if current is None or current.epoch != expected_epoch:
                    return
                self._epoch += 1
                updated = AcceptedInferenceLease(resolved, runtime_id, self._epoch)
                self._lease = updated
                listeners = tuple(self._listeners)
            self._notify(listeners, updated)

    def _clear_for_runtime_change(self, runtime_id: str) -> None:
        with self._notification_lock:
            with self._lock:
                if self._lease is None or self._lease.runtime_id == runtime_id:
                    return
                self._epoch += 1
                self._lease = None
                listeners = tuple(self._listeners)
            self._notify(listeners, None)

    @staticmethod
    def _same_plan_identity(left: ExecutionPlan, right: ExecutionPlan) -> bool:
        return bool(
            left.pack_id == right.pack_id
            and left.model == right.model
            and left.policy_version == right.policy_version
        )

    @staticmethod
    def _same_execution_identity(left: ExecutionPlan, right: ExecutionPlan) -> bool:
        return bool(
            left.pack_id == right.pack_id
            and left.model == right.model
            and left.policy_version == right.policy_version
            and left.backend is right.backend
            and left.context_window == right.context_window
            and left.require_full_gpu == right.require_full_gpu
        )

    @staticmethod
    def _notify(listeners, lease: AcceptedInferenceLease | None) -> None:
        for listener in listeners:
            try:
                listener(lease)
            except (OSError, RuntimeError, TypeError, ValueError):
                continue


__all__ = ["AcceptedInferenceCoordinator", "AcceptedInferenceLease"]
