"""STRENGTH-NEUTRAL OFFENSIVE STYLE V1: re-run the three-point allocation diagnostic under a named config so
player-level 3PA allocation can be compared before/after. python3 style_neutrality_allocation.py <config> [n_sims]"""
import sys

import run_three_point_allocation_diagnostic as run
import style_neutrality_eval as sne

if __name__ == "__main__":
    name = sys.argv[1]
    sne.apply_config(sne.CONFIGS[name])
    run.main(f"sn_{name}", int(sys.argv[2]) if len(sys.argv) > 2 else 40)
