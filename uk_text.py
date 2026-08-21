"""Ukrainian count + noun forms for match stats."""

from __future__ import annotations


def uk_noun(n: int, one: str, few: str, many: str) -> str:
    value = abs(int(n))
    if value % 100 in (11, 12, 13, 14):
        word = many
    else:
        mod = value % 10
        if mod == 1:
            word = one
        elif mod in (2, 3, 4):
            word = few
        else:
            word = many
    return f"{value} {word}"


def games_phrase(n: int) -> str:
    return uk_noun(n, "гра", "ігри", "ігор")


def bans_phrase(n: int) -> str:
    return uk_noun(n, "бан", "бани", "банів")


def hero_pick_line(hero: str, picks: int, wr: float, wins: int, losses: int) -> str:
    return f"{hero} — {games_phrase(picks)} ({round(wr)}% WR, {wins}-{losses})"


def hero_ban_line(hero: str, bans: int, played: int = 0, wr: float = 0, wins: int = 0, losses: int = 0) -> str:
    if played <= 0:
        return f"{hero} — {bans_phrase(bans)} ({games_phrase(0)})"
    return f"{hero} — {bans_phrase(bans)} ({round(wr)}% WR, {wins}-{losses})"
