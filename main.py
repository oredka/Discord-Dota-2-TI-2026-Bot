"""Track The International 2026 through OpenDota and post match events to Discord."""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

OPENDOTA_CACHE_SECONDS = 300
OPENDOTA_API_ENDPOINT = "https://api.opendota.com/api"
OPENDOTA_LEAGUE_ID = 19719
OPENDOTA_HEADERS = {"User-Agent": "ti2026-discord-webhook/1.0"}
LIQUIPEDIA_API = "https://liquipedia.net/dota2/api.php"
MAINCAST_DOTA2_URL = "https://www.youtube.com/@Dota2_maincast/streams"
STATE_FILE = Path(os.getenv("STATE_FILE", "states/match_states.json"))
TEAM_CATALOG_FILE = Path(os.getenv("TEAM_CATALOG_FILE", "team_metadata.json"))
LIQUIPEDIA_CACHE_SECONDS = 600
TI_START_DATE = datetime(2026, 8, 13, tzinfo=UTC)
REST_DAYS = {5}
MIN_SERIES_PER_DAY = {
    1: 12,
    2: 12,
    3: 12,
    4: 5,
    5: 0,
    6: 4,
    7: 4,
    8: 4,
    9: 4,
    10: 2,
    11: 2,
}
# Set to 1 to record every current result in the state without posting anything to Discord.
SILENT_BOOTSTRAP = os.getenv("SILENT_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes")


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def liquipedia_headers() -> dict[str, str]:
    user_agent = os.getenv("LIQUIPEDIA_USER_AGENT", "TI2026DiscordBot/1.0 (GitHub Actions)")
    return {"User-Agent": user_agent, "Accept": "application/json"}


def post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    try:
        response = requests.post(url, json=payload, headers=headers or {}, timeout=30)
        response.raise_for_status()
        return response.json() if response.text else {}
    except requests.exceptions.HTTPError as error:
        raise RuntimeError(f"HTTP {error.response.status_code}: {error.response.text}") from error
    except requests.exceptions.RequestException as error:
        raise RuntimeError(f"Network error: {error}") from error


def get_json(url: str, headers: dict[str, str], max_retries: int = 3, retry_delay: float = 2.0) -> Any:
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
    matches = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/matches", OPENDOTA_HEADERS)
    teams_data = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/teams", OPENDOTA_HEADERS)
    team_names = {t["team_id"]: t["name"].strip() for t in teams_data if t.get("name")}

    for match in matches:
        match["radiant_name"] = team_names.get(match.get("radiant_team_id"), "Radiant")
        match["dire_name"] = team_names.get(match.get("dire_team_id"), "Dire")

    matches = [match for match in matches if match.get("leagueid") == league_id]
    league = {"displayName": "The International 2026", "id": league_id}
    sorted_matches = sorted(
        matches,
        key=lambda x: (tournament_day(x), x.get("start_time") or 0, x.get("series_id") or 0, x.get("match_id") or 0),
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


def team_label(name: str, catalog: dict[str, dict[str, str]]) -> str:
    flag = catalog.get(name.strip().casefold(), {}).get("flag", "")
    return f"{flag} {name}".strip()


def is_finished(match: dict) -> bool:
    return match.get("radiant_win") is not None


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


def tournament_day(match: dict) -> int:
    """Calculate which tournament day this match is on (1-indexed) based on UTC time.
    Each day starts at 00:00 UTC.
    """
    start_time = match.get("start_time")
    if not start_time:
        return 0

    dt_utc = datetime.fromtimestamp(start_time, UTC)
    current_date_utc = datetime(dt_utc.year, dt_utc.month, dt_utc.day, tzinfo=UTC)
    delta = current_date_utc - TI_START_DATE
    return max(1, delta.days + 1)


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
    return sorted(series_games, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))


def _team_on_side(gm: dict, team_id: int | None, name: str, side: str) -> bool:
    name = name.strip().casefold()
    if side == "radiant":
        if team_id:
            return gm.get("radiant_team_id") == team_id
        return (gm.get("radiant_name") or "").strip().casefold() == name
    if team_id:
        return gm.get("dire_team_id") == team_id
    return (gm.get("dire_name") or "").strip().casefold() == name


