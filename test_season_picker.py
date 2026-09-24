"""Season picker: supported-season discovery, parsing, confirmation, and hand-off into the season flow."""
import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import loader
import main
import season_picker as sp

SEASONS = loader.available_seasons()
ORDERED = SEASONS[::-1]  # oldest first, as History Sim lists them
EARLIEST, LATEST = ORDERED[0], ORDERED[-1]
MIDDLE = ORDERED[len(ORDERED) // 2]


def run_history(answers, seasons=None):
    """Drive main._run_history_sim with scripted answers; returns (season_flow_calls, printed_text)."""
    calls = []
    out = io.StringIO()
    feed = iter(answers)
    with mock.patch.object(main, "_prompt", lambda text="": next(feed)), \
            mock.patch.object(main, "run_season_flow", lambda *a, **k: calls.append(a[-1])), \
            mock.patch.object(main, "run_multi_season_flow", lambda abbrev, run: calls.append(tuple(run))), \
            contextlib.redirect_stdout(out):
        main._run_history_sim(seasons or SEASONS, {})
    return calls, out.getvalue()


class TestSupportedSeasons(unittest.TestCase):
    def test_discovery_returns_real_seasons_with_every_required_file(self):
        self.assertGreaterEqual(len(SEASONS), 30)
        for season in SEASONS:
            for name in ("rosters.json", "schedule.json", "team_defense.json", "team_conferences.json",
                         "injuries.json", "roster_membership.json"):
                self.assertTrue((loader.CACHE_DIR / season / name).exists(), (season, name))

    def test_ordering_is_newest_first_and_deterministic(self):
        self.assertEqual(SEASONS, sorted(SEASONS, reverse=True))
        self.assertEqual(SEASONS, loader.available_seasons())

    def test_every_advertised_season_loads_teams_and_schedule(self):
        for season in SEASONS:
            self.assertGreaterEqual(len(loader.load_teams(season)), 29, season)
            self.assertTrue(loader.load_schedule(season), season)

    def test_incomplete_cache_season_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("rosters.json", "schedule.json", "team_defense.json", "team_conferences.json",
                         "injuries.json", "roster_membership.json"):
                (root / "2000-01").mkdir(exist_ok=True)
                (root / "2000-01" / name).write_text("{}")
            (root / "1999-00").mkdir()
            (root / "1999-00" / "rosters.json").write_text("{}")
            with mock.patch.object(loader, "CACHE_DIR", root):
                self.assertEqual(loader.available_seasons(), ["2000-01"])


class TestDisplayAndParsing(unittest.TestCase):
    def test_season_names_are_player_facing(self):
        for season in SEASONS:
            self.assertRegex(season, r"^\d{4}-\d{2}$")
        self.assertEqual(sp.season_span(SEASONS), f"{EARLIEST} through {LATEST}")
        self.assertEqual(sp.run_label([EARLIEST]), f"{EARLIEST} NBA Season")
        self.assertEqual(sp.run_label(["1996-97", "1997-98", "1998-99"]), "1996-97 through 1998-99 (3 seasons)")
        self.assertEqual(sp.team_count_text([29]), "29 teams")
        self.assertEqual(sp.team_count_text([29, 30, 30]), "29 to 30 teams")

    def test_parse_valid_and_default(self):
        seasons = ["1996-97", "1997-98", "1998-99"]
        self.assertEqual(sp.parse_season_choice("2", seasons), sp.Choice("season", 1))
        self.assertEqual(sp.parse_season_choice(" 1998-99 ", seasons), sp.Choice("season", 2))
        self.assertEqual(sp.parse_season_choice("", seasons, default_index=0), sp.Choice("default", 0))

    def test_parse_invalid_and_back(self):
        seasons = ["1996-97", "1997-98"]
        for raw in ("0", "3", "-1", "abc", "1996", "1.5", "", "   "):
            self.assertEqual(sp.parse_season_choice(raw, seasons).kind, "invalid", raw)
        for raw in ("b", "B", "back", "q", "quit"):
            self.assertEqual(sp.parse_season_choice(raw, seasons).kind, "back", raw)


class TestHistorySimFlow(unittest.TestCase):
    def test_earliest_middle_latest_reach_the_season_flow(self):
        for season in (EARLIEST, MIDDLE, LATEST):
            index = ORDERED.index(season) + 1
            calls, text = run_history([str(index), "", "1"])
            self.assertEqual(calls, [season])
            self.assertIn(f"{season} NBA Season", text)

    def test_range_hands_off_to_multi_season_flow(self):
        calls, _ = run_history(["1", "3", "1"])
        self.assertEqual(calls, [tuple(ORDERED[:3])])

    def test_selected_season_controls_the_loaded_league(self):
        seen = []
        real_load = loader.load_teams

        def spy(season):
            seen.append(season)
            return real_load(season)
        with mock.patch.object(main, "load_teams", spy):
            calls, _ = run_history([str(ORDERED.index(MIDDLE) + 1), "", "1"])
        self.assertEqual(calls, [MIDDLE])
        self.assertEqual(set(seen), {MIDDLE})

    def test_team_counts_follow_the_season(self):
        _, early_text = run_history(["1", "", "1"])
        _, late_text = run_history([str(len(ORDERED)), "", "1"])
        counts = lambda text: int(re.search(r"\n  (\d+) teams", text).group(1))
        self.assertEqual(counts(early_text), len(loader.load_teams(EARLIEST)))
        self.assertEqual(counts(late_text), len(loader.load_teams(LATEST)))
        self.assertNotEqual(counts(early_text), counts(late_text))

    def test_invalid_input_reprompts_without_crashing(self):
        calls, text = run_history(["abc", "0", "999", "", "1", "", "1"], seasons=SEASONS)
        self.assertEqual(calls, [ORDERED[0]])
        self.assertIn("Please enter a number", text)
        self.assertEqual(len(calls), 1)

    def test_back_from_picker_returns_to_the_mode_menu(self):
        calls, _ = run_history(["b"])
        self.assertEqual(calls, [])

    def test_choose_another_loops_back_to_the_picker(self):
        calls, text = run_history(["1", "", "2", "b"])
        self.assertEqual(calls, [])
        self.assertEqual(text.count("PICK A SEASON"), 2)

    def test_invalid_confirmation_answer_reprompts(self):
        calls, text = run_history(["1", "", "x", "1"])
        self.assertEqual(calls, [ORDERED[0]])
        self.assertIn("Please enter 1 or 2", text)

    def test_season_that_fails_to_load_returns_cleanly(self):
        def broken(season):
            raise FileNotFoundError("rosters.json")
        with mock.patch.object(main, "load_teams", broken):
            calls, text = run_history(["1", "", "b"])
        self.assertEqual(calls, [])
        self.assertIn("Can't load", text)


class TestNoHiddenDefaultSeason(unittest.TestCase):
    def test_main_never_relies_on_the_loader_default_season(self):
        import inspect
        source = "\n".join(line for line in inspect.getsource(main).splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("DEFAULT_SEASON", source)
        for name in ("load_teams", "load_schedule", "load_roster_membership", "load_team_coaches",
                     "load_league_pace_variation", "load_real_best_record"):
            self.assertIsNone(re.search(rf"\b{name}\(\s*\)", source), name)

    def test_title_range_comes_from_the_available_seasons(self):
        out = io.StringIO()
        with mock.patch.object(main, "_prompt", lambda text="": ""), contextlib.redirect_stdout(out):
            main.print_title(["2001-02", "2000-01"])
        self.assertIn("2000-01 through 2001-02", out.getvalue())


if __name__ == "__main__":
    unittest.main()
