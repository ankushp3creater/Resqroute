"""Source-specific severity mappings (L-A1, AC-A8).

IMPORTANT: district-wise WARNING colour codes and district-wise NOWCAST colour codes must NOT share a
mapping. These tables are intentionally EMPTY: no official IMD code table has been verified yet, and
inventing one is prohibited. Fill them in ONLY from official IMD documentation. Any unmapped code
normalises to "unknown" (never to "green").
"""
from __future__ import annotations

from typing import Any, Dict

# feed name -> {original source value -> normalised severity ("green"|"yellow"|"orange"|"red")}
SEVERITY_MAPS: Dict[str, Dict[Any, str]] = {
    "district_warning": {},   # TODO(verified IMD docs): fill from the official district-warning legend
    "district_nowcast": {},   # TODO(verified IMD docs): nowcast has its OWN legend - do not copy the above
}


def map_severity(feed: str, original_value: Any) -> str:
    return SEVERITY_MAPS.get(feed, {}).get(original_value, "unknown")
