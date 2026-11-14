"""Smoke-test the actual server process and documented configuration path."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml
from conftest import WireClient

from network_apps.configuration import load_settings


def test_server_command_relays_over_tcp(tmp_path: Path) -> None:
    """Start the installed module and prove readiness through actual delivery."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    server_config = tmp_path / "server.yaml"
    server_config.write_text(
        yaml.safe_dump(
            {
                "host": "127.0.0.1",
                "port": port,
                "log_directory": str(tmp_path / "logs"),
            }
        ),
        encoding="utf-8",
    )
    client_config = tmp_path / "client.yaml"
    client_config.write_text(
        yaml.safe_dump({"host": "127.0.0.1", "port": port}),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "network_apps.relay", "--config", str(server_config)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    connected: list[WireClient] = []
    try:
        settings = load_settings(client_config)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"Server exited: {process.communicate()}")
            try:
                alice = WireClient(settings, "Alice")
            except OSError:
                time.sleep(0.05)
            else:
                connected.append(alice)
                break
        else:
            raise AssertionError("Server did not become responsive")
        bob = WireClient(settings, "Bob")
        connected.append(bob)
        alice.publish("CLI server ready")
        assert bob.until("message")["payload"] == {
            "kind": "chat",
            "text": "CLI server ready",
        }
    finally:
        for client in connected:
            client.close()
        process.terminate()
        process.communicate(timeout=5)
