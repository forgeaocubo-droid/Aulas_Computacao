# TCP relay, terminal chat, and circular racing

Three Python applications share one bounded, channel-aware TCP protocol:

- **Relay server:** registers display names, stamps sender identity, and
  broadcasts messages to other participants in the same channel.
- **Terminal chat:** independent input/sending and receive/display threads,
  plus background TCP transport workers.
- **Pygame race:** a steerable car for each connected game client, a circular
  track, forward lap validation, lap times, and average speed per lap.

No database, web frontend, or external game assets are required.

## Requirements and installation

Use **Python 3.13** on Windows. The code targets Python 3.12+, but the original
Pygame 2.6.1 package does not provide a Python 3.14 Windows wheel. Python 3.14
was tested during setup and could not build Pygame. Do not install unrelated compiler packages to bypass this incompatibility.

Open PowerShell in this project directory:

```powershell
Set-Location 'C:\GIT_Pedro\Aulas_Computacao\Aula 2\network_apps'
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install -e '.[dev]'
```

If only Python 3.14 is installed, use an isolated runtime instead. This does
not modify the global Python installation or PATH:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) '.python'
.\.venv\Scripts\uv.exe python install 3.13 --no-bin
$runtime = Get-ChildItem '.python' -Filter python.exe -Recurse |
    Select-Object -First 1 -ExpandProperty FullName
