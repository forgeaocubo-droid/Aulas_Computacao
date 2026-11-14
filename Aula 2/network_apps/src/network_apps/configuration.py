"""Load explicit, validated YAML settings."""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

type Setting = str | int | float


@dataclass(frozen=True)
class Settings:
    """Validated options shared by the entry points."""

    values: dict[str, Setting]

    def text(self, key: str) -> str:
        """Return a required textual setting."""
        value = self.values[key]
        if not isinstance(value, str):
            raise TypeError(f"{key} must be text")
        return value

    def integer(self, key: str) -> int:
        """Return a required positive integer setting."""
        value = self.values[key]
        if type(value) is not int:
            raise ValueError(f"{key} must be an integer")
        return value

    def number(self, key: str) -> float:
        """Return a numeric setting."""
        return float(self.values[key])


COMMON: dict[str, Setting] = {
    "host": "localhost",
    "port": 8765,
    "max_frame_bytes": 65536,
    "idle_timeout": 45.0,
    "log_level": "INFO",
    "log_directory": "logs",
    "log_max_bytes": 1048576,
    "log_backups": 3,
}
SERVER: dict[str, Setting] = {
    "max_clients": 32,
    "max_pending_messages": 1000,
    "max_pending_bytes": 8388608,
    "messages_per_second": 30,
    "outbound_messages": 128,
    "handshake_timeout": 5.0,
    "write_timeout": 3.0,
}
CLIENT: dict[str, Setting] = {
    "connect_timeout": 5.0,
    "race_updates_per_second": 20,
}


def load_settings(path: Path, *, server: bool = False) -> Settings:
    """Read settings; reject unknown keys and invalid limits."""
    with path.open(encoding="utf-8") as stream:
        raw: object = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise TypeError("Configuration must be a YAML mapping")
    values = COMMON.copy()
    if server:
        values.update(SERVER)
    else:
        values.update(CLIENT)
    for key, value in raw.items():
        if not isinstance(key, str) or key not in values:
            raise ValueError(f"Unknown configuration key: {key!r}")
        default = values[key]
        if isinstance(default, str):
            if not isinstance(value, str):
                raise TypeError(f"{key} must be text")
        elif type(default) is int:
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
        elif type(value) not in (int, float) or not 0 < float(value) < float("inf"):
            raise ValueError(f"{key} must be a finite positive number")
        values[key] = value
    settings = Settings(values)
    if settings.integer("port") > 65535:
        raise ValueError("port must be between 1 and 65535")
    if not settings.text("host").strip():
        raise ValueError("host must not be empty")
    if settings.text("log_level") not in logging.getLevelNamesMapping():
        raise ValueError("Unsupported log_level")
    if settings.integer("max_frame_bytes") < 1024:
        raise ValueError("max_frame_bytes must be at least 1024")
    if not server and settings.integer("race_updates_per_second") > 25:
        raise ValueError("race_updates_per_second must not exceed 25")
    return settings
