"""Series keys, scores, standings, and tournament-day helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from config import (
    DEFAULT_BEST_OF,
    GRAND_FINAL_BEST_OF,
    GRAND_FINAL_DAY,
    MIN_SERIES_PER_DAY,
    START_DATE,
)
from uk_text import hero_ban_line, hero_pick_line

CLOSE_GRACE_CURRENT_DAY = 7200
CLOSE_GRACE_PAST_DAY = 3600
NEXT_CALENDAR_DAY_OFFSET = 14400


class DayCloseStatus(NamedTuple):
    ready: bool
    all_series_complete: bool
    has_expected_series: bool
    is_next_day: bool


def teams(match: dict) -> tuple[str, str]:
    return (match.get("radiant_name") or "Radiant", match.get("dire_name") or "Dire")


def match_state(match: dict) -> str:
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


def tournament_day(match: dict) -> int:
    """Calculate which tournament day this match is on (1-indexed) based on UTC time.
    Each day starts at 00:00 UTC.
    """
    start_time = match.get("start_time")
    if not start_time:
        return 0

    dt_utc = datetime.fromtimestamp(start_time, UTC)
    current_date_utc = datetime(dt_utc.year, dt_utc.month, dt_utc.day, tzinfo=UTC)
    delta = current_date_utc - START_DATE
    day = delta.days + 1
    return max(1, day)


def current_tournament_day(now: int) -> int:
    """Calculate the current tournament day (1-indexed) based on UTC time."""
    now_dt = datetime.fromtimestamp(now, UTC)
    current_date_utc = datetime(now_dt.year, now_dt.month, now_dt.day, tzinfo=UTC)
    delta = (current_date_utc - START_DATE).days
    if delta < 0:
        return 0
    return delta + 1


def games_in_series(match: dict, matches: list[dict]) -> list[dict]:
    s_key = series_key(match)
    series_games = [game for game in matches if series_key(game) == s_key]
    return sorted(series_games, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0))


def _series_sides(series_games: list[dict]) -> tuple[int | None, str, int | None, str]:
    first = series_games[0]
    return (
        first.get("radiant_team_id"),
        (first.get("radiant_name") or "Radiant").strip(),
        first.get("dire_team_id"),
        (first.get("dire_name") or "Dire").strip(),
    )


def _series_index(match: dict, series_games: list[dict]) -> int:
    mid = str(match.get("match_id"))
    for i, game in enumerate(series_games):
        if str(game.get("match_id")) == mid:
            return i
    return -1


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
        g_rad_id = gm.get("radiant_team_id")
        g_rad_name = (gm.get("radiant_name") or "").strip().casefold()

        is_left_radiant = (g_rad_id == left_team_id if left_team_id else g_rad_name == left_name.casefold())
        is_left_dire = (
            gm.get("dire_team_id") == left_team_id
            if left_team_id
            else (gm.get("dire_name") or "").strip().casefold() == left_name.casefold()
        )

        if is_left_radiant:
            if outcome:
                left_wins += 1
            else:
                right_wins += 1
        elif is_left_dire:
            if outcome:
                right_wins += 1
            else:
                left_wins += 1
        else:
            is_right_radiant = (
                g_rad_id == right_team_id if right_team_id else g_rad_name == right_name.casefold()
            )
            if is_right_radiant:
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


def score(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    left_team_id, left_name, right_team_id, right_name = _series_sides(series_games)
    return count_series_wins(series_games, left_team_id, left_name, right_team_id, right_name)


def score_up_to(match: dict, matches: list[dict]) -> tuple[int, int]:
    series_games = games_in_series(match, matches)
    if not series_games:
        return 0, 0
    current_game_index = _series_index(match, series_games)
    if current_game_index == -1:
        return 0, 0
    left_team_id, left_name, right_team_id, right_name = _series_sides(series_games)
    return count_series_wins(
        series_games[: current_game_index + 1],
        left_team_id,
        left_name,
        right_team_id,
        right_name,
    )


def game_number(match: dict, matches: list[dict]) -> int:
    series_games = games_in_series(match, matches)
    index = _series_index(match, series_games)
    return index + 1 if index >= 0 else 1


def ordered_day_series_keys(day_matches: list[dict]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for match in sorted(day_matches, key=lambda x: (x.get("start_time") or 0, x.get("match_id") or 0)):
        key = series_key(match)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def is_grand_final_series(match: dict, day_matches: list[dict], day: int) -> bool:
    """Day 11: first series is Lower Bracket Final (Bo3), second series is Grand Final (Bo5)."""
    if day != GRAND_FINAL_DAY or not day_matches:
        return False
    series_keys = ordered_day_series_keys(day_matches)
    return len(series_keys) >= 2 and series_key(match) == series_keys[1]


def get_series_best_of(match: dict, day_matches: list[dict], day: int) -> int:
    """Grand Final is Bo5 (3-0 / 3-1 / 3-2). Lower Bracket Final and every other series are Bo3."""
    return GRAND_FINAL_BEST_OF if is_grand_final_series(match, day_matches, day) else DEFAULT_BEST_OF


def series_is_complete(left_wins: int, right_wins: int, best_of: int) -> bool:
    """True once a team has the wins needed for this format: 2 in Bo3, 3 in Bo5."""
    return max(left_wins, right_wins) >= best_of // 2 + 1


def completed_series_for_day(day_matches: list[dict], day: int) -> tuple[int, bool]:
    """Return (completed series count, whether every series on this day is complete)."""
    day_series_map: dict[str, list[dict]] = {}
    for match in day_matches:
        day_series_map.setdefault(series_key(match), []).append(match)

    completed = 0
    all_complete = True
    for series_games in day_series_map.values():
        first = series_games[0]
        best_of = get_series_best_of(first, day_matches, day)
        left_wins, right_wins = score(first, day_matches)
        if series_is_complete(left_wins, right_wins, best_of):
            completed += 1
        else:
            all_complete = False
    return completed, all_complete


def day_close_status(day: int, day_matches: list[dict], now: int, curr_day: int) -> DayCloseStatus:
    """Whether the day-finished announcement can go out, plus the flags that gate retries."""
    if not day_matches:
        return DayCloseStatus(False, False, False, False)

    last_match_end = match_end_time(max(day_matches, key=match_end_time))
    next_day_start = int((START_DATE + timedelta(days=day)).timestamp())
    is_next_day = now >= next_day_start + NEXT_CALENDAR_DAY_OFFSET
    completed_count, all_series_complete = completed_series_for_day(day_matches, day)
    has_expected_series = completed_count >= MIN_SERIES_PER_DAY.get(day, 4)
    ready = (
        (all_series_complete and has_expected_series and now > last_match_end + CLOSE_GRACE_CURRENT_DAY)
        or (
            day < curr_day
            and all_series_complete
            and has_expected_series
            and now > last_match_end + CLOSE_GRACE_PAST_DAY
        )
        or is_next_day
    )
    return DayCloseStatus(ready, all_series_complete, has_expected_series, is_next_day)


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
                place_str = "14–16 місця" if s_day <= 3 else "11–13 місця"
                elim_info[loser] = {"day": s_day, "place": place_str, "stage": "swiss"}
        else:
            playoff_losses[loser] += 1
            if playoff_losses[loser] >= 2 and loser not in elim_info:
                elim_count = len([t for t, info in elim_info.items() if info["stage"] == "playoffs"])
                if elim_count < 2:
                    place_str = "9–10 місця"
                elif elim_count < 4:
                    place_str = "7–8 місця"
                elif elim_count < 6:
                    place_str = "5–6 місця"
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
            name.casefold(),
        ),
    )

    elim_ordered = sorted(
        [name for name in game_stats if name in elim_map],
        key=lambda name: (
            -int(elim_map[name]["day"]),
            -series_stats[name]["wins"],
            series_stats[name]["losses"],
            -game_stats[name]["wins"],
            game_stats[name]["losses"],
            name.casefold(),
        ),
    )

    rows: list[tuple[int, str, int, int, int, int, str | None]] = []
    place = 0
    previous: tuple[int, int, int, int] | None = None
    for index, name in enumerate(active_ordered, start=1):
        record = (
            series_stats[name]["wins"],
            series_stats[name]["losses"],
            game_stats[name]["wins"],
            game_stats[name]["losses"],
        )
        if record != previous:
            place, previous = index, record
        rows.append((place, name, *record, None))

    start_elim_idx = len(active_ordered) + 1
    prev_elim_key: tuple[object, int, int, int, int] | None = None
    elim_place = start_elim_idx
    for offset, name in enumerate(elim_ordered):
        index = start_elim_idx + offset
        elim_key = (
            elim_map[name]["day"],
            series_stats[name]["wins"],
            series_stats[name]["losses"],
            game_stats[name]["wins"],
            game_stats[name]["losses"],
        )
        if elim_key != prev_elim_key:
            elim_place, prev_elim_key = index, elim_key
        rows.append(
            (
                elim_place,
                name,
                series_stats[name]["wins"],
                series_stats[name]["losses"],
                game_stats[name]["wins"],
                game_stats[name]["losses"],
                str(elim_map[name]["place"]),
            )
        )

    return rows


def hero_stats(
    matches: list[dict],
    up_to_day: int,
    heroes_catalog: dict[int, str],
    liquipedia_hero_stats: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Top-10 picked and banned heroes. Liquipedia first; OpenDota picks_bans as fallback."""
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
            team_slot = pb.get("team")
            if is_pick:
                picks[hid] = picks.get(hid, 0) + 1
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
        losses = cnt - w
        wr = (w / cnt) * 100 if cnt else 0
        name = heroes_catalog.get(hid, f"Hero {hid}")
        return hero_pick_line(name, cnt, wr, w, losses)

    def ban_label(hid: int, cnt: int) -> str:
        p_cnt = picks.get(hid, 0)
        w = wins.get(hid, 0)
        losses = p_cnt - w
        wr = (w / p_cnt) * 100 if p_cnt else 0
        name = heroes_catalog.get(hid, f"Hero {hid}")
        return hero_ban_line(name, cnt, p_cnt, wr, w, losses)

    return {
        "picks": [f"{i+1}. {pick_label(hid, cnt)}" for i, (hid, cnt) in enumerate(top_picks)],
        "bans": [f"{i+1}. {ban_label(hid, cnt)}" for i, (hid, cnt) in enumerate(top_bans)],
    }
