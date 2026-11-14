"""Top-down circular racing with a separate live telemetry panel."""

import argparse
import hashlib
import logging
import math
import queue
from pathlib import Path

import pygame
import yaml

from network_apps.client import Client
from network_apps.configuration import Settings, load_settings
from network_apps.logging_setup import configure_logging
from network_apps.protocol import ProtocolError
from network_apps.racing.driving import INNER_RADIUS, OUTER_RADIUS, Car
from network_apps.racing.session import Session

LOGGER = logging.getLogger(__name__)
WHITE = "#F4F7FA"
NAVY = "#152638"
YELLOW = "#F2C94C"
BLUE = "#247BA0"
CORAL = "#E76F51"
PALETTE = ("#E76F51", "#59D3BC", "#C7A5F0", "#F5A9C6", "#A9CFED")
CENTER = (365, 345)
SIZE = (1120, 760)


class View:
    """Draw the circuit and aligned telemetry without external assets."""

    def __init__(self) -> None:
        """Initialize reusable render surfaces and bundled fonts."""
        self.surface = pygame.Surface(SIZE)
        self.small = pygame.font.Font(None, 24)
        self.large = pygame.font.Font(None, 66)

    def label(
        self,
        value: str,
        position: tuple[int, int],
        *,
        color: str = WHITE,
        large: bool = False,
    ) -> None:
        """Render a legible single-line label clipped to its region."""
        font = self.small
        if large:
            font = self.large
        rendered = font.render(value, True, color)
        self.surface.blit(rendered, position)

    def car(self, x: float, y: float, heading: float, name: str, color: str) -> None:
        """Draw a directional car with a name so color is not the only cue."""
        sprite = pygame.Surface((30, 16), pygame.SRCALPHA)
        pygame.draw.rect(sprite, color, (0, 0, 30, 16), border_radius=4)
        pygame.draw.rect(sprite, NAVY, (17, 3, 5, 10))
        rotated = pygame.transform.rotate(sprite, -math.degrees(heading))
        position = (round(CENTER[0] + x), round(CENTER[1] + y))
        self.surface.blit(rotated, rotated.get_rect(center=position))
        self.label(name[:16], (position[0] - 25, position[1] - 32))

    def render(self, car: Car, session: Session, name: str, dt: float) -> None:
        """Draw one frame and interpolate only remote presentation positions."""
        self.surface.fill(BLUE)
        pygame.draw.circle(self.surface, "#354452", CENTER, round(OUTER_RADIUS))
        pygame.draw.circle(self.surface, BLUE, CENTER, round(INNER_RADIUS))
        for radius in (round(INNER_RADIUS), round(OUTER_RADIUS)):
            pygame.draw.circle(self.surface, WHITE, CENTER, radius, 3)
        for index in range(10):
            for row in range(2):
                color = NAVY
                if (index + row) % 2:
                    color = WHITE
                pygame.draw.rect(
                    self.surface, color, (565 + index * 9, 345 + row * 9, 9, 9)
                )
        self.label("Circular circuit", (280, 300))
        self.label("Clockwise laps", (288, 330))
        self.car(car.x, car.y, car.heading, name, YELLOW)
        for identifier, opponent in session.opponents.items():
            blend = min(1.0, dt * 12)
            opponent.display_x += (opponent.x - opponent.display_x) * blend
            opponent.display_y += (opponent.y - opponent.display_y) * blend
            index = hashlib.sha256(identifier.encode()).digest()[0] % len(PALETTE)
            self.car(
                opponent.display_x,
                opponent.display_y,
                opponent.heading,
                opponent.name,
                PALETTE[index],
            )
        pygame.draw.rect(self.surface, NAVY, (730, 0, 390, 710))
        self.label(name[:24], (760, 30))
        self.label(f"{car.kmh:5.1f} km/h", (755, 65), color=YELLOW, large=True)
        self.label(f"Completed laps: {car.laps}", (760, 135))
        if not car.valid:
            self.label("Invalid lap: cross the start to reset", (750, 167), color=CORAL)
        self.label("Recent laps: time / average speed", (760, 210))
        for index, lap in enumerate(reversed(car.history[-5:])):
            number = car.laps - index
            self.label(
                f"{number}:  {lap.seconds:.2f} s   /   {lap.average_kmh:.1f} km/h",
                (760, 245 + index * 28),
            )
        self.label(f"Drivers: {len(session.opponents) + 1}", (760, 410))
        for index, opponent in enumerate(list(session.opponents.values())[:6]):
            self.label(
                f"{opponent.name[:20]}  |  {opponent.laps} laps",
                (760, 445 + index * 26),
            )
        status = session.status
        if session.queued:
            status = "Waiting for another driver (buffering)"
        self.surface.set_clip(pygame.Rect(750, 620, 360, 85))
        self.label(status[:48], (750, 630), color=YELLOW)
        if len(status) > 48:
            self.label(status[48:96], (750, 655), color=CORAL)
        self.surface.set_clip(None)
        pygame.draw.rect(self.surface, NAVY, (0, 710, 1120, 50))
        self.label(
            "W / Up: accelerate    S / Down: brake    A D / Left Right: steer"
            "    Esc: leave",
            (30, 727),
        )


