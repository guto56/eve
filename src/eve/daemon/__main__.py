"""``python -m eve.daemon`` — ponto de entrada do processo de background."""

from eve.daemon.server import run

if __name__ == "__main__":
    run()
