"""Discord webhook payloads: embeds and publish."""

from __future__ import annotations

import json
import sys

from config import (
    DEFAULT_EMBED_COLOR,
    DISCORD_USERNAME,
    LIQUIPEDIA_URL,
    LOGO_BASE_URL,
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
    "⚠️ Не вдалося зберегти стан матчів у Git. Наступний запуск може повторно опублікувати події."
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
        f"Бани: {', '.join(radiant_bans) or '—'}",
        f"**Пітьма:** {', '.join(dire_picks) or '—'}",
        f"Бани: {', '.join(dire_bans) or '—'}",
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
    logo = team_info(name, catalog).get("logo", "").strip()
    if not logo:
        return None
    if logo.startswith(("http://", "https://")):
        url = logo
    else:
        url = f"{LOGO_BASE_URL.rstrip('/')}/{logo.lstrip('/')}"
    return {"url": url}


def _payload(embeds: list[dict], content: str | None = None) -> dict[str, object]:
    body: dict[str, object] = {"embeds": embeds}
    if content:
        body["content"] = content
    return body


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


def _match_result_payload(
    *,
    title: str,
    description: str,
    winner_name: str,
    catalog: dict[str, dict[str, str]],
    match: dict,
    duration_field: str,
    duration: str,
    left_score: int,
    right_score: int,
    draft: str,
    draft_field: str,
) -> dict[str, object]:
    match_id = match.get("match_id")
    match_url = opendota_match_url(match_id) if match_id else ""
    embed: dict[str, object] = {
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
        _field(duration_field, duration),
        _field("Рахунок серії", f"{left_score} — {right_score}"),
        _field("Переможець", team_label(winner_name, catalog)),
    ]
    if match_url:
        fields.append(_field("OpenDota", f"[Матч {match_id}]({match_url})"))
    if draft:
        fields.append(_field(draft_field, draft, inline=False))
    embed["fields"] = fields
    return _payload([embed])


def message(
    kind: str,
    match: dict | None,
    matches: list[dict],
    catalog: dict[str, dict[str, str]],
    is_grand_final: bool = False,
    day: int | None = None,
    heroes_catalog: dict[int, str] | None = None,
    liquipedia_hero_stats: dict[str, list[str]] | None = None,
) -> dict[str, object] | list[dict[str, object]]:
    """Build Discord webhook payload(s) with embeds. Split only when Discord limits require it."""
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
                "title": "🧙‍♂️ Топ-10 героїв турніру",
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
    left_name, right_name = teams(first_game)
    left_name, right_name = left_name.strip(), right_name.strip()
    left_label = team_label(left_name, catalog)
    right_label = team_label(right_name, catalog)
    duration = format_duration(match.get("duration"))
    last_game_num = game_number(match, matches)
    draft = format_draft(match, heroes_catalog)

    if kind == "game_finished":
        left_score, right_score = score_up_to(match, matches)
        winner_name = game_winner_name(match)
        return _match_result_payload(
            title=f"🎮 ГРА {last_game_num} ЗАВЕРШИЛАСЯ",
            description=f"{left_label} **{left_score} — {right_score}** {right_label}",
            winner_name=winner_name,
            catalog=catalog,
            match=match,
            duration_field="Тривалість",
            duration=duration,
            left_score=left_score,
            right_score=right_score,
            draft=draft,
            draft_field="Драфт",
        )

    left_total, right_total = score(match, matches)
    winner_name = left_name if left_total > right_total else right_name
    if is_grand_final:
        title = f"🏆🥇 ПЕРЕМОЖЕЦЬ {TOURNAMENT_NAME.upper()}"
        description = f"{team_label(winner_name, catalog)} (**{left_total} — {right_total}**)"
    else:
        title = "🏆 МАТЧ ЗАВЕРШИВСЯ"
        description = f"{left_label} **{left_total} — {right_total}** {right_label}"
    return _match_result_payload(
        title=title,
        description=description,
        winner_name=winner_name,
        catalog=catalog,
        match=match,
        duration_field=f"Тривалість гри {last_game_num}",
        duration=duration,
        left_score=left_total,
        right_score=right_total,
        draft=draft,
        draft_field=f"Драфт (гра {last_game_num})",
    )


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
