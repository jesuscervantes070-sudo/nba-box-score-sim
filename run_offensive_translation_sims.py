"""Simulate every sampled game once with captured offensive components; writes the reusable cache
backtests/offensive_translation_sim_cache_v1.json.gz.  python3 run_offensive_translation_sims.py"""
import gzip
import json

import offensive_translation_capture as otc


def main():
    with gzip.open("backtests/player_input_team_strength_dataset_v1.json.gz", "rt") as f:
        records = json.load(f)["records"]
    otc.run_all(records, n_sims=otc.N_SIMS, workers=8)


if __name__ == "__main__":
    main()
