"""Configuration, terminal sanitization, and frame rejection tests."""

from pathlib import Path

import pytest

from network_apps.chat import safe_text
from network_apps.configuration import load_settings
from network_apps.protocol import ProtocolError, decode, encode


@pytest.mark.parametrize(
    "configuration",
    [
        "port: 0",
        "port: true",
        "port: 65536",
        "connect_timeout: .nan",
        "unknown: 1",
        "race_updates_per_second: 26",
        "host: ''",
    ],
)
def test_invalid_settings(tmp_path: Path, configuration: str) -> None:
    """Reject malformed settings rather than silently selecting defaults."""
    path = tmp_path / "client.yaml"
    path.write_text(configuration, encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        load_settings(path)


def test_server_starts_without_credentials(tmp_path: Path) -> None:
    """An empty server configuration is valid; no certificate or token is needed."""
    path = tmp_path / "server.yaml"
    path.write_text("{}", encoding="utf-8")
    assert load_settings(path, server=True).integer("port") == 8765


@pytest.mark.parametrize("body", [b"[]", b'{"a":NaN}', b"{", b"\xff"])
def test_invalid_json(body: bytes) -> None:
    """Reject malformed and non-finite messages."""
    with pytest.raises(ProtocolError):
        decode(body)


def test_oversized_encode_and_terminal_controls() -> None:
    """Frame limits and display escaping apply before transport/presentation."""
    with pytest.raises(ProtocolError):
        encode({"text": "a" * 100}, 10)
    assert safe_text("\x1b[31mhello\n") == "\\u001b[31mhello\\u000a"
