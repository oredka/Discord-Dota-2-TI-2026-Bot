"""Track The International 2026 through OpenDota and post match events to Discord."""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime

from api.liquipedia import fetch_liquipedia_hero_stats
from api.opendota import (
    ensure_details_for_hero_stats,
    ensure_match_details,
    fetch_heroes_catalog_cached,
    fetch_matches_cached,
)
from config import LAST_DAY, LEAGUE_ID, REST_DAYS
from discord_messages import load_team_catalog, message, publish
from state import load_states, save_states
from tournament import (
    current_tournament_day,
    day_close_status,
    get_series_best_of,
    is_grand_final_series,
    is_series_announced,
    match_end_time,
    match_state,
    score_up_to,
    series_is_complete,
    series_key,
    teams,
    tournament_day,
)

PUBLISH_DELAY_SECONDS = 0.5

SILENT_BOOTSTRAP = os.getenv("SILENT_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes")


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


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
        if match is not None and kind in ("game_finished", "series_finished"):
            ensure_match_details(match, day_states)
        if kind == "day_finished":
            lp = ctx.get("liquipedia_hero_stats") or {}
            if not lp.get("picks") and not lp.get("bans"):
                ensure_details_for_hero_stats(
                    ctx["matches"],
                    day or (tournament_day(match) if match else 0),
                    day or 0,
                    day_states,
                )
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
            time.sleep(PUBLISH_DELAY_SECONDS)
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
    catalog = load_team_catalog()
    heroes_catalog = fetch_heroes_catalog_cached(states)
    liquipedia_hero_stats = fetch_liquipedia_hero_stats(states)

    _league, matches = fetch_matches_cached(states, LEAGUE_ID)
    if not matches:
        print("Warning: No matches available to process. Exiting run safely.")
        return 0

    sorted_matches = sorted(
        matches,
        key=lambda x: (x.get("start_time") or 0, x.get("series_id") or 0, x.get("match_id") or 0),
    )

    day_to_matches: dict[int, list[dict]] = {}
    for match in sorted_matches:
        d = tournament_day(match)
        if d > 0:
            day_to_matches.setdefault(d, []).append(match)

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
    days_to_process = set(day_to_matches.keys())
    if curr_day >= 1:
        for d in range(1, min(curr_day, LAST_DAY) + 1):
            days_to_process.add(d)

    for day in sorted(days_to_process):
        day_matches = day_to_matches.get(day, [])
        day_states = load_states(day)

        if not day_matches:
            if day in REST_DAYS and day <= curr_day and f"day:{day}" not in day_states:
                if announce(
                    ctx,
                    day_states,
                    f"day:{day}",
                    "tournament_day_no_matches",
                    None,
                    f"Day {day} announcement (no matches)",
                    day=day,
                ):
                    published += 1
            save_states(day_states, day)
            continue

        has_new_finished_matches = any(
            str(m["match_id"]) not in day_states and match_state(m) == "finished" for m in day_matches
        )

        if has_new_finished_matches and f"day:{day}" not in day_states:
            first_match = day_matches[0]
            if announce(ctx, day_states, f"day:{day}", "tournament_day", first_match, f"Day {day} announcement", day=day):
                published += 1

        for match in day_matches:
            match_id = str(match["match_id"])
            radiant, dire = teams(match)
            radiant, dire = radiant.strip(), dire.strip()
            if match_state(match) != "finished":
                processed += 1
                continue

            best_of = get_series_best_of(match, day_matches, day)
            already_announced_game = match_id in day_states
            left_wins, right_wins = score_up_to(match, matches)
            is_series_end = series_is_complete(left_wins, right_wins, best_of)
            is_grand_final = is_series_end and is_grand_final_series(match, day_matches, day)

            if not already_announced_game:
                if not is_series_end:
                    if announce(
                        ctx,
                        day_states,
                        match_id,
                        "game_finished",
                        match,
                        f"Game finished: {radiant} vs {dire} (Match ID: {match_id})",
                    ):
                        published += 1
                else:
                    day_states[match_id] = "announced"

            if is_series_end:
                series_state_key = f"done:{series_key(match)}"
                if is_series_announced(day_states, match):
                    day_states[series_state_key] = "announced"
                else:
                    prefix = "Series finished (delayed)" if already_announced_game else "Series finished"
                    label = f"{prefix}: {radiant} vs {dire} ({left_wins} — {right_wins})"
                    if announce(ctx, day_states, series_state_key, "series_finished", match, label, is_grand_final):
                        published += 1

            processed += 1

        if day_matches and all(match_state(m) == "finished" for m in day_matches):
            close = day_close_status(day, day_matches, now, curr_day)
            if close.ready:
                last_match = max(day_matches, key=match_end_time)
                if announce(
                    ctx,
                    day_states,
                    f"day:{day}:finished",
                    "day_finished",
                    last_match,
                    f"Day {day} finished announcement",
                    day=day,
                ):
                    published += 1
            elif not close.all_series_complete or (
                day == curr_day and not close.has_expected_series and not close.is_next_day
            ):
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
