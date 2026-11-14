"""Time-based driving and forward lap measurement, independent of Pygame."""

import math
from dataclasses import dataclass, field

RADIUS = 245.0
INNER_RADIUS = 200.0
OUTER_RADIUS = 290.0
METRES_PER_PIXEL = 0.2
MAX_SPEED = 220.0


@dataclass(frozen=True)
class Lap:
    """Describe one completed forward lap."""

    seconds: float
    average_kmh: float


@dataclass
class Car:
    """Integrate steering and reject laps containing shortcuts or reversals."""

    x: float = RADIUS
    y: float = 0.0
    heading: float = math.pi / 2
    speed: float = 0.0
    laps: int = 0
    history: list[Lap] = field(default_factory=list)
    progress: float = 0.0
    elapsed: float = 0.0
    distance: float = 0.0
    valid: bool = True
    checkpoints: int = 0

    def step(self, dt: float, throttle: bool, brake: bool, steering: int) -> None:
        """Advance at most 100 ms; steering is -1, 0, or 1."""
        if not math.isfinite(dt) or not 0 < dt <= 0.1:
            raise ValueError("Driving step must be between zero and 0.1 seconds")
        if steering not in (-1, 0, 1):
            raise ValueError("Steering must be -1, 0, or 1")
        acceleration = -18.0
        if throttle:
            acceleration = 80.0
        if brake:
            acceleration = -160.0
        self.speed = max(0.0, min(MAX_SPEED, self.speed + acceleration * dt))
        self.heading += steering * 1.65 * dt * min(1.0, self.speed / 60)
        old_x, old_y = self.x, self.y
        self.x += math.cos(self.heading) * self.speed * dt
        self.y += math.sin(self.heading) * self.speed * dt
        radial = math.hypot(self.x, self.y)
        on_track = INNER_RADIUS <= radial <= OUTER_RADIUS
        if not on_track:
            boundary = max(INNER_RADIUS, min(OUTER_RADIUS, radial))
            if radial > 0:
                self.x *= boundary / radial
                self.y *= boundary / radial
            self.speed *= 0.9
        self.observe(old_x, old_y, dt, on_track)

    def observe(self, old_x: float, old_y: float, dt: float, on_track: bool) -> None:
        """Measure ordered angular progress and actual forward travel distance."""
        old_angle = math.atan2(old_y, old_x)
        angle = math.atan2(self.y, self.x)
        delta = (angle - old_angle + math.pi) % math.tau - math.pi
        crossed = old_angle < 0 <= angle and delta > 0
        if not on_track or delta < -0.001:
            self.valid = False
        if not self.valid:
            self.progress = 0
            self.checkpoints = 0
            self.elapsed = 0
            self.distance = 0
            if crossed and on_track:
                self.valid = True
            return
        self.elapsed += dt
        self.distance += math.hypot(self.x - old_x, self.y - old_y)
        self.progress += delta
        while (
            self.checkpoints < 3
            and self.progress >= (self.checkpoints + 1) * math.pi / 2
        ):
            self.checkpoints += 1
        if crossed and self.checkpoints == 3:
            average = self.distance * METRES_PER_PIXEL / self.elapsed * 3.6
            self.history.append(Lap(self.elapsed, average))
            del self.history[:-100]
            self.laps += 1
            self.progress = 0
            self.checkpoints = 0
            self.elapsed = 0
            self.distance = 0

    @property
    def kmh(self) -> float:
        """Return instantaneous speed using the virtual track scale."""
        return self.speed * METRES_PER_PIXEL * 3.6