def count_series_wins(
    sm: list[dict],
    left_team_id: int | None,
    left_name: str,
    right_team_id: int | None,
    right_name: str,
) -> tuple[int, int]:
    left_wins = right_wins = 0
    for gm in sm:
        outcome = gm.get("radiant_win")
        if outcome is None:
            continue

        if _team_on_side(gm, left_team_id, left_name, "radiant"):
            if outcome:
                left_wins += 1
            else:
                right_wins += 1
        elif _team_on_side(gm, left_team_id, left_name, "dire"):
            if outcome:
                right_wins += 1
            else:
                left_wins += 1
        elif _team_on_side(gm, right_team_id, right_name, "radiant"):
            if outcome:
                right_wins += 1
            else:
                left_wins += 1
        else:
            if outcome:
                left_wins += 1
            else:
                right_wins += 1
    return left_wins, right_wins


def _series_sides(series_games: list[dict]) -> tuple[int | None, str, int | None, str]:
    first_game = series_games[0]
    return (
        first_game.get("radiant_team_id"),
        (first_game.get("radiant_name") or "Radiant").strip(),
        first_game.get("dire_team_id"),
        (first_game.get("dire_name") or "Dire").strip(),
    )


def _match_index(series_games: list[dict], match: dict) -> int:
    match_id = str(match.get("match_id"))
    for i, game in enumerate(series_games):
        if str(game.get("match_id")) == match_id:
            return i
    return -1


