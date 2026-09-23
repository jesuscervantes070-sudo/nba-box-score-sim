"""Runner: python3 run_three_point_resolution_diagnostic.py <label> [n_sims]
Writes backtests/three_point_resolution_<label>.json using ONLY the locked development large/extreme
sample (never the frozen holdout)."""
import json
import sys
import time
from pathlib import Path

import large_extreme_matchup_diagnostic as led
import three_point_resolution_diagnostic as tpd
import historical_game_snapshot as hgs
import historical_game_outcome as hgo
import historical_predictive_backtest as hpb
import margin_compression_diagnostic as mcd
import shot_zone_ingestion as szi


def real_player_three_point() -> dict:
    out = {}
    for row in szi.load_shot_zones(tpd.SEASON).values():
        fga = sum(row.get(k, 0) or 0 for k in ("above_break3_fga", "left_corner3_fga", "right_corner3_fga"))
        fgm = sum(row.get(k, 0) or 0 for k in ("above_break3_fgm", "left_corner3_fgm", "right_corner3_fgm"))
        out[row["player_name"]] = (fgm, fga)
    return out


def main(label: str, n_sims: int):
    t0 = time.time()
    sample = led.load_or_create_large_extreme_sample()
    game_ids = sample["game_ids"]
    real_players = real_player_three_point()
    games, all_player_rows = [], {}
    for gid in game_ids:
        snap = hgs.build_historical_game_snapshot(gid, tpd.SEASON, tpd.ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        attempts = tpd.simulate_attempts(home_id, away_id, home_five, away_five, profiles, gid, n_sims)
        home = tpd.side_summary(attempts, home_five, profiles)
        away = tpd.side_summary(attempts, away_five, profiles)
        home_nr = hpb._team_net_rating_as_of(snap.home_team, snap.game_date, tpd.SEASON)
        away_nr = hpb._team_net_rating_as_of(snap.away_team, snap.game_date, tpd.SEASON)
        home_strong = home_nr >= away_nr
        real = tpd.real_game_three_point_pct(gid)
        strong_s, weak_s = (home, away) if home_strong else (away, home)
        strong_t, weak_t = (snap.home_team, snap.away_team) if home_strong else (snap.away_team, snap.home_team)
        real_gap = (real[strong_t] - real[weak_t]) if real[strong_t] is not None and real[weak_t] is not None else None
        games.append({
            "game_id": gid, "strength_diff": abs(home_nr - away_nr), "strong_team": strong_t, "weak_team": weak_t,
            "real_3pt_gap": real_gap,
            "input_primary_five_gap": strong_s["primary_five_mean_rate"] - weak_s["primary_five_mean_rate"],
            "input_attempt_weighted_gap": strong_s["attempt_weighted_input_rate"] - weak_s["attempt_weighted_input_rate"],
            "expected_make_prob_gap": strong_s["mean_make_probability"] - weak_s["mean_make_probability"],
            "sim_3pt_gap": strong_s["pct"] - weak_s["pct"],
            "strong": strong_s, "weak": weak_s,
        })
        for five, side in ((home_five, "HOME"), (away_five, "AWAY")):
            for pid in five:
                all_player_rows[pid] = profiles[pid].three_point_shrunk_rate
    def avg(key):
        return tpd._mean([g[key] for g in games if g[key] is not None])
    summary = {k: avg(k) for k in ("real_3pt_gap", "input_primary_five_gap", "input_attempt_weighted_gap",
                                   "expected_make_prob_gap", "sim_3pt_gap")}
    summary["mean_league_sim_3pt_pct"] = tpd._mean([g["strong"]["pct"] for g in games] + [g["weak"]["pct"] for g in games])
    summary["mean_share_by_shooter_rank"] = [
        tpd._mean([g[side]["share_by_shooter_rank"][i] for g in games for side in ("strong", "weak")]) for i in range(5)]
    curves = {
        "catch_and_shoot_open": tpd.response_curve(),
        "pull_up_open": tpd.response_curve(release_mode=tpd.sr.ReleaseMode.PULL_UP),
        "catch_and_shoot_tight": tpd.response_curve(contest=tpd.sr.ContestBucket.TIGHT),
    }
    out = {"label": label, "n_sims": n_sims, "n_games": len(games), "summary": summary, "games": games,
           "engine_input_distribution": tpd.quantiles(list(all_player_rows.values())),
           "response_curves": curves, "runtime_seconds": time.time() - t0}
    Path("backtests").mkdir(exist_ok=True)
    with open(f"backtests/three_point_resolution_{label}.json", "w") as f:
        json.dump(out, f, indent=2, sort_keys=True, default=str)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 60)
