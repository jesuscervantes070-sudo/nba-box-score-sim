"""Side file for the OPTIONAL impact-metric probe: (canonical player name, expected minutes) per team for the
same sampled games as the main dataset, so prior-season impact metrics can be joined by name.
Names are used only by the probe, never by the primary models.
    python3 player_input_impact_probe_dataset.py <season>   -> backtests/_pits_names_<season>.json"""
import json
import sys

import historical_game_snapshot as hgs
import player_input_team_strength_dataset as ds


def main(season):
    out = {}
    for gid in ds.sample_game_ids(season):
        try:
            snap = hgs.build_historical_game_snapshot(gid, season, ds.TRUTH_SEASONS, mode=hgs.MODE_PREGAME_EXPECTED)
        except Exception:
            continue
        out[gid] = {side: [(p.canonical_name, p.expected_minutes, p.availability_status) for p in ts.players]
                    for side, ts in (("home", snap.home_team_snapshot), ("away", snap.away_team_snapshot))}
    with open(f"backtests/_pits_names_{season}.json", "w") as f:
        json.dump(out, f)
    print("done", season, len(out))


if __name__ == "__main__":
    main(sys.argv[1])
