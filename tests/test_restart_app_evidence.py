from tools import restart_app_and_wait as restart


def test_ready_event_after_rotation(monkeypatch, tmp_path):
    log = tmp_path / "app.log"
    log.write_text("old\n" * 40)
    monkeypatch.setattr(restart, "LOG_PATH", log)
    monkeypatch.setattr(restart, "stop_the_running_app", lambda: 0)
    monkeypatch.setattr(restart.time, "sleep", lambda _: None)

    def launch(*args, **kwargs):
        log.rename(log.with_name(log.name + ".1"))
        log.write_text("Adaptive Polish model prewarmed loaded=True\n")

    monkeypatch.setattr(restart.subprocess, "Popen", launch)
    assert restart.main() == 0


def test_lost_startup_evidence_cannot_pass(monkeypatch, tmp_path):
    log = tmp_path / "app.log"
    log.write_text("old\n" * 40)
    monkeypatch.setattr(restart, "LOG_PATH", log)
    monkeypatch.setattr(restart, "stop_the_running_app", lambda: 0)
    monkeypatch.setattr(restart.time, "sleep", lambda _: None)

    def launch(*args, **kwargs):
        log.write_text("Adaptive Polish model prewarmed loaded=True\n")

    monkeypatch.setattr(restart.subprocess, "Popen", launch)
    assert restart.main() == 6
