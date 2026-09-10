"""
Loader for the poa_containment calibration artifact. Reuses
rim_protection_calibration.py's generic engine directly (no new
grid-search code), per this phase's efficiency instruction.
"""
import json
from pathlib import Path
from typing import Optional

CALIBRATION_PATH = Path(__file__).parent / "poa_containment_calibration.json"


def load_calibration() -> Optional[dict]:
    if not CALIBRATION_PATH.exists():
        return None
    try:
        with open(CALIBRATION_PATH) as f:
            return json.load(f).get("poa_containment")
    except (json.JSONDecodeError, OSError):
        return None


def get_calibrated_params() -> Optional[dict]:
    return load_calibration()


def save_calibration(entry: dict, version: str = "v1") -> None:
    payload = {"calibration_version": version, "poa_containment": entry}
    with open(CALIBRATION_PATH, "w") as f:
        json.dump(payload, f, indent=2)
