"""IMD district name/ID -> boundary district_id crosswalk (F6, AC-B6).

Intentionally empty until verified against IMD's own district identifiers. Do NOT fuzzy-match: a wrong
association would put a warning on the wrong district. Example (format only, not data):
    CROSSWALK = {"<IMD district id or exact name>": "HR-058"}
"""
from __future__ import annotations

from typing import Dict, Optional

CROSSWALK: Dict[str, str] = {}


def to_district_id(imd_key: str) -> Optional[str]:
    return CROSSWALK.get(imd_key)
