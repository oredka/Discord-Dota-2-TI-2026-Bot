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
MAINCAST_DOTA2_URL = "https://www.youtube.com/@Dota2_maincast/streams"
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
    
    # 4. Filter matches that actually belong to the league (sometimes API returns more)
    matches = [m for m in matches if m.get("leagueid") == league_id]
    
    league = {"displayName": "The International 2026", "id": league_id}
    # Sort strictly: first by start_time, then by series_id, then by match_id
    sorted_matches = sorted(
        matches, 
        key=lambda x: (x.get("start_time") or 0, x.get("series_id") or 0, x.get("match_id") or 0)
    )
    return league, sorted_matches


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
    # Fallback to team IDs if series_id is missing
    r_id = match.get("radiant_team_id") or 0
    d_id = match.get("dire_team_id") or 0
    ids = sorted([r_id, d_id])
    return f"pair:{ids[0]}:{ids[1]}"


def tournament_day(match: dict) -> int:
    """Calculate which tournament day this match is on (1-indexed)."""
    start_time = match.get("start_time") or 0
    if start_time == 0:
        return 0
    # Use EEST (UTC+3) or similar for TI to avoid day shift in middle of games
    # But for simplicity, let's just shift UTC by -5 hours (Seattle time)
    seattle_time = start_time - (5 * 3600)
    day_diff = (seattle_time // 86400) - (int(TI_START_DATE.timestamp() - (5 * 3600)) // 86400)
    return max(1, day_diff + 1)


def games_in_series(match: dict, matches: list[dict]) -> list[dict]:
    series_games = [game for game in matches if series_key(game) == series_key(match)]
    return sorted(series_games, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))


