"""Routing: geometry validation and route-record building (F4, I7, L-B2)."""
from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any, Dict, List, Tuple

from backend.models.records import iso


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _valid_pos(p: Any) -> bool:
    if not isinstance(p, (list, tuple)) or len(p) < 2:
        return False
    x, y = p[0], p[1]
    for v in (x, y):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return False
    return -180 <= x <= 180 and -90 <= y <= 90


def parse_and_validate_route_geometry(geom: Any) -> List[Tuple[float, float]]:
    """Accept LineString / MultiLineString with valid coordinates; return flat (lon, lat) list."""
    if not isinstance(geom, dict):
        raise ValueError("geometry must be an object")
    gtype, coords = geom.get("type"), geom.get("coordinates")
    if gtype == "LineString":
        lines = [coords]
    elif gtype == "MultiLineString":
        lines = coords
    else:
        raise ValueError(f"unsupported geometry type {gtype!r}")
    if not isinstance(lines, list):
        raise ValueError("coordinates must be a list")
    out: List[Tuple[float, float]] = []
    for line in lines:
        if not isinstance(line, list):
            raise ValueError("line must be a list of positions")
        for p in line:
            if not _valid_pos(p):
                raise ValueError("invalid coordinate")
            out.append((float(p[0]), float(p[1])))
    if len(out) < 2:
        raise ValueError("geometry needs at least two positions")
    if len(set(out)) < 2:
        raise ValueError("geometry has no extent")
    return out


def build_route_record(*, distance: Any, duration: Any, geometry: Any, provider: str,
                       retrieved_at: datetime, notes: List[str]) -> Dict[str, Any]:
    """Validate one provider route; raises ValueError if it is not usable (never repaired/fabricated)."""
    coords = parse_and_validate_route_geometry(geometry)
    for name, v in (("distance", distance), ("duration", duration)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{name} must be a positive number")
    flat_geometry = {"type": "LineString", "coordinates": [[x, y] for x, y in coords]}
    return {
        "route_id": new_id("rt"),
        "geometry": flat_geometry,
        "distance_m": max(1, int(round(distance))),
        "duration_s": max(1, int(round(duration))),
        "provider": provider,
        "retrieved_at": iso(retrieved_at),
        "provider_notes": list(notes),
    }
