"""
Logging configuration for the Binance Futures Trading Bot.
Sets up both a rotating file handler and a console handler.
"""

import logging
import logging.handlers
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_FILE = LOG_DIR / "trading_bot.log"

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """
    Configure root logger with a rotating file handler and a console handler.

    Args:
        log_level: Minimum level shown on the console (DEBUG/INFO/WARNING/ERROR).

    Returns:
        Configured root logger.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)   # file always captures DEBUG+

    # Avoid duplicate handlers when the function is called more than once
    root_logger.handlers.clear()

    # Rotating file handler — keeps last 5 × 5 MB slices
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root_logger.addHandler(file_handler)

    # Console handler — respects the --log-level CLI flag
    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root_logger.addHandler(console_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Return a named child logger (inherits root configuration)."""
    return logging.getLogger(name)