def run_game(client: Client, settings: Settings, name: str) -> None:
    """Keep all Pygame work on the main thread and socket writes off it."""
    try:
        pygame.init()
        window = pygame.display.set_mode(SIZE, pygame.RESIZABLE)
        pygame.display.set_caption("Circular circuit - multiplayer racing")
        clock = pygame.time.Clock()
        view = View()
        car = Car()
        session = Session(client.identifier)
        client.start()
        running = True
        since_publish = 0.0
        period = 1 / settings.integer("race_updates_per_second")
        while running:
            dt = min(clock.tick(60) / 1000, 0.05)
            if dt <= 0:
                continue
            for event in pygame.event.get():
                if (
                    event.type == pygame.QUIT
                    or event.type == pygame.KEYDOWN
                    and event.key == pygame.K_ESCAPE
                ):
                    running = False
                elif event.type == pygame.VIDEORESIZE:
                    window = pygame.display.set_mode(
                        (max(900, event.w), max(620, event.h)), pygame.RESIZABLE
                    )
            needs_snapshot = False
            for _ in range(512):
                try:
                    message = client.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    needs_snapshot = session.event(message) or needs_snapshot
                except ProtocolError as error:
                    session.status = f"Rejected peer data: {error}"
                    LOGGER.error("%s", session.status)
            keys = pygame.key.get_pressed()
            steering = 0
            if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                steering -= 1
            if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                steering += 1
            if not client.stopped.is_set():
                car.step(
                    dt,
                    bool(keys[pygame.K_w] or keys[pygame.K_UP]),
                    bool(keys[pygame.K_s] or keys[pygame.K_DOWN]),
                    steering,
                )
                since_publish += dt
                if since_publish >= period:
                    since_publish %= period
                    try:
                        client.publish(session.snapshot(car))
                    except (OSError, ValueError) as error:
                        session.status = f"Send failed: {error}"
                        LOGGER.error("%s", session.status)
                        client.stopped.set()
                elif needs_snapshot:
                    # The next normal update serves as the fresh join snapshot.
                    since_publish = period
            else:
                session.status = "Disconnected. Press Esc to leave."
            view.render(car, session, name, dt)
            width, height = window.get_size()
            scale = min(width / SIZE[0], height / SIZE[1])
            target = (round(SIZE[0] * scale), round(SIZE[1] * scale))
            resized = pygame.transform.smoothscale(view.surface, target)
            window.fill(NAVY)
            window.blit(resized, ((width - target[0]) // 2, (height - target[1]) // 2))
            pygame.display.flip()
    finally:
        try:
            client.close()
        finally:
            pygame.quit()


def main() -> None:
    """Connect a named race client from an explicit YAML configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        configure_logging(settings, "race")
        run_game(Client(settings, args.name, "race"), settings, args.name)
    except (
        OSError,
        ValueError,
        TypeError,
        EOFError,
        yaml.YAMLError,
        pygame.error,
    ) as error:
        parser.exit(1, f"Race failed: {error}\n")
    except KeyboardInterrupt:
        LOGGER.info("Race stopped")


if __name__ == "__main__":
    main()
