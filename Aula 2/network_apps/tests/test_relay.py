"""Acceptance tests for actual TCP routing, bounds, and client lifecycles."""

import asyncio
import struct
import time
from collections.abc import Callable

import pytest
from conftest import Running, WireClient

from network_apps.client import Client
from network_apps.configuration import Setting, Settings
from network_apps.protocol import ProtocolError, client_socket, encode
from network_apps.relay import Pending, Relay


def test_fanout_identity_no_echo_and_channel_isolation(
    relay_factory: Callable[[dict[str, Setting]], Running], clients: list[WireClient]
) -> None:
    """Two chat recipients receive stamped identity; sender and race do not."""
    service = relay_factory({})
    alice = WireClient(service.settings, "Alice")
    bob = WireClient(service.settings, "Bob")
    carol = WireClient(service.settings, "Carol")
    race = WireClient(service.settings, "Driver", "race")
    clients.extend([alice, bob, carol, race])
    alice.until("joined")
    alice.until("joined")
    alice.send(
        {
            "type": "publish",
            "request": "one",
            "sender": {"name": "Forged"},
            "payload": {"text": "hello"},
        }
    )
    for recipient in (bob, carol):
        event = recipient.until("message")
        assert event["sender"] == {"id": alice.welcome["id"], "name": "Alice"}
        assert event["payload"] == {"text": "hello"}
    assert alice.until("accepted")["status"] == "delivered"
    for isolated in (alice, race):
        isolated.socket.settimeout(0.15)
        with pytest.raises(TimeoutError):
            isolated.read()


def test_queue_waits_for_other_channel_peer_and_drains_once(
    relay_factory: Callable[[dict[str, Setting]], Running], clients: list[WireClient]
) -> None:
    """Pending messages skip their sender and other channels, with FIFO drain."""
    service = relay_factory({})
    alice = WireClient(service.settings, "Alice")
    clients.append(alice)
    for number in range(3):
        alice.publish(str(number))
        assert alice.until("accepted")["status"] == "queued"
    driver = WireClient(service.settings, "Driver", "race")
    clients.append(driver)
    bob = WireClient(service.settings, "Bob")
    clients.append(bob)
    assert [bob.until("message")["payload"] for _ in range(3)] == [
        {"kind": "chat", "text": str(number)} for number in range(3)
    ]
    carol = WireClient(service.settings, "Carol")
    clients.append(carol)
    carol.socket.settimeout(0.15)
    with pytest.raises(TimeoutError):
        carol.read()


@pytest.mark.parametrize(
    "limits", [{"max_pending_messages": 1}, {"max_pending_bytes": 1}]
)
def test_queue_rejection_is_explicit(
    relay_factory: Callable[[dict[str, Setting]], Running],
    clients: list[WireClient],
    limits: dict[str, Setting],
) -> None:
    """Reject overflow without evicting an already accepted message."""
    service = relay_factory(limits)
    alice = WireClient(service.settings, "Alice")
    clients.append(alice)
    alice.publish("first")
    response = alice.read()
    if "max_pending_messages" in limits:
        assert response["status"] == "queued"
        alice.publish("second")
        response = alice.read()
    assert response["type"] == "error"
    assert "full" in str(response["detail"])


def test_open_registration_duplicates_and_limits(
    relay_factory: Callable[[dict[str, Setting]], Running], clients: list[WireClient]
) -> None:
    """Anyone may join without credentials; names and capacity are enforced."""
    service = relay_factory({"max_clients": 2})
    alice = WireClient(service.settings, "Alice")
    duplicate = WireClient(service.settings, "ALICE")
    assert duplicate.socket.recv(1) == b""
    bob = WireClient(service.settings, "Bob")
    full = WireClient(service.settings, "Carol")
    clients.extend([alice, duplicate, bob, full])
    assert alice.welcome["type"] == "welcome"
    assert bob.welcome["type"] == "welcome"
    for rejected in (duplicate, full):
        assert rejected.welcome["type"] == "error"


def test_fragmented_and_oversized_frames(
    relay_factory: Callable[[dict[str, Setting]], Running], clients: list[WireClient]
) -> None:
    """TCP stream fragmentation is legal but excessive declared lengths are not."""
    service = relay_factory({})
    alice = WireClient(service.settings, "Alice")
    clients.append(alice)
    wire = encode({"type": "ping"}, 65536)
    for piece in (wire[:2], wire[2:5], wire[5:]):
        alice.socket.sendall(piece)
    assert alice.read()["type"] == "pong"
    alice.socket.sendall(struct.pack("!I", 65537))
    assert alice.read()["type"] == "error"


def test_rate_limit(
    relay_factory: Callable[[dict[str, Setting]], Running], clients: list[WireClient]
) -> None:
    """Rate excess returns an error and closes the offending session."""
    service = relay_factory({"messages_per_second": 2})
    alice = WireClient(service.settings, "Alice")
    clients.append(alice)
    for _ in range(3):
        alice.send({"type": "ping"})
    assert "rate" in str(alice.until("error")["detail"])


def test_client_shutdown_after_server_disconnect(
    relay_factory: Callable[[dict[str, Setting]], Running],
) -> None:
    """All reusable-client workers stop when the server closes their socket."""
    service = relay_factory({"idle_timeout": 0.15})
    client = Client(service.settings, "Alice", "chat")
    client.start()
    try:
        assert client.stopped.wait(3)
    finally:
        client.close()
    assert all(not worker.is_alive() for worker in client.workers)


