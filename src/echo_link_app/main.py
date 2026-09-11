from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from echo_link_app.branding import APP_NAME, ICON_ICO
from echo_link_app.startup import configure_startup
from echo_link_app.subscription_dialog import EntitlementCheckThread, SubscriptionDialog
from echo_link_app.theme import apply_dark_theme
from echo_link_app.window import EchoLinkWindow
from echo_link_app.windows_identity import (
    close_installer_mutex,
    configure_windows_app_identity,
    create_installer_mutex,
)
from echo_link_core.data_protection import DataProtectionError
from echo_link_core.audio import list_microphones
from echo_link_core.hotkeys import parse_hotkey, probe_hotkey_registration
from echo_link_core.paths import data_root, log_path, resource_path
from echo_link_core.recommendation import (
    apply_first_run_recommendation,
    local_hardware_profile,
    recommend_local_speech,
)
from echo_link_core.subscription import subscription_enforcement_required
from echo_link_core.storage import SCHEMA_VERSION, EchoLinkStore
from echo_link_core.transcription import cuda_available, discover_local_models, local_model_path
from echo_link_core.version import build_commit, product_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} voice-to-text dictation")
    parser.add_argument("--smoke-test", action="store_true", help="Open briefly and exit")
    parser.add_argument(
        "--startup",
        action="store_true",
        help="Start quietly in the system tray",
    )
    parser.add_argument(
        "--enable-global-hotkeys",
        action="store_true",
        default=True,
        help="Enable Version 2 global shortcuts for this run (default)",
    )
    parser.add_argument(
        "--disable-global-hotkeys",
        action="store_false",
        dest="enable_global_hotkeys",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--diagnostics", action="store_true", help="Print local readiness as JSON")
    parser.add_argument(
        "--internal-evaluation",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--workflow-probe",
        action="store_true",
        help="Run the isolated packaged hotkey, HUD, cue, transcription, and insertion probe",
    )
    parser.add_argument("--probe-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--probe-screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--probe-hotkey", help=argparse.SUPPRESS)
    parser.add_argument("--probe-timeout", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--workflow-probe-target", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--probe-target-title", default="", help=argparse.SUPPRESS)
    parser.add_argument("--probe-target-status", type=Path, help=argparse.SUPPRESS)
    return parser


def _subscription_required_for_runtime() -> bool:
    if getattr(sys, "frozen", False):
        return subscription_enforcement_required()
    return os.environ.get("MANUSCRIPT_TEST_BYPASS_SUBSCRIPTION") != "1"


def _internal_test_command_allowed(
    args: argparse.Namespace,
    *,
    subscription_required: bool,
) -> bool:
    uses_internal_command = any(
        (
            args.smoke_test,
            args.workflow_probe,
            args.workflow_probe_target,
            args.internal_evaluation,
        )
    )
    # A production build sets subscription_required, which refuses these.
    # The harness itself is also left out of a production archive entirely -
    # see EchoLink.spec - so a shipped build cannot import it even if this
    # gate is defeated. A testing build keeps both, because
    # tools/installer_lifecycle_probe.py drives --workflow-probe against the
    # INSTALLED executable and that is how release acceptance works.
    return not uses_internal_command or not subscription_required


def _run_workflow_probe_cli(args: argparse.Namespace) -> int:
    from tools import windows_workflow_probe

    if args.workflow_probe_target:
        if not args.probe_target_title or args.probe_target_status is None:
            return 2
        return windows_workflow_probe._run_target(
            args.probe_target_title,
            args.probe_target_status,
        )
    if args.probe_output is None:
        return 2
    probe_args = ["--packaged-mode", "--output", str(args.probe_output)]
    if args.probe_screenshot is not None:
        probe_args.extend(("--screenshot", str(args.probe_screenshot)))
    if args.probe_hotkey:
        probe_args.extend(("--hotkey", args.probe_hotkey))
    if args.probe_timeout is not None:
        probe_args.extend(("--timeout", str(args.probe_timeout)))
    return windows_workflow_probe.main(probe_args)


def configure_logging() -> None:
    handler = RotatingFileHandler(log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def diagnostics() -> dict[str, object]:
    store = EchoLinkStore()
    settings = store.get_settings()
    model = local_model_path(settings.model_name)
    hardware = local_hardware_profile()
    recommendation = recommend_local_speech(
        discover_local_models(),
        language=settings.language,
        profile=hardware,
    )
    try:
        parse_hotkey(settings.hotkey)
        hotkey_valid = True
    except ValueError:
        hotkey_valid = False
    live_hotkey_probe = os.environ.get("ECHOLINK_DISABLE_HOTKEY") != "1"
    hotkey_probe = (
        probe_hotkey_registration(settings.hotkey)
        if hotkey_valid and live_hotkey_probe
        else {
            "registered": bool(hotkey_valid and not live_hotkey_probe),
            "error": "" if hotkey_valid else "Invalid hotkey.",
        }
    )
    try:
        parse_hotkey(settings.private_paste_hotkey)
        private_paste_hotkey_valid = True
    except ValueError:
        private_paste_hotkey_valid = False
    private_paste_hotkey_probe = (
        probe_hotkey_registration(settings.private_paste_hotkey)
        if private_paste_hotkey_valid and live_hotkey_probe
        else {
            "registered": bool(private_paste_hotkey_valid and not live_hotkey_probe),
            "error": "" if private_paste_hotkey_valid else "Invalid hotkey.",
        }
    )
    return {
        "app": APP_NAME,
        "runtime": "local-only",
        # Both: the release a person can name, and the exact code behind
        # it. --diagnostics is what a support request pastes.
        "version": product_version(),
        "code_commit": build_commit(),
        "schema_version": SCHEMA_VERSION,
        "speech_model": settings.model_name,
        "speech_model_ready": model is not None,
        "installed_models": [item.name for item in discover_local_models()],
        "microphone_count": len(list_microphones()),
        "hotkey": settings.hotkey,
        "hotkey_valid": hotkey_valid,
        "hotkey_registered": bool(hotkey_probe["registered"]),
        "hotkey_registration_error": str(hotkey_probe["error"]),
        "cuda_available": cuda_available(),
        "cuda_runtime_ready": hardware.cuda,
        "hardware": {
            "cpu_threads": hardware.cpu_threads,
            "memory_gb": hardware.memory_gb,
            "cuda": hardware.cuda,
        },
        "recommended_speech_model": recommendation.model_name if recommendation else None,
        "recommended_speech_device": recommendation.device if recommendation else None,
        "first_run_setup_applied": store.get_meta("first_run_setup_applied"),
        "streaming_enabled": settings.streaming_enabled,
        "whisper_mode": settings.whisper_mode,
        "review_before_insert": settings.review_before_insert,
        "history_enabled": settings.keep_history,
        "recovery_enabled": settings.keep_recovery_text,
        "personal_learning_enabled": settings.personal_learning_enabled,
        "insertion_mode": settings.insertion_mode.value,
        "learned_correction_count": len(store.list_learned_corrections()),
        "echo_bar_enabled": settings.show_echo_bar,
        "echo_bar_position": [settings.echo_bar_x, settings.echo_bar_y],
        "sound_cues_enabled": settings.sound_enabled,
        "pause_media_during_dictation": settings.pause_media_during_dictation,
        "close_to_tray": settings.close_to_tray,
        "pre_roll_ms": settings.pre_roll_ms,
        "max_recording_seconds": settings.max_recording_seconds,
        "start_sound": settings.start_sound,
        "stop_sound": settings.stop_sound,
        "audio_output_index": settings.audio_output_index,
        "bluetooth_delay_ms": settings.bluetooth_delay_ms,
        "hands_free_enabled": settings.hands_free_enabled,
        "hands_free_hotkey": settings.hands_free_hotkey,
        "command_hotkey": settings.command_hotkey,
        "private_paste": {
            "storage": "process-memory-only",
            "windows_clipboard_used": False,
            "automatic_clipboard_fallback": False,
            "hotkey": settings.private_paste_hotkey,
            "hotkey_valid": private_paste_hotkey_valid,
            "hotkey_registered": bool(private_paste_hotkey_probe["registered"]),
            "hotkey_registration_error": str(private_paste_hotkey_probe["error"]),
        },
        "silence_auto_stop_enabled": settings.silence_auto_stop_enabled,
        "silence_timeout_seconds": settings.silence_timeout_seconds,
        "audio_retention": False,
        "database_security": store.database_security_status(),
    }


def _explain_unusable_data_folder(exc: BaseException) -> int:
    """Say why the data folder cannot be used, on screen rather than as a crash.

    data_root() refuses a path that is a network location or that contains a
    symlink or junction anywhere along it (see data_protection). Both are
    ordinary on a managed machine: the Windows local application data folder
    redirected to a share, or the user profile junctioned onto another volume.

    It is reached before anything else - configure_logging() resolves the log
    path through it - so the failure landed outside the startup handler below
    and the writer got a raw traceback in a console they do not have. There is
    no log to point them at either, because the log is what could not be
    opened.
    """
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName(APP_NAME)
    QMessageBox.critical(
        None,
        APP_NAME,
        f"{APP_NAME} could not use its data folder.\n\n{exc}\n\n"
        f"This usually means the Windows local application data folder is "
        f"redirected to a network location, or that part of the path is a "
        f"shortcut. Manuscript keeps your writing on this computer and will "
        f"not store it on a network share.",
    )
    return 1


def _run_application(args: argparse.Namespace) -> int:
    try:
        configure_logging()
        configure_windows_app_identity()
        lock_path = data_root() / "echo-link.lock"
    except (DataProtectionError, OSError) as exc:
        return _explain_unusable_data_folder(exc)
    lock = QLockFile(str(lock_path))
    lock.setStaleLockTime(0)
    locked = lock.tryLock(100)
    if args.diagnostics:
        if not locked:
            # stdout is the documented interface of --diagnostics; callers parse it.
            print(json.dumps({"app": APP_NAME, "error": f"{APP_NAME} is already running."}))  # noqa: T201
            return 1
        try:
            print(json.dumps(diagnostics(), indent=2))  # noqa: T201
            return 0
        finally:
            lock.unlock()

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(QIcon(str(resource_path(*ICON_ICO))))
    apply_dark_theme(app)

    if not locked and args.smoke_test:
        logging.getLogger(__name__).error("Smoke test refused to access live app data")
        return 1
    if not locked:
        QMessageBox.information(None, APP_NAME, f"{APP_NAME} is already running.")
        return 0

    try:
        store = EchoLinkStore()
        configure_startup(store.get_settings().start_with_windows)
        apply_first_run_recommendation(
            store,
            discover_local_models(),
            profile=local_hardware_profile(),
        )
        subscription_required = _subscription_required_for_runtime()
        if subscription_required and not args.smoke_test:
            activation = SubscriptionDialog()
            if activation.exec() != SubscriptionDialog.DialogCode.Accepted:
                return 0
        window = EchoLinkWindow(
            store,
            enable_hotkey=not args.smoke_test and args.enable_global_hotkeys,
            enable_tray=not args.smoke_test,
            internal_evaluation=args.internal_evaluation,
        )
        if not subscription_required:
            window.setWindowTitle(
                f"{window.windowTitle()} - TEST BUILD (subscription off)"
            )
        app.aboutToQuit.connect(window.shutdown)
        if subscription_required and not args.smoke_test:
            subscription_timer = QTimer(window)
            subscription_timer.setInterval(15 * 60 * 1000)

            def recheck_subscription() -> None:
                if getattr(window, "_subscription_check", None) is not None:
                    return
                worker = EntitlementCheckThread(window)
                window._subscription_check = worker

                def finished(active: bool, message: str) -> None:
                    window._subscription_check = None
                    worker.deleteLater()
                    if not active:
                        QMessageBox.critical(
                            window,
                            "Subscription verification required",
                            message or "Manuscript could not verify an active subscription.",
                        )
                        app.quit()

                worker.completed.connect(finished)
                worker.start()

            subscription_timer.timeout.connect(recheck_subscription)
            subscription_timer.start()
            window._subscription_timer = subscription_timer
        if args.startup and not args.smoke_test:
            window.hide()
        else:
            window.show()
        if args.smoke_test:
            QTimer.singleShot(900, app.quit)
        return app.exec()
    except Exception:
        logging.getLogger(__name__).exception("Manuscript startup failed")
        QMessageBox.critical(
            None,
            "Manuscript could not start",
            f"Open this log for details:\n{log_path()}",
        )
        return 1
    finally:
        if lock.isLocked():
            lock.unlock()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not _internal_test_command_allowed(
        args,
        subscription_required=_subscription_required_for_runtime(),
    ):
        logging.getLogger(__name__).error(
            "Internal test command refused by the production build"
        )
        return 2
    mutex_handle = create_installer_mutex()
    try:
        if args.workflow_probe or args.workflow_probe_target:
            return _run_workflow_probe_cli(args)
        return _run_application(args)
    finally:
        close_installer_mutex(mutex_handle)
