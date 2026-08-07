from echo_link_core.polish_types import (
    ExecutionPlan,
    PolishMode,
    PolishTask,
    RuntimeBackend,
)


def test_execution_plan_user_status_hides_model_identity():
    value = ExecutionPlan(
        policy_version=1,
        mode=PolishMode.BALANCED,
        task=PolishTask.PROOFREAD,
        use_llm=True,
        pack_id="gemma4-e4b-it-qat-v1",
        model="gemma4:e4b-it-qat",
        backend=RuntimeBackend.CUDA,
    )

    assert value.status_label == "Local AI | CUDA"
    assert value.model not in value.status_label
    assert value.model in value.diagnostic_label
