"""Track The International 2026 through STRATZ and post match events to Discord."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import requests

STRATZ_CACHE_SECONDS = 300
OPENDOTA_CACHE_SECONDS = 300
OPENDOTA_API_ENDPOINT = "https://api.opendota.com/api"
OPENDOTA_LEAGUE_ID = 19719
LIQUIPEDIA_API = "https://liquipedia.net/dota2/api.php"
LIQUIPEDIA_PAGE = "The_International/2026"
LIQUIPEDIA_URL = "https://liquipedia.net/dota2/The_International/2026"
MAINCAST_DOTA2_URL = "https://www.youtube.com/@Dota2_maincast"
STATE_FILE = Path(os.getenv("STATE_FILE", ".state/match_states.json"))
TEAM_CATALOG_FILE = Path(os.getenv("TEAM_CATALOG_FILE", "team_metadata.json"))
SERIES_BEST_OF = int(os.getenv("SERIES_BEST_OF", "3"))
LIQUIPEDIA_CACHE_SECONDS = 600
TI_START_DATE = datetime(2026, 8, 13, tzinfo=UTC)

# Deprecated STRATZ settings
STRATZ_ENDPOINT = "https://api.stratz.com/graphql"
QUERY = ""


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    try:
        response = requests.post(url, json=payload, headers=headers or {}, timeout=30)
        response.raise_for_status()
        return response.json() if response.text else {}
    except requests.exceptions.HTTPError as error:
        raise RuntimeError(f"HTTP {error.response.status_code}: {error.response.text}") from error
    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Network error: {error}") from error


def get_json(url: str, headers: dict[str, str]) -> dict:
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except (requests.exceptions.RequestException, json.JSONDecodeError) as error:
        raise RuntimeError(f"Liquipedia request failed: {error}") from error


def liquipedia_context(states: dict[str, object]) -> dict[str, object]:
    """Fetch the public page at most once per 10 minutes; match tracking must survive a failure."""
    cached = states.get("liquipedia")
    if isinstance(cached, dict) and time.time() - cached.get("fetched_at", 0) < LIQUIPEDIA_CACHE_SECONDS:
        return cached
    query = urlencode({"action": "parse", "page": LIQUIPEDIA_PAGE, "prop": "displaytitle", "format": "json"})
    user_agent = os.getenv("LIQUIPEDIA_USER_AGENT", "TI2026DiscordBot/1.0 (GitHub Actions)")
    try:
        data = get_json(f"{LIQUIPEDIA_API}?{query}", {"User-Agent": user_agent, "Accept": "application/json"})
        return {"fetched_at": time.time(), "title": data.get("parse", {}).get("displaytitle", "The International 2026")}
    except RuntimeError as error:
        print(f"Warning: {error}; continuing without Liquipedia extras.", file=sys.stderr)
        return cached if isinstance(cached, dict) else {"fetched_at": time.time()}


def fetch_matches_cached(states: dict[str, object], league_id: int) -> tuple[dict, list[dict]]:
    """Fetch from OpenDota with caching to reduce API calls."""
    cached = states.get("opendota_cache")
    if isinstance(cached, dict) and time.time() - cached.get("fetched_at", 0) < OPENDOTA_CACHE_SECONDS:
        print(f"Using cached OpenDota data (age: {int(time.time() - cached.get('fetched_at', 0))}s).")
        return cached.get("league", {}), cached.get("matches", [])
    
    try:
        league, matches = fetch_matches(league_id)
        states["opendota_cache"] = {"fetched_at": time.time(), "league": league, "matches": matches}
        print(f"Fetched {len(matches)} matches from OpenDota.")
        return league, matches
    except RuntimeError as error:
        if isinstance(cached, dict):
            print(f"Error: {error}; using stale OpenDota cache.", file=sys.stderr)
            return cached.get("league", {}), cached.get("matches", [])
        raise


def fetch_matches(league_id: int) -> tuple[dict, list[dict]]:
    # 1. Fetch matches
    matches = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/matches", {"User-Agent": "ti2026-discord-webhook/1.0"})
    
    # 2. Fetch teams to map names
    teams_data = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/teams", {"User-Agent": "ti2026-discord-webhook/1.0"})
    team_names = {t["team_id"]: t["name"] for t in teams_data}
    
    # 3. Enrich matches with team names
    for m in matches:
        m["radiant_name"] = team_names.get(m.get("radiant_team_id"), "Radiant")
        m["dire_name"] = team_names.get(m.get("dire_team_id"), "Dire")
    
    league = {"displayName": "The International 2026", "id": league_id}
    return league, sorted(matches, key=lambda item: item.get("start_time") or 0)


def teams(match: dict) -> tuple[str, str]:
    return (match.get("radiant_name") or "Radiant", match.get("dire_name") or "Dire")


def load_team_catalog() -> dict[str, dict[str, str]]:
    if not TEAM_CATALOG_FILE.exists():
        return {}
    try:
        data = json.loads(TEAM_CATALOG_FILE.read_text(encoding="utf-8"))
        return {name.casefold(): item for name, item in data.items() if isinstance(item, dict)}
    except json.JSONDecodeError:
        print("Warning: team_metadata.json is invalid; using team names only.", file=sys.stderr)
        return {}


def team_info(name: str, catalog: dict[str, dict[str, str]]) -> dict[str, str]:
    return catalog.get(name.casefold(), {})


def team_label(name: str, catalog: dict[str, dict[str, str]]) -> str:
    flag = team_info(name, catalog).get("flag", "")
    return f"{flag} {name}".strip()


def discord_color(value: str) -> int:
    try:
        return int(value.removeprefix("#"), 16)
    except ValueError:
        return 0xD32F2F


def match_state(match: dict, now: int) -> str:
    if match.get("radiant_win") is not None:
        return "finished"
    return "scheduled" if (match.get("start_time") or 0) > now else "live"


def series_key(match: dict) -> str:
    if match.get("series_id"):
        return f"series:{match['series_id']}"
    team_names = "|".join(sorted(teams(match)))
    return f"pair:{team_names}:{(match.get('start_time') or 0) // 86400}"


def tournament_day(match: dict) -> int:
    """Calculate which tournament day this match is on (1-indexed)."""
    start_time = match.get("start_time") or 0
    if start_time == 0:
        return 0
    day_diff = (start_time // 86400) - (int(TI_START_DATE.timestamp()) // 86400)
    return max(1, day_diff + 1)


def games_in_series(match: dict, matches: list[dict]) -> list[dict]:
    return [game for game in matches if series_key(game) == series_key(match)]


def score(match: dict, matches: list[dict]) -> tuple[int, int]:
    radiant, dire = teams(match)
    radiant_wins = dire_wins = 0
    for game in games_in_series(match, matches):
        outcome = game.get("radiant_win")
        if outcome is None:
            continue
        winner = teams(game)[0] if outcome else teams(game)[1]
        if winner == radiant:
            radiant_wins += 1
        elif winner == dire:
            dire_wins += 1
    return radiant_wins, dire_wins


def game_number(match: dict, matches: list[dict]) -> int:
    return games_in_series(match, matches).index(match) + 1


def format_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "—"


def format_start(unix_time: int | None) -> str:
    if not unix_time:
        return "час уточнюється"
    # Discord renders this timestamp in every channel member's local timezone.
    return f"<t:{unix_time}:t>"


def message(kind: str, league: dict, match: dict, matches: list[dict], liquipedia: dict[str, object], catalog: dict[str, dict[str, str]]) -> str:
    radiant, dire = teams(match)
    radiant_label, dire_label = team_label(radiant, catalog), team_label(dire, catalog)
    first, second = score(match, matches)
    if kind == "tournament_day":
        day = tournament_day(match)
        return f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\n{MAINCAST_DOTA2_URL}"
    if kind == "live":
        return f"🔴 **МАТЧ РОЗПОЧАВСЯ**\n{radiant_label} 🆚 {dire_label}\nГра {game_number(match, matches)}"
    if kind == "game_finished":
        return f"🎮 **ГРА {game_number(match, matches)} ЗАВЕРШИЛАСЯ**\n{radiant_label} {first} — {second} {dire_label}\n⏱ Тривалість: {format_duration(match.get('duration'))}"
    winner = radiant if first > second else dire
    return f"🏆 **МАТЧ ЗАВЕРШИВСЯ**\n{radiant_label} {first} — {second} {dire_label}\n🥇 Переможець: {team_label(winner, catalog)}"


def load_states() -> dict[str, object]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_states(states: dict[str, object]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(states, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(webhook_url: str, text: str) -> None:
    post_json(
        webhook_url,
        {
            "username": "The International 2026",
            "content": text,
        },
    )


def main() -> int:
    if SERIES_BEST_OF < 1:
        raise RuntimeError("SERIES_BEST_OF must be at least 1")
    
    webhook_url = require("DISCORD_WEBHOOK_URL")
    now = int(datetime.now(UTC).timestamp())
    states = load_states()
    liquipedia = liquipedia_context(states)
    catalog = load_team_catalog()
    
    league, matches = fetch_matches_cached(states, OPENDOTA_LEAGUE_ID)
    
    new_states = dict(states)
    published = 0
    wins_required = SERIES_BEST_OF // 2 + 1
    processed = 0
    
    print(f"Processing {len(matches)} matches with BO{SERIES_BEST_OF} format (need {wins_required} wins to clinch)...")

    # Track tournament days to announce each day once
    announced_days = set()
    for match in matches:
        day = tournament_day(match)
        if day > 0:
            announced_days.add(day)
    
    for match in matches:
        match_id = str(match["id"])
        current = match_state(match, now)
        previous = states.get(match_id)
        radiant, dire = teams(match)
        day = tournament_day(match)
        
        # Announce tournament day once per day
        # Only announce if the day has already started (match is live or finished)
        if day > 0 and current != "scheduled":
            day_state_key = f"day:{day}"
            if new_states.get(day_state_key) != "announced":
                # Only announce if we were already tracking this match (previous state exists)
                # This avoids historical spam on the first run.
                if previous is not None:
                    try:
                        publish(webhook_url, message("tournament_day", league, match, matches, liquipedia, catalog))
                        published += 1
                        print(f"  ✓ Day {day} announcement")
                    except RuntimeError as error:
                        print(f"  ✗ Error announcing day {day}: {error}", file=sys.stderr)
                new_states[day_state_key] = "announced"
        
        try:
            if current == "live" and previous is not None and previous != "live":
                publish(webhook_url, message("live", league, match, matches, liquipedia, catalog))
                new_states[match_id] = "live"
                published += 1
                print(f"  ✓ Match started: {radiant} vs {dire}")
            elif current == "finished":
                # On first run, remember history without spamming a new channel.
                if previous is not None and previous != "finished":
                    publish(webhook_url, message("game_finished", league, match, matches, liquipedia, catalog))
                    published += 1
                    print(f"  ✓ Game finished: {radiant} vs {dire} (Match ID: {match_id})")
                new_states[match_id] = "finished"

                first, second = score(match, matches)
                series_state_key = f"done:{series_key(match)}"
                if max(first, second) >= wins_required:
                    # Only announce series finish if we were tracking this specific game 
                    # AND the series wasn't already marked as finished in the OLD states.
                    if previous is not None and states.get(series_state_key) != "finished":
                        publish(webhook_url, message("series_finished", league, match, matches, liquipedia, catalog))
                        published += 1
                        winner = radiant if first > second else dire
                        print(f"  ✓ Series finished: {winner} wins {first} — {second} (Key: {series_state_key})")
                    new_states[series_state_key] = "finished"
        except RuntimeError as error:
            print(f"  ✗ Error processing {match_id} ({radiant} vs {dire}): {error}", file=sys.stderr)
        
        processed += 1

    new_states["liquipedia"] = liquipedia
    save_states(new_states)
    print(f"Processed {processed} match(es), published {published} Discord update(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