def test_pending_retained_when_all_writes_fail() -> None:
    """No disconnected recipient counts as successful offline delivery."""
    from unittest.mock import AsyncMock, Mock

    from network_apps.configuration import COMMON, SERVER
    from network_apps.relay import Peer

    async def scenario() -> None:
        """Exercise a queue drain whose only recipient fails to write."""
        relay = Relay(Settings(COMMON | SERVER))
        item = Pending("sender", "chat", encode({"type": "message"}, 1024))
        relay.store(item)
        writer = Mock(spec=asyncio.StreamWriter)
        writer.is_closing.return_value = False
        writer.drain = AsyncMock(side_effect=ConnectionError("Test failure"))

        def closed() -> None:
            """Model a writer becoming unavailable after its socket fails."""
            writer.is_closing.return_value = True

        writer.close.side_effect = closed
        peer = Peer("recipient", "Recipient", "chat", writer, asyncio.Queue(2))
        relay.peers[peer.identifier] = peer
        task = asyncio.create_task(relay.writer_loop(peer))
        await relay.flush()
        await task
        assert list(relay.pending) == [item]
        assert relay.pending_bytes == len(item.wire)

    asyncio.run(scenario())


def test_two_reusable_race_clients_exchange_states(
    relay_factory: Callable[[dict[str, Setting]], Running],
) -> None:
    """Actual background transports synchronize two independent race sessions."""
    from network_apps.racing.driving import Car
    from network_apps.racing.session import Session

    service = relay_factory({})
    first = Client(service.settings, "One", "race")
    second = Client(service.settings, "Two", "race")
    sessions = [Session(first.identifier), Session(second.identifier)]
    try:
        first.start()
        second.start()
        first.publish(sessions[0].snapshot(Car()))
        second.publish(sessions[1].snapshot(Car()))
        for client, session, other in (
            (first, sessions[0], second),
            (second, sessions[1], first),
        ):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                session.event(client.events.get(timeout=2))
                if (
                    other.identifier in session.opponents
                    and session.opponents[other.identifier].sequence == 1
                ):
                    break
            else:
                pytest.fail("Race state did not reach the other client")
    finally:
        first.close()
        second.close()


def test_slow_peer_does_not_block_another_writer() -> None:
    """A stalled socket is retired while another recipient receives promptly."""
    from unittest.mock import AsyncMock, Mock

    from network_apps.configuration import COMMON, SERVER
    from network_apps.relay import Peer

    async def scenario() -> None:
        """Isolate backpressure deterministically instead of filling TCP buffers."""
        relay = Relay(Settings(COMMON | SERVER | {"write_timeout": 0.1}))
        stalled = asyncio.Event()
        slow_writer = Mock(spec=asyncio.StreamWriter)
        slow_writer.drain = AsyncMock(side_effect=stalled.wait)
        fast_writer = Mock(spec=asyncio.StreamWriter)
        fast_writer.drain = AsyncMock()
        slow = Peer("slow", "Slow", "chat", slow_writer, asyncio.Queue(2))
        fast = Peer("fast", "Fast", "chat", fast_writer, asyncio.Queue(2))
        tasks = [asyncio.create_task(relay.writer_loop(peer)) for peer in (slow, fast)]
        try:
            slow_receipt = relay.offer(slow, b"test")
            fast_receipt = relay.offer(fast, b"test")
            assert await asyncio.wait_for(fast_receipt, 0.05)
            assert not slow_receipt.done()
            assert not await asyncio.wait_for(slow_receipt, 1)
            slow_writer.close.assert_called()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_idle_partial_frame_expires(
    relay_factory: Callable[[dict[str, Setting]], Running],
) -> None:
    """A peer cannot keep registration alive by sending a partial header."""
    service = relay_factory({"handshake_timeout": 0.15})
    sock = client_socket(service.settings)
    try:
        sock.sendall(b"\x00")
        deadline = time.monotonic() + 2
        assert sock.recv(1) == b""
        assert time.monotonic() < deadline
    finally:
        sock.close()


def test_queue_accepts_exact_byte_capacity_then_rejects() -> None:
    """Include the four-byte prefix when enforcing the precise memory bound."""
    from network_apps.configuration import COMMON, SERVER

    wire = encode({"type": "message"}, 1024)
    relay = Relay(Settings(COMMON | SERVER | {"max_pending_bytes": len(wire)}))
    first = Pending("one", "chat", wire)
    relay.store(first)
    assert relay.pending_bytes == len(wire)
    with pytest.raises(ProtocolError, match="full"):
        relay.store(Pending("two", "chat", wire))
    assert list(relay.pending) == [first]


def test_departure_is_broadcast(
    relay_factory: Callable[[dict[str, Setting]], Running], clients: list[WireClient]
) -> None:
    """A disconnected peer is removed from the live roster."""
    service = relay_factory({})
    alice = WireClient(service.settings, "Alice")
    bob = WireClient(service.settings, "Bob")
    clients.extend([alice, bob])
    bob.close()
    assert alice.until("left")["id"] == bob.welcome["id"]
