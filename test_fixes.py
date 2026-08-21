import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from config import MIN_SERIES_PER_DAY, REST_DAYS
from discord_messages import build_test_payload, discord_color, format_draft, load_team_catalog, message
from tournament import (
    game_number,
    get_series_best_of,
    is_series_announced,
    score,
    score_up_to,
    series_key,
    standings,
)


def _payloads(result) -> list[dict]:
    return result if isinstance(result, list) else [result]


class TestBotFixes(unittest.TestCase):
    def test_day8_team_spirit_iron_wing(self):
        # Game 1: Spirit (Rad) vs Iron Wing (Dire), Spirit won
        g1 = {
            "match_id": 8955197224,
            "radiant_win": True,
            "start_time": int(datetime(2026, 8, 20, 7, 19, tzinfo=UTC).timestamp()),
            "duration": 3744,
            "radiant_team_id": 7119388,
            "dire_team_id": 10150413,
            "radiant_name": "Team Spirit",
            "dire_name": "Iron Wing",
            "series_id": 1131001,
        }
        # Game 2: Iron Wing (Rad) vs Spirit (Dire), Spirit won (radiant_win=False)
        g2 = {
            "match_id": 8955247801,
            "radiant_win": False,
            "start_time": int(datetime(2026, 8, 20, 8, 44, tzinfo=UTC).timestamp()),
            "duration": 2646,
            "radiant_team_id": 10150413,
            "dire_team_id": 7119388,
            "radiant_name": "Iron Wing",
            "dire_name": "Team Spirit",
            "series_id": 1131002,
        }
        matches = [g1, g2]

        self.assertEqual(series_key(g1), series_key(g2))
        self.assertEqual(series_key(g1), "day:8:pair:7119388:10150413")

        self.assertEqual(game_number(g1, matches), 1)
        self.assertEqual(score_up_to(g1, matches), (1, 0))

        self.assertEqual(game_number(g2, matches), 2)
        self.assertEqual(score_up_to(g2, matches), (2, 0))
        self.assertEqual(score(g2, matches), (2, 0))

        wins_required = 3 // 2 + 1
        left_wins_g1, right_wins_g1 = score_up_to(g1, matches)
        is_series_end_g1 = left_wins_g1 >= wins_required or right_wins_g1 >= wins_required
        self.assertFalse(is_series_end_g1)

        left_wins_g2, right_wins_g2 = score_up_to(g2, matches)
        is_series_end_g2 = left_wins_g2 >= wins_required or right_wins_g2 >= wins_required
        self.assertTrue(is_series_end_g2)

    def test_is_series_announced_backward_compat(self):
        match = {
            "match_id": 8942993144,
            "radiant_team_id": 9247354,
            "dire_team_id": 10150538,
            "series_id": 1130024,
            "start_time": 1786590206,
        }
        old_day_states = {"done:series:1130024": "announced"}
        self.assertTrue(is_series_announced(old_day_states, match))

        new_day_states = {"done:day:1:pair:9247354:10150538": "announced"}
        self.assertTrue(is_series_announced(new_day_states, match))

        empty_states = {}
        self.assertFalse(is_series_announced(empty_states, match))

    def test_day_finished_check(self):
        d8_base = int(datetime(2026, 8, 20, 6, 0, tzinfo=UTC).timestamp())
        g1 = {"match_id": 1, "radiant_win": True, "start_time": d8_base, "duration": 3600, "radiant_team_id": 10, "dire_team_id": 20}
        g2 = {"match_id": 2, "radiant_win": True, "start_time": d8_base + 4000, "duration": 3600, "radiant_team_id": 10, "dire_team_id": 20}

        day_matches = [g1, g2]
        day_series_map = {}
        for m in day_matches:
            day_series_map.setdefault(series_key(m), []).append(m)

        completed_series = 0
        for sm in day_series_map.values():
            w1, w2 = score(sm[0], day_matches)
            if w1 >= 2 or w2 >= 2:
                completed_series += 1

        self.assertEqual(completed_series, 1)
        self.assertLess(completed_series, MIN_SERIES_PER_DAY[8])

        g3 = {"match_id": 3, "radiant_win": True, "start_time": d8_base + 8000, "duration": 3600, "radiant_team_id": 30, "dire_team_id": 40}
        g4 = {"match_id": 4, "radiant_win": True, "start_time": d8_base + 12000, "duration": 3600, "radiant_team_id": 30, "dire_team_id": 40}
        g5 = {"match_id": 5, "radiant_win": True, "start_time": d8_base + 16000, "duration": 3600, "radiant_team_id": 50, "dire_team_id": 60}
        g6 = {"match_id": 6, "radiant_win": True, "start_time": d8_base + 20000, "duration": 3600, "radiant_team_id": 50, "dire_team_id": 60}
        g7 = {"match_id": 7, "radiant_win": True, "start_time": d8_base + 24000, "duration": 3600, "radiant_team_id": 70, "dire_team_id": 80}
        g8 = {"match_id": 8, "radiant_win": True, "start_time": d8_base + 28000, "duration": 3600, "radiant_team_id": 70, "dire_team_id": 80}
        day_matches.extend([g3, g4, g5, g6, g7, g8])

        day_series_map = {}
        for m in day_matches:
            day_series_map.setdefault(series_key(m), []).append(m)

        completed_series = 0
        for sm in day_series_map.values():
            w1, w2 = score(sm[0], day_matches)
            if w1 >= 2 or w2 >= 2:
                completed_series += 1

        self.assertEqual(completed_series, 4)
        self.assertGreaterEqual(completed_series, MIN_SERIES_PER_DAY[8])

    def test_rest_days(self):
        self.assertIn(5, REST_DAYS)
        self.assertNotIn(8, REST_DAYS)
        self.assertNotIn(1, REST_DAYS)

    def test_existing_tournament_data_standings(self):
        cache_path = Path("states/match_states.json")
        if not cache_path.exists():
            self.skipTest("opendota cache not present")
        with cache_path.open(encoding="utf-8-sig") as f:
            data = json.load(f)
        matches = data["opendota_cache"]["matches"]
        catalog = load_team_catalog()

        for day in range(1, 5):
            st = standings(matches, day)
            self.assertTrue(len(st) > 0)
            names = [row[1] for row in st]
            self.assertEqual(len(names), len(set(names)))

            msg = message(
                "day_finished",
                {"displayName": "The International 2026"},
                matches[0],
                matches,
                catalog,
                int(datetime.now(UTC).timestamp()),
                day=day,
            )
            payloads = _payloads(msg)
            self.assertTrue(payloads[0].get("embeds"))


