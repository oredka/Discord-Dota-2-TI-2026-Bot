"""OpenDota fetch, cache, and match-detail enrichment."""

from __future__ import annotations

import sys
import time

from config import LEAGUE_ID, OPENDOTA_API_ENDPOINT, OPENDOTA_CACHE_SECONDS, TOURNAMENT_NAME, USER_AGENT
from http_client import get_json
from state import load_states, save_states
from tournament import tournament_day

DETAILS_KEY = "_match_details"
MAX_DETAIL_FETCHES_PER_RUN = 60
DETAIL_FETCH_DELAY = 1.05

_detail_fetches_this_run = 0
_last_detail_fetch_at = 0.0


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def fetch_matches(league_id: int) -> tuple[dict, list[dict]]:
    matches = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/matches", _headers())
    teams_data = get_json(f"{OPENDOTA_API_ENDPOINT}/leagues/{league_id}/teams", _headers())
    if not isinstance(matches, list):
        matches = []
    if not isinstance(teams_data, list):
        teams_data = []
    team_names = {t["team_id"]: t["name"].strip() for t in teams_data if t.get("name")}

    for m in matches:
        m["radiant_name"] = team_names.get(m.get("radiant_team_id"), "Radiant")
        m["dire_name"] = team_names.get(m.get("dire_team_id"), "Dire")

    matches = [m for m in matches if m.get("leagueid") == league_id]
    league = {"displayName": TOURNAMENT_NAME, "id": league_id}
    sorted_matches = sorted(
        matches,
        key=lambda x: (tournament_day(x), x.get("start_time") or 0, x.get("series_id") or 0, x.get("match_id") or 0),
    )
    return league, sorted_matches


def fetch_matches_cached(states: dict[str, object], league_id: int = LEAGUE_ID) -> tuple[dict, list[dict]]:
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
        return {"displayName": TOURNAMENT_NAME, "id": league_id}, []


def fetch_heroes_catalog_cached(states: dict[str, object]) -> dict[int, str]:
    """Fetch OpenDota heroes list and return map of hero_id -> localized_name."""
    cached = states.get("heroes_catalog")
    if isinstance(cached, dict) and cached:
        try:
            return {int(k): v for k, v in cached.items() if isinstance(v, str)}
        except (TypeError, ValueError):
            pass
    try:
        data = get_json(f"{OPENDOTA_API_ENDPOINT}/constants/heroes", _headers())
        heroes = {
            int(h["id"]): h.get("localized_name", f"Hero {h['id']}")
            for h in data.values()
            if isinstance(h, dict) and h.get("id")
        }
        states["heroes_catalog"] = {str(k): v for k, v in heroes.items()}
        return heroes
    except Exception as error:
        print(f"Warning: could not fetch heroes catalog: {error}", file=sys.stderr)
        return {}


def _rate_limit_details() -> None:
    global _last_detail_fetch_at
    wait = DETAIL_FETCH_DELAY - (time.time() - _last_detail_fetch_at)
    if wait > 0:
        time.sleep(wait)
    _last_detail_fetch_at = time.time()


def fetch_match_details(match_id: int | str) -> dict:
    data = get_json(f"{OPENDOTA_API_ENDPOINT}/matches/{match_id}", _headers())
    if not isinstance(data, dict):
        return {}
    return {"picks_bans": data.get("picks_bans") or []}


def _details_bucket(day_states: dict[str, object]) -> dict:
    bucket = day_states.get(DETAILS_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        day_states[DETAILS_KEY] = bucket
    return bucket


def apply_cached_picks_bans(match: dict, day_states: dict[str, object]) -> bool:
    """Attach cached picks_bans onto match. True means do not fetch again."""
    mid = str(match.get("match_id") or "")
    if match.get("picks_bans"):
        if mid:
            _details_bucket(day_states)[mid] = {"picks_bans": match["picks_bans"]}
        return True
    if not mid:
        return False
    bucket = _details_bucket(day_states)
    if mid not in bucket:
        return False
    cached = bucket[mid]
    if isinstance(cached, dict):
        match["picks_bans"] = cached.get("picks_bans") or []
    return True


def ensure_match_details(match: dict, day_states: dict[str, object]) -> None:
    """Fetch /matches/{id} once, cache picks_bans on the day state, attach to the match dict."""
    global _detail_fetches_this_run
    if apply_cached_picks_bans(match, day_states):
        return
    mid = match.get("match_id")
    if not mid:
        return
    if _detail_fetches_this_run >= MAX_DETAIL_FETCHES_PER_RUN:
        return
    try:
        _rate_limit_details()
        details = fetch_match_details(mid)
        _detail_fetches_this_run += 1
    except RuntimeError as error:
        print(f"Warning: could not fetch OpenDota match {mid}: {error}", file=sys.stderr)
        return
    pb = details.get("picks_bans") or []
    _details_bucket(day_states)[str(mid)] = {"picks_bans": pb}
    if pb:
        match["picks_bans"] = pb


def collect_details_from_days(up_to_day: int, current_day: int, current_day_states: dict[str, object]) -> dict[str, dict]:
    collected: dict[str, dict] = {}
    for day in range(1, up_to_day + 1):
        st = current_day_states if day == current_day else load_states(day)
        bucket = st.get(DETAILS_KEY)
        if isinstance(bucket, dict):
            for mid, payload in bucket.items():
                if isinstance(payload, dict):
                    collected[str(mid)] = payload
    return collected


def ensure_details_for_hero_stats(
    matches: list[dict],
    up_to_day: int,
    current_day: int,
    current_day_states: dict[str, object],
) -> None:
    """Fill missing picks_bans for finished matches up to `up_to_day`, caching per day."""
    cache = collect_details_from_days(up_to_day, current_day, current_day_states)
    other_days: dict[int, dict[str, object]] = {}
    dirty_days: set[int] = set()

    for match in matches:
        if match.get("radiant_win") is None:
            continue
        day = tournament_day(match)
        if day < 1 or day > up_to_day:
            continue
        mid = str(match.get("match_id") or "")
        if not mid:
            continue
        if match.get("picks_bans"):
            continue
        if mid in cache:
            cached = cache[mid]
            if isinstance(cached, dict) and cached.get("picks_bans"):
                match["picks_bans"] = cached["picks_bans"]
            continue
        day_states = current_day_states if day == current_day else other_days.setdefault(day, load_states(day))
        ensure_match_details(match, day_states)
        if match.get("picks_bans"):
            cache[mid] = {"picks_bans": match["picks_bans"]}
            if day != current_day:
                dirty_days.add(day)

    for day in dirty_days:
        save_states(other_days[day], day)