def score(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    left_id, left_name, right_id, right_name = _series_sides(series_games)
    return count_series_wins(series_games, left_id, left_name, right_id, right_name)


def score_up_to(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    current_game_index = _match_index(series_games, match)
    if current_game_index == -1:
        return 0, 0
    left_id, left_name, right_id, right_name = _series_sides(series_games)
    return count_series_wins(series_games[: current_game_index + 1], left_id, left_name, right_id, right_name)


def game_number(match: dict, matches: list[dict]) -> int:
    index = _match_index(games_in_series(match, matches), match)
    return index + 1 if index >= 0 else 1


def eliminated_teams(matches: list[dict], up_to_day: int) -> dict[str, dict[str, object]]:
    """Map of team names to elimination info: {'day': int, 'place': str}."""
    series_map: dict[str, list[dict]] = {}
    for match in matches:
        if tournament_day(match) > up_to_day:
            continue
        series_map.setdefault(series_key(match), []).append(match)

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
                place_str = "14-16 місце" if s_day <= 3 else "11-13 місце"
                elim_info[loser] = {"day": s_day, "place": place_str, "stage": "swiss"}
        else:
            playoff_losses[loser] += 1
            if playoff_losses[loser] >= 2 and loser not in elim_info:
                elim_count = len([t for t, info in elim_info.items() if info["stage"] == "playoffs"])
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
        radiant, dire = radiant.strip(), dire.strip()

        known = [name for name in (radiant, dire) if name not in ("Radiant", "Dire")]
        for name in known:
            game_stats.setdefault(name, {"wins": 0, "losses": 0})
            series_stats.setdefault(name, {"wins": 0, "losses": 0})

        outcome = match.get("radiant_win")
        if outcome is not None and len(known) >= 2:
            winner, loser = (radiant, dire) if outcome else (dire, radiant)
            game_stats[winner]["wins"] += 1
            game_stats[loser]["losses"] += 1

        series_map.setdefault(series_key(match), []).append(match)

    for sm in series_map.values():
        first = sm[0]
        r_name, d_name = teams(first)
        r_name, d_name = r_name.strip(), d_name.strip()
        r_wins, d_wins = count_series_wins(sm, first.get("radiant_team_id"), r_name, first.get("dire_team_id"), d_name)

        if r_wins > d_wins and (r_wins >= 2 or d_wins >= 2 or len(sm) == 1):
            if r_name in series_stats and d_name in series_stats:
                series_stats[r_name]["wins"] += 1
                series_stats[d_name]["losses"] += 1
        elif d_wins > r_wins and (r_wins >= 2 or d_wins >= 2 or len(sm) == 1):
            if r_name in series_stats and d_name in series_stats:
                series_stats[d_name]["wins"] += 1
                series_stats[r_name]["losses"] += 1

    elim_map = eliminated_teams(matches, day)

    def record_key(name: str) -> tuple:
        return (
            -series_stats[name]["wins"],
            series_stats[name]["losses"],
            -game_stats[name]["wins"],
            game_stats[name]["losses"],
            name.casefold(),
        )

    active_ordered = sorted((name for name in game_stats if name not in elim_map), key=record_key)
    elim_ordered = sorted(
        (name for name in game_stats if name in elim_map),
        key=lambda name: (-int(elim_map[name]["day"]), *record_key(name)),
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
            str(elim_map[name]["place"]),
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
    try:
        data = get_json(f"{LIQUIPEDIA_API}?{query}", liquipedia_headers())
        html = data.get("parse", {}).get("text", {}).get("*", "")
        if not html:
            return {}

        parser = LiquipediaTableParser()
        parser.feed(html)

        heroes_data = []
        for table in parser.tables:
            for row in table:
                if len(row) >= 16 and row[2].isdigit() and row[3].isdigit() and row[4].isdigit() and "%" in row[5] and row[15].isdigit():
                    hero = row[1]
                    picks = int(row[2])
                    wins = int(row[3])
                    losses = int(row[4])
                    try:
                        wr = float(row[5].replace("%", "").strip())
                    except ValueError:
                        wr = (wins / picks * 100) if picks else 0.0
                    bans = int(row[15])
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
    """Calculate or provide Top-10 picked and banned heroes."""
    if liquipedia_hero_stats and (liquipedia_hero_stats.get("picks") or liquipedia_hero_stats.get("bans")):
        return liquipedia_hero_stats

    played = [m for m in matches if tournament_day(m) <= up_to_day and is_finished(m)]
    picks: dict[int, int] = {}
    bans: dict[int, int] = {}
    wins: dict[int, int] = {}

    for match in played:
        r_win = match.get("radiant_win")
        for pb in match.get("picks_bans") or []:
            hid = pb.get("hero_id")
            if not hid:
                continue
            if pb.get("is_pick"):
                picks[hid] = picks.get(hid, 0) + 1
                team_slot = pb.get("team")
                if (team_slot == 0 and r_win) or (team_slot == 1 and not r_win):
                    wins[hid] = wins.get(hid, 0) + 1
            else:
                bans[hid] = bans.get(hid, 0) + 1

    if not picks and not bans:
        return {}

    top_picks = sorted(picks.items(), key=lambda x: (-x[1], heroes_catalog.get(x[0], str(x[0]))))[:10]
    top_bans = sorted(bans.items(), key=lambda x: (-x[1], heroes_catalog.get(x[0], str(x[0]))))[:10]

    def pick_label(hid: int, cnt: int) -> str:
        w = wins.get(hid, 0)
        wr = round((w / cnt) * 100) if cnt else 0
        name = heroes_catalog.get(hid, f"Hero {hid}")
        return f"{name} — {cnt} ігор ({wr}% WR, {w}-{cnt - w})"

    def ban_label(hid: int, cnt: int) -> str:
        p_cnt = picks.get(hid, 0)
        w = wins.get(hid, 0)
        name = heroes_catalog.get(hid, f"Hero {hid}")
        if p_cnt > 0:
            wr = round((w / p_cnt) * 100)
            return f"{name} — {cnt} банів ({wr}% WR, {w}-{p_cnt - w})"
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
        data = get_json(f"{OPENDOTA_API_ENDPOINT}/constants/heroes", OPENDOTA_HEADERS)
        heroes = {int(h["id"]): h.get("localized_name", f"Hero {h['id']}") for h in data.values() if isinstance(h, dict) and h.get("id")}
        states["heroes_catalog"] = heroes
        return heroes
    except Exception as error:
        print(f"Warning: could not fetch heroes catalog: {error}", file=sys.stderr)
        return {}


def format_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "—"


def ordered_day_series_keys(day_matches: list[dict]) -> list[str]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for match in day_matches:
        grouped[series_key(match)].append(match)
    return sorted(grouped, key=lambda key: min((game.get("start_time") or 0) for game in grouped[key]))


def is_grand_final_series(match: dict, day_matches: list[dict], day: int) -> bool:
    """Day 11: first series is Lower Bracket Final (Bo3), second series is Grand Final (Bo5)."""
    if day != 11 or not day_matches:
        return False
    series_keys = ordered_day_series_keys(day_matches)
    return len(series_keys) >= 2 and series_key(match) == series_keys[1]


def get_series_best_of(match: dict, day_matches: list[dict], day: int) -> int:
    """TI 2026: Grand Final is Bo5 (3-0 / 3-1 / 3-2). Lower Bracket Final and every other series are Bo3 (2-0 / 2-1)."""
    return 5 if is_grand_final_series(match, day_matches, day) else 3


def series_is_complete(left_wins: int, right_wins: int, best_of: int) -> bool:
    """True once a team has the wins needed for this format: 2 in Bo3, 3 in Bo5."""
    return max(left_wins, right_wins) >= best_of // 2 + 1


def message(
    kind: str,
    match: dict | None,
    matches: list[dict],
    catalog: dict[str, dict[str, str]],
    is_grand_final: bool = False,
    day: int | None = None,
    heroes_catalog: dict[int, str] | None = None,
    liquipedia_hero_stats: dict[str, list[str]] | None = None,
) -> str | list[str]:
    if kind == "tournament_day":
        if day is None and match:
            day = tournament_day(match)
        return f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\n{MAINCAST_DOTA2_URL}\n\u200b\n"
    if kind == "tournament_day_no_matches":
        if day is None and match:
            day = tournament_day(match)
        return f"📅 **ДЕНЬ {day} THE INTERNATIONAL 2026**\nСьогодні ігор не заплановано\n\u200b\n"
    if kind == "day_finished":
        if day is None and match:
            day = tournament_day(match)
        table = "".join(
            f"{place}. {team_label(name, catalog)} ({elim_place})\n"
            if elim_place
            else f"{place}. {team_label(name, catalog)} {s_wins}-{s_losses} ({g_wins}-{g_losses})\n"
            for place, name, s_wins, s_losses, g_wins, g_losses, elim_place in standings(matches, day or 0)
        )

        sections = [f"🏆 **ДЕНЬ {day} THE INTERNATIONAL 2026 ЗАВЕРШИВСЯ**\n\n{table.strip()}"]
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
        return (
            f"🎮 **ГРА {game_number(match, matches)} ЗАВЕРШИЛАСЯ**\n"
            f"{left_label} {left_score} — {right_score} {right_label}\n"
            f"⏱ Тривалість: {format_duration(match.get('duration'))}\n\u200b\n"
        )

    left_total, right_total = score(match, matches)
    winner_name = left_name if left_total > right_total else right_name
    winner_label = team_label(winner_name, catalog)
    last_game_duration = format_duration(match.get("duration"))
    last_game_num = game_number(match, matches)
    winner_line = (
        f"🥇 ПЕРЕМОЖЕЦЬ THE INTERNATIONAL 2026 - {winner_label}!"
        if is_grand_final
        else f"🥇 Переможець: {winner_label}"
    )
    return (
        f"🏆 **МАТЧ ЗАВЕРШИВСЯ**\n"
        f"{left_label} {left_total} — {right_total} {right_label}\n"
        f"⏱ Тривалість гри {last_game_num}: {last_game_duration}\n"
        f"{winner_line}\n\u200b\n"
    )


def state_file(day: int | None = None) -> Path:
    if day is not None:
        return STATE_FILE.with_name(f"match_states_day{day}.json")
    return STATE_FILE


def load_states(day: int | None = None) -> dict[str, object]:
    file = state_file(day)
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def save_states(states: dict[str, object], day: int | None = None) -> None:
    file = state_file(day)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(states, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def publish(webhook_url: str, text: str) -> None:
    post_json(webhook_url, {"username": "The International 2026", "content": text})


def announce(
    ctx: dict,
    day_states: dict[str, object],
    key: str,
    kind: str,
    match: dict | None,
    label: str,
    is_grand_final: bool = False,
    day: int | None = None,
) -> bool:
    """Publish one event at most once. Returns True when a Discord message was sent."""
    if key in day_states:
        return False

    if SILENT_BOOTSTRAP:
        day_states[key] = "announced"
        return False
    try:
        msg = message(
            kind,
            match,
            ctx["matches"],
            ctx["catalog"],
            is_grand_final,
            day=day,
            heroes_catalog=ctx.get("heroes_catalog"),
            liquipedia_hero_stats=ctx.get("liquipedia_hero_stats"),
        )
        parts = msg if isinstance(msg, list) else [msg]
        for part in parts:
            publish(ctx["webhook_url"], part)
            time.sleep(0.5)
        day_states[key] = "announced"
        print(f"  ✓ {label}")
        return True
    except RuntimeError as error:
        print(f"  ✗ Error publishing {label}: {error}", file=sys.stderr)
        return False


def _announce_series_if_needed(
    ctx: dict,
    day_states: dict[str, object],
    match: dict,
    radiant: str,
    dire: str,
    left_wins: int,
    right_wins: int,
    is_grand_final: bool,
    delayed: bool,
) -> bool:
    series_state_key = f"done:{series_key(match)}"
    if is_series_announced(day_states, match):
        day_states[series_state_key] = "announced"
        return False
    suffix = " (delayed)" if delayed else ""
    label = f"Series finished{suffix}: {radiant} vs {dire} ({left_wins} — {right_wins})"
    return announce(ctx, day_states, series_state_key, "series_finished", match, label, is_grand_final)


def _day_series_progress(day: int, day_matches: list[dict]) -> tuple[bool, int]:
    day_series_map: dict[str, list[dict]] = {}
    for match in day_matches:
        day_series_map.setdefault(series_key(match), []).append(match)

    completed_series_count = 0
    all_series_complete = True
    for sm in day_series_map.values():
        first_gm = sm[0]
        best_of = get_series_best_of(first_gm, day_matches, day)
        w1, w2 = score(first_gm, day_matches)
        if series_is_complete(w1, w2, best_of):
            completed_series_count += 1
        else:
            all_series_complete = False
    return all_series_complete, completed_series_count


def _day_close_status(day: int, day_matches: list[dict], now: int, curr_day: int) -> tuple[bool, bool]:
    """Returns (ready_to_close, should_retract_finished_marker)."""
    last_match_end = match_end_time(max(day_matches, key=match_end_time))
    is_next_day = now >= int((TI_START_DATE + timedelta(days=day)).timestamp()) + 14400
    all_series_complete, completed_series_count = _day_series_progress(day, day_matches)
    has_expected_series = completed_series_count >= MIN_SERIES_PER_DAY.get(day, 4)
    ready = (
        (all_series_complete and has_expected_series and now > last_match_end + 7200)
        or (day < curr_day and all_series_complete and has_expected_series and now > last_match_end + 3600)
        or is_next_day
    )
    retract = not ready and (
        not all_series_complete or (day == curr_day and not has_expected_series and not is_next_day)
    )
    return ready, retract


def main() -> int:
    webhook_url = require("DISCORD_WEBHOOK_URL")
    now = int(datetime.now(UTC).timestamp())
    states = load_states()
    catalog = load_team_catalog()
    heroes_catalog = fetch_heroes_catalog_cached(states)
    liquipedia_hero_stats = fetch_liquipedia_hero_stats(states)

    _, matches = fetch_matches_cached(states, OPENDOTA_LEAGUE_ID)
    if not matches:
        print("Warning: No matches available to process. Exiting run safely.")
        return 0

    sorted_matches = sorted(matches, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))
    day_to_matches: dict[int, list[dict]] = defaultdict(list)
    for match in sorted_matches:
        day = tournament_day(match)
        if day > 0:
            day_to_matches[day].append(match)

    ctx = {
        "webhook_url": webhook_url,
        "matches": matches,
        "catalog": catalog,
        "heroes_catalog": heroes_catalog,
        "liquipedia_hero_stats": liquipedia_hero_stats,
    }

    published = 0
    processed = 0
    curr_day = current_tournament_day(now)
    days_to_process = set(day_to_matches)
    if curr_day >= 1:
        days_to_process.update(range(1, min(curr_day, 11) + 1))

    for day in sorted(days_to_process):
        day_matches = sorted(
            day_to_matches.get(day, []),
            key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0),
        )
        day_states = load_states(day)

        if not day_matches:
            if day in REST_DAYS and day <= curr_day and f"day:{day}" not in day_states:
                if announce(ctx, day_states, f"day:{day}", "tournament_day_no_matches", None, f"Day {day} announcement (no matches)", day=day):
                    published += 1
            save_states(day_states, day)
            continue

        has_new_finished_matches = any(str(m["match_id"]) not in day_states and is_finished(m) for m in day_matches)
        if has_new_finished_matches and f"day:{day}" not in day_states:
            if announce(ctx, day_states, f"day:{day}", "tournament_day", day_matches[0], f"Day {day} announcement", day=day):
                published += 1

        for match in day_matches:
            match_id = str(match["match_id"])
            radiant, dire = (name.strip() for name in teams(match))
            if not is_finished(match):
                processed += 1
                continue

            best_of = get_series_best_of(match, day_matches, day)
            already_announced_game = match_id in day_states
            left_wins, right_wins = score_up_to(match, matches)
            is_series_end = series_is_complete(left_wins, right_wins, best_of)
            is_grand_final = is_series_end and is_grand_final_series(match, day_matches, day)

            if not already_announced_game:
                if not is_series_end:
                    if announce(ctx, day_states, match_id, "game_finished", match, f"Game finished: {radiant} vs {dire} (Match ID: {match_id})"):
                        published += 1
                else:
                    day_states[match_id] = "announced"

            if is_series_end and _announce_series_if_needed(
                ctx, day_states, match, radiant, dire, left_wins, right_wins, is_grand_final, already_announced_game
            ):
                published += 1

            processed += 1

        if day_matches and all(is_finished(m) for m in day_matches):
            ready_to_close, retract_finished = _day_close_status(day, day_matches, now, curr_day)
            if ready_to_close:
                last_match = max(day_matches, key=match_end_time)
                if announce(ctx, day_states, f"day:{day}:finished", "day_finished", last_match, f"Day {day} finished announcement"):
                    published += 1
            elif retract_finished:
                day_states.pop(f"day:{day}:finished", None)

        save_states(day_states, day)

    save_states(states)
    print(f"Processed {processed} match(es), published {published} Discord update(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
