"""Run a network_apps entry point from the Aula 2 folder using native Python.

The launcher starts a child process with the same Python interpreter used to
run it instead of importing the package into the launcher process.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT: Path = Path(__file__).resolve().parent / "network_apps"
PYTHON: Path = Path(sys.executable)


def ensure_config(config_name: str) -> Path:
    """Create the YAML configuration from its example when it is missing.

    Returns:
        The configuration path relative to the project directory.
    """
    relative = Path("config") / f"{config_name}.yaml"
    target = PROJECT / relative
    if not target.is_file():
        example = PROJECT / "config" / f"{config_name}.example.yaml"
        shutil.copyfile(example, target)
    return relative


def display_name() -> str:
    """Return the name from the first argument or ask for it interactively."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Display name: ").strip()


def run(command: list[str]) -> int:
    """Run the application and wait for it to finish, including after Ctrl+C.

    The child shares the console, so it receives Ctrl+C directly and performs
    its own orderly shutdown; the launcher only keeps waiting for it.
    """
    environment = os.environ.copy()
    source = str(PROJECT / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source, environment.get("PYTHONPATH")) if part
    )
    process = subprocess.Popen(command, cwd=PROJECT, env=environment)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


def launch(module: str, config_name: str, *, needs_name: bool) -> None:
    """Start ``module`` with its configuration and exit with its status code."""
    if sys.version_info < (3, 12):
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(
            f"Python {version} is not supported; use Python 3.12 or newer.",
            file=sys.stderr,
        )
        sys.exit(1)
    config = ensure_config(config_name)
    command = [str(PYTHON), "-m", module, "--config", str(config)]
    if needs_name:
        command.extend(["--name", display_name()])
    exit_code = run(command)
    if exit_code != 0 and sys.stdin.isatty():
        # Keep a double-clicked console window open so the error is readable.
        input("Press Enter to close...")
    sys.exit(exit_code)
