"""
Loader for the shot-zone calibration artifact (shot_zone_calibration.json)
-- a SEPARATE artifact from player_ability_calibration.json/_v2.json AND
from ball_security_calibration.json; never touches either. Same safe-
fallback contract as both: a missing/malformed artifact or attribute
returns None, never raises, never invents a value.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

CALIBRATION_PATH = Path(__file__).parent / "shot_zone_calibration.json"


@dataclass(frozen=True)
class CalibratedShotZoneParams:
    lambda_: float
    M: float
    exposure_unit: str
    weighted_mae: float
    weighted_rmse: float
    n_observations: int
    status: str
    calibration_version: str


def load_calibration() -> Dict[str, CalibratedShotZoneParams]:
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
            result[attr] = CalibratedShotZoneParams(
                lambda_=entry["lambda"], M=entry["M"], exposure_unit=entry["exposure_unit"],
                weighted_mae=entry["weighted_mae"], weighted_rmse=entry["weighted_rmse"],
                n_observations=entry["n_observations"], status=entry["status"],
                calibration_version=version,
            )
        except KeyError:
            continue
    return result


def get_calibrated_params(attribute: str) -> Optional[CalibratedShotZoneParams]:
    return load_calibration().get(attribute)


def save_calibration(results: Dict[str, dict], version: str = "v1") -> None:
    """results: {attribute: {"lambda","M","exposure_unit","weighted_mae","weighted_rmse","n_observations","status"}}"""
    payload = {"calibration_version": version, "attributes": results}
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)
