"""Headless rendering and application-loop lifecycle checks."""

import os
import threading
from collections.abc import Callable
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame
import pytest
from conftest import Running

from network_apps.chat import run_chat
from network_apps.client import Client
from network_apps.configuration import Setting
from network_apps.racing.driving import Car
from network_apps.racing.game import View, run_game
from network_apps.racing.session import Session


def test_render_and_game_loop_cleanup(
    relay_factory: Callable[[dict[str, Setting]], Running], tmp_path: Path
) -> None:
    """Render a frame, then run a connected game loop until a window close."""
    service = relay_factory({})
    client = Client(service.settings, "Driver", "race")
    pygame.init()
    view = View()
    view.render(Car(), Session(client.identifier), "Driver", 0.016)
    pygame.image.save(view.surface, tmp_path / "circuit.png")
    assert view.surface.get_size() == (1120, 760)

    def close_window() -> None:
        """Inject a real Pygame quit event after several network updates."""
        pygame.event.post(pygame.event.Event(pygame.QUIT))

    timer = threading.Timer(0.35, close_window)
    timer.start()
    try:
        run_game(client, service.settings, "Driver")
    finally:
        timer.join(2)
    assert all(not worker.is_alive() for worker in client.workers)
    assert not pygame.get_init()


def test_terminal_quit_joins_sender_and_receiver(
    relay_factory: Callable[[dict[str, Setting]], Running],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The terminal application quits without leaving input/display workers."""
    service = relay_factory({})
    client = Client(service.settings, "Alice", "chat")

    def quit_input(stopped: threading.Event) -> str:
        """Simulate an entered quit command without touching the real terminal."""
        return "/quit"

    monkeypatch.setattr("network_apps.chat.input_lines", quit_input)
    run_chat(client)
    assert all(not worker.is_alive() for worker in client.workers)