def score(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    first_game = series_games[0]
    left_team_id = first_game.get("radiant_team_id")
    right_team_id = first_game.get("dire_team_id")
    
    left_wins = right_wins = 0
    for game in series_games:
        outcome = game.get("radiant_win")
        if outcome is None:
            continue
        g_radiant_id = game.get("radiant_team_id")
        if g_radiant_id == left_team_id:
            if outcome: left_wins += 1
            else: right_wins += 1
        elif g_radiant_id == right_team_id:
            if outcome: right_wins += 1
            else: left_wins += 1
    return left_wins, right_wins


def score_up_to(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    
    first_game = series_games[0]
    left_team_id = first_game.get("radiant_team_id")
    right_team_id = first_game.get("dire_team_id")

    try:
        current_game_index = -1
        # Use match_id for exact identification in the list
        for i, g in enumerate(series_games):
            if str(g.get("match_id")) == str(match.get("match_id")):
                current_game_index = i
                break
        
        if current_game_index == -1:
            return 0, 0
    except Exception:
        return 0, 0
        
    games_to_count = series_games[:current_game_index + 1]
    
    left_wins = right_wins = 0
    for game in games_to_count:
        outcome = game.get("radiant_win")
        if outcome is None:
            continue
        g_radiant_id = game.get("radiant_team_id")
        if g_radiant_id == left_team_id:
            if outcome: left_wins += 1
            else: right_wins += 1
        elif g_radiant_id == right_team_id:
            if outcome: right_wins += 1
            else: left_wins += 1
    return left_wins, right_wins


def game_number(match: dict, matches: list[dict]) -> int:
    series_games = games_in_series(match, matches)
    for i, g in enumerate(series_games):
        if str(g.get("match_id")) == str(match.get("match_id")):
            return i + 1
    return 1


def format_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "—"


def format_start(unix_time: int | None) -> str:
    if not unix_time:
        return "час уточнюється"
    # Discord renders this timestamp in every channel member's local timezone.
    return f"<t:{unix_time}:t>"


def message(kind: str, league: dict, match: dict, matches: list[dict], liquipedia: dict[str, object], catalog: dict[str, dict[str, str]]) -> str:
    series_games = games_in_series(match, matches)
    first_game = series_games[0]
    left_name = first_game.get("radiant_name") or "Radiant"
    right_name = first_game.get("dire_name") or "Dire"
    left_label = team_label(left_name, catalog)
    right_label = team_label(right_name, catalog)

    if kind == "tournament_day":
        day = tournament_day(match)
        res = f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\n{MAINCAST_DOTA2_URL}"
        return res + "\n\u200b\n"
    if kind == "live":
        res = f"🔴 **МАТЧ РОЗПОЧАВСЯ**\n{left_label} 🆚 {right_label}\nГра {game_number(match, matches)}"
        return res + "\n\u200b\n"
    if kind == "game_finished":
        left_score, right_score = score_up_to(match, matches)
        res = f"🎮 **ГРА {game_number(match, matches)} ЗАВЕРШИЛАСЯ**\n{left_label} {left_score} — {right_score} {right_label}\n⏱ Тривалість: {format_duration(match.get('duration'))}"
        return res + "\n\u200b\n"
    
    left_total, right_total = score(match, matches)
    winner_name = left_name if left_total > right_total else right_name
    res = f"🏆 **МАТЧ ЗАВЕРШИВСЯ**\n{left_label} {left_total} — {right_total} {right_label}\n🥇 Переможець: {team_label(winner_name, catalog)}"
    return res + "\n\u200b\n"


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

    # Track tournament days and series to announce each once per run
    announced_days_in_run = set()
    announced_series_in_run = set()
    
    for match in matches:
        match_id = str(match["match_id"])
        current = match_state(match, now)
        previous = states.get(match_id)
        radiant, dire = teams(match)
        day = tournament_day(match)
        
        # Announce tournament day once per day
        # Only announce if the day has already started (match is live or finished)
        if day > 0 and current != "scheduled":
            day_state_key = f"day:{day}"
            if new_states.get(day_state_key) != "announced" and day not in announced_days_in_run:
                # Only announce if we are not in historical spam mode
                is_recent = (now - (match.get("start_time") or 0)) < 3600 * 48 
                if previous is not None or is_recent:
                    try:
                        publish(webhook_url, message("tournament_day", league, match, matches, liquipedia, catalog))
                        published += 1
                        print(f"  ✓ Day {day} announcement")
                        announced_days_in_run.add(day)
                    except RuntimeError as error:
                        print(f"  ✗ Error announcing day {day}: {error}", file=sys.stderr)
                new_states[day_state_key] = "announced"
        
        try:
            if current == "live" and previous != "live":
                # Announce if it just became live, or if it's the first time we see it and it's recent
                is_recent = (now - (match.get("start_time") or 0)) < 3600 * 48 
                if previous is not None or is_recent:
                    publish(webhook_url, message("live", league, match, matches, liquipedia, catalog))
                    published += 1
                    print(f"  ✓ Match started: {radiant} vs {dire}")
                new_states[match_id] = "live"
            elif current == "finished":
                # Check if this specific match completed the series
                first_so_far, second_up_to = score_up_to(match, matches)
                series_state_key = f"done:{series_key(match)}"
                is_series_clinching = max(first_so_far, second_up_to) >= wins_required

                if previous != "finished":
                    # Announce if it just finished, or if it's the first time we see it and it's recent
                    is_recent = (now - (match.get("start_time") or 0)) < 3600 * 48 
                    if previous is not None or is_recent:
                        # Skip "game finished" message if this game clinches the entire series
                        if not is_series_clinching:
                            publish(webhook_url, message("game_finished", league, match, matches, liquipedia, catalog))
                            published += 1
                            print(f"  ✓ Game finished: {radiant} vs {dire} (Match ID: {match_id})")
                        else:
                            print(f"  - Skipping game message for {match_id} (series clincher)")
                    new_states[match_id] = "finished"

                # Check if this series was already announced as finished in state or this run
                if new_states.get(series_state_key) != "finished" and series_state_key not in announced_series_in_run:
                    # A series is finished if one team reaches enough wins
                    if is_series_clinching:
                        is_recent = (now - (match.get("start_time") or 0)) < 3600 * 48 
                        if previous is not None or is_recent:
                            publish(webhook_url, message("series_finished", league, match, matches, liquipedia, catalog))
                            published += 1
                            # Consistently identify winner based on the same Left/Right logic used in message()
                            series_games = games_in_series(match, matches)
                            first_game = series_games[0]
                            left_name = first_game.get("radiant_name") or "Radiant"
                            right_name = first_game.get("dire_name") or "Dire"
                            winner = left_name if first_so_far > second_up_to else right_name
                            
                            print(f"  ✓ Series finished: {winner} wins {first_so_far} — {second_up_to} (Key: {series_state_key})")
                            announced_series_in_run.add(series_state_key)
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
