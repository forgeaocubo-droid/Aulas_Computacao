"""Verify operator filtering without mutating pytest's logging handlers."""

import logging
from pathlib import Path

import pytest

from network_apps.configuration import COMMON, Settings
from network_apps.logging_setup import configure_logging


def test_terminal_filter_and_debug_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Terminal excludes WARNING/DEBUG while the configured file retains them."""
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    root.handlers = []
    try:
        settings = Settings(
            COMMON | {"log_directory": str(tmp_path), "log_level": "DEBUG"}
        )
        configure_logging(settings, "test")
        logger = logging.getLogger("network_apps.test")
        logger.debug("Debug detail")
        logger.info("Operator information")
        logger.warning("File warning")
        logger.error("Operator failure")
        terminal = capsys.readouterr().err
        assert "Operator information" in terminal
        assert "Operator failure" in terminal
        assert "Debug detail" not in terminal
        assert "File warning" not in terminal
        for handler in root.handlers:
            handler.flush()
        content = next(tmp_path.glob("test-*.log")).read_text(encoding="utf-8")
        assert "Debug detail" in content
        assert "File warning" in content
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)
