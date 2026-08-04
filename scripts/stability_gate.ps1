param(
    [switch]$SkipBuildDryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Marker = Join-Path $Root ".manuscript-canonical-root"
$ExpectedMarker = "manuscript-windows-v2-canonical-v1"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $Root)
    $Python = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
}
if (
    -not (Test-Path -LiteralPath $Marker) -or
    (Get-Content -Raw -LiteralPath $Marker).Trim() -ne $ExpectedMarker
) {
    throw "Run this gate from the canonical Manuscript/Windows Version 2 project root."
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing project Python environment."
}

$GateBase = Join-Path ([System.IO.Path]::GetTempPath()) "ManuscriptStabilityGate"
$GateRoot = Join-Path $GateBase ([guid]::NewGuid().ToString("N"))
$DataRoot = Join-Path $GateRoot "data"
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null

$ManagedEnvironment = @(
    "PYTHONPATH",
    "MANUSCRIPT_V2_DATA_DIR",
    "ECHOLINK_DISABLE_HOTKEY",
    "ECHOLINK_DISABLE_SOUNDS",
    "ECHOLINK_DISABLE_MEDIA_CONTROL",
    "ECHOLINK_DISABLE_AUTO_HUD",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "QT_QPA_PLATFORM"
)
$PreviousEnvironment = @{}
foreach ($Name in $ManagedEnvironment) {
    $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable(
        $Name,
        "Process"
    )
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:MANUSCRIPT_V2_DATA_DIR = $DataRoot
$env:ECHOLINK_DISABLE_HOTKEY = "1"
$env:ECHOLINK_DISABLE_SOUNDS = "1"
$env:ECHOLINK_DISABLE_MEDIA_CONTROL = "1"
$env:ECHOLINK_DISABLE_AUTO_HUD = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:QT_QPA_PLATFORM = "offscreen"

$Groups = @(
    @(
        "tests/test_packaging.py",
        "tests/test_documents.py::test_schema_v6_upgrade_preserves_existing_history_and_adds_workspace",
        "tests/test_drafts_panel.py::test_drafts_panel_lists_all_templates_and_runs_on_worker"
    ),
    @(
        "tests/test_rewrite_palette.py::test_resizing_card_expands_original_suggested_and_queue_text",
        "tests/test_rewrite_palette.py::test_ai_proofread_prefetches_next_replacement_before_user_decision",
        "tests/test_rewrite_palette.py::test_ten_section_document_is_prefetched_before_first_decision_and_advances_ready",
        "tests/test_rewrite_palette.py::test_ten_section_prefetch_continues_after_third_section_fails_quality_gate",
        "tests/test_rewrite_palette.py::test_failed_sixth_section_is_repaired_after_all_eleven_sections_are_prefetched",
        "tests/test_rewrite_palette.py::test_pending_queue_hides_rule_text_until_each_ai_result_is_ready",
        "tests/test_rewrite_palette.py::test_accept_applies_each_section_immediately_before_advancing",
        "tests/test_rewrite_palette.py::test_automatic_badge_disappears_when_text_entry_loses_focus",
        "tests/test_rewrite_palette.py::test_queued_focus_clear_during_badge_press_does_not_close_hud",
        "tests/test_rewrite_palette.py::test_palette_word_accept_replaces_only_current_review_range",
        "tests/test_rewrite_palette.py::test_failed_document_step_keeps_current_review_instead_of_resetting_to_zero",
        "tests/test_rewrite_palette.py::test_window_keeps_user_opened_hud_but_clears_target_when_focus_leaves_text_box"
    ),
    @(
        "tests/test_insertion.py::test_word_document_replace_uses_direct_adapter_without_foreground_recheck",
        "tests/test_insertion.py::test_replace_selection_retries_transient_focus_recovery",
        "tests/test_insertion.py::test_word_replacement_waits_for_async_paste_before_clipboard_restore",
        "tests/test_insertion.py::test_activate_does_not_restore_or_resize_non_minimized_window",
        "tests/test_insertion.py::test_activate_restores_window_only_when_minimized"
    ),
    @(
        "tests/test_polish_hardware.py::test_manual_correction_reuses_installed_quality_pack_after_latency_failure",
        "tests/test_polish_hardware.py::test_loaded_different_gpu_pack_is_reclaimable_for_fast_pack",
        "tests/test_sound_cues.py::test_activation_sound_uses_original_windows_speech_on_cue",
        "tests/test_sound_cues.py::test_original_windows_activation_file_is_played_instead_of_generated_tone",
        "tests/test_gui.py::test_system_tray_keeps_hotkey_service_ready_when_window_closes",
        "tests/test_gui.py::test_dictation_hotkey_uses_target_captured_at_physical_keypress",
        "tests/test_windows_workflow_probe_cli.py::test_app_cli_dispatches_probe_before_live_logging",
        "tests/test_windows_workflow_probe_cli.py::test_app_cli_target_mode_is_hidden_and_does_not_log"
    ),
    @(
        "tests/test_shared_contract.py",
        "tests/test_polish.py",
        "tests/test_polish_quality_repair.py",
        "tests/test_selected_text.py"
    )
)

try {
    $GroupNumber = 0
    foreach ($Group in $Groups) {
        $GroupNumber += 1
        $BaseTemp = Join-Path $GateRoot ("pytest-" + $GroupNumber)
        & $Python -m pytest -q --basetemp $BaseTemp -p no:cacheprovider @Group
        if ($LASTEXITCODE -ne 0) {
            throw "Stability test group $GroupNumber failed."
        }
    }

    & $Python (Join-Path $Root "tools\privacy_check.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Privacy check failed."
    }

    if (-not $SkipBuildDryRun) {
        & $Python (Join-Path $Root "tools\build.py") --dry-run --testing-unlocked
        if ($LASTEXITCODE -ne 0) {
            throw "Canonical build dry-run failed."
        }
    }

    Write-Host "Manuscript stability gate passed."
}
finally {
    foreach ($Name in $ManagedEnvironment) {
        [Environment]::SetEnvironmentVariable(
            $Name,
            $PreviousEnvironment[$Name],
            "Process"
        )
    }
    $ExpectedPrefix = $GateBase + [System.IO.Path]::DirectorySeparatorChar
    $ResolvedGateRoot = [System.IO.Path]::GetFullPath($GateRoot)
    if (
        -not $ResolvedGateRoot.StartsWith(
            $ExpectedPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "Refusing to clean data outside the stability gate folder."
    }
    if (Test-Path -LiteralPath $ResolvedGateRoot) {
        Remove-Item -LiteralPath $ResolvedGateRoot -Recurse -Force
    }
}
