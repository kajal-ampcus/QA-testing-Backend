"""Centralized logger configuration with one log folder per service."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def configure_logging(
    service_name: str,
    base_log_dir: str | Path | None = None,
    log_level: str | None = None,
) -> logging.Logger:
    """Create a service-specific logger and persist it under logs/<service>/.

    Example:
        configure_logging("api") -> logs/api/api.log
        configure_logging("worker") -> logs/worker/worker.log
    """
    service_name = service_name.strip().lower() or "app"
    env_base_dir = os.environ.get("LOG_BASE_DIR")
    base_path = Path(base_log_dir) if base_log_dir is not None else Path(env_base_dir) if env_base_dir else Path(__file__).resolve().parents[1] / "logs"
    if not base_path.is_absolute():
        base_path = Path.cwd() / base_path
    root_dir = base_path
    service_dir = root_dir / service_name
    service_dir.mkdir(parents=True, exist_ok=True)

    level_name = (log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger_name = f"qa_platform.{service_name}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False

    # Avoid duplicate handlers when the function is called repeatedly during reloads.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(service_dir / f"{service_name}.log", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Library modules log through logging.getLogger(__name__); route them to
    # the same service output instead of Python's handler-less root logger.
    for package in ("core", "infra"):
        package_logger = logging.getLogger(package)
        package_logger.setLevel(level)
        package_logger.propagate = False
        for handler in list(package_logger.handlers):
            package_logger.removeHandler(handler)
        package_logger.addHandler(stream_handler)
        package_logger.addHandler(file_handler)

    return logger
