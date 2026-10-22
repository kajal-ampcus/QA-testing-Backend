from unittest.mock import Mock

import pytest

from scripts import run_worker


def test_stale_display_files_are_removed(tmp_path, monkeypatch):
    paths = (tmp_path / "X99", tmp_path / ".X99-lock")
    for path in paths:
        path.write_text("8")
    monkeypatch.setattr(run_worker, "_display_paths", lambda: paths)
    monkeypatch.setattr(run_worker, "_display_ready", lambda: False)
    run_worker._clean_stale_display()
    assert all(not path.exists() for path in paths)


def test_running_display_is_not_removed(tmp_path, monkeypatch):
    path = tmp_path / "X99"
    path.touch()
    monkeypatch.setattr(run_worker, "_display_paths", lambda: (path, path))
    monkeypatch.setattr(run_worker, "_display_ready", lambda: True)
    with pytest.raises(SystemExit, match="already running"):
        run_worker._clean_stale_display()
    assert path.exists()


def test_dead_process_cannot_pass_readiness():
    process = Mock(returncode=1)
    process.poll.return_value = 1
    with pytest.raises(SystemExit, match="Xvfb exited"):
        run_worker._wait_ready(process, lambda: True, "Xvfb")


def test_readiness_timeout(monkeypatch):
    monkeypatch.setattr(run_worker.time, "sleep", lambda _: None)
    process = Mock()
    process.poll.return_value = None
    with pytest.raises(SystemExit, match="did not become ready"):
        run_worker._wait_ready(process, lambda: False, "x11vnc")
