"""Configure redacted operational output and rotating per-process logs."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from network_apps.configuration import Settings


class OperatorFilter(logging.Filter):
    """Restrict terminal logging to INFO and ERROR."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Select operator-facing levels."""
        return record.levelno in (logging.INFO, logging.ERROR)


def configure_logging(settings: Settings, application: str) -> None:
    """Create bounded log files and a separately filtered terminal handler."""
    directory = Path(settings.text("log_directory"))
    directory.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        directory / f"{application}-{os.getpid()}.log",
        maxBytes=settings.integer("log_max_bytes"),
        backupCount=settings.integer("log_backups"),
        encoding="utf-8",
    )
    file_handler.setLevel(settings.text("log_level"))
    terminal = logging.StreamHandler()
    terminal.setLevel(logging.INFO)
    terminal.addFilter(OperatorFilter())
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[terminal, file_handler],
        force=True,
    )
