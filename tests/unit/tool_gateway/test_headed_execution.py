from pathlib import Path

from core.tool_gateway.playwright_client import host_suite_dir


def test_host_suite_dir_maps_container_path(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMATION_HOST_ROOT", "D:/testing-platform/QA-testing-Backend/artifacts/automation")
    monkeypatch.setenv("AUTOMATION_ARTIFACT_DIR", "/app/artifacts/automation")
    mapped = host_suite_dir(Path("/app/artifacts/automation/project/generation"))
    assert mapped == "D:/testing-platform/QA-testing-Backend/artifacts/automation/project/generation"


def test_host_suite_dir_leaves_a_desktop_path_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMATION_HOST_ROOT", "D:/testing-platform/QA-testing-Backend/artifacts/automation")
    monkeypatch.setenv("AUTOMATION_ARTIFACT_DIR", "/app/artifacts/automation")
    mapped = host_suite_dir(Path("D:/testing-platform/QA-testing-Backend/artifacts/automation/project"))
    assert mapped.endswith("artifacts/automation/project")
