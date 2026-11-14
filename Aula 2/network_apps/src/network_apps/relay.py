"""Channel-aware TCP relay with bounded queues and isolated socket writers."""

import argparse
import asyncio
import contextlib
import logging
import struct
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from network_apps.configuration import Settings, load_settings
from network_apps.logging_setup import configure_logging
from network_apps.protocol import Message, ProtocolError, decode, encode, text

LOGGER = logging.getLogger(__name__)


@dataclass(eq=False)
class Peer:
    """Own a registered connection and its bounded transmission queue."""

    identifier: str
    name: str
    channel: str
    writer: asyncio.StreamWriter
    outbox: asyncio.Queue[tuple[bytes, asyncio.Future[bool]]]
    task: asyncio.Task[None] | None = None
    arrivals: deque[float] = field(default_factory=deque)

    def description(self) -> Message:
        """Return public registration metadata."""
        return {"id": self.identifier, "name": self.name}


@dataclass(eq=False)
class Pending:
    """Retain the original sender, channel, and serialized relay envelope."""

    sender: str
    channel: str
    wire: bytes


class Relay:
    """Keep broker mutations on one event loop; perform writes per connection."""

    def __init__(self, settings: Settings) -> None:
        """Initialize independently bounded peer and offline-message storage."""
        self.settings = settings
        self.peers: dict[str, Peer] = {}
        self.pending: deque[Pending] = deque()
        self.pending_bytes = 0
        self.flush_lock = asyncio.Lock()
        self.flush_tasks: set[asyncio.Task[None]] = set()
        self.handlers: set[asyncio.Task[None]] = set()

    def wire(self, message: Message) -> bytes:
        """Encode a server envelope using the configured bound."""
        return encode(message, self.settings.integer("max_frame_bytes"))

    def offer(self, peer: Peer, wire: bytes) -> asyncio.Future[bool]:
        """Schedule one write without blocking another peer's receive loop."""
        receipt: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        try:
            peer.outbox.put_nowait((wire, receipt))
        except asyncio.QueueFull:
            LOGGER.error("Slow peer disconnected session=%s", peer.identifier)
            receipt.set_result(False)
            peer.writer.close()
        return receipt

    async def writer_loop(self, peer: Peer) -> None:
        """Write serialized frames and settle every queued delivery receipt."""
        current: asyncio.Future[bool] | None = None
        try:
            while True:
                wire, current = await peer.outbox.get()
                peer.writer.write(wire)
                await asyncio.wait_for(
                    peer.writer.drain(), self.settings.number("write_timeout")
                )
                if not current.done():
                    current.set_result(True)
                current = None
        except (OSError, TimeoutError, ConnectionError) as error:
            LOGGER.error(
                "Peer write failed session=%s error=%s",
                peer.identifier,
                type(error).__name__,
            )
        finally:
            if current is not None and not current.done():
                current.set_result(False)
            while not peer.outbox.empty():
                _, receipt = peer.outbox.get_nowait()
                if not receipt.done():
                    receipt.set_result(False)
            peer.writer.close()

    def recipients(self, sender: str, channel: str) -> list[Peer]:
        """Select live same-channel peers other than the original sender."""
        return [
            peer
            for peer in self.peers.values()
            if peer.identifier != sender
            and peer.channel == channel
            and not peer.writer.is_closing()
        ]

    def store(self, pending: Pending) -> None:
        """Append only when both configured offline queue bounds permit it."""
        if len(self.pending) >= self.settings.integer(
            "max_pending_messages"
        ) or self.pending_bytes + len(pending.wire) > self.settings.integer(
            "max_pending_bytes"
        ):
            raise ProtocolError("Pending queue is full; message was not accepted")
        self.pending.append(pending)
        self.pending_bytes += len(pending.wire)

    async def flush(self) -> None:
        """Drain eligible buffered messages in order, retaining failed writes."""
        async with self.flush_lock:
            while True:
                eligible = [
                    item
                    for item in self.pending
                    if self.recipients(item.sender, item.channel)
                ]
                if not eligible:
                    return
                item = eligible[0]
                peers = self.recipients(item.sender, item.channel)
                receipts = [self.offer(peer, item.wire) for peer in peers]
                delivered = await asyncio.gather(*receipts)
                if any(delivered):
                    self.pending.remove(item)
                    self.pending_bytes -= len(item.wire)

    def schedule_flush(self) -> None:
        """Track asynchronous queue draining for orderly server shutdown."""
        if any(not task.done() for task in self.flush_tasks):
            return
        task = asyncio.create_task(self.flush())
        self.flush_tasks.add(task)
        task.add_done_callback(self.flush_tasks.discard)

    async def read(self, reader: asyncio.StreamReader, timeout: float) -> Message:
        """Bound the entire frame read, including fragmented headers/bodies."""

        async def frame() -> Message:
            """Read one bounded frame."""
            length = struct.unpack("!I", await reader.readexactly(4))[0]
            if not 0 < length <= self.settings.integer("max_frame_bytes"):
                raise ProtocolError("Invalid frame length")
            return decode(await reader.readexactly(length))

        return await asyncio.wait_for(frame(), timeout)

    async def register(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Peer:
        """Validate the hello message before publishing a connection."""
        hello = await self.read(reader, self.settings.number("handshake_timeout"))
        if (
            hello.get("type") != "hello"
            or type(hello.get("version")) is not int
            or hello.get("version") != 1
        ):
            raise ProtocolError("Unsupported registration or protocol version")
        name = text(hello, "name", 32).strip()
        channel = text(hello, "channel")
        if channel not in ("chat", "race"):
            raise ProtocolError("Unsupported channel")
        # Checked after reading hello so closing never discards unread input,
        # which would reset the socket before the client reads the rejection.
        if len(self.handlers) > self.settings.integer("max_clients"):
            raise ProtocolError("Server connection limit reached")
        if any(peer.name.casefold() == name.casefold() for peer in self.peers.values()):
            raise ProtocolError("Display name is already connected")
        peer = Peer(
            uuid.uuid4().hex,
            name,
            channel,
            writer,
            asyncio.Queue(self.settings.integer("outbound_messages")),
        )
        roster = self.recipients(peer.identifier, channel)
        # Queue the roster before making this session visible to other senders.
        self.offer(
            peer,
            self.wire(
                {
                    "type": "welcome",
                    "id": peer.identifier,
                    "peers": [other.description() for other in roster],
                }
            ),
        )
        self.peers[peer.identifier] = peer
        peer.task = asyncio.create_task(self.writer_loop(peer))
        joined = self.wire({"type": "joined", "peer": peer.description()})
        for other in roster:
            self.offer(other, joined)
        self.schedule_flush()
        LOGGER.info("Peer connected session=%s channel=%s", peer.identifier, channel)
        return peer

    async def publish(self, peer: Peer, message: Message) -> None:
        """Stamp identity, relay data, and acknowledge acceptance or buffering."""
        request = text(message, "request", 64)
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ProtocolError("payload must be an object")
        wire = self.wire(
            {
                "type": "message",
                "sender": peer.description(),
                "payload": payload,
            }
        )
        others = self.recipients(peer.identifier, peer.channel)
        status = "delivered"
        if others:
            delivered = await asyncio.gather(
                *(self.offer(other, wire) for other in others)
            )
            if not any(delivered):
                self.store(Pending(peer.identifier, peer.channel, wire))
                status = "queued"
        else:
            self.store(Pending(peer.identifier, peer.channel, wire))
            status = "queued"
        self.offer(
            peer, self.wire({"type": "accepted", "request": request, "status": status})
        )
        # A registration may have raced a failed transmission or queue append.
        self.schedule_flush()

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Own one connection from registration through bounded cleanup."""
        current = asyncio.current_task()
        assert current is not None
        self.handlers.add(current)
        peer: Peer | None = None
        try:
            peer = await self.register(reader, writer)
            while True:
                message = await self.read(reader, self.settings.number("idle_timeout"))
                now = time.monotonic()
                while peer.arrivals and peer.arrivals[0] <= now - 1:
                    peer.arrivals.popleft()
                if len(peer.arrivals) >= self.settings.integer("messages_per_second"):
                    raise ProtocolError("Message rate exceeded; connection closed")
                peer.arrivals.append(now)
                kind = message.get("type")
                if kind == "ping":
                    self.offer(peer, self.wire({"type": "pong"}))
                elif kind == "publish":
                    try:
                        await self.publish(peer, message)
                    except ProtocolError as error:
                        self.offer(
                            peer,
                            self.wire(
                                {
                                    "type": "error",
                                    "request": message.get("request"),
                                    "detail": str(error),
                                }
                            ),
                        )
                else:
                    raise ProtocolError("Unsupported message type")
        except ProtocolError as error:
            LOGGER.error("Connection rejected: %s", error)
            wire = self.wire({"type": "error", "detail": str(error)})
            try:
                if peer is None:
                    writer.write(wire)
                    await asyncio.wait_for(
                        writer.drain(), self.settings.number("write_timeout")
                    )
                else:
                    await self.offer(peer, wire)
            except (OSError, TimeoutError):
                LOGGER.debug("Could not transmit rejection", exc_info=True)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            LOGGER.info("Connection ended reason=%s", type(error).__name__)
        finally:
            if peer is not None:
                self.peers.pop(peer.identifier, None)
                left = self.wire({"type": "left", "id": peer.identifier})
                for other in self.recipients(peer.identifier, peer.channel):
                    self.offer(other, left)
                if peer.task is not None:
                    peer.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await peer.task
            self.handlers.discard(current)
            writer.close()
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 2)

    async def close(self) -> None:
        """Cancel and join all owned connection and queue-draining tasks."""
        tasks = list(self.handlers | self.flush_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def serve(self) -> None:
        """Accept open TCP connections until cancelled by the entry point."""
        server = await asyncio.start_server(
            self.handle,
            self.settings.text("host"),
            self.settings.integer("port"),
            limit=self.settings.integer("max_frame_bytes") + 4,
        )
        LOGGER.info(
            "Relay listening host=%s port=%s",
            self.settings.text("host"),
            self.settings.integer("port"),
        )
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self.close()


def main() -> None:
    """Run the relay using an explicit YAML file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config, server=True)
        configure_logging(settings, "relay")
        asyncio.run(Relay(settings).serve())
    except KeyboardInterrupt:
        LOGGER.info("Relay stopped")
    except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
        parser.exit(1, f"Relay startup failed: {error}\n")


if __name__ == "__main__":
    main()
