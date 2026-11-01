"""Haryana district boundary dataset: load, validate, expose (F2, I5, I11, E8)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.services.geography.matcher import DistrictIndex, PreparedDistrict

EXPECTED_DISTRICT_COUNT = 23  # spec 5.2: Haryana has 23 districts incl. Hansi
KNOWN_MISSING_DISTRICTS = ["Hansi"]
# Districts whose polygon may include territory that now belongs to Hansi. Which districts lost land
# to Hansi has NOT been verified; this list is configurable and deliberately conservative.
BOUNDARY_UNCERTAIN_DISTRICTS = ["Hisar"]
SOURCE_NAME = "National Water Data Portal (NWDP) / Survey of India (SOI) district boundaries"
HARYANA_BBOX = (73.5, 27.0, 78.5, 31.5)  # sanity bounds: lon_min, lat_min, lon_max, lat_max
COVERAGE_WARNING = (
    "This boundary dataset has 22 of Haryana's 23 districts. The Hansi district boundary is missing, "
    "and the dataset does not represent fully verified current boundaries. Route sections near "
    "affected districts may be assigned to an older district polygon."
)


class GeographyUnavailable(Exception):
    pass


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _ring_error(ring: Any) -> Optional[str]:
    if not isinstance(ring, list) or len(ring) < 4:
        return "ring has fewer than 4 positions"
    for p in ring:
        if not isinstance(p, (list, tuple)) or len(p) < 2 or not _num(p[0]) or not _num(p[1]):
            return "invalid position"
        x, y = p[0], p[1]
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            return "coordinate out of range"
        if not (HARYANA_BBOX[0] <= x <= HARYANA_BBOX[2] and HARYANA_BBOX[1] <= y <= HARYANA_BBOX[3]):
            return "coordinate outside the expected Haryana area"
    if ring[0][0] != ring[-1][0] or ring[0][1] != ring[-1][1]:
        return "ring is not closed"
    return None


def _polygons(geometry: Any) -> List[List[List[tuple]]]:
    if not isinstance(geometry, dict):
        raise ValueError("geometry missing")
    gtype, coords = geometry.get("type"), geometry.get("coordinates")
    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        raise ValueError(f"unsupported geometry type {gtype!r}")
    if not isinstance(polys, list) or not polys:
        raise ValueError("no polygons")
    out = []
    for poly in polys:
        if not isinstance(poly, list) or not poly:
            raise ValueError("empty polygon")
        rings = []
        for ring in poly:
            err = _ring_error(ring)
            if err:
                raise ValueError(err)
            rings.append([(float(p[0]), float(p[1])) for p in ring])
        out.append(rings)
    return out


@dataclass
class DistrictDataset:
    index: DistrictIndex
    by_id: Dict[str, Dict[str, Any]]
    uncertain_ids: set
    metadata: Dict[str, Any]
    rejected: List[Dict[str, str]]
    api_bytes: bytes = b""
    names: Dict[str, str] = field(default_factory=dict)

    @property
    def district_ids(self) -> List[str]:
        return list(self.by_id)


def load_dataset(path: Path) -> DistrictDataset:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except (OSError, ValueError) as exc:
        raise GeographyUnavailable(f"boundary file unreadable: {type(exc).__name__}")
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise GeographyUnavailable("boundary file is not a GeoJSON FeatureCollection")

    prepared: List[PreparedDistrict] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    seen_names: set = set()
    rejected: List[Dict[str, str]] = []
    for i, feat in enumerate(data["features"]):
        props = (feat or {}).get("properties") or {}
        name = str(props.get("district") or "").strip()
        code = str(props.get("dtcode") or "").strip()
        state = str(props.get("state") or "").strip()
        label = name or f"feature#{i}"
        try:
            if not name or not code:
                raise ValueError("missing district name or code")
            if state != "HR":
                raise ValueError("state code is not HR")
            did = f"HR-{code}"  # stable ID from the source code, never from array position
            if did in by_id:
                raise ValueError("duplicate district_id")
            if name.casefold() in seen_names:
                raise ValueError("duplicate district name")
            polys = _polygons(feat.get("geometry"))
            prepared.append(PreparedDistrict(did, name, polys))
            seen_names.add(name.casefold())
            by_id[did] = {
                "district_id": did, "district_name": name, "state_code": "HR", "dtcode": code,
                "geometry": feat["geometry"],
            }
        except ValueError as exc:
            rejected.append({"feature": label, "reason": str(exc)})
    if not prepared:
        raise GeographyUnavailable("no valid district features in boundary file")

    uncertain = {d["district_id"] for d in by_id.values() if d["district_name"] in BOUNDARY_UNCERTAIN_DISTRICTS}
    available = len(prepared)
    complete = available >= EXPECTED_DISTRICT_COUNT and not KNOWN_MISSING_DISTRICTS
    metadata = {
        "source": SOURCE_NAME, "source_agency": "Survey of India (SOI)",
        "retrieved_at": mtime,
        "retrieved_at_note": "Timestamp of the local data file; the original download date is not recorded in the file.",
        "boundary_vintage": None, "crs": "EPSG:4326",
        "expected_count": EXPECTED_DISTRICT_COUNT, "available_count": available,
        "coverage_status": "complete" if complete else "partial",
        "missing_districts": list(KNOWN_MISSING_DISTRICTS),
        "boundary_uncertain_districts": sorted(BOUNDARY_UNCERTAIN_DISTRICTS),
        "warning": None if complete else COVERAGE_WARNING,
        "rejected_features": rejected,
    }
    features = []
    for d in by_id.values():
        features.append({
            "type": "Feature", "id": d["district_id"],
            "properties": {
                "district_id": d["district_id"], "district_name": d["district_name"],
                "state_code": "HR", "dtcode": d["dtcode"], "source_name": SOURCE_NAME,
                "retrieved_at": mtime, "boundary_vintage": None,
                "boundary_uncertain": d["district_id"] in uncertain,
            },
            "geometry": d["geometry"],
        })
    payload = {
        "type": "FeatureCollection", "state": {"code": "HR", "name": "Haryana"},
        "features": features, "metadata": metadata,
        "coverage_status": metadata["coverage_status"],
        "missing_districts": metadata["missing_districts"], "source": SOURCE_NAME,
    }
    return DistrictDataset(
        index=DistrictIndex(prepared), by_id=by_id, uncertain_ids=uncertain, metadata=metadata,
        rejected=rejected, api_bytes=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        names={k: v["district_name"] for k, v in by_id.items()},
    )
