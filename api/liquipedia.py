"""Liquipedia page extras and hero-statistics parser."""

from __future__ import annotations

import sys
import time
from html.parser import HTMLParser
from urllib.parse import urlencode

from config import (
    LIQUIPEDIA_API,
    LIQUIPEDIA_CACHE_SECONDS,
    LIQUIPEDIA_PAGE,
    LIQUIPEDIA_STATISTICS_PAGE,
    TOURNAMENT_NAME,
    USER_AGENT,
)
from http_client import get_json
from uk_text import hero_ban_line, hero_pick_line


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


def liquipedia_context(states: dict[str, object]) -> dict[str, object]:
    """Fetch the public page at most once per cache window; match tracking must survive a failure."""
    cached = states.get("liquipedia")
    if isinstance(cached, dict) and time.time() - cached.get("fetched_at", 0) < LIQUIPEDIA_CACHE_SECONDS:
        return cached
    query = urlencode({"action": "parse", "page": LIQUIPEDIA_PAGE, "prop": "displaytitle", "format": "json"})
    try:
        data = get_json(f"{LIQUIPEDIA_API}?{query}", {"User-Agent": USER_AGENT, "Accept": "application/json"})
        return {"fetched_at": time.time(), "title": data.get("parse", {}).get("displaytitle", TOURNAMENT_NAME)}
    except RuntimeError as error:
        print(f"Warning: {error}; continuing without Liquipedia extras.", file=sys.stderr)
        return cached if isinstance(cached, dict) else {"fetched_at": time.time()}


def fetch_liquipedia_hero_stats(states: dict[str, object]) -> dict[str, list[str]]:
    """Fetch Top-10 hero statistics directly from Liquipedia Statistics page."""
    cached = states.get("liquipedia_hero_stats")
    if isinstance(cached, dict) and time.time() - cached.get("fetched_at", 0) < LIQUIPEDIA_CACHE_SECONDS:
        return cached.get("data", {})

    query = urlencode({"action": "parse", "page": LIQUIPEDIA_STATISTICS_PAGE, "format": "json"})
    try:
        data = get_json(f"{LIQUIPEDIA_API}?{query}", {"User-Agent": USER_AGENT, "Accept": "application/json"})
        html = data.get("parse", {}).get("text", {}).get("*", "")
        if not html:
            return cached.get("data", {}) if isinstance(cached, dict) else {}

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
                    heroes_data.append(
                        {
                            "hero": hero,
                            "picks": picks,
                            "wins": wins,
                            "losses": losses,
                            "wr": wr,
                            "bans": bans,
                        }
                    )

        if not heroes_data:
            return cached.get("data", {}) if isinstance(cached, dict) else {}

        top_picks = sorted(heroes_data, key=lambda x: (-x["picks"], x["hero"]))[:10]
        top_bans = sorted(heroes_data, key=lambda x: (-x["bans"], x["hero"]))[:10]

        res = {
            "picks": [
                f"{i+1}. {hero_pick_line(h['hero'], h['picks'], h['wr'], h['wins'], h['losses'])}"
                for i, h in enumerate(top_picks)
            ],
            "bans": [
                f"{i+1}. {hero_ban_line(h['hero'], h['bans'], h['picks'], h['wr'], h['wins'], h['losses'])}"
                for i, h in enumerate(top_bans)
            ],
        }
        states["liquipedia_hero_stats"] = {"fetched_at": time.time(), "data": res}
        return res
    except Exception as error:
        print(f"Warning: could not fetch Liquipedia hero stats: {error}", file=sys.stderr)
        return cached.get("data", {}) if isinstance(cached, dict) else {}
