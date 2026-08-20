"""Track The International 2026 through STRATZ and post match events to Discord."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
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
# SERIES_BEST_OF = int(os.getenv("SERIES_BEST_OF", "3"))  # Removed in favor of dynamic detection
LIQUIPEDIA_CACHE_SECONDS = 600
TI_START_DATE = datetime(2026, 8, 13, tzinfo=UTC)
REST_DAYS = {5}
MIN_SERIES_PER_DAY = {
    1: 12,
    2: 12,
    3: 12,
    4: 5,
    5: 0,
    6: 2,
    7: 2,
    8: 2,
    9: 2,
    10: 2,
    11: 2,
}
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


def get_json(url: str, headers: dict[str, str], max_retries: int = 3, retry_delay: float = 2.0) -> dict:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code in (429, 500, 502, 503, 504, 520, 521, 522, 524):
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, json.JSONDecodeError) as error:
            last_error = error
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (attempt + 1))
            else:
                raise RuntimeError(f"Request to {url} failed: {error}") from error
    if last_error:
        raise RuntimeError(f"Request to {url} failed: {last_error}") from last_error
    return {}


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
        if isinstance(cached, dict) and cached.get("matches"):
            print(f"Warning: {error}; using stale OpenDota cache.", file=sys.stderr)
            return cached.get("league", {}), cached.get("matches", [])
        print(f"Warning: could not fetch OpenDota matches: {error}", file=sys.stderr)
        return {"displayName": "The International 2026", "id": league_id}, []


def fetch_matches(league_id: int) -> tuple[dict, list[dict]]:
    # 1. Fetch matches
    matches = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/matches", {"User-Agent": "ti2026-discord-webhook/1.0"})
    
    # 2. Fetch teams to map names
    teams_data = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/teams", {"User-Agent": "ti2026-discord-webhook/1.0"})
    team_names = {t["team_id"]: t["name"].strip() for t in teams_data if t.get("name")}
    
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
        data = json.loads(TEAM_CATALOG_FILE.read_text(encoding="utf-8-sig"))
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


def team_pair(match: dict) -> tuple:
    r_id = match.get("radiant_team_id") or 0
    d_id = match.get("dire_team_id") or 0
    if r_id and d_id and r_id != d_id:
        return tuple(sorted([r_id, d_id]))
    r_name = (match.get("radiant_name") or "Radiant").strip().casefold()
    d_name = (match.get("dire_name") or "Dire").strip().casefold()
    return tuple(sorted([r_name, d_name]))


def series_key(match: dict) -> str:
    day = tournament_day(match)
    pair = team_pair(match)
    return f"day:{day}:pair:{pair[0]}:{pair[1]}"


def is_series_announced(day_states: dict[str, object], match: dict) -> bool:
    s_key = series_key(match)
    if f"done:{s_key}" in day_states:
        return True
    if match.get("series_id") and f"done:series:{match['series_id']}" in day_states:
        return True
    pair = team_pair(match)
    if f"done:pair:{pair[0]}:{pair[1]}" in day_states:
        return True
    return False


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


def current_tournament_day(now: int) -> int:
    """Calculate the current tournament day (1-indexed) based on UTC time."""
    now_dt = datetime.fromtimestamp(now, UTC)
    current_date_utc = datetime(now_dt.year, now_dt.month, now_dt.day, tzinfo=UTC)
    delta = (current_date_utc - TI_START_DATE).days
    if delta < 0:
        return 0
    return delta + 1


def games_in_series(match: dict, matches: list[dict]) -> list[dict]:
    s_key = series_key(match)
    series_games = [game for game in matches if series_key(game) == s_key]
    # Ensure they are sorted by match_id to maintain game order even if times are identical
    return sorted(series_games, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))


def count_series_wins(sm: list[dict], left_team_id: int | None, left_name: str, right_team_id: int | None, right_name: str) -> tuple[int, int]:
    left_wins = right_wins = 0
    for gm in sm:
        outcome = gm.get("radiant_win")
        if outcome is None:
            continue
        g_rad_id = gm.get("radiant_team_id")
        g_rad_name = (gm.get("radiant_name") or "").strip().casefold()

        is_left_radiant = (g_rad_id == left_team_id if left_team_id else g_rad_name == left_name.casefold())
        is_left_dire = (gm.get("dire_team_id") == left_team_id if left_team_id else (gm.get("dire_name") or "").strip().casefold() == left_name.casefold())

        if is_left_radiant:
            if outcome: left_wins += 1
            else: right_wins += 1
        elif is_left_dire:
            if outcome: right_wins += 1
            else: left_wins += 1
        else:
            is_right_radiant = (g_rad_id == right_team_id if right_team_id else g_rad_name == right_name.casefold())
            if is_right_radiant:
                if outcome: right_wins += 1
                else: left_wins += 1
            else:
                if outcome: left_wins += 1
                else: right_wins += 1
    return left_wins, right_wins


def score(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    first_game = series_games[0]
    left_team_id = first_game.get("radiant_team_id")
    right_team_id = first_game.get("dire_team_id")
    left_name = (first_game.get("radiant_name") or "Radiant").strip()
    right_name = (first_game.get("dire_name") or "Dire").strip()
    return count_series_wins(series_games, left_team_id, left_name, right_team_id, right_name)


def score_up_to(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    
    first_game = series_games[0]
    left_team_id = first_game.get("radiant_team_id")
    right_team_id = first_game.get("dire_team_id")
    left_name = (first_game.get("radiant_name") or "Radiant").strip()
    right_name = (first_game.get("dire_name") or "Dire").strip()

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
    return count_series_wins(games_to_count, left_team_id, left_name, right_team_id, right_name)


def game_number(match: dict, matches: list[dict]) -> int:
    series_games = games_in_series(match, matches)
    for i, g in enumerate(series_games):
        if str(g.get("match_id")) == str(match.get("match_id")):
            return i + 1
    return 1


def eliminated_teams(matches: list[dict], up_to_day: int) -> dict[str, dict[str, object]]:
    """Map of team names to elimination info: {'day': int, 'place': str}."""
    series_map: dict[str, list[dict]] = {}
    for m in matches:
        if tournament_day(m) > up_to_day:
            continue
        sk = series_key(m)
        series_map.setdefault(sk, []).append(m)

    sorted_series = sorted(series_map.values(), key=lambda sm: min(g.get("start_time", 0) for g in sm))

    swiss_losses: dict[str, int] = {}
    swiss_wins: dict[str, int] = {}
    playoff_losses: dict[str, int] = {}
    elim_info: dict[str, dict[str, object]] = {}

    for sm in sorted_series:
        first = sm[0]
        s_day = tournament_day(first)
        r_name, d_name = teams(first)
        r_name, d_name = r_name.strip(), d_name.strip()
        r_id = first.get("radiant_team_id")
        d_id = first.get("dire_team_id")
        r_wins, d_wins = count_series_wins(sm, r_id, r_name, d_id, d_name)

        swiss_losses.setdefault(r_name, 0)
        swiss_losses.setdefault(d_name, 0)
        swiss_wins.setdefault(r_name, 0)
        swiss_wins.setdefault(d_name, 0)
        playoff_losses.setdefault(r_name, 0)
        playoff_losses.setdefault(d_name, 0)

        if r_wins > d_wins:
            winner, loser = r_name, d_name
        elif d_wins > r_wins:
            winner, loser = d_name, r_name
        else:
            continue

        if swiss_wins[loser] < 4:
            swiss_losses[loser] += 1
            if swiss_losses[loser] >= 4 and loser not in elim_info:
                # In Swiss: Day 3 elimination -> 14-16 місце; Day 4 elimination -> 11-13 місце
                place_str = "14-16 місце" if s_day <= 3 else "11-13 місце"
                elim_info[loser] = {"day": s_day, "place": place_str, "stage": "swiss"}
        else:
            playoff_losses[loser] += 1
            if playoff_losses[loser] >= 2 and loser not in elim_info:
                elim_count = len([t for t, info in elim_info.items() if info["stage"] == "playoffs"])
                # Playoff eliminations order: 2 teams -> 9-10th, 2 teams -> 7-8th, 2 teams -> 5-6th, 1 -> 4th, 1 -> 3rd, 1 -> 2nd
                if elim_count < 2:
                    place_str = "9-10 місце"
                elif elim_count < 4:
                    place_str = "7-8 місце"
                elif elim_count < 6:
                    place_str = "5-6 місце"
                elif elim_count == 6:
                    place_str = "4 місце"
                elif elim_count == 7:
                    place_str = "3 місце"
                else:
                    place_str = "2 місце"
                elim_info[loser] = {"day": s_day, "place": place_str, "stage": "playoffs"}

        if swiss_wins[winner] < 4:
            swiss_wins[winner] += 1

    return elim_info


def standings(matches: list[dict], day: int) -> list[tuple[int, str, int, int, int, int, str | None]]:
    """Series & games won/lost per team up to the end of `day`, ordered by current place in the tournament.

    Active teams are ranked first by series wins, then by game wins, fewest losses; equal records share a place.
    Eliminated teams are placed below active teams, ranked by stage/day eliminated and game record.
    Returns: list of (display_place, team_name, series_wins, series_losses, game_wins, game_losses, elim_place_str_or_None)
    """
    game_stats: dict[str, dict[str, int]] = {}
    series_stats: dict[str, dict[str, int]] = {}

    series_map: dict[str, list[dict]] = {}
    for match in matches:
        if tournament_day(match) > day:
            continue
        radiant, dire = teams(match)
        radiant = radiant.strip()
        dire = dire.strip()
        
        known = [name for name in (radiant, dire) if name not in ("Radiant", "Dire")]
        for name in known:
            game_stats.setdefault(name, {"wins": 0, "losses": 0})
            series_stats.setdefault(name, {"wins": 0, "losses": 0})

        outcome = match.get("radiant_win")
        if outcome is not None and len(known) >= 2:
            winner, loser = (radiant, dire) if outcome else (dire, radiant)
            game_stats[winner]["wins"] += 1
            game_stats[loser]["losses"] += 1

        sk = series_key(match)
        series_map.setdefault(sk, []).append(match)

    # Compute series wins/losses
    for sm in series_map.values():
        first = sm[0]
        r_name, d_name = teams(first)
        r_name, d_name = r_name.strip(), d_name.strip()
        r_id = first.get("radiant_team_id")
        d_id = first.get("dire_team_id")
        r_wins, d_wins = count_series_wins(sm, r_id, r_name, d_id, d_name)

        if r_wins > d_wins and (r_wins >= 2 or d_wins >= 2 or len(sm) == 1):
            if r_name in series_stats and d_name in series_stats:
                series_stats[r_name]["wins"] += 1
                series_stats[d_name]["losses"] += 1
        elif d_wins > r_wins and (r_wins >= 2 or d_wins >= 2 or len(sm) == 1):
            if r_name in series_stats and d_name in series_stats:
                series_stats[d_name]["wins"] += 1
                series_stats[r_name]["losses"] += 1

    elim_map = eliminated_teams(matches, day)

    active_ordered = sorted(
        [name for name in game_stats if name not in elim_map],
        key=lambda name: (
            -series_stats[name]["wins"],
            series_stats[name]["losses"],
            -game_stats[name]["wins"],
            game_stats[name]["losses"],
            name.casefold()
        )
    )

    elim_ordered = sorted(
        [name for name in game_stats if name in elim_map],
        key=lambda name: (
            -int(elim_map[name]["day"]),
            -series_stats[name]["wins"],
            series_stats[name]["losses"],
            -game_stats[name]["wins"],
            game_stats[name]["losses"],
            name.casefold()
        )
    )

    rows: list[tuple[int, str, int, int, int, int, str | None]] = []
    place = 0
    previous: tuple[int, int, int, int] | None = None
    for index, name in enumerate(active_ordered, start=1):
        record = (series_stats[name]["wins"], series_stats[name]["losses"], game_stats[name]["wins"], game_stats[name]["losses"])
        if record != previous:
            place, previous = index, record
        rows.append((place, name, *record, None))

    start_elim_idx = len(active_ordered) + 1
    prev_elim_key: tuple[object, int, int, int, int] | None = None
    elim_place = start_elim_idx
    for offset, name in enumerate(elim_ordered):
        index = start_elim_idx + offset
        elim_key = (elim_map[name]["day"], series_stats[name]["wins"], series_stats[name]["losses"], game_stats[name]["wins"], game_stats[name]["losses"])
        if elim_key != prev_elim_key:
            elim_place, prev_elim_key = index, elim_key
        rows.append((
            elim_place,
            name,
            series_stats[name]["wins"],
            series_stats[name]["losses"],
            game_stats[name]["wins"],
            game_stats[name]["losses"],
            str(elim_map[name]["place"])
        ))

    return rows


class LiquipediaTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table_stack: list[list[list[str]]] = []
        self.curr_cell: list[str] = []
        self.in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag == "table":
            self.table_stack.append([])
        elif tag == "tr":
            if self.table_stack:
                self.table_stack[-1].append([])
        elif tag in ("td", "th"):
            self.curr_cell = []
            self.in_cell = True

    def handle_endtag(self, tag: str):
        if tag in ("td", "th"):
            if self.table_stack and self.table_stack[-1] and self.table_stack[-1][-1] is not None:
                self.table_stack[-1][-1].append("".join(self.curr_cell).strip())
            self.in_cell = False
        elif tag == "table":
            if self.table_stack:
                finished_table = self.table_stack.pop()
                if finished_table:
                    self.tables.append(finished_table)

    def handle_data(self, data: str):
        if self.in_cell:
            self.curr_cell.append(data)


def fetch_liquipedia_hero_stats(states: dict[str, object]) -> dict[str, list[str]]:
    """Fetch Top-10 hero statistics directly from Liquipedia Statistics page."""
    cached = states.get("liquipedia_hero_stats")
    if isinstance(cached, dict) and time.time() - cached.get("fetched_at", 0) < LIQUIPEDIA_CACHE_SECONDS:
        return cached.get("data", {})

    query = urlencode({"action": "parse", "page": "The_International/2026/Statistics", "format": "json"})
    user_agent = os.getenv("LIQUIPEDIA_USER_AGENT", "TI2026DiscordBot/1.0 (GitHub Actions)")
    try:
        data = get_json(f"{LIQUIPEDIA_API}?{query}", {"User-Agent": user_agent, "Accept": "application/json"})
        html = data.get("parse", {}).get("text", {}).get("*", "")
        if not html:
            return {}

        parser = LiquipediaTableParser()
        parser.feed(html)

        heroes_data = []
        for table in parser.tables:
            for r in table:
                if len(r) >= 16 and r[2].isdigit() and r[3].isdigit() and r[4].isdigit() and "%" in r[5] and r[15].isdigit():
                    hero = r[1]
                    picks = int(r[2])
                    wins = int(r[3])
                    losses = int(r[4])
                    try:
                        wr = float(r[5].replace("%", "").strip())
                    except ValueError:
                        wr = (wins / picks * 100) if picks else 0.0
                    bans = int(r[15])
                    heroes_data.append({
                        "hero": hero,
                        "picks": picks,
                        "wins": wins,
                        "losses": losses,
                        "wr": wr,
                        "bans": bans,
                    })

        if not heroes_data:
            return {}

        top_picks = sorted(heroes_data, key=lambda x: (-x["picks"], x["hero"]))[:10]
        top_bans = sorted(heroes_data, key=lambda x: (-x["bans"], x["hero"]))[:10]

        res = {
            "picks": [f"{i+1}. {h['hero']} — {h['picks']} ігор ({round(h['wr'])}% WR, {h['wins']}-{h['losses']})" for i, h in enumerate(top_picks)],
            "bans": [f"{i+1}. {h['hero']} — {h['bans']} банів ({round(h['wr'])}% WR, {h['wins']}-{h['losses']})" for i, h in enumerate(top_bans)],
        }
        states["liquipedia_hero_stats"] = {"fetched_at": time.time(), "data": res}
        return res
    except Exception as error:
        print(f"Warning: could not fetch Liquipedia hero stats: {error}", file=sys.stderr)
        return cached.get("data", {}) if isinstance(cached, dict) else {}


def hero_stats(
    matches: list[dict],
    up_to_day: int,
    heroes_catalog: dict[int, str],
    liquipedia_hero_stats: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Calculate or provide Top-10 picked, banned, and highest winrate heroes."""
    if liquipedia_hero_stats and (liquipedia_hero_stats.get("picks") or liquipedia_hero_stats.get("bans")):
        return liquipedia_hero_stats

    played = [m for m in matches if tournament_day(m) <= up_to_day and m.get("radiant_win") is not None]
    
    picks: dict[int, int] = {}
    bans: dict[int, int] = {}
    wins: dict[int, int] = {}

    for m in played:
        r_win = m.get("radiant_win")
        pb_list = m.get("picks_bans") or []
        for pb in pb_list:
            hid = pb.get("hero_id")
            if not hid:
                continue
            is_pick = pb.get("is_pick")
            team_slot = pb.get("team")  # 0 for Radiant, 1 for Dire
            if is_pick:
                picks[hid] = picks.get(hid, 0) + 1
                if (team_slot == 0 and r_win) or (team_slot == 1 and not r_win):
                    wins[hid] = wins.get(hid, 0) + 1
            else:
                bans[hid] = bans.get(hid, 0) + 1

    if not picks and not bans:
        return {}

    # Top 10 picks
    top_picks = sorted(picks.items(), key=lambda x: (-x[1], heroes_catalog.get(x[0], str(x[0]))))[:10]
    # Top 10 bans
    top_bans = sorted(bans.items(), key=lambda x: (-x[1], heroes_catalog.get(x[0], str(x[0]))))[:10]

    def pick_label(hid: int, cnt: int) -> str:
        w = wins.get(hid, 0)
        l = cnt - w
        wr = round((w / cnt) * 100) if cnt else 0
        name = heroes_catalog.get(hid, f"Hero {hid}")
        return f"{name} — {cnt} ігор ({wr}% WR, {w}-{l})"

    def ban_label(hid: int, cnt: int) -> str:
        p_cnt = picks.get(hid, 0)
        w = wins.get(hid, 0)
        l = p_cnt - w
        wr = round((w / p_cnt) * 100) if p_cnt else 0
        name = heroes_catalog.get(hid, f"Hero {hid}")
        if p_cnt > 0:
            return f"{name} — {cnt} банів ({wr}% WR, {w}-{l})"
        return f"{name} — {cnt} банів (0 ігор)"

    return {
        "picks": [f"{i+1}. {pick_label(hid, cnt)}" for i, (hid, cnt) in enumerate(top_picks)],
        "bans": [f"{i+1}. {ban_label(hid, cnt)}" for i, (hid, cnt) in enumerate(top_bans)],
    }


