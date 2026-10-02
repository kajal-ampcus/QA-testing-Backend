from infra.db.models import Base


def test_run_execution_registers_automation_fk_tables() -> None:
    import apps.worker.tasks.run_execution  # noqa: F401

    for name in (
        "projects",
        "requirements",
        "application_maps",
        "test_cases",
        "automation_scripts",
        "test_runs",
        "test_results",
    ):
        assert name in Base.metadata.tables, name