class TestGrandFinalBestOf(unittest.TestCase):
    def test_bo5_applies_to_whole_last_series(self):
        day11 = int(datetime(2026, 8, 23, 12, 0, tzinfo=UTC).timestamp())
        earlier = {
            "match_id": 1,
            "start_time": day11,
            "radiant_team_id": 10,
            "dire_team_id": 20,
            "radiant_win": True,
        }
        gf1 = {
            "match_id": 2,
            "start_time": day11 + 4000,
            "radiant_team_id": 30,
            "dire_team_id": 40,
            "radiant_win": True,
        }
        gf2 = {
            "match_id": 3,
            "start_time": day11 + 8000,
            "radiant_team_id": 30,
            "dire_team_id": 40,
            "radiant_win": True,
        }
        day_matches = [earlier, gf1, gf2]
        self.assertEqual(get_series_best_of(earlier, day_matches, 11), 3)
        self.assertEqual(get_series_best_of(gf1, day_matches, 11), 5)
        self.assertEqual(get_series_best_of(gf2, day_matches, 11), 5)
        wins_required = 5 // 2 + 1
        self.assertEqual(wins_required, 3)
        self.assertFalse(score_up_to(gf2, [gf1, gf2])[0] >= wins_required)


class TestDiscordEmbeds(unittest.TestCase):
    def setUp(self):
        self.catalog = load_team_catalog()
        self.now = int(datetime(2026, 8, 13, 18, 0, tzinfo=UTC).timestamp())
        self.heroes = {1: "Anti-Mage", 2: "Axe", 3: "Bane", 4: "Bloodseeker"}
        start = int(datetime(2026, 8, 13, 12, 0, tzinfo=UTC).timestamp())
        self.g1 = {
            "match_id": 8000000001,
            "radiant_win": True,
            "start_time": start,
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
            ],
        }

    def test_discord_color(self):
        self.assertEqual(discord_color("#00AEEF"), 0x00AEEF)
        self.assertEqual(discord_color("not-a-color"), 0xD32F2F)

    def test_format_draft(self):
        text = format_draft(self.g1, self.heroes)
        self.assertIn("Сяйво", text)
        self.assertIn("Пітьма", text)
        self.assertIn("Anti-Mage", text)
        self.assertIn("Axe", text)
        self.assertIn("Bane", text)
        self.assertEqual(format_draft({}, self.heroes), "")
        self.assertEqual(format_draft(None, self.heroes), "")

    def test_game_finished_embed(self):
        payload = message(
            "game_finished",
            {"displayName": "The International 2026"},
            self.g1,
            [self.g1],
            self.catalog,
            self.now,
            heroes_catalog=self.heroes,
        )
        embed = payload["embeds"][0]
        self.assertEqual(embed["color"], discord_color(self.catalog["team liquid"]["color"]))
        self.assertIn("opendota.com/matches/8000000001", json.dumps(embed))
        names = [field["name"] for field in embed["fields"]]
        self.assertIn("Тривалість", names)
        self.assertIn("Рахунок серії", names)
        self.assertIn("Переможець", names)
        self.assertIn("Драфт", names)
        self.assertIn("thumbnail", embed)

    def test_game_finished_omits_draft_when_missing(self):
        match = {**self.g1, "picks_bans": []}
        payload = message(
            "game_finished",
            {"displayName": "The International 2026"},
            match,
            [match],
            self.catalog,
            self.now,
            heroes_catalog=self.heroes,
        )
        names = [field["name"] for field in payload["embeds"][0]["fields"]]
        self.assertNotIn("Драфт", names)

    def test_series_finished_embed(self):
        g2 = {**self.g1, "match_id": 8000000002, "start_time": self.g1["start_time"] + 4000, "radiant_win": False}
        payload = message(
            "series_finished",
            {"displayName": "The International 2026"},
            g2,
            [self.g1, g2],
            self.catalog,
            self.now,
            heroes_catalog=self.heroes,
        )
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "🏆 МАТЧ ЗАВЕРШИВСЯ")
        blob = json.dumps(embed, ensure_ascii=False)
        self.assertIn("opendota.com/matches/8000000002", blob)
        self.assertIn("Переможець", blob)

    def test_day_messages(self):
        day = message("tournament_day", {}, None, [], self.catalog, self.now, day=1)
        rest = message("tournament_day_no_matches", {}, None, [], self.catalog, self.now, day=5)
        self.assertIn("ДЕНЬ 1", day["embeds"][0]["title"])
        self.assertIn("ігор не заплановано", rest["embeds"][0]["description"])
        self.assertNotIn("content", day)
        self.assertNotIn("content", rest)

    def test_day_finished_without_hero_stats(self):
        match = {**self.g1, "picks_bans": []}
        payload = message(
            "day_finished",
            {},
            match,
            [match],
            self.catalog,
            self.now,
            day=1,
            heroes_catalog={},
            liquipedia_hero_stats={},
        )
        embed = _payloads(payload)[0]["embeds"][0]
        self.assertIn("ЗАВЕРШИВСЯ", embed["title"])
        self.assertIn("Team Liquid", embed["description"])
        self.assertIn("недоступна", embed.get("footer", {}).get("text", ""))

    def test_day_finished_with_liquipedia_stats(self):
        stats = {"picks": ["1. Anti-Mage — 10 ігор (60% WR, 6-4)"], "bans": ["1. Axe — 8 банів (0 ігор)"]}
        payload = message(
            "day_finished",
            {},
            self.g1,
            [self.g1],
            self.catalog,
            self.now,
            day=1,
            liquipedia_hero_stats=stats,
        )
        embeds = _payloads(payload)[0]["embeds"]
        self.assertGreaterEqual(len(embeds), 2)
        self.assertIn("Anti-Mage", embeds[1]["description"])

    def test_build_test_payload_kinds(self):
        for kind in ("day", "day_no_matches", "game_finished", "series_finished"):
            payload = build_test_payload(kind)
            self.assertIn("embeds", payload)
            self.assertTrue(payload["embeds"][0]["title"])


class TestMatchDetailCache(unittest.TestCase):
    def test_cached_empty_picks_are_not_refetched(self):
        from api.opendota import apply_cached_picks_bans

        match = {"match_id": 123, "picks_bans": []}
        day_states = {"_match_details": {"123": {"picks_bans": []}}}
        self.assertTrue(apply_cached_picks_bans(match, day_states))
        self.assertEqual(match.get("picks_bans"), [])

    def test_applies_cached_picks(self):
        from api.opendota import apply_cached_picks_bans

        match = {"match_id": 123}
        pb = [{"hero_id": 1, "is_pick": True, "team": 0}]
        day_states = {"_match_details": {"123": {"picks_bans": pb}}}
        self.assertTrue(apply_cached_picks_bans(match, day_states))
        self.assertEqual(match["picks_bans"], pb)


if __name__ == "__main__":
    unittest.main()