def fetch_heroes_catalog_cached(states: dict[str, object]) -> dict[int, str]:
    """Fetch OpenDota heroes list and return map of hero_id -> localized_name."""
    cached = states.get("heroes_catalog")
    if isinstance(cached, dict) and cached:
        return {int(k): v for k, v in cached.items()}
    try:
        data = get_json(f"{OPENDOTA_API_ENDPOINT}/constants/heroes", {"User-Agent": "ti2026-discord-webhook/1.0"})
        heroes = {int(h["id"]): h.get("localized_name", f"Hero {h['id']}") for h in data.values() if isinstance(h, dict) and h.get("id")}
        states["heroes_catalog"] = heroes
        return heroes
    except Exception as error:
        print(f"Warning: could not fetch heroes catalog: {error}", file=sys.stderr)
        return {}


def format_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "—"


def format_start(unix_time: int | None) -> str:
    if not unix_time:
        return "час уточнюється"
    # Discord renders this timestamp in every channel member's local timezone.
    return f"<t:{unix_time}:t>"


def get_series_best_of(match: dict, day_matches: list[dict], day: int) -> int:
    """TI 2026: Grand Final (the last match of Day 11) is Bo5, others are Bo3."""
    if day == 11 and match == day_matches[-1]:
        return 5
    return 3