& $runtime -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install -e '.[dev]'
```

The current workspace already has this local Python 3.13 environment prepared.
Dependencies and private runtime directories are excluded from Git.
For a production server, install the package in an isolated supported Python
environment without the `dev` extra.

The runtime libraries alone are also listed in `Aula 2\requirements.txt`
(`pip install -r ..\requirements.txt`).

## Configuration

Copy the example configuration files:

```powershell
Copy-Item 'config\server.example.yaml' 'config\server.yaml'
Copy-Item 'config\client.example.yaml' 'config\client.yaml'
```

The server opens a plain TCP port and accepts any client that sends a valid
`hello` message. There is no encryption, certificate, or access token: anyone
who can reach the port can join, read, and send messages.

## Run the three applications

### Quick launchers

The `Aula 2` folder contains Python launchers that can be double-clicked or run
with any Python from any directory (for example `python "Aula 2\chat.py" Alice`).
They start the application with `network_apps\.venv313` and create
`config\server.yaml` or `config\client.yaml` from the example file if missing:

| Launcher | Application |
| --- | --- |
| `Aula 2\server.py` | Relay server |
| `Aula 2\chat.py [name]` | Terminal chat |
| `Aula 2\game.py [name]` | Race game |

When the name is omitted, the chat and game launchers ask for it.
On failure, the window waits for Enter so the error can be read.
Shared launcher logic is in `Aula 2\_launcher.py`.

### Manual commands

Run each command in its own PowerShell window, from this project directory.

Start one server:

```powershell
.\.venv313\Scripts\python.exe -m network_apps.relay --config 'config\server.yaml'
```

Start two terminal chats:

```powershell
.\.venv313\Scripts\python.exe -m network_apps.chat --config 'config\client.yaml' --name Alice
```

```powershell
.\.venv313\Scripts\python.exe -m network_apps.chat --config 'config\client.yaml' --name Bob
```

Start two race clients:

```powershell
.\.venv313\Scripts\python.exe -m network_apps.racing.game --config 'config\client.yaml' --name DriverOne
```

```powershell
.\.venv313\Scripts\python.exe -m network_apps.racing.game --config 'config\client.yaml' --name DriverTwo
```

Equivalent installed commands are `relay-server`, `terminal-chat`, and
`circuit-race` in the environment's Scripts directory.
All entry points support `--help`.

Names must be nonempty, printable, at most 32 characters, and unique
case-insensitively across active connections. Names are chosen during connection,
not stored as accounts. Reconnection creates a new session.

### Terminal chat

Type a line and press Enter to send it. Received messages include the sender's
registered name. Your messages are not echoed by the server.
`/quit` or Ctrl+C exits. Input polling allows a remote disconnect to stop the
chat without waiting for another Enter key. Use an interactive terminal;
Windows input uses `msvcrt`, rather than redirected standard input.

### Race controls and rules

| Action | Keys |
| --- | --- |
| Accelerate | W or Up |
| Brake | S or Down |
| Steer | A/D or Left/Right |
| Leave | Esc or close the window |

Drive clockwise. Steering requires movement. The car starts at the right-hand
start line facing down. Track edges constrain movement, slow the car, and
invalidate the current lap. A valid lap requires forward travel through three
ordered quarter-track checkpoints and then across the start line.
Reverse travel invalidates the lap; cross the start line forward to reset it.
Standing at the start, reversing across it, or cutting the infield cannot
complete a lap.

One logical pixel represents **0.2 metres**. Instantaneous speed is derived
from the simulated movement speed. Average lap speed is the actual sampled
distance travelled during the valid lap divided by its duration, converted
to km/h. The centreline circumference is approximately 308 metres.
The panel shows completed laps and the five most recent lap times/speeds;
the client keeps up to 100 recent lap records in memory.

Only race-channel connections create cars. A new car starts at the line,
then receives current peer snapshots. Names accompany colors; overlapping
starting positions are intentional because cars have no collisions.
The window is resizable, with a 900-by-620 minimum and letterboxed scaling.

The simulation runs locally and is **not cheat-resistant**. There is no finish
line target, durable leaderboard, reconnect recovery, or authoritative server
physics. Remote cars are visually interpolated. On connection loss the game
stops local driving, shows a disconnected status, and allows Esc to exit.

## Relay contract

TCP frames contain a four-byte network-order unsigned length and one UTF-8 JSON
object. The server validates the length before allocating a body.
Registration sends:

```json
{"type":"hello","version":1,"name":"Alice","channel":"chat"}
```

The server returns `welcome` with a server-generated session `id` and `peers`.
Subsequent membership events are `joined` and `left`.
Application messages use:

```json
{"type":"publish","request":"unique-request-id","payload":{"kind":"chat","text":"Hello"}}
```

Recipients receive `message` with a server-owned `sender: {"id": "...",
"name": "..."}` and the unchanged payload. The sender receives `accepted`
with its request ID and `status` of `delivered` or `queued`.
Errors use `error` with `detail` and, for rejected publications, the request ID.
Client `ping` messages receive `pong`.

- Chat and race traffic never cross channels.
- A sending session never receives its own relayed message.
- With no eligible recipient, messages stay in server memory without expiration.
- Eligible queued messages drain in queue order. A message is removed once at
  least one eligible recipient's socket write succeeds; it is not replayed for
  later arrivals. Undeliverable messages in other channels do not block drain.
- This is **best-effort socket delivery**, not an acknowledgement that an
  application consumed a message. There are no exactly-once or durability
  guarantees. A new session using a previous name is a different sender.
- Server restart loses all pending messages and registrations.
- A full pending queue rejects new messages explicitly, retaining existing
  messages. A racing client alone also queues its updates: by default, roughly
  50 seconds of 20 Hz updates fill the 1,000-message queue. Further updates
  show errors until another driver drains it. There is no hidden state-dropping
  exception to the buffering contract.
- Race clients reject invalid peer payloads, ignore stale sequence numbers,
  and ignore buffered updates from sessions absent from their current roster.
- Outbound buffers are bounded; slow peers are disconnected and logged.
  Malformed frames, invalid registration, unsupported messages, rate excess,
  and idle/partial-frame timeouts close the connection.
- No automatic reconnect or resend occurs. Start the client again explicitly.

The relay does not interpret race rules, chat text, or game snapshots.

## YAML settings and logging

The example YAML files list every setting and its default. Paths are relative
to the **working directory**, not the YAML file location. Unknown keys and
invalid types/limits fail startup.

| Setting | Default / purpose |
| --- | --- |
| `host`, `port` | Client localhost; example server all interfaces; port 8765 |
| `max_clients` | 32 active/registering application connections |
| `max_frame_bytes` | 65,536 bytes, including JSON envelope but excluding prefix |
| `max_pending_messages` | 1,000 buffered messages across both channels |
| `max_pending_bytes` | 8,388,608 bytes, including stored frame prefixes |
| `messages_per_second` | 30 per peer, including heartbeats |
| `outbound_messages` | 128 queued frames per server-side peer |
| `handshake_timeout` | 5 seconds for server registration |
| `connect_timeout` | 5 seconds for client connection/registration operations |
| `write_timeout` | 3 seconds per server socket drain |
| `idle_timeout` | 45 seconds; server bounds the entire incoming frame |
| `race_updates_per_second` | 20, maximum 25; leave capacity for heartbeats |
| `log_level` | INFO; set DEBUG for detailed file logs |
| `log_directory` | `logs`, separate files for each process |
| `log_max_bytes`, `log_backups` | 1 MiB per file, 3 retained backups |

The terminal shows INFO and ERROR only. File handlers retain the configured
verbosity. Logs exclude message bodies.
The reusable client bounds its receive queue at 512 events and outgoing queue
at 128 publications; saturation produces an error and stops the connection.
Coordinate rate and frame settings across server and clients. Relay envelope
metadata also counts toward frame size, so allow payload headroom.

## Internet hosting

1. Set server `host` to an appropriate listening address and allow inbound TCP
   on the chosen port through the host firewall and any required NAT mapping.
2. Configure clients with the server's DNS name or IP address.

Traffic is unencrypted and unauthenticated; any reachable client can connect.
Connected peers are trusted for game state. Application connection/frame/rate
limits do not prevent bandwidth exhaustion or floods. This is a teaching/demo
application, not a hardened public chat service.

## Development and validation

```powershell
.\.venv313\Scripts\python.exe -m pytest -q
.\.venv313\Scripts\python.exe -m ruff check .
.\.venv313\Scripts\python.exe -m ruff format --check .
.\.venv313\Scripts\python.exe -m mypy src
```

Tests run real loopback TCP connections.
They cover routing, identity stamping, channels, memory bounds, open registration,
framing, rate limits, worker shutdown, lap rules,
peer sequences, and headless Pygame rendering. They do not operate your real
keyboard or validate public Internet deployment.

Source is split by responsibility: `relay.py` owns routing, `protocol.py` owns
framing/socket helpers, `client.py` owns client transport, `chat.py` owns terminal
interaction, and `racing` separates driving rules, membership/state, and drawing.
Change driving rules independently of Pygame; add deterministic regression tests
when modifying lap or networking behavior. Use precise types and explicit
if/else control flow rather than conditional expressions.
