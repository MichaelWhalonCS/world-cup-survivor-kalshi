"""Application configuration using pydantic-settings."""

from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import model_validator
from pydantic_settings import BaseSettings


APP_TZ_NAME = "America/Chicago"


def _resolve_app_timezone() -> tzinfo:
    """Return Central Time zone, falling back to UTC if tzdata is unavailable."""
    try:
        return ZoneInfo(APP_TZ_NAME)
    except ZoneInfoNotFoundError:
        return UTC


APP_TZ = _resolve_app_timezone()


def now_in_app_tz() -> datetime:
    """Current datetime in app timezone (Central when available)."""
    return datetime.now(APP_TZ)


# Survivor-pool round transition dates for the 2026 FIFA World Cup (Central Time).
# These are pick-window boundaries based on the FIFA-published schedule:
# Group: Jun 11–27 (MD1 Jun 11–16, MD2 Jun 16–22, MD3 Jun 25–27)
# R32: Jun 29 – Jul 3;  R16: Jul 4–7;  QF: Jul 9–11;  SF: Jul 14–15;  Final: Jul 19
_ROUND_SCHEDULE: list[tuple[str, date]] = [
    ("MD1",   date(2026, 6, 11)),
    ("MD2",   date(2026, 6, 16)),
    ("MD3",   date(2026, 6, 23)),
    ("R32",   date(2026, 6, 29)),
    ("R16",   date(2026, 7, 4)),
    ("QF",    date(2026, 7, 9)),
    ("SF",    date(2026, 7, 14)),
    ("Final", date(2026, 7, 19)),
]


def _detect_current_round() -> str:
    """Determine the current tournament round based on today's date (Central time)."""
    today = now_in_app_tz().date()
    result = "MD1"  # default before or at tournament start
    for round_code, start_date in _ROUND_SCHEDULE:
        if today >= start_date:
            result = round_code
    return result


class Settings(BaseSettings):
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: Path = Path("./secrets/kalshi_private_key.pem")
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    log_level: str = "INFO"

    # Output paths
    html_output_path: Path = Path("./docs/index.html")
    snapshot_dir: Path = Path("./data/snapshots")

    # Data file paths
    groups_path: Path = Path("./data/groups.json")
    matches_path: Path = Path("./data/matches.json")

    # Current tournament round — set to "auto" to detect from today's date.
    # Valid explicit values: MD1, MD2, MD3, R32, R16, QF, SF, Final
    current_round: str = "auto"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @model_validator(mode="after")
    def _resolve_auto_round(self):
        if self.current_round == "auto":
            self.current_round = _detect_current_round()
        return self


settings = Settings()
