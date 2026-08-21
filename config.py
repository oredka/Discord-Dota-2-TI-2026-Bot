"""Tournament settings loaded from tournament_config.json."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / "tournament_config.json"
STATE_FILE = Path(os.getenv("STATE_FILE", "states/match_states.json"))
TEAM_CATALOG_FILE = Path(os.getenv("TEAM_CATALOG_FILE", "team_metadata.json"))


def _load() -> dict:
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))


_CFG = _load()

TOURNAMENT_NAME = str(_CFG["tournament_name"])
DISCORD_USERNAME = str(_CFG.get("discord_username") or TOURNAMENT_NAME)
START_DATE = datetime.strptime(str(_CFG["start_date"]), "%Y-%m-%d").replace(tzinfo=UTC)
LEAGUE_ID = int(_CFG["league_id"])
LAST_DAY = int(_CFG.get("last_day") or _CFG["grand_final_day"])
GRAND_FINAL_DAY = int(_CFG["grand_final_day"])
GRAND_FINAL_BEST_OF = int(_CFG.get("grand_final_best_of", 5))
DEFAULT_BEST_OF = int(_CFG.get("default_best_of", 3))
REST_DAYS = {int(day) for day in _CFG["rest_days"]}
MIN_SERIES_PER_DAY = {int(day): int(count) for day, count in _CFG["min_series_per_day"].items()}
MAINCAST_DOTA2_URL = str(_CFG["maincast_youtube_url"])
OPENDOTA_API_ENDPOINT = str(_CFG["opendota_api_endpoint"])
OPENDOTA_MATCH_URL_TEMPLATE = str(_CFG["opendota_match_url_template"])
OPENDOTA_CACHE_SECONDS = int(_CFG["opendota_cache_seconds"])
LIQUIPEDIA_CACHE_SECONDS = int(_CFG["liquipedia_cache_seconds"])
LIQUIPEDIA_API = str(_CFG["liquipedia_api"])
LIQUIPEDIA_PAGE = str(_CFG["liquipedia_page"])
LIQUIPEDIA_STATISTICS_PAGE = str(_CFG["liquipedia_statistics_page"])
LIQUIPEDIA_URL = str(_CFG["liquipedia_url"])
USER_AGENT = os.getenv("LIQUIPEDIA_USER_AGENT", "").strip() or str(_CFG["user_agent"])
DEFAULT_EMBED_COLOR = 0xD32F2F
