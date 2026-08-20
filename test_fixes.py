import unittest
from datetime import datetime, UTC, timedelta
import json
import main

class TestBotFixes(unittest.TestCase):
    def test_day8_team_spirit_iron_wing(self):
        # Game 1: Spirit (Rad) vs Iron Wing (Dire), Spirit won
        g1 = {
            "match_id": 8955197224,
            "radiant_win": True,
            "start_time": int(datetime(2026, 8, 20, 7, 19, tzinfo=UTC).timestamp()), # Day 8
            "duration": 3744, # 62:24
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
            "start_time": int(datetime(2026, 8, 20, 8, 44, tzinfo=UTC).timestamp()), # Day 8
            "duration": 2646, # 44:06
            "radiant_team_id": 10150413,
            "dire_team_id": 7119388,
            "radiant_name": "Iron Wing",
            "dire_name": "Team Spirit",
            "series_id": 1131002,
        }
        matches = [g1, g2]
        
        # Check series_key
        self.assertEqual(main.series_key(g1), main.series_key(g2))
        self.assertEqual(main.series_key(g1), "day:8:pair:7119388:10150413")
        
        # Check game 1
        self.assertEqual(main.game_number(g1, matches), 1)
        self.assertEqual(main.score_up_to(g1, matches), (1, 0))
        
        # Check game 2
        self.assertEqual(main.game_number(g2, matches), 2)
        self.assertEqual(main.score_up_to(g2, matches), (2, 0))
        self.assertEqual(main.score(g2, matches), (2, 0))
        
        # Bo3 required wins
        wins_required = 3 // 2 + 1 # 2
        left_wins_g1, right_wins_g1 = main.score_up_to(g1, matches)
        is_series_end_g1 = left_wins_g1 >= wins_required or right_wins_g1 >= wins_required
        self.assertFalse(is_series_end_g1)
        
        left_wins_g2, right_wins_g2 = main.score_up_to(g2, matches)
        is_series_end_g2 = left_wins_g2 >= wins_required or right_wins_g2 >= wins_required
        self.assertTrue(is_series_end_g2)

    def test_is_series_announced_backward_compat(self):
        match = {
            "match_id": 8942993144,
            "radiant_team_id": 9247354,
            "dire_team_id": 10150538,
            "series_id": 1130024,
            "start_time": 1786590206, # Day 1
        }
        # Old state with done:series:1130024
        old_day_states = {"done:series:1130024": "announced"}
        self.assertTrue(main.is_series_announced(old_day_states, match))
        
        # New state with done:day:1:pair:9247354:10150538
        new_day_states = {"done:day:1:pair:9247354:10150538": "announced"}
        self.assertTrue(main.is_series_announced(new_day_states, match))
        
        # Unannounced state
        empty_states = {}
        self.assertFalse(main.is_series_announced(empty_states, match))

    def test_day_finished_check(self):
        d8_base = int(datetime(2026, 8, 20, 6, 0, tzinfo=UTC).timestamp())
        g1 = {"match_id": 1, "radiant_win": True, "start_time": d8_base, "duration": 3600, "radiant_team_id": 10, "dire_team_id": 20}
        g2 = {"match_id": 2, "radiant_win": True, "start_time": d8_base + 4000, "duration": 3600, "radiant_team_id": 10, "dire_team_id": 20}
        
        # Only series 1 finished
        day_matches = [g1, g2]
        day_series_map = {}
        for m in day_matches:
            day_series_map.setdefault(main.series_key(m), []).append(m)
        
        completed_series = 0
        for sm in day_series_map.values():
            w1, w2 = main.score(sm[0], day_matches)
            if w1 >= 2 or w2 >= 2:
                completed_series += 1
        
        self.assertEqual(completed_series, 1)
        self.assertLess(completed_series, main.MIN_SERIES_PER_DAY[8])
        
        # Now series 2, 3, 4 plays
        g3 = {"match_id": 3, "radiant_win": True, "start_time": d8_base + 8000, "duration": 3600, "radiant_team_id": 30, "dire_team_id": 40}
        g4 = {"match_id": 4, "radiant_win": True, "start_time": d8_base + 12000, "duration": 3600, "radiant_team_id": 30, "dire_team_id": 40}
        g5 = {"match_id": 5, "radiant_win": True, "start_time": d8_base + 16000, "duration": 3600, "radiant_team_id": 50, "dire_team_id": 60}
        g6 = {"match_id": 6, "radiant_win": True, "start_time": d8_base + 20000, "duration": 3600, "radiant_team_id": 50, "dire_team_id": 60}
        g7 = {"match_id": 7, "radiant_win": True, "start_time": d8_base + 24000, "duration": 3600, "radiant_team_id": 70, "dire_team_id": 80}
        g8 = {"match_id": 8, "radiant_win": True, "start_time": d8_base + 28000, "duration": 3600, "radiant_team_id": 70, "dire_team_id": 80}
        day_matches.extend([g3, g4, g5, g6, g7, g8])
        
        day_series_map = {}
        for m in day_matches:
            day_series_map.setdefault(main.series_key(m), []).append(m)
            
        completed_series = 0
        for sm in day_series_map.values():
            w1, w2 = main.score(sm[0], day_matches)
            if w1 >= 2 or w2 >= 2:
                completed_series += 1
                
        self.assertEqual(completed_series, 4)
        self.assertGreaterEqual(completed_series, main.MIN_SERIES_PER_DAY[8])

    def test_rest_days(self):
        self.assertIn(5, main.REST_DAYS)
        self.assertNotIn(8, main.REST_DAYS)
        self.assertNotIn(1, main.REST_DAYS)

    def test_existing_tournament_data_standings(self):
        with open("states/match_states.json", encoding="utf-8-sig") as f:
            data = json.load(f)
        matches = data["opendota_cache"]["matches"]
        catalog = main.load_team_catalog()
        
        # Check standings and messages for day 1 to 4
        for day in range(1, 5):
            st = main.standings(matches, day)
            self.assertTrue(len(st) > 0)
            names = [row[1] for row in st]
            self.assertEqual(len(names), len(set(names))) # unique teams
            
            # Test day finished message formatting
            msg = main.message(
                "day_finished",
                {"displayName": "The International 2026"},
                matches[0],
                matches,
                catalog,
                int(datetime.now(UTC).timestamp()),
                day=day,
            )
            self.assertTrue(bool(msg))

if __name__ == "__main__":
    unittest.main()
