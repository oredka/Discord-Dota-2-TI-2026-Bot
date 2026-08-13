"""Track The International 2026 through STRATZ and post match events to Discord."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
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
STATE_FILE = Path(os.getenv("STATE_FILE", "states/match_states.json"))
TEAM_CATALOG_FILE = Path(os.getenv("TEAM_CATALOG_FILE", "team_metadata.json"))
SERIES_BEST_OF = int(os.getenv("SERIES_BEST_OF", "3"))
LIQUIPEDIA_CACHE_SECONDS = 600
TI_START_DATE = datetime(2026, 8, 13, tzinfo=UTC)
# Set to 1 to record every current result in the state without posting anything to Discord.
SILENT_BOOTSTRAP = os.getenv("SILENT_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes")

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
    # Sort strictly: first by tournament day, then by start_time, then by series_id, then by match_id
    sorted_matches = sorted(
        matches, 
        key=lambda x: (tournament_day(x), x.get("start_time") or 0, x.get("series_id") or 0, x.get("match_id") or 0)
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
    return catalog.get(name.strip().casefold(), {})


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
    return "scheduled"


def match_end_time(match: dict) -> int:
    start = match.get("start_time") or 0
    return start + (match.get("duration") or 0) if start else 0


def series_key(match: dict) -> str:
    if match.get("series_id"):
        return f"series:{match['series_id']}"
    # Fallback to team IDs if series_id is missing
    r_id = match.get("radiant_team_id") or 0
    d_id = match.get("dire_team_id") or 0
    ids = sorted([r_id, d_id])
    return f"pair:{ids[0]}:{ids[1]}"


TI_START_TIMESTAMP = 1786633200  # Aug 13 15:00:00 UTC (Day 1 Start)


def tournament_day(match: dict) -> int:
    """Calculate which tournament day this match is on (1-indexed) based on UTC time.
    Each day starts at 00:00 UTC.
    """
    start_time = match.get("start_time")
    if not start_time:
        return 0
    
    dt_utc = datetime.fromtimestamp(start_time, UTC)
    
    # TI 2026 starts on Aug 13, 2026 UTC
    start_date_utc = datetime(2026, 8, 13, tzinfo=UTC)
    
    # Calculate days since start (00:00 UTC of each day)
    current_date_utc = datetime(dt_utc.year, dt_utc.month, dt_utc.day, tzinfo=UTC)
    delta = current_date_utc - start_date_utc
    
    day = delta.days + 1
    # If the match is before the official start day, it's day 1 (pre-tournament or early games)
    return max(1, day)


def games_in_series(match: dict, matches: list[dict]) -> list[dict]:
    s_key = series_key(match)
    series_games = [game for game in matches if series_key(game) == s_key]
    # Ensure they are sorted by match_id to maintain game order even if times are identical
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


def standings(matches: list[dict], day: int) -> list[tuple[int, str, int, int]]:
    """Games won/lost per team up to the end of `day`, ordered by current place in the tournament.

    Teams are ranked by wins, then by fewest losses; equal records share a place.
    """
    stats: dict[str, dict[str, int]] = {}
    for match in matches:
        if tournament_day(match) > day:
            continue
        radiant, dire = teams(match)
        # Ensure we use stripped names for matching with catalog and consistency
        radiant = radiant.strip()
        dire = dire.strip()
        
        known = [name for name in (radiant, dire) if name not in ("Radiant", "Dire")]
        for name in known:
            stats.setdefault(name, {"wins": 0, "losses": 0})
        outcome = match.get("radiant_win")
        if outcome is None or len(known) < 2:
            continue
        winner, loser = (radiant, dire) if outcome else (dire, radiant)
        stats[winner]["wins"] += 1
        stats[loser]["losses"] += 1

    ordered = sorted(stats, key=lambda name: (-stats[name]["wins"], stats[name]["losses"], name.casefold()))

    rows: list[tuple[int, str, int, int]] = []
    place = 0
    previous: tuple[int, int] | None = None
    for index, name in enumerate(ordered, start=1):
        record = (stats[name]["wins"], stats[name]["losses"])
        if record != previous:
            place, previous = index, record
        rows.append((place, name, *record))
    return rows


def format_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "—"


def format_start(unix_time: int | None) -> str:
    if not unix_time:
        return "час уточнюється"
    # Discord renders this timestamp in every channel member's local timezone.
    return f"<t:{unix_time}:t>"


def message(kind: str, league: dict, match: dict, matches: list[dict], catalog: dict[str, dict[str, str]], now: int) -> str:
    series_games = games_in_series(match, matches)
    first_game = series_games[0]
    left_name = (first_game.get("radiant_name") or "Radiant").strip()
    right_name = (first_game.get("dire_name") or "Dire").strip()
    left_label = team_label(left_name, catalog)
    right_label = team_label(right_name, catalog)

    if kind == "tournament_day":
        day = tournament_day(match)
        res = f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\n{MAINCAST_DOTA2_URL}"
        return res + "\n\u200b\n"
    if kind == "day_finished":
        day = tournament_day(match)
        # Simple text format: 1. 🇷🇺 Team (4-2), ordered by current place in the tournament
        table = "".join(
            f"{place}. {team_label(name, catalog)} ({wins}-{losses})\n"
            for place, name, wins, losses in standings(matches, day)
        )
        res = f"🏆 **ДЕНЬ {day} THE INTERNATIONAL 2026 ЗАВЕРШИВСЯ**\n\n{table}"
        return res + "\n\u200b\n"
    if kind == "game_finished":
        left_score, right_score = score_up_to(match, matches)
        res = f"🎮 **ГРА {game_number(match, matches)} ЗАВЕРШИЛАСЯ**\n{left_label} {left_score} — {right_score} {right_label}\n⏱ Тривалість: {format_duration(match.get('duration'))}"
        return res + "\n\u200b\n"
    
    left_total, right_total = score(match, matches)
    winner_name = left_name if left_total > right_total else right_name
    res = f"🏆 **МАТЧ ЗАВЕРШИВСЯ**\n{left_label} {left_total} — {right_total} {right_label}\n🥇 Переможець: {team_label(winner_name, catalog)}"
    return res + "\n\u200b\n"


def load_states(day: int | None = None) -> dict[str, object]:
    file = STATE_FILE
    if day is not None:
        file = STATE_FILE.with_name(f"match_states_day{day}.json")
    
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_states(states: dict[str, object], day: int | None = None) -> None:
    file = STATE_FILE
    if day is not None:
        file = STATE_FILE.with_name(f"match_states_day{day}.json")
        
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(states, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(webhook_url: str, text: str) -> None:
    post_json(
        webhook_url,
        {
            "username": "The International 2026",
            "content": text,
        },
    )


def announce(ctx: dict, day_states: dict[str, object], key: str, kind: str, match: dict, label: str) -> bool:
    """Publish one event at most once, ever. Returns True when a Discord message was sent.

    A key already present in the day state was handled by an earlier run, so a repeated Action run
    stays silent. If the key is missing, it is published to Discord.
    """
    if key in day_states:
        return False
    
    if SILENT_BOOTSTRAP:
        day_states[key] = "announced"
        return False
    try:
        publish(ctx["webhook_url"], message(kind, ctx["league"], match, ctx["matches"], ctx["catalog"], ctx["now"]))
        # Small delay to avoid Discord rate limits when re-sending many messages
        time.sleep(0.5)
        day_states[key] = "announced"
        print(f"  ✓ {label}")
        return True
    except RuntimeError as error:
        print(f"  ✗ Error publishing {label}: {error}", file=sys.stderr)
        return False


def main() -> int:
    if SERIES_BEST_OF < 1:
        raise RuntimeError("SERIES_BEST_OF must be at least 1")
    
    webhook_url = require("DISCORD_WEBHOOK_URL")
    now = int(datetime.now(UTC).timestamp())
    states = load_states()
    liquipedia = liquipedia_context(states)
    catalog = load_team_catalog()
    
    league, matches = fetch_matches_cached(states, OPENDOTA_LEAGUE_ID)
    
    # Sort matches by start time to process them chronologically
    sorted_matches = sorted(matches, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))
    
    # Group matches by tournament day
    day_to_matches = {}
    for match in sorted_matches:
        d = tournament_day(match)
        if d > 0:
            if d not in day_to_matches:
                day_to_matches[d] = []
            day_to_matches[d].append(match)
    
    ctx = {
        "webhook_url": webhook_url,
        "league": league,
        "matches": matches,
        "liquipedia": liquipedia,
        "catalog": catalog,
        "now": now,
    }

    published = 0
    wins_required = SERIES_BEST_OF // 2 + 1
    processed = 0

    for day in sorted(day_to_matches.keys()):
        day_matches = day_to_matches[day]
        # Sort matches within the day strictly chronologically
        day_matches = sorted(day_matches, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))
        
        # Every announcement is keyed in this dict, so a repeated run publishes nothing new.
        day_states = load_states(day)

        # Ensure Day X announcement is sent first if we are announcing anything from this day
        # and it hasn't been announced yet.
        has_new_finished_matches = any(
            str(m["match_id"]) not in day_states and match_state(m, now) == "finished" 
            for m in day_matches
        )
        
        if has_new_finished_matches and f"day:{day}" not in day_states:
            # Use the first match of the day to trigger the "Day started" message
            first_match = day_matches[0]
            if announce(ctx, day_states, f"day:{day}", "tournament_day", first_match, f"Day {day} announcement"):
                published += 1

        for match in day_matches:
            match_id = str(match["match_id"])
            radiant, dire = teams(match)
            radiant, dire = radiant.strip(), dire.strip()
            if match_state(match, now) != "finished":
                processed += 1
                continue

            # Check if this match was already announced in this day's state
            if match_id not in day_states:
                left_wins, right_wins = score_up_to(match, matches)
                wins_required = SERIES_BEST_OF // 2 + 1
                is_series_end = left_wins >= wins_required or right_wins >= wins_required

                if not is_series_end:
                    if announce(ctx, day_states, match_id, "game_finished", match, f"Game finished: {radiant} vs {dire} (Match ID: {match_id})"):
                        published += 1
                else:
                    # Mark the game as announced even if we don't send a separate message for it,
                    # to avoid it being picked up as "new" in future checks.
                    day_states[match_id] = "announced"

                if is_series_end:
                    series_state_key = f"done:{series_key(match)}"
                    label = f"Series finished: {radiant} vs {dire} ({left_wins} — {right_wins})"
                    if announce(ctx, day_states, series_state_key, "series_finished", match, label):
                        published += 1
            else:
                # IMPORTANT: If the game was already announced, we MUST still check if the series 
                # completion needs to be announced. This handles cases where a game was posted 
                # but the subsequent series message was missed in a previous run.
                left_wins, right_wins = score_up_to(match, matches)
                wins_required = SERIES_BEST_OF // 2 + 1
                if left_wins >= wins_required or right_wins >= wins_required:
                    series_state_key = f"done:{series_key(match)}"
                    label = f"Series finished (delayed): {radiant} vs {dire} ({left_wins} — {right_wins})"
                    if announce(ctx, day_states, series_state_key, "series_finished", match, label):
                        published += 1

            processed += 1

        # The results table closes the day once every game of that day has a result.
        if all(match_state(m, now) == "finished" for m in day_matches):
            last_match = max(day_matches, key=match_end_time)
            if announce(ctx, day_states, f"day:{day}:finished", "day_finished", last_match, f"Day {day} finished announcement"):
                published += 1

        save_states(day_states, day)

    states["liquipedia"] = liquipedia
    save_states(states)
    
    # Ensure a placeholder file exists in states/ so git tracks the directory if it's empty
    # though save_states already creates files.
    
    print(f"Processed {processed} match(es), published {published} Discord update(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
