"""Validate race payloads and keep membership separate from buffered updates."""

import logging
import math
from dataclasses import dataclass, field

from network_apps.protocol import Message, ProtocolError, finite_number, text
from network_apps.racing.driving import OUTER_RADIUS, Car

LOGGER = logging.getLogger(__name__)


@dataclass
class Opponent:
    """Keep the newest authoritative peer state and an interpolated position."""

    name: str
    sequence: int = -1
    x: float = 245
    y: float = 0
    display_x: float = 245
    display_y: float = 0
    heading: float = math.pi / 2
    speed: float = 0
    laps: int = 0
    last_lap: float = 0


@dataclass
class Session:
    """Apply membership events before accepting peer movement."""

    identifier: str
    opponents: dict[str, Opponent] = field(default_factory=dict)
    sequence: int = 0
    status: str = "Connected"
    queued: bool = False

    def add_peer(self, peer: Message) -> None:
        """Spawn only a registered game session at the starting line."""
        identifier = text(peer, "id")
        name = text(peer, "name", 32)
        if identifier != self.identifier and identifier not in self.opponents:
            self.opponents[identifier] = Opponent(name)

    def event(self, event: Message) -> bool:
        """Apply one event; request a fresh local snapshot after peer arrival."""
        kind = event.get("type")
        if kind == "welcome":
            peers = event.get("peers")
            if not isinstance(peers, list):
                raise ProtocolError("Invalid channel roster")
            for peer in peers:
                if not isinstance(peer, dict):
                    raise ProtocolError("Invalid peer in roster")
                self.add_peer(peer)
            return True
        if kind == "joined":
            peer = event.get("peer")
            if not isinstance(peer, dict):
                raise ProtocolError("Invalid join event")
            self.add_peer(peer)
            return True
        if kind == "left":
            self.opponents.pop(text(event, "id"), None)
        elif kind == "message":
            sender = event.get("sender")
            payload = event.get("payload")
            if not isinstance(sender, dict) or not isinstance(payload, dict):
                raise ProtocolError("Invalid race envelope")
            identifier = text(sender, "id")
            if identifier not in self.opponents:
                return False
            self.update(self.opponents[identifier], payload)
        elif kind in ("error", "disconnected"):
            self.status = str(event.get("detail", "Connection failed"))
            LOGGER.error("Race connection: %s", self.status)
        elif kind == "accepted":
            self.queued = event.get("status") == "queued"
        return False

    def update(self, opponent: Opponent, payload: Message) -> None:
        """Reject invalid peer states before replacing any existing state."""
        if payload.get("kind") != "state":
            raise ProtocolError("Unsupported race payload")
        sequence = payload.get("sequence")
        laps = payload.get("laps")
        if type(sequence) is not int or type(laps) is not int:
            raise ProtocolError("Race counters must be integers")
        assert isinstance(sequence, int) and isinstance(laps, int)
        if sequence < 0 or laps < 0:
            raise ProtocolError("Race counters must not be negative")
        if sequence <= opponent.sequence:
            return
        x = finite_number(payload, "x")
        y = finite_number(payload, "y")
        heading = finite_number(payload, "heading")
        speed = finite_number(payload, "speed")
        last_lap = finite_number(payload, "last_lap")
        if math.hypot(x, y) > OUTER_RADIUS + 1 or not 0 <= speed <= 220:
            raise ProtocolError("Race position or speed is out of bounds")
        if not 0 <= last_lap <= 86400:
            raise ProtocolError("Race lap duration is out of bounds")
        opponent.sequence = sequence
        opponent.x, opponent.y = x, y
        opponent.heading = heading % math.tau
        opponent.speed = speed
        opponent.laps = laps
        opponent.last_lap = last_lap

    def snapshot(self, car: Car) -> Message:
        """Build a bounded current-state snapshot for all race peers."""
        self.sequence += 1
        last_lap = 0.0
        if car.history:
            last_lap = car.history[-1].seconds
        return {
            "kind": "state",
            "sequence": self.sequence,
            "x": car.x,
            "y": car.y,
            "heading": car.heading,
            "speed": car.speed,
            "laps": car.laps,
            "last_lap": last_lap,
        }
