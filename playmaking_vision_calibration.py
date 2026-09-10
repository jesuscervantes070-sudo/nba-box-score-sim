"""
Loader for the playmaking_vision calibration artifact
(playmaking_vision_calibration.json) -- a SEPARATE artifact, never
touches any prior phase's file. Reuses rim_protection_calibration.py's
generic grid-search/evaluate/shrinkage engine directly (per this phase's
own "reuse calibration infra, do not rebuild" instruction) -- those
functions are already attribute-agnostic (take rows_by_season, rate_fn,
weight_fn), so no new grid-search code is written here.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rim_protection_calibration import (  # noqa: F401 -- re-exported, generic engine reused as-is
    evaluate, grid_search_on_train, rank_correlation, _shrunk_rate, _season_year, LAMBDA_GRID, M_GRID,
)

CALIBRATION_PATH = Path(__file__).parent / "playmaking_vision_calibration.json"


@dataclass(frozen=True)
class CalibratedVisionParams:
    lambda_: float
    M: float
    weight: str
    train_weighted_mae: float
    heldout_weighted_mae: float
    heldout_rank_corr: Optional[float]
    n_train_observations: int
    n_heldout_observations: int
    status: str
    calibration_version: str


def load_calibration() -> Optional[CalibratedVisionParams]:
    if not CALIBRATION_PATH.exists():
        return None
    try:
        with open(CALIBRATION_PATH) as f:
            data = json.load(f)
        entry = data["playmaking_vision"]
        return CalibratedVisionParams(
            lambda_=entry["lambda"], M=entry["M"], weight=entry["weight"],
            train_weighted_mae=entry["train_weighted_mae"], heldout_weighted_mae=entry["heldout_weighted_mae"],
            heldout_rank_corr=entry.get("heldout_rank_corr"),
            n_train_observations=entry["n_train_observations"], n_heldout_observations=entry["n_heldout_observations"],
            status=entry["status"], calibration_version=data.get("calibration_version", "unknown"),
        )
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def get_calibrated_params() -> Optional[CalibratedVisionParams]:
    return load_calibration()


def save_calibration(entry: dict, version: str = "v1") -> None:
    payload = {"calibration_version": version, "playmaking_vision": entry}
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)
