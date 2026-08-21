"""Discord webhook payloads: embeds, publish, and sample test messages."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from config import (
    DEFAULT_EMBED_COLOR,
    DISCORD_USERNAME,
    LIQUIPEDIA_URL,
    MAINCAST_DOTA2_URL,
    OPENDOTA_MATCH_URL_TEMPLATE,
    TEAM_CATALOG_FILE,
    TOURNAMENT_NAME,
)
from http_client import post_json
from tournament import (
    game_number,
    games_in_series,
    hero_stats,
    score,
    score_up_to,
    standings,
    teams,
    tournament_day,
)

EMBED_DESC_LIMIT = 4096
EMBED_FIELD_LIMIT = 1024
EMBED_TOTAL_LIMIT = 6000
STATE_PUSH_FAILURE_ALERT = (
    "⚠️ Не вдалося зберегти стан матчів у git. Наступний запуск може повторно опублікувати події."
)


def discord_color(value: str) -> int:
    try:
        return int(value.removeprefix("#"), 16)
    except ValueError:
        return DEFAULT_EMBED_COLOR


def load_team_catalog() -> dict[str, dict[str, str]]:
    if not TEAM_CATALOG_FILE.exists():
        return {}
    try:
        data = json.loads(TEAM_CATALOG_FILE.read_text(encoding="utf-8-sig"))
        return {
            name.casefold(): {**item, "name": name}
            for name, item in data.items()
            if isinstance(item, dict)
        }
    except json.JSONDecodeError:
        print("Warning: team_metadata.json is invalid; using team names only.", file=sys.stderr)
        return {}


def team_info(name: str, catalog: dict[str, dict[str, str]]) -> dict[str, str]:
    return catalog.get(name.strip().casefold(), {})


def team_label(name: str, catalog: dict[str, dict[str, str]]) -> str:
    info = team_info(name, catalog)
    display = info.get("name") or name
    flag = info.get("flag", "")
    return f"{flag} {display}".strip()


def format_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else "—"


def format_start(unix_time: int | None) -> str:
    if not unix_time:
        return "час уточнюється"
    return f"<t:{unix_time}:t>"


def opendota_match_url(match_id: object) -> str:
    return OPENDOTA_MATCH_URL_TEMPLATE.format(match_id=match_id)


def game_winner_name(match: dict) -> str:
    radiant, dire = teams(match)
    if match.get("radiant_win"):
        return radiant.strip()
    return dire.strip()


def format_draft(match: dict | None, heroes_catalog: dict[int, str] | None = None) -> str:
    """Compact Ukrainian Radiant/Dire picks and bans. Empty if draft data is missing."""
    if not match:
        return ""
    pb_list = match.get("picks_bans") or []
    if not pb_list:
        return ""
    catalog = heroes_catalog or {}
    radiant_picks: list[str] = []
    radiant_bans: list[str] = []
    dire_picks: list[str] = []
    dire_bans: list[str] = []
    for item in sorted(pb_list, key=lambda row: row.get("order", 0) if isinstance(row, dict) else 0):
        if not isinstance(item, dict):
            continue
        hid = item.get("hero_id")
        if not hid:
            continue
        name = catalog.get(int(hid), f"Hero {hid}")
        is_pick = bool(item.get("is_pick"))
        team = item.get("team")
        if team == 0:
            (radiant_picks if is_pick else radiant_bans).append(name)
        elif team == 1:
            (dire_picks if is_pick else dire_bans).append(name)
    if not (radiant_picks or radiant_bans or dire_picks or dire_bans):
        return ""
    lines = [
        f"**Сяйво:** {', '.join(radiant_picks) or '—'}",
        f"бани: {', '.join(radiant_bans) or '—'}",
        f"**Пітьма:** {', '.join(dire_picks) or '—'}",
        f"бани: {', '.join(dire_bans) or '—'}",
    ]
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _embed_size(embed: dict) -> int:
    size = len(embed.get("title") or "") + len(embed.get("description") or "")
    size += len((embed.get("footer") or {}).get("text") or "")
    for field in embed.get("fields") or []:
        size += len(field.get("name") or "") + len(field.get("value") or "")
    return size


def _field(name: str, value: str, inline: bool = True) -> dict[str, object]:
    return {"name": name, "value": _clip(value, EMBED_FIELD_LIMIT), "inline": inline}


def _team_embed_color(name: str, catalog: dict[str, dict[str, str]]) -> int:
    color = team_info(name, catalog).get("color", "")
    return discord_color(color) if color else DEFAULT_EMBED_COLOR


def _thumbnail(name: str, catalog: dict[str, dict[str, str]]) -> dict[str, str] | None:
    logo = team_info(name, catalog).get("logo", "")
    if logo:
        return {"url": logo}
    return None


def _payload(embeds: list[dict], content: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {"embeds": embeds}
    if content:
        body["content"] = content
    return body


def _as_list(result: dict[str, object] | list[dict[str, object]]) -> list[dict[str, object]]:
    return result if isinstance(result, list) else [result]


def _day_title(day: int, finished: bool = False) -> str:
    if finished:
        return f"🏆 ДЕНЬ {day} {TOURNAMENT_NAME.upper()} ЗАВЕРШИВСЯ"
    return f"📅 ДЕНЬ {day} {TOURNAMENT_NAME.upper()}"


def _standings_table(matches: list[dict], day: int, catalog: dict[str, dict[str, str]]) -> str:
    return "".join(
        f"{place}. {team_label(name, catalog)} ({elim_place})\n"
        if elim_place
        else f"{place}. {team_label(name, catalog)} {s_wins}-{s_losses} ({g_wins}-{g_losses})\n"
        for place, name, s_wins, s_losses, g_wins, g_losses, elim_place in standings(matches, day)
    ).strip()


def _hero_stats_description(h_stats: dict[str, list[str]]) -> str:
    sections: list[str] = []
    if h_stats.get("picks"):
        sections.append("🗡 **Топ-10 піків:**\n" + "\n".join(h_stats["picks"]))
    if h_stats.get("bans"):
        sections.append("🚫 **Топ-10 банів:**\n" + "\n".join(h_stats["bans"]))
    return "\n\n".join(sections)


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
) -> dict[str, object] | list[dict[str, object]]:
    """Build Discord webhook payload(s) with embeds. Split only when Discord limits require it."""
    del league, now  # kept in the signature so callers/tests stay stable
    if day is None and match:
        day = tournament_day(match)

    if kind == "tournament_day":
        embed: dict[str, object] = {
            "title": _day_title(day or 0),
            "description": _clip(f"[Maincast]({MAINCAST_DOTA2_URL})\n[Liquipedia]({LIQUIPEDIA_URL})", EMBED_DESC_LIMIT),
            "color": DEFAULT_EMBED_COLOR,
            "url": LIQUIPEDIA_URL,
        }
        return _payload([embed])

    if kind == "tournament_day_no_matches":
        embed = {
            "title": _day_title(day or 0),
            "description": "Сьогодні ігор не заплановано",
            "color": DEFAULT_EMBED_COLOR,
            "url": LIQUIPEDIA_URL,
        }
        return _payload([embed])

    if kind == "day_finished":
        table = _standings_table(matches, day or 0, catalog)
        standings_embed: dict[str, object] = {
            "title": _day_title(day or 0, finished=True),
            "description": _clip(table, EMBED_DESC_LIMIT),
            "color": DEFAULT_EMBED_COLOR,
            "url": LIQUIPEDIA_URL,
        }
        h_stats = hero_stats(matches, day or 0, heroes_catalog or {}, liquipedia_hero_stats=liquipedia_hero_stats)
        if h_stats.get("picks") or h_stats.get("bans"):
            hero_embed: dict[str, object] = {
                "title": "🧙‍♂️ Топ героїв турніру",
                "description": _clip(_hero_stats_description(h_stats), EMBED_DESC_LIMIT),
                "color": DEFAULT_EMBED_COLOR,
            }
            combined = _embed_size(standings_embed) + _embed_size(hero_embed)
            if combined > EMBED_TOTAL_LIMIT:
                return [_payload([standings_embed]), _payload([hero_embed])]
            return _payload([standings_embed, hero_embed])
        standings_embed["footer"] = {"text": "Статистика героїв недоступна"}
        return _payload([standings_embed])

    if not match:
        raise ValueError(f"message kind {kind!r} requires a match")

    series_games = games_in_series(match, matches)
    first_game = series_games[0] if series_games else match
    left_name = (first_game.get("radiant_name") or "Radiant").strip()
    right_name = (first_game.get("dire_name") or "Dire").strip()
    left_label = team_label(left_name, catalog)
    right_label = team_label(right_name, catalog)
    match_id = match.get("match_id")
    duration = format_duration(match.get("duration"))
    last_game_num = game_number(match, matches)
    draft = format_draft(match, heroes_catalog)
    match_url = opendota_match_url(match_id) if match_id else ""

    if kind == "game_finished":
        left_score, right_score = score_up_to(match, matches)
        winner_name = game_winner_name(match)
        embed = {
            "title": f"🎮 ГРА {last_game_num} ЗАВЕРШИЛАСЯ",
            "description": f"{left_label} **{left_score} — {right_score}** {right_label}",
            "color": _team_embed_color(winner_name, catalog),
        }
        if match_url:
            embed["url"] = match_url
        thumb = _thumbnail(winner_name, catalog)
        if thumb:
            embed["thumbnail"] = thumb
        fields = [
            _field("Тривалість", duration),
            _field("Рахунок серії", f"{left_score} — {right_score}"),
            _field("Переможець", team_label(winner_name, catalog)),
        ]
        if match_url:
            fields.append(_field("OpenDota", f"[Матч {match_id}]({match_url})"))
        if draft:
            fields.append(_field("Драфт", draft, inline=False))
        embed["fields"] = fields
        return _payload([embed])

    left_total, right_total = score(match, matches)
    winner_name = left_name if left_total > right_total else right_name
    if is_grand_final:
        title = f"🏆🥇 ПЕРЕМОЖЕЦЬ {TOURNAMENT_NAME.upper()}"
        description = f"{team_label(winner_name, catalog)} (**{left_total} — {right_total}**)"
    else:
        title = "🏆 МАТЧ ЗАВЕРШИВСЯ"
        description = f"{left_label} **{left_total} — {right_total}** {right_label}"

    embed = {
        "title": title,
        "description": description,
        "color": _team_embed_color(winner_name, catalog),
    }
    if match_url:
        embed["url"] = match_url
    thumb = _thumbnail(winner_name, catalog)
    if thumb:
        embed["thumbnail"] = thumb
    fields = [
        _field(f"Тривалість гри {last_game_num}", duration),
        _field("Рахунок серії", f"{left_total} — {right_total}"),
        _field("Переможець", team_label(winner_name, catalog)),
    ]
    if match_url:
        fields.append(_field("OpenDota", f"[Матч {match_id}]({match_url})"))
    if draft:
        fields.append(_field(f"Драфт (гра {last_game_num})", draft, inline=False))
    embed["fields"] = fields
    return _payload([embed])


def publish(webhook_url: str, payload: dict[str, object]) -> None:
    body = dict(payload)
    body["username"] = DISCORD_USERNAME
    post_json(webhook_url, body)


def publish_alert(webhook_url: str, text: str) -> None:
    publish(webhook_url, {"content": text})


def publish_state_push_failure(webhook_url: str) -> None:
    if not webhook_url.strip():
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL for state-push alert")
    publish_alert(webhook_url, STATE_PUSH_FAILURE_ALERT)


def build_test_payload(message_type: str) -> dict[str, object]:
    """Sample webhook payload for workflows/scripts — same builder as production."""
    catalog = load_team_catalog()
    now = int(datetime.now(UTC).timestamp())
    day1 = int(datetime(2026, 8, 13, 12, 0, tzinfo=UTC).timestamp())
    heroes = {1: "Anti-Mage", 2: "Axe", 3: "Bane", 4: "Bloodseeker", 5: "Crystal Maiden", 6: "Drow Ranger"}
    g1 = {
        "match_id": 8000000001,
        "radiant_win": True,
        "start_time": day1,
        "duration": 2535,
        "radiant_team_id": 2163,
        "dire_team_id": 8261500,
        "radiant_name": "Team Liquid",
        "dire_name": "Xtreme Gaming",
        "picks_bans": [
            {"hero_id": 1, "is_pick": True, "team": 0, "order": 0},
            {"hero_id": 2, "is_pick": True, "team": 1, "order": 1},
            {"hero_id": 3, "is_pick": False, "team": 0, "order": 2},
            {"hero_id": 4, "is_pick": False, "team": 1, "order": 3},
            {"hero_id": 5, "is_pick": True, "team": 0, "order": 4},
            {"hero_id": 6, "is_pick": True, "team": 1, "order": 5},
        ],
    }
    g2 = {
        **g1,
        "match_id": 8000000002,
        "radiant_win": False,
        "start_time": day1 + 3600,
        "duration": 2100,
    }
    g3 = {
        **g1,
        "match_id": 8000000003,
        "radiant_win": True,
        "start_time": day1 + 7200,
        "duration": 2535,
    }
    matches = [g1, g2, g3]
    league = {"displayName": TOURNAMENT_NAME, "id": 0}
    kind_map = {
        "day": "tournament_day",
        "tournament_day": "tournament_day",
        "day_no_matches": "tournament_day_no_matches",
        "tournament_day_no_matches": "tournament_day_no_matches",
        "game_finished": "game_finished",
        "series_finished": "series_finished",
        "day_finished": "day_finished",
    }
    kind = kind_map.get(message_type, "tournament_day")
    match = g1 if kind in ("game_finished", "series_finished", "day_finished", "tournament_day") else None
    day = 5 if kind == "tournament_day_no_matches" else 1
    result = message(
        kind,
        league,
        match,
        matches,
        catalog,
        now,
        day=day,
        heroes_catalog=heroes,
    )
    return _as_list(result)[0]
