"""Deterministic geometry: point-in-polygon and route-to-district matching (F5, L-B3, L-A4).

Pure Python (no shapely) so it installs anywhere. Edges are bucketed into horizontal bands so each
point-in-polygon test touches only a few edges.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

EARTH_RADIUS_M = 6371008.8
Point = Tuple[float, float]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


class _Ring:
    __slots__ = ("minx", "miny", "maxx", "maxy", "ny", "dy", "bands")

    def __init__(self, pts: Sequence[Point]):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.minx, self.maxx = min(xs), max(xs)
        self.miny, self.maxy = min(ys), max(ys)
        n = len(pts) - 1  # closed ring: last == first
        self.ny = max(1, min(256, n // 8))
        span = self.maxy - self.miny
        self.dy = span / self.ny if span > 0 else 1.0
        self.bands: List[list] = [[] for _ in range(self.ny)]
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            if y1 == y2:
                continue
            lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
            b0 = max(0, min(self.ny - 1, int((lo - self.miny) / self.dy)))
            b1 = max(0, min(self.ny - 1, int((hi - self.miny) / self.dy)))
            for b in range(b0, b1 + 1):
                self.bands[b].append((x1, y1, x2, y2))

    def contains(self, x: float, y: float) -> bool:
        if x < self.minx or x > self.maxx or y < self.miny or y > self.maxy:
            return False
        b = int((y - self.miny) / self.dy)
        if b >= self.ny:
            b = self.ny - 1
        inside = False
        for x1, y1, x2, y2 in self.bands[b]:
            if (y1 > y) != (y2 > y):
                if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
        return inside


class _Polygon:
    __slots__ = ("outer", "holes")

    def __init__(self, rings: Sequence[Sequence[Point]]):
        self.outer = _Ring(rings[0])
        self.holes = [_Ring(r) for r in rings[1:]]

    def contains(self, x: float, y: float) -> bool:
        if not self.outer.contains(x, y):
            return False
        return not any(h.contains(x, y) for h in self.holes)


class PreparedDistrict:
    __slots__ = ("district_id", "name", "polygons", "minx", "miny", "maxx", "maxy")

    def __init__(self, district_id: str, name: str, polygons: Sequence[Sequence[Sequence[Point]]]):
        self.district_id = district_id
        self.name = name
        self.polygons = [_Polygon(p) for p in polygons]
        self.minx = min(p.outer.minx for p in self.polygons)
        self.miny = min(p.outer.miny for p in self.polygons)
        self.maxx = max(p.outer.maxx for p in self.polygons)
        self.maxy = max(p.outer.maxy for p in self.polygons)

    def contains(self, x: float, y: float) -> bool:
        if x < self.minx or x > self.maxx or y < self.miny or y > self.maxy:
            return False
        return any(p.contains(x, y) for p in self.polygons)


class DistrictIndex:
    def __init__(self, districts: Sequence[PreparedDistrict]):
        self.districts = list(districts)

    def locate(self, x: float, y: float, hint: Optional[int] = None) -> Optional[int]:
        if hint is not None and self.districts[hint].contains(x, y):
            return hint
        for i, d in enumerate(self.districts):
            if i != hint and d.contains(x, y):
                return i
        return None


def match_route(index: DistrictIndex, coords: Sequence[Point], step_m: float = 500.0,
                bisect_iters: int = 14) -> Dict:
    """Walk the route polyline, label each sample with a district (or None = unknown), refine
    district-boundary crossings by bisection, and return ordered sections.

    A section is assigned to a district ONLY when the sampled point lies inside that district's polygon.
    """
    if len(coords) < 2:
        raise ValueError("route needs at least two coordinates")
    pts: List[Tuple[float, float, float, bool]] = [(coords[0][0], coords[0][1], 0.0, True)]
    cum = 0.0
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        seg = haversine_m(x1, y1, x2, y2)
        k = max(1, int(math.ceil(seg / step_m)))
        for j in range(1, k + 1):
            t = j / k
            pts.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, cum + seg * t, j == k))
        cum += seg
    total = cum

    labels: List[Optional[int]] = []
    hint: Optional[int] = None
    for x, y, _, _ in pts:
        idx = index.locate(x, y, hint)
        labels.append(idx)
        if idx is not None:
            hint = idx

    raw_sections: List[Dict] = []

    def close(label, start, end, cs):
        if len(cs) < 2:
            cs = cs + cs
        raw_sections.append({"label": label, "start_m": start, "end_m": end, "coordinates": cs})

    cur_label = labels[0]
    cur_start = 0.0
    cur_coords: List[Point] = [(pts[0][0], pts[0][1])]
    for i in range(1, len(pts)):
        x, y, c, orig = pts[i]
        if labels[i] == cur_label:
            if orig:
                cur_coords.append((x, y))
            continue
        px, py, pc, _ = pts[i - 1]
        lo, hi = 0.0, 1.0
        for _ in range(bisect_iters):
            mid = (lo + hi) / 2
            lab = index.locate(px + (x - px) * mid, py + (y - py) * mid, cur_label)
            if lab == cur_label:
                lo = mid
            else:
                hi = mid
        bx, by, bc = px + (x - px) * hi, py + (y - py) * hi, pc + (c - pc) * hi
        cur_coords.append((bx, by))
        close(cur_label, cur_start, bc, cur_coords)
        cur_label, cur_start, cur_coords = labels[i], bc, [(bx, by)]
        if orig:
            cur_coords.append((x, y))
    close(cur_label, cur_start, total, cur_coords)

    sections = []
    ordered: List[str] = []
    verified = 0.0
    for s in raw_sections:
        length = max(0.0, s["end_m"] - s["start_m"])
        did = index.districts[s["label"]].district_id if s["label"] is not None else None
        if did is not None:
            verified += length
            if did not in ordered:
                ordered.append(did)
        sections.append({
            "district_id": did,
            "status": "matched" if did is not None else "unknown",
            "start_m": s["start_m"], "end_m": s["end_m"], "length_m": length,
            "geometry": {"type": "LineString", "coordinates": [[a, b] for a, b in s["coordinates"]]},
        })
    return {
        "total_length_m": total, "verified_length_m": verified,
        "unknown_length_m": max(0.0, total - verified),
        "coverage_ratio": (verified / total) if total > 0 else 0.0,
        "sections": sections, "district_ids": ordered,
    }
