"""Terminal chat with dedicated sending and receiving/display threads."""

import argparse
import logging
import os
import queue
import sys
import threading
from pathlib import Path

import yaml

from network_apps.client import Client
from network_apps.configuration import load_settings
from network_apps.logging_setup import configure_logging
from network_apps.protocol import Message

LOGGER = logging.getLogger(__name__)


def safe_text(value: object) -> str:
    """Escape terminal controls in untrusted display text."""
    result: list[str] = []
    for char in str(value):
        if char.isprintable():
            result.append(char)
        else:
            result.append(f"\\u{ord(char):04x}")
    return "".join(result)


def input_lines(stopped: threading.Event) -> str | None:
    """Read one line with polling so remote closure interrupts terminal input."""
    if os.name == "nt":
        import msvcrt

        characters: list[str] = []
        while not stopped.wait(0.03):
            if not msvcrt.kbhit():
                continue
            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()
            elif char == "\x03":
                stopped.set()
                return None
            elif char in ("\r", "\n"):
                print()
                return "".join(characters)
            elif char == "\b":
                if characters:
                    characters.pop()
                    print("\b \b", end="", flush=True)
            elif char.isprintable() and len(characters) < 4000:
                characters.append(char)
                print(char, end="", flush=True)
        return None
    import select

    while not stopped.wait(0.05):
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if ready:
            line = sys.stdin.readline()
            if not line:
                stopped.set()
                return None
            return line.rstrip("\n")
    return None


def display_event(event: Message) -> None:
    """Render chat messages, membership events, and explicit broker errors."""
    kind = event.get("type")
    if kind == "message":
        sender = event.get("sender")
        payload = event.get("payload")
        if isinstance(sender, dict) and isinstance(payload, dict):
            print(
                f"\r{safe_text(sender.get('name'))}: "
                f"{safe_text(payload.get('text', '[Unsupported message]'))}",
                flush=True,
            )
    elif kind == "error" or kind == "disconnected":
        LOGGER.error("%s", safe_text(event.get("detail")))
    elif kind == "accepted" and event.get("status") == "queued":
        print("[Queued: waiting for another chat participant]", flush=True)
    elif kind == "joined":
        peer = event.get("peer")
        if isinstance(peer, dict):
            print(f"[{safe_text(peer.get('name'))} joined]", flush=True)
    elif kind == "left":
        print("[A participant left]", flush=True)


def run_chat(client: Client) -> None:
    """Start independent input/sending and receive/display workers."""

    def sending() -> None:
        """Send entered lines until the user quits or the connection closes."""
        try:
            while not client.stopped.is_set():
                line = input_lines(client.stopped)
                if line is None or line.strip() == "/quit":
                    break
                if line.strip():
                    client.publish({"kind": "chat", "text": line})
        except (OSError, ValueError) as error:
            LOGGER.error("Send failed: %s", error)
        finally:
            client.stopped.set()

    def displaying() -> None:
        """Display events without blocking the terminal input worker."""
        while not client.stopped.is_set() or not client.events.empty():
            try:
                event = client.events.get(timeout=0.1)
            except queue.Empty:
                continue
            display_event(event)

    sender = threading.Thread(target=sending, name="chat-sender")
    receiver = threading.Thread(target=displaying, name="chat-display")
    print("Connected. Type a message; /quit leaves the chat.", flush=True)
    client.start()
    sender.start()
    receiver.start()
    try:
        while not client.stopped.wait(0.1):
            pass
    except KeyboardInterrupt:
        client.stopped.set()
    finally:
        client.close()
        sender.join(2)
        receiver.join(2)
        if sender.is_alive() or receiver.is_alive():
            raise RuntimeError("Chat worker did not stop")


def main() -> None:
    """Read explicit connection settings and enter terminal chat."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
        configure_logging(settings, "chat")
        client = Client(settings, args.name, "chat")
        run_chat(client)
    except (OSError, ValueError, TypeError, EOFError, yaml.YAMLError) as error:
        parser.exit(1, f"Chat failed: {error}\n")


if __name__ == "__main__":
    main()
