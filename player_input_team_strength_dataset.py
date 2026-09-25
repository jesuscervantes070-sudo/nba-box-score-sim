"""PLAYER INPUTS -> TEAM STRENGTH DIAGNOSTIC V1: dataset builder.

One record per real game: the two teams' PREGAME_EXPECTED player state (exactly what the detailed
engine is given -- every field is read from a leakage-audited `HistoricalGameSnapshot`), the real
final scores as targets, and, for a deterministic subset of evaluation games, the detailed
simulator's own expected scores. Nothing here uses team net rating, standings, Elo or any outcome
as a feature; `net_rating_*` is stored for comparison only.

    python3 player_input_team_strength_dataset.py <season> [n_sims]   -> backtests/_pits_<season>.json
python3 player_input_team_strength_dataset.py merge                 -> backtests/player_input_team_strength_dataset_v1.json.gz
(`merge` also folds in the optional probe-only player names written by player_input_impact_probe_dataset.py and
assigns each game its chronological fold.)
"""
import gzip
import hashlib
import json
import sys
import time
from pathlib import Path

import historical_game_outcome as hgo
import historical_game_snapshot as hgs
import historical_predictive_backtest as hpb
from detailed_game import simulate_detailed_game, DetailedGameSimulationFault

SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]
TRUTH_SEASONS = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
SAMPLE_MOD, SAMPLE_RESIDUE = 5, 3   # ~20% of each season's games, chosen by hash of game_id
SIM_MOD, SIM_RESIDUE = 2, 0         # half of the sampled games also get detailed-engine simulations
FEATURE_VERSION = "pits-v1"

OFFENSE_FIELDS = ("rim_finishing_shrunk_rate", "floater_short_mid_shrunk_rate", "midrange_shrunk_rate",
                  "three_point_shrunk_rate", "free_throw_shrunk_rate", "rim_access_creation_shrunk_rate",
                  "foul_drawing_shrunk_rate", "passing_accuracy_ast_pct", "playmaking_vision_shrunk_rate",
                  "ball_security_error_rate")
DEFENSE_FIELDS = ("poa_containment_shrunk_rate", "rim_protection_suppression_rate", "defensive_playmaking_per36",
                  "foul_discipline_shrunk_rate")
REBOUND_FIELDS = ("offensive_rebounding_shrunk_rate", "defensive_rebounding_shrunk_rate")
TENDENCY_FIELDS = ("drive_aggression", "pass_vs_shoot", "three_point_preference", "midrange_preference",
                   "pullup_vs_catch")
ROLE_FIELDS = ("role_off_initiation", "role_off_finishing", "role_off_spacing")
PLAYER_FIELDS = OFFENSE_FIELDS + DEFENSE_FIELDS + REBOUND_FIELDS + TENDENCY_FIELDS + ROLE_FIELDS
FORBIDDEN_FEATURE_KEYS = ("net_rating", "home_score", "away_score", "margin", "team", "player_id", "win")


def frozen_holdout_ids():
    with open("backtests/backtest_v1_holdout_game_ids.json") as f:
        return set(json.load(f)["game_ids"])


def sample_game_ids(season: str):
    with open(Path("cache") / season / "schedule.json") as f:
        games = json.load(f)["games"]
    holdout = frozen_holdout_ids()
    picked = [g["game_id"] for g in games
              if int(hashlib.sha256(g["game_id"].encode()).hexdigest(), 16) % SAMPLE_MOD == SAMPLE_RESIDUE
              and g["game_id"] not in holdout]
    return sorted(picked)


def is_sim_game(game_id: str) -> bool:
    return int(hashlib.sha256(("sim|" + game_id).encode()).hexdigest(), 16) % SIM_MOD == SIM_RESIDUE


def team_state(team_snapshot) -> list:
    """Compact per-player rows: expected minutes, primary-five flag, availability, and every engine field.
    No player id and no team name -- identity cannot reach a primary model."""
    rows = []
    for p in team_snapshot.players:
        profile = p.simulation_profile
        rows.append({"minutes": p.expected_minutes, "primary": bool(p.is_primary_five), "status": p.availability_status,
                     "f": [getattr(profile, name) for name in PLAYER_FIELDS]})
    return rows