def message(
    kind: str,
    league: dict,
    match: dict | None,
    matches: list[dict],
    catalog: dict[str, dict[str, str]],
    now: int,
    is_grand_final: bool = False,
    day: int | None = None,
    heroes_catalog: dict[int, str] | None = None,
    liquipedia_hero_stats: dict[str, list[str]] | None = None,
) -> str | list[str]:
    if kind == "tournament_day":
        if day is None and match:
            day = tournament_day(match)
        res = f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\n{MAINCAST_DOTA2_URL}"
        return res + "\n\u200b\n"
    if kind == "tournament_day_no_matches":
        if day is None and match:
            day = tournament_day(match)
        res = f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\nСьогодні ігор не заплановано"
        return res + "\n\u200b\n"
    if kind == "day_finished":
        if day is None and match:
            day = tournament_day(match)
        # Format: 1. 🇷🇺 Team 4-1 (9-4) or 16. 🇪🇺 OG (13-16 місце)
        table = "".join(
            f"{place}. {team_label(name, catalog)} ({elim_place})\n"
            if elim_place
            else f"{place}. {team_label(name, catalog)} {s_wins}-{s_losses} ({g_wins}-{g_losses})\n"
            for place, name, s_wins, s_losses, g_wins, g_losses, elim_place in standings(matches, day or 0)
        )
        
        sections = [f"🏆 **ДЕНЬ {day} THE INTERNATIONAL 2026 ЗАВЕРШИВСЯ**\n\n{table.strip()}"]
        
        # Add hero stats if available
        h_stats = hero_stats(matches, day or 0, heroes_catalog or {}, liquipedia_hero_stats=liquipedia_hero_stats)
        if h_stats:
            h_sections = []
            if h_stats.get("picks"):
                h_sections.append("🗡 **Топ-10 піків:**\n" + "\n".join(h_stats["picks"]))
            if h_stats.get("bans"):
                h_sections.append("🚫 **Топ-10 банів:**\n" + "\n".join(h_stats["bans"]))
            if h_sections:
                sections.append("🧙‍♂️ **ТОП ГЕРОЇВ ТУРНІРУ**\n\n" + "\n\n".join(h_sections))

        full_text = "\n\n".join(sections) + "\n\u200b\n"
        if len(full_text) <= 2000:
            return full_text
        return [sec + "\n\u200b\n" for sec in sections]

    series_games = games_in_series(match, matches)
    first_game = series_games[0]
    left_name = (first_game.get("radiant_name") or "Radiant").strip()
    right_name = (first_game.get("dire_name") or "Dire").strip()
    left_label = team_label(left_name, catalog)
    right_label = team_label(right_name, catalog)

    if kind == "game_finished":
        left_score, right_score = score_up_to(match, matches)
        res = f"🎮 **ГРА {game_number(match, matches)} ЗАВЕРШИЛАСЯ**\n{left_label} {left_score} — {right_score} {right_label}\n⏱ Тривалість: {format_duration(match.get('duration'))}"
        return res + "\n\u200b\n"
    
    left_total, right_total = score(match, matches)
    winner_name = left_name if left_total > right_total else right_name
    
    last_game_duration = format_duration(match.get("duration"))
    last_game_num = game_number(match, matches)

    if is_grand_final:
        res = f"🏆🥇 **ПЕРЕМОЖЕЦЬ THE INTERNATIONAL 2026**\n{team_label(winner_name, catalog)} ({left_total} — {right_total})\n⏱ Тривалість гри {last_game_num}: {last_game_duration}"
    else:
        res = f"🏆 **МАТЧ ЗАВЕРШИВСЯ**\n{left_label} {left_total} — {right_total} {right_label}\n⏱ Тривалість гри {last_game_num}: {last_game_duration}\n🥇 Переможець: {team_label(winner_name, catalog)}"
    
    return res + "\n\u200b\n"


