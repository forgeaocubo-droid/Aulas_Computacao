"""Start the relay server. Usage: python server.py"""

from _launcher import launch

if __name__ == "__main__":
    launch("network_apps.relay", "server", needs_name=False)
