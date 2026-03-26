"""
Configuration loading for the trading bot.

Priority (highest → lowest):
  1. Environment variables  (BINANCE_TESTNET_API_KEY, etc.)
  2. config.toml            (project root)
  3. .env file              (project root)
  4. Built-in defaults

Usage::

    from bot.config import load_config
    cfg = load_config()
    print(cfg.api_key, cfg.log_level)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_TOML  = PROJECT_ROOT / "config.toml"
ENV_FILE     = PROJECT_ROOT / ".env"


@dataclass
class BotConfig:
    """Resolved bot configuration."""
    api_key:          str  = ""
    api_secret:       str  = ""
    base_url:         str  = "https://testnet.binancefuture.com"
    recv_window:      int  = 5000
    log_level:        str  = "INFO"
    dry_run:          bool = False
    default_symbol:   str  = "BTCUSDT"


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file (no shell expansion)."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _load_toml(path: Path) -> dict:
    """
    Load a TOML config file.
    Uses the stdlib `tomllib` (Python 3.11+) or falls back to `tomli`.
    Returns an empty dict if the file is absent or no TOML lib is available.
    """
    if not path.exists():
        return {}
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            import tomli as tomllib  # pip install tomli

        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def load_config(config_path: Optional[Path] = None, env_path: Optional[Path] = None) -> BotConfig:
    """
    Build a BotConfig by merging .env, config.toml, and environment variables.

    Args:
        config_path: Override for config.toml location.
        env_path:    Override for .env file location.

    Returns:
        Fully resolved BotConfig instance.
    """
    env_file_vars = _load_env_file(env_path or ENV_FILE)
    toml_data     = _load_toml(config_path or CONFIG_TOML)
    toml_bot      = toml_data.get("bot", {})

    def get(env_key: str, toml_key: str, default: str = "") -> str:
        """Resolve a string value: env var → toml → .env file → default."""
        return (
            os.environ.get(env_key)
            or toml_bot.get(toml_key)
            or env_file_vars.get(env_key)
            or default
        )

    def get_bool(env_key: str, toml_key: str, default: bool = False) -> bool:
        raw = get(env_key, toml_key, str(default))
        return str(raw).lower() in {"1", "true", "yes"}

    def get_int(env_key: str, toml_key: str, default: int = 0) -> int:
        try:
            return int(get(env_key, toml_key, str(default)))
        except ValueError:
            return default

    return BotConfig(
        api_key        = get("BINANCE_TESTNET_API_KEY",    "api_key"),
        api_secret     = get("BINANCE_TESTNET_API_SECRET", "api_secret"),
        base_url       = get("BINANCE_BASE_URL",           "base_url",       "https://testnet.binancefuture.com"),
        recv_window    = get_int("BINANCE_RECV_WINDOW",    "recv_window",    5000),
        log_level      = get("BOT_LOG_LEVEL",              "log_level",      "INFO"),
        dry_run        = get_bool("BOT_DRY_RUN",           "dry_run",        False),
        default_symbol = get("BOT_DEFAULT_SYMBOL",         "default_symbol", "BTCUSDT"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Sample config.toml (written to project root if missing)
# ──────────────────────────────────────────────────────────────────────────────

SAMPLE_TOML = """\
# Binance Futures Testnet — Bot Configuration
# Values here are overridden by environment variables.

[bot]
# api_key    = "YOUR_API_KEY"       # prefer env var BINANCE_TESTNET_API_KEY
# api_secret = "YOUR_API_SECRET"    # prefer env var BINANCE_TESTNET_API_SECRET

base_url       = "https://testnet.binancefuture.com"
recv_window    = 5000
log_level      = "INFO"    # DEBUG | INFO | WARNING | ERROR
dry_run        = false
default_symbol = "BTCUSDT"
"""


def write_sample_config(path: Path = CONFIG_TOML) -> None:
    """Write a sample config.toml if one does not already exist."""
    if not path.exists():
        path.write_text(SAMPLE_TOML, encoding="utf-8")
