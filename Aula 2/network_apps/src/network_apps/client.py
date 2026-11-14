"""Reusable TCP client with explicit worker ownership and bounded event queues."""

import logging
import queue
import socket
import threading
import time
import uuid

from network_apps.configuration import Settings
from network_apps.protocol import (
    Message,
    ProtocolError,
    client_socket,
    encode,
    receive,
)

LOGGER = logging.getLogger(__name__)


class Client:
    """Own a TCP socket, reader/heartbeat workers, and bounded event delivery."""

    def __init__(self, settings: Settings, name: str, channel: str) -> None:
        """Connect and register before starting background socket operations."""
        self.settings = settings
        self.limit = settings.integer("max_frame_bytes")
        self.events: queue.Queue[Message] = queue.Queue(512)
        self.outgoing: queue.Queue[Message] = queue.Queue(128)
        self.stopped = threading.Event()
        self.write_lock = threading.Lock()
        self.socket = client_socket(settings)
        self.workers: list[threading.Thread] = []
        try:
            self.socket.settimeout(settings.number("connect_timeout"))
            self.socket.sendall(
                encode(
                    {
                        "type": "hello",
                        "version": 1,
                        "name": name,
                        "channel": channel,
                    },
                    self.limit,
                )
            )
            self.welcome = receive(self.socket, self.limit)
            if self.welcome.get("type") != "welcome":
                raise ProtocolError(
                    str(self.welcome.get("detail", "Registration failed"))
                )
            identifier = self.welcome.get("id")
            if not isinstance(identifier, str):
                raise ProtocolError("Missing server session ID")
            self.identifier = identifier
            self.socket.settimeout(settings.number("idle_timeout"))
            self.events.put_nowait(self.welcome)
        except (OSError, ValueError, EOFError):
            self.socket.close()
            raise

    def start(self) -> None:
        """Start one receiver, one sender, and one heartbeat worker."""
        if self.workers:
            raise RuntimeError("Client has already started")
        for target in (self.read_loop, self.heartbeat, self.write_loop):
            worker = threading.Thread(target=target, name=target.__name__)
            self.workers.append(worker)
            worker.start()

    def send(self, message: Message) -> None:
        """Serialize concurrent writes and surface connection failures."""
        if self.stopped.is_set():
            raise ConnectionError("Connection is closed")
        wire = encode(message, self.limit)
        with self.write_lock:
            self.socket.sendall(wire)

    def publish(self, payload: Message) -> str:
        """Publish application data and return its acknowledgement identifier."""
        request = uuid.uuid4().hex
        if self.stopped.is_set():
            raise ConnectionError("Connection is closed")
        try:
            self.outgoing.put_nowait(
                {"type": "publish", "request": request, "payload": payload}
            )
        except queue.Full as error:
            raise ConnectionError(
                "Outgoing queue is full; connection cannot keep up"
            ) from error
        return request

    def write_loop(self) -> None:
        """Perform application writes outside the game/terminal input thread."""
        try:
            while not self.stopped.is_set():
                try:
                    message = self.outgoing.get(timeout=0.1)
                except queue.Empty:
                    continue
                self.send(message)
        except (OSError, ValueError) as error:
            LOGGER.error("Send failed: %s", error)
            self.stopped.set()

    def read_loop(self) -> None:
        """Read events until closed; a full consumer queue is a visible failure."""
        try:
            while not self.stopped.is_set():
                message = receive(self.socket, self.limit)
                if message.get("type") != "pong":
                    self.events.put(message, timeout=1)
        except (OSError, ValueError, EOFError, queue.Full) as error:
            if not self.stopped.is_set():
                LOGGER.error("Receive failed: %s", type(error).__name__)
                try:
                    self.events.put_nowait(
                        {"type": "disconnected", "detail": str(error)}
                    )
                except queue.Full:
                    LOGGER.error("Event queue full; connection stopped")
        finally:
            self.stopped.set()

    def heartbeat(self) -> None:
        """Keep idle registered sessions alive without a busy loop."""
        interval = min(10.0, self.settings.number("idle_timeout") / 3)
        while not self.stopped.wait(interval):
            try:
                self.send({"type": "ping"})
            except (OSError, ValueError) as error:
                LOGGER.error("Heartbeat failed: %s", type(error).__name__)
                self.stopped.set()

    def close(self) -> None:
        """Interrupt socket reads and join every owned worker."""
        self.stopped.set()
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            LOGGER.debug("Socket already disconnected")
        self.socket.close()
        deadline = time.monotonic() + 3
        for worker in self.workers:
            worker.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in self.workers):
            raise RuntimeError("Networking worker did not stop")
