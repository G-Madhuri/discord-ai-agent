"""Entry point: python -m app.discord.run_bot"""

from app.core.logging import configure_logging
from app.discord.bot import run

if __name__ == "__main__":
    configure_logging()
    run()
