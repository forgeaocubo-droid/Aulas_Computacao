"""Start a terminal chat client. Usage: python chat.py [name]"""

from _launcher import launch

if __name__ == "__main__":
    launch("network_apps.chat", "client", needs_name=True)
