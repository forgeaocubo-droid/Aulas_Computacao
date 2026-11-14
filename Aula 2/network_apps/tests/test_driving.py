"""Deterministic lap and peer-session regression tests."""

import math

import pytest

from network_apps.protocol import ProtocolError
from network_apps.racing.driving import METRES_PER_PIXEL, RADIUS, Car
from network_apps.racing.session import Session


def travel(car: Car, start: float, end: float, *, on_track: bool = True) -> None:
    """Observe a controlled angular trajectory without a GUI or wall clock."""
    steps = 400
    car.x = RADIUS * math.cos(start)
    car.y = RADIUS * math.sin(start)
    for index in range(1, steps + 1):
        angle = start + (end - start) * index / steps
        old_x, old_y = car.x, car.y
        car.x = RADIUS * math.cos(angle)
        car.y = RADIUS * math.sin(angle)
        car.observe(old_x, old_y, 0.02, on_track)


def test_forward_lap_and_average_speed() -> None:
    """One full clockwise traversal records duration and actual distance."""
    car = Car()
    travel(car, 0, math.tau + 0.01)
    assert car.laps == 1
    assert car.history[0].seconds == pytest.approx(8.0, abs=0.03)
    expected = math.tau * RADIUS * METRES_PER_PIXEL / 8 * 3.6
    assert car.history[0].average_kmh == pytest.approx(expected, rel=0.01)


def test_partial_reverse_and_shortcut_do_not_count() -> None:
    """Incomplete, backward, and off-track circuits cannot finish valid laps."""
    partial = Car()
    travel(partial, 0, math.pi)
    assert partial.laps == 0
    reverse = Car()
    travel(reverse, 0, -math.tau - 0.1)
    assert reverse.laps == 0
    shortcut = Car()
    travel(shortcut, 0, math.tau + 0.01, on_track=False)
    assert shortcut.laps == 0
    assert not shortcut.valid


def test_driving_timestep_and_braking() -> None:
    """Movement approximately agrees across small time steps and brakes stop."""
    first, second = Car(), Car()
    for _ in range(50):
        first.step(0.02, True, False, 0)
    for _ in range(100):
        second.step(0.01, True, False, 0)
    assert first.x == pytest.approx(second.x, abs=1)
    assert first.y == pytest.approx(second.y, abs=1)
    for _ in range(100):
        first.step(0.02, False, True, 0)
    assert first.speed == 0
    with pytest.raises(ValueError):
        first.step(float("nan"), True, False, 0)


def test_departed_sessions_and_stale_sequences() -> None:
    """Buffered messages do not resurrect peers or overwrite newer positions."""
    session = Session("local")
    session.event({"type": "joined", "peer": {"id": "peer", "name": "Peer"}})
    snapshot = session.snapshot(Car())
    envelope = {
        "type": "message",
        "sender": {"id": "peer", "name": "Peer"},
        "payload": snapshot,
    }
    session.event(envelope)
    assert session.opponents["peer"].sequence == 1
    session.opponents["peer"].x = 240
    session.event(envelope)
    assert session.opponents["peer"].x == 240
    session.event({"type": "left", "id": "peer"})
    session.event(envelope)
    assert not session.opponents


def test_peer_validation_is_atomic() -> None:
    """An invalid state cannot partially modify an existing opponent."""
    session = Session("local")
    session.add_peer({"id": "peer", "name": "Peer"})
    state = session.snapshot(Car())
    state["speed"] = float("inf")
    with pytest.raises(ProtocolError):
        session.update(session.opponents["peer"], state)
    assert session.opponents["peer"].sequence == -1


def test_many_laps_with_start_line_overshoot() -> None:
    """Variable crossing overshoot does not lose a later otherwise valid lap."""
    car = Car()
    angle = 0.0
    for _ in range(1100):
        previous_x, previous_y = car.x, car.y
        angle += 0.057
        car.x = RADIUS * math.cos(angle)
        car.y = RADIUS * math.sin(angle)
        car.observe(previous_x, previous_y, 0.05, True)
    assert car.laps == int(angle / math.tau)
