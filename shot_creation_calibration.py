"""
Loader for the shot-creation-internals calibration artifact
(shot_creation_calibration.json) -- stores BOTH internal components
(`rim_access_creation`, `perimeter_space_creation`) in one file, each
independently calibrated. Reuses rim_protection_calibration.py's generic
engine directly (no new grid-search code), per this phase's own "reuse
calibration infra" instruction.
"""
import json
from pathlib import Path
from typing import Optional

CALIBRATION_PATH = Path(__file__).parent / "shot_creation_calibration.json"


def load_calibration() -> dict:
    if not CALIBRATION_PATH.exists():
        return {}
    try:
        with open(CALIBRATION_PATH) as f:
            return json.load(f).get("components", {})
    except (json.JSONDecodeError, OSError):
        return {}


def get_calibrated_params(component: str) -> Optional[dict]:
    return load_calibration().get(component)


def save_calibration(components: dict, version: str = "v1") -> None:
    payload = {"calibration_version": version, "components": components}
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)
