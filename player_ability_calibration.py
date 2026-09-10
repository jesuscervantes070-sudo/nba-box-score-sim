"""
Loader for the versioned empirical-calibration artifact
(player_ability_calibration.json) -- see that file for the exact
methodology, search ranges, and per-attribute results.

SAFE FALLBACK BY DESIGN: if the artifact file is missing, malformed,
or lacks an entry for a given attribute, callers get None back and
MUST fall back to their own provisional defaults -- this module never
raises to avoid breaking estimation over a missing calibration file,
and never invents a value.

KNOWN LIMITATION, stated plainly: the (lambda, M) pair selected for
each attribute was the minimum-weighted-MAE point across ALL
walk-forward (T, T+1) pairs available, not a further held-out split of
those pairs. Every individual PREDICTION is genuinely out-of-sample
(built only from evidence through T to predict T+1), which is the
real leakage guarantee that matters -- but the PARAMETER SELECTION
itself was not cross-validated against a separate holdout set of
walk-forward pairs, so there is a secondary, smaller overfitting risk
in the exact (lambda, M) chosen. Flagged for a future phase, not
addressed here (small grids, modest sample -- the risk is real but
likely minor).
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

CALIBRATION_PATH = Path(__file__).parent / "player_ability_calibration.json"
CALIBRATION_V2_PATH = Path(__file__).parent / "player_ability_calibration_v2.json"


@dataclass(frozen=True)
class CalibratedAttributeParams:
    lambda_: float
    M: float
    exposure_unit: str
    weighted_mae: float
    weighted_rmse: float
    n_observations: int
    training_range: tuple
    calibration_version: str


def load_calibration() -> Dict[str, CalibratedAttributeParams]:
    """Every calibrated attribute's fitted params, keyed by attribute
    name. Returns {} (never raises) if the artifact is missing or
    malformed -- callers keep using their own provisional defaults."""
    if not CALIBRATION_PATH.exists():
        return {}
    try:
        with open(CALIBRATION_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    version = data.get("calibration_version", "unknown")
    result = {}
    for attr, entry in data.get("attributes", {}).items():
        try:
            result[attr] = CalibratedAttributeParams(
                lambda_=entry["lambda"], M=entry["M"], exposure_unit=entry["exposure_unit"],
                weighted_mae=entry["weighted_mae"], weighted_rmse=entry["weighted_rmse"],
                n_observations=entry["n_observations"],
                training_range=tuple(entry["training_range"]),
                calibration_version=version,
            )
        except KeyError:
            continue  # malformed entry for this one attribute -- skip it, don't fail the whole load
    return result


def get_calibrated_params(attribute: str) -> Optional[CalibratedAttributeParams]:
    """One attribute's calibrated params, or None if not calibrated
    (e.g. ball_security, deliberately excluded this phase) or the
    artifact is unavailable."""
    return load_calibration().get(attribute)


def load_age_adjustments() -> dict:
    """Every calibrated attribute's OPTIONAL age adjustment, from the
    v2 artifact (player_ability_calibration_v2.json) -- see that file
    for the real held-out validation each one is based on. Returns {}
    (never raises) if the v2 file is missing -- v1 behavior (no age
    adjustment at all) is always a safe, valid fallback. An attribute
    present in v1 but absent here, or present with type "none", both
    mean "no adjustment for this attribute" -- a real, tested result
    for offensive_rebounding/defensive_rebounding, not a gap."""
    if not CALIBRATION_V2_PATH.exists():
        return {}
    try:
        with open(CALIBRATION_V2_PATH) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    result = {}
    for attr, entry in data.get("attributes", {}).items():
        adj = entry.get("age_adjustment")
        if adj and adj.get("type") in ("bucket", "linear"):
            result[attr] = adj
    return result


def apply_age_adjustment(attribute: str, raw_prediction: float, age: Optional[float]) -> float:
    """Applies attribute's v2 age adjustment to `raw_prediction` (an
    already lambda/M-shrunk estimate) if one exists and `age` is
    known; otherwise returns `raw_prediction` UNCHANGED -- this is
    always a safe no-op when age adjustment isn't available or isn't
    wanted (see estimate_attribute's apply_age_adjustment=False
    default in player_ability_estimation.py). The correction is
    PREDICTIVE (expected T -> T+1 change), never a retroactive
    discount on the player's own current-season estimate -- see
    player_ability_calibration_v2.json's own methodology note."""
    if age is None:
        return raw_prediction
    adjustments = load_age_adjustments()
    adj = adjustments.get(attribute)
    if adj is None:
        return raw_prediction
    if adj["type"] == "bucket":
        for bucket_key, correction in adj["buckets"].items():
            lo, hi = (float(x) for x in bucket_key.split("-"))
            if lo <= age < hi:
                return raw_prediction - correction
        return raw_prediction
    if adj["type"] == "linear":
        correction = adj["slope_per_year"] * age + adj["intercept"]
        return raw_prediction - correction
    return raw_prediction
