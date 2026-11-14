"""Real, ephemeral TCP relay fixtures."""

import asyncio
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from network_apps.configuration import CLIENT, COMMON, SERVER, Setting, Settings
from network_apps.protocol import Message, client_socket, encode, receive
from network_apps.relay import Relay


@dataclass
class Running:
    """Expose a listening relay and matching validated client settings."""

    relay: Relay
    settings: Settings


@pytest.fixture
def relay_factory() -> Iterator[Callable[[dict[str, Setting]], Running]]:
    """Start isolated real TCP listeners and join all workers after each test."""
    services: list[
        tuple[asyncio.AbstractEventLoop, threading.Thread, Relay, asyncio.Server]
    ] = []

    def create(overrides: dict[str, Setting]) -> Running:
        """Create a relay on an OS-assigned port."""
        values = COMMON | SERVER | overrides
        relay = Relay(Settings(values))
        loop = asyncio.new_event_loop()
        server = loop.run_until_complete(
            asyncio.start_server(relay.handle, "127.0.0.1", 0)
        )
        port = server.sockets[0].getsockname()[1]
        thread = threading.Thread(target=loop.run_forever)
        thread.start()
        services.append((loop, thread, relay, server))
        client_values = COMMON | CLIENT
        client_values.update({"port": port, "host": "127.0.0.1"})
        return Running(relay, Settings(client_values))

    yield create
    for loop, thread, relay, server in reversed(services):

        async def shutdown(
            listener: asyncio.Server = server, broker: Relay = relay
        ) -> None:
            """Close the listener and every accepted connection."""
            listener.close()
            await listener.wait_closed()
            await broker.close()

        asyncio.run_coroutine_threadsafe(shutdown(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        assert not thread.is_alive()
        loop.close()


class WireClient:
    """Synchronous protocol test client with explicit event selection."""

    def __init__(
        self,
        settings: Settings,
        name: str,
        channel: str = "chat",
    ) -> None:
        """Open a real TCP session and retain its registration response."""
        self.socket = client_socket(settings)
        self.socket.settimeout(2)
        self.limit = settings.integer("max_frame_bytes")
        self.send(
            {
                "type": "hello",
                "version": 1,
                "name": name,
                "channel": channel,
            }
        )
        self.welcome = self.read()

    def send(self, message: Message) -> None:
        """Send a complete bounded protocol frame."""
        self.socket.sendall(encode(message, self.limit))

    def read(self) -> Message:
        """Receive the next event."""
        return receive(self.socket, self.limit)

    def until(self, kind: str) -> Message:
        """Skip known control events while finding the expected event."""
        for _ in range(100):
            event = self.read()
            if event["type"] == kind:
                return event
        raise AssertionError(f"No {kind} event")

    def publish(self, value: str, request: str = "test") -> None:
        """Publish a simple application message."""
        self.send(
            {
                "type": "publish",
                "request": request,
                "payload": {"kind": "chat", "text": value},
            }
        )

    def close(self) -> None:
        """Close the test connection."""
        self.socket.close()


@pytest.fixture
def clients() -> Iterator[list[WireClient]]:
    """Close protocol test clients even when an assertion fails."""
    opened: list[WireClient] = []
    yield opened
    for client in opened:
        client.close()
