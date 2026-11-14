"""Bounded length-prefixed JSON messages over plain TCP sockets."""

import json
import math
import socket
import struct
from typing import cast

from network_apps.configuration import Settings

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type Message = dict[str, Json]
VERSION = 1


class ProtocolError(ValueError):
    """A peer supplied an invalid or unsupported message."""


def encode(message: Message, limit: int) -> bytes:
    """Serialize a finite JSON object and enforce the wire size limit."""
    try:
        body = json.dumps(
            message, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (ValueError, RecursionError) as error:
        raise ProtocolError("Message is not finite, supported JSON") from error
    if not 0 < len(body) <= limit:
        raise ProtocolError("Message exceeds the frame size limit")
    return struct.pack("!I", len(body)) + body


def decode(body: bytes) -> Message:
    """Decode a JSON object; reject non-finite constants and deep structures."""
    try:
        value: object = json.loads(body, parse_constant=reject_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ProtocolError("Invalid JSON object") from error
    if not isinstance(value, dict):
        raise ProtocolError("A message must be a JSON object")
    return cast(Message, value)


def reject_constant(value: str) -> Json:
    """Reject JSON extensions such as NaN and Infinity."""
    raise ValueError(f"Unsupported JSON constant: {value}")


def exact(sock: socket.socket, length: int) -> bytes:
    """Read exactly one bounded segment or report a disconnected peer."""
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise EOFError("Server closed the connection")
        chunks.extend(chunk)
    return bytes(chunks)


def receive(sock: socket.socket, limit: int) -> Message:
    """Read and validate one frame without allocating an oversized body."""
    length = struct.unpack("!I", exact(sock, 4))[0]
    if not 0 < length <= limit:
        raise ProtocolError("Invalid frame length")
    return decode(exact(sock, length))


def client_socket(settings: Settings) -> socket.socket:
    """Open a plain TCP connection to the configured relay."""
    connection = socket.create_connection(
        (settings.text("host"), settings.integer("port")),
        timeout=settings.number("connect_timeout"),
    )
    connection.settimeout(settings.number("idle_timeout"))
    return connection


def text(message: Message, key: str, maximum: int = 128) -> str:
    """Extract bounded, nonempty text without control characters."""
    value = message.get(key)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(not char.isprintable() for char in value)
    ):
        raise ProtocolError(f"Invalid {key}")
    return value


def finite_number(message: Message, key: str) -> float:
    """Extract a finite JSON number, excluding booleans."""
    value = message.get(key)
    if type(value) not in (int, float):
        raise ProtocolError(f"Invalid numeric {key}")
    assert isinstance(value, (int, float))
    try:
        result = float(value)
    except OverflowError as error:
        raise ProtocolError(f"Invalid numeric {key}") from error
    if not math.isfinite(result):
        raise ProtocolError(f"Invalid numeric {key}")
    return result
