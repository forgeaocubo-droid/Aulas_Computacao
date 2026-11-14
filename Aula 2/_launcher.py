"""Run a network_apps entry point from the Aula 2 folder using the project venv.

The applications depend on packages installed only in ``network_apps/.venv313``,
so the launchers start a child process with that interpreter instead of
importing the package into whichever Python ran the launcher.
"""

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT: Path = Path(__file__).resolve().parent / "network_apps"
PYTHON: Path = PROJECT / ".venv313" / "Scripts" / "python.exe"


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
    process = subprocess.Popen(command, cwd=PROJECT)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            continue


def launch(module: str, config_name: str, *, needs_name: bool) -> None:
    """Start ``module`` with its configuration and exit with its status code."""
    if not PYTHON.is_file():
        print(f"Python environment not found: {PYTHON}", file=sys.stderr)
        print("See network_apps/README.md for installation steps.", file=sys.stderr)
        exit_code = 1
    else:
        config = ensure_config(config_name)
        command = [str(PYTHON), "-m", module, "--config", str(config)]
        if needs_name:
            command.extend(["--name", display_name()])
        exit_code = run(command)
    if exit_code != 0 and sys.stdin.isatty():
        # Keep a double-clicked console window open so the error is readable.
        input("Press Enter to close...")
    sys.exit(exit_code)
