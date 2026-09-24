"""Allocation / scoring side-effect measurement over the frozen holdout games (measurement only).
Usage: python3 holdout_allocation_measure.py <label> [n_sims]   -> backtests/holdout_allocation_<label>.json
Run once per engine version (e.g. a checkout before and after the allocation fixes) to compare them;
per-player counts come from the event stream, so no engine code is touched."""
import hashlib
import json
import statistics as st
import sys

import historical_game_snapshot as hgs
import historical_predictive_backtest as hpb
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault
from possession_state import SpatialZone
from action_opportunity import PERIMETER_ZONES

ALL_SEASONS = ["2021-22", "2022-23", "2023-24"]


def _is_three(zone_name):
    zone = SpatialZone(zone_name)
    return zone in PERIMETER_ZONES or zone == SpatialZone.BACKCOURT


def herfindahl(shares):
    return sum(s * s for s in shares)


def team_row(counts, profiles, five, n_sims, team_scores, turnovers, possessions):
    fga = {p: counts[p]["fga"] for p in five}
    tpa = {p: counts[p]["3pa"] for p in five}
    total_fga = sum(fga.values()) or 1
    total_3pa = sum(tpa.values()) or 1
    pts = sorted((counts[p]["pts"] / n_sims for p in five), reverse=True)
    return {
        "team_3pa": sum(tpa.values()) / n_sims,
        "team_3pt_pct": (sum(counts[p]["3pm"] for p in five) / sum(tpa.values())) if sum(tpa.values()) else None,
        "top1_3pa_share": max(tpa.values()) / total_3pa, "hhi_3pa": herfindahl([v / total_3pa for v in tpa.values()]),
        "top1_fga_share": max(fga.values()) / total_fga,
        "shooter_weighted_3pt_ability": sum(profiles[p].three_point_shrunk_rate * tpa[p] for p in five) / total_3pa,
        "fg_points_rank1": pts[0], "fg_points_rank2": pts[1], "fg_points_rank3": pts[2],
        "mean_team_score": st.mean(team_scores), "team_score_sd": st.pstdev(team_scores),
        "turnovers_per_100_poss": turnovers / possessions * 100 if possessions else None,
    }


def main(label, n_sims):
    with open("backtests/backtest_v1_holdout_game_ids.json") as f:
        game_ids = json.load(f)["game_ids"]
    rows = []
    for gid in game_ids:
        try:
            snap = hgs.build_historical_game_snapshot(gid, hpb.BACKTEST_SEASON, ALL_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception as e:
            print("skip", gid, e, flush=True)
            continue
        home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snap)
        counts = {p: {"fga": 0, "fgm": 0, "3pa": 0, "3pm": 0, "pts": 0} for p in home_five + away_five}
        scores = {"HOME": [], "AWAY": []}
        tov = {"HOME": 0, "AWAY": 0}
        poss = {"HOME": 0, "AWAY": 0}
        valid = 0
        for i in range(n_sims):
            seed = int(hashlib.sha256(f"alloc-measure|{gid}|{i}".encode()).hexdigest()[:16], 16)
            try:
                r = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
            except DetailedGameSimulationFault:
                continue
            valid += 1
            scores["HOME"].append(r.final_home_score)
            scores["AWAY"].append(r.final_away_score)
            for rec in r.possessions:
                side = "HOME" if rec.offense_team_id == home_id else "AWAY"
                poss[side] += 1
                tov[side] += rec.provisional_deltas.turnovers + rec.provisional_deltas.team_turnovers
                zone = None
                for e in rec.events:
                    t = e.event_type.value
                    if t == "SHOT_RELEASED":
                        zone = e.zone
                    elif t == "SHOT_RESOLVED" and e.primary_player_id in counts and zone is not None:
                        c = counts[e.primary_player_id]
                        three = _is_three(zone)
                        c["fga"] += 1
                        c["3pa"] += 1 if three else 0
                        if e.metadata.get("made"):
                            c["fgm"] += 1
                            c["3pm"] += 1 if three else 0
                            c["pts"] += 3 if three else 2
        if not valid:
            continue
        rows.append({"game_id": gid,
                     "home": team_row(counts, profiles, home_five, valid, scores["HOME"], tov["HOME"], poss["HOME"]),
                     "away": team_row(counts, profiles, away_five, valid, scores["AWAY"], tov["AWAY"], poss["AWAY"])})
        print(gid, len(rows), flush=True)
    keys = [k for k in rows[0]["home"] if rows[0]["home"][k] is not None]
    summary = {k: st.mean(v for r in rows for v in (r["home"][k], r["away"][k]) if v is not None) for k in keys}
    summary["n_games"] = len(rows)
    with open(f"backtests/holdout_allocation_{label}.json", "w") as f:
        json.dump({"label": label, "n_sims": n_sims, "summary": summary, "games": rows}, f, indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40)