def load_states(day: int | None = None) -> dict[str, object]:
    file = STATE_FILE
    if day is not None:
        file = STATE_FILE.with_name(f"match_states_day{day}.json")
    
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8-sig"))
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


def announce(ctx: dict, day_states: dict[str, object], key: str, kind: str, match: dict | None, label: str, is_grand_final: bool = False, day: int | None = None) -> bool:
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
        msg = message(
            kind,
            ctx["league"],
            match,
            ctx["matches"],
            ctx["catalog"],
            ctx["now"],
            is_grand_final,
            day=day,
            heroes_catalog=ctx.get("heroes_catalog"),
            liquipedia_hero_stats=ctx.get("liquipedia_hero_stats"),
        )
        if isinstance(msg, list):
            for part in msg:
                publish(ctx["webhook_url"], part)
                time.sleep(0.5)
        else:
            publish(ctx["webhook_url"], msg)
            time.sleep(0.5)
        day_states[key] = "announced"
        print(f"  ✓ {label}")
        return True
    except RuntimeError as error:
        print(f"  ✗ Error publishing {label}: {error}", file=sys.stderr)
        return False


def main() -> int:
    webhook_url = require("DISCORD_WEBHOOK_URL")
    now = int(datetime.now(UTC).timestamp())
    states = load_states()
    liquipedia = liquipedia_context(states)
    catalog = load_team_catalog()
    heroes_catalog = fetch_heroes_catalog_cached(states)
    liquipedia_hero_stats = fetch_liquipedia_hero_stats(states)
    
    league, matches = fetch_matches_cached(states, OPENDOTA_LEAGUE_ID)
    if not matches:
        print("Warning: No matches available to process. Exiting run safely.")
        return 0
    
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
        "heroes_catalog": heroes_catalog,
        "liquipedia_hero_stats": liquipedia_hero_stats,
        "now": now,
    }

    published = 0
    processed = 0

    curr_day = current_tournament_day(now)
    days_to_process = set(day_to_matches.keys())
    if curr_day >= 1:
        # Include all tournament days up to current day (up to 11)
        for d in range(1, min(curr_day, 11) + 1):
            days_to_process.add(d)

    for day in sorted(days_to_process):
        day_matches = day_to_matches.get(day, [])
        # Sort matches within the day strictly chronologically
        day_matches = sorted(day_matches, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))
        
        # Every announcement is keyed in this dict, so a repeated run publishes nothing new.
        day_states = load_states(day)

        if not day_matches:
            # On rest days (no matches scheduled/played), announce the day if it hasn't been announced yet
            if day in REST_DAYS and day <= curr_day and f"day:{day}" not in day_states:
                if announce(ctx, day_states, f"day:{day}", "tournament_day_no_matches", None, f"Day {day} announcement (no matches)", day=day):
                    published += 1
            save_states(day_states, day)
            continue

        # Ensure Day X announcement is sent first if we are announcing anything from this day
        # and it hasn't been announced yet.
        has_new_finished_matches = any(
            str(m["match_id"]) not in day_states and match_state(m, now) == "finished" 
            for m in day_matches
        )
        
        if has_new_finished_matches and f"day:{day}" not in day_states:
            # Use the first match of the day to trigger the "Day started" message
            first_match = day_matches[0]
            if announce(ctx, day_states, f"day:{day}", "tournament_day", first_match, f"Day {day} announcement", day=day):
                published += 1

        for match in day_matches:
            match_id = str(match["match_id"])
            radiant, dire = teams(match)
            radiant, dire = radiant.strip(), dire.strip()
            if match_state(match, now) != "finished":
                processed += 1
                continue

            # Determine best-of format for this match
            best_of = get_series_best_of(match, day_matches, day)
            wins_required = best_of // 2 + 1
            
            # Grand Final is the last match of the last day
            is_grand_final = (day == 11 and match == day_matches[-1])

            # Check if this match was already announced in this day's state
            if match_id not in day_states:
                left_wins, right_wins = score_up_to(match, matches)
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
                    if not is_series_announced(day_states, match):
                        label = f"Series finished: {radiant} vs {dire} ({left_wins} — {right_wins})"
                        if announce(ctx, day_states, series_state_key, "series_finished", match, label, is_grand_final):
                            published += 1
                    else:
                        day_states[series_state_key] = "announced"
            else:
                # IMPORTANT: If the game was already announced, we MUST still check if the series 
                # completion needs to be announced. This handles cases where a game was posted 
                # but the subsequent series message was missed in a previous run.
                left_wins, right_wins = score_up_to(match, matches)
                if left_wins >= wins_required or right_wins >= wins_required:
                    series_state_key = f"done:{series_key(match)}"
                    if not is_series_announced(day_states, match):
                        label = f"Series finished (delayed): {radiant} vs {dire} ({left_wins} — {right_wins})"
                        if announce(ctx, day_states, series_state_key, "series_finished", match, label, is_grand_final):
                            published += 1
                    else:
                        day_states[series_state_key] = "announced"

            processed += 1

        # The results table closes the day once every game of that day has a result.
        # Only close the day when:
        # 1. All matches of the day are finished
        # 2. All series of the day are completed (reach required wins)
        # 3. Minimum expected series for the day have finished AND 1 hour buffer passed, OR it's next day (04:00 UTC)
        if day_matches and all(match_state(m, now) == "finished" for m in day_matches):
            last_match = max(day_matches, key=match_end_time)
            last_match_end = match_end_time(last_match)
            next_day_start = int((TI_START_DATE + timedelta(days=day)).timestamp())
            is_next_day = (now >= next_day_start + 14400) # 4 hours into next day

            # Group day matches by series
            day_series_map: dict[str, list[dict]] = {}
            for m in day_matches:
                day_series_map.setdefault(series_key(m), []).append(m)

            all_series_complete = True
            completed_series_count = 0
            for sk, sm in day_series_map.items():
                first_gm = sm[0]
                b_of = get_series_best_of(first_gm, day_matches, day)
                w_req = b_of // 2 + 1
                w1, w2 = score(first_gm, day_matches)
                if w1 >= w_req or w2 >= w_req:
                    completed_series_count += 1
                else:
                    all_series_complete = False

            min_expected = MIN_SERIES_PER_DAY.get(day, 2)
            has_expected_series = (completed_series_count >= min_expected)

            is_ready_to_close = (
                (all_series_complete and has_expected_series and now > last_match_end + 3600)
                or (day < curr_day and all_series_complete and now > last_match_end + 3600)
                or is_next_day
            )

            if is_ready_to_close:
                if announce(ctx, day_states, f"day:{day}:finished", "day_finished", last_match, f"Day {day} finished announcement"):
                    published += 1
            else:
                # If day was prematurely marked finished in state but more games/series are in progress, retract state
                if not all_series_complete or (day == curr_day and not has_expected_series and not is_next_day):
                    day_states.pop(f"day:{day}:finished", None)

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