def simulate_summary(snapshot, n_sims: int) -> dict:
    home_id, away_id, home_five, away_five, profiles = hgs.snapshot_to_engine_input(snapshot)
    home, away = [], []
    for i in range(n_sims):
        seed = int(hashlib.sha256(f"pits-sim|{snapshot.game_id}|{i}".encode()).hexdigest()[:16], 16)
        try:
            r = simulate_detailed_game(home_id, away_id, home_five, away_five, profiles, rng_seed=seed)
        except DetailedGameSimulationFault:
            continue
        home.append(r.final_home_score)
        away.append(r.final_away_score)
    if not home:
        return {}
    margins = [h - a for h, a in zip(home, away)]
    mean = sum(margins) / len(margins)
    return {"n_sims": len(home), "home_score": sum(home) / len(home), "away_score": sum(away) / len(away),
            "margin": mean, "home_win_prob": sum(1 for m in margins if m > 0) / len(margins)}


def build_record(game_id: str, season: str, n_sims: int, simulate: bool):
    outcome = hgo.get_game_outcome(game_id, season)
    if outcome is None:
        return None
    snap = hgs.build_historical_game_snapshot(game_id, season, TRUTH_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
    hgs.audit_snapshot_temporal_safety(snap)
    record = {
        "game_id": game_id, "season": season, "date": snap.game_date, "feature_version": FEATURE_VERSION,
        "mode": snap.mode,
        "home": team_state(snap.home_team_snapshot), "away": team_state(snap.away_team_snapshot),
        "target": {"home_score": outcome.home_score, "away_score": outcome.away_score},
        "context_only": {
            "home_team": snap.home_team, "away_team": snap.away_team,
            "net_rating_home": hpb._team_net_rating_as_of(snap.home_team, snap.game_date, season),
            "net_rating_away": hpb._team_net_rating_as_of(snap.away_team, snap.game_date, season),
        },
    }
    if simulate:
        record["detailed_sim"] = simulate_summary(snap, n_sims)
    return record


def main(season: str, n_sims: int):
    t0 = time.time()
    ids = sample_game_ids(season)
    records = []
    for i, gid in enumerate(ids):
        simulate = is_sim_game(gid) and season != SEASONS[0]
        try:
            rec = build_record(gid, season, n_sims, simulate)
        except Exception as e:
            print("skip", gid, type(e).__name__, e, flush=True)
            continue
        if rec is not None:
            records.append(rec)
        if i % 10 == 0:
            print(season, i, len(ids), round(time.time() - t0), flush=True)
    with open(f"backtests/_pits_{season}.json", "w") as f:
        json.dump({"season": season, "n_sims": n_sims, "records": records}, f)
    print("done", season, len(records), round(time.time() - t0), flush=True)


DATASET_PATH = Path("backtests/player_input_team_strength_dataset_v1.json.gz")


def merge():
    import player_input_team_strength_diagnostic as d
    records = d.load_records(from_parts=True)
    dates, seasons = [r["date"] for r in records], [r["season"] for r in records]
    fold = {}
    for k, block in enumerate(d.expanding_blocks(dates, seasons, SEASONS[1])):
        for i in block:
            fold[i] = f"test_block_{k + 1}"
    names = {}
    for season in SEASONS:
        path = Path(f"backtests/_pits_names_{season}.json")
        if path.exists():
            names.update(json.load(open(path)))
    out = []
    for i, r in enumerate(records):
        out.append({**r, "fold": fold.get(i, "train_only"), "probe_only_names": names.get(r["game_id"])})
    payload = {"feature_version": FEATURE_VERSION, "player_fields": list(PLAYER_FIELDS), "seasons": SEASONS,
               "truth_seasons": TRUTH_SEASONS, "n_records": len(out),
               "sample_rule": f"sha256(game_id) % {SAMPLE_MOD} == {SAMPLE_RESIDUE}, frozen holdout excluded", "records": out}
    with gzip.open(DATASET_PATH, "wt") as f:
        json.dump(payload, f)
    print("wrote", DATASET_PATH, len(out))


if __name__ == "__main__":
    if sys.argv[1] == "merge":
        merge()
    else:
        main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40)
