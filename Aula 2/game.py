"""Start a race game client. Usage: python game.py [name]"""

from _launcher import launch

if __name__ == "__main__":
    launch("network_apps.racing.game", "client", needs_name=True)
