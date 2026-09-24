import logging
from pathlib import Path

from infra.logging_config import configure_logging


def test_configure_logging_creates_service_log_directory(tmp_path: Path) -> None:
    logger = configure_logging(service_name="api", base_log_dir=tmp_path)

    assert (tmp_path / "api").is_dir()
    assert logger.name == "qa_platform.api"
    assert logger.level == logging.INFO
    assert any(isinstance(handler, logging.FileHandler) for handler in logger.handlers)
