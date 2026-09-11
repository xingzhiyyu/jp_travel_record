"""Regional sea/land masks built from directed OpenStreetMap coastlines.

OSM coastlines have land on their left. Polygonize them together with the
region boundary, then classify faces by the original edge direction. Open
coasts are never closed with a made-up straight line through a bay.
"""

import json
import math
import os
import re
import tempfile
import urllib.parse
from datetime import datetime, timezone

from PIL import ImageDraw
from shapely.geometry import LineString, box
from shapely.geometry.polygon import orient
from shapely.ops import polygonize_full, unary_union

from .cartography import PAPER, WATER, world_point
from .sources import DataSourceError, OVERPASS_ENDPOINTS


COAST_REGIONS = {
    "kansai": (33.8, 134.0, 35.9, 136.5),
    "tokaido": (33.8, 136.5, 35.9, 138.7),
    "tokyo": (34.65, 138.7, 36.4, 141.0),
}


def validate_bounds(bounds):
    if len(bounds) != 4 or not all(math.isfinite(v) for v in bounds):
        raise DataSourceError("范围必须是四个有限数：南 西 北 东")
    south, west, north, east = bounds
    if not (-85 <= south < north <= 85 and -180 <= west < east <= 180):
        raise DataSourceError("范围无效；不支持跨日期变更线，纬度须在 -85 到 85 之间")
    return tuple(bounds)


def fetch_coastline(http, name, bounds=None):
    bounds = validate_bounds(COAST_REGIONS[name] if bounds is None else bounds)
    bbox = ",".join(f"{v:.4f}" for v in bounds)
    query = f'[out:json][timeout:180];way["natural"="coastline"]({bbox});out geom;'
    encoded = urllib.parse.urlencode({"data": query}).encode()
    for endpoint in OVERPASS_ENDPOINTS:
        cached = http.cached(endpoint, namespace="overpass-coastline", method="POST", data=encoded)
        if cached:
            try:
                data = json.loads(cached)
            except (ValueError, UnicodeError):
                continue
            if isinstance(data, dict) and data.get("elements") and not data.get("remark"):
                return data
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = http.json(endpoint, namespace="overpass-coastline", method="POST", data=encoded,
                             headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=200,
                             max_age=30 * 24 * 3600)
            if not isinstance(data, dict) or not data.get("elements") or data.get("remark"):
                raise DataSourceError("海岸线下载不完整")
            return data
        except (DataSourceError, OSError) as exc:
            errors.append(str(exc))
    raise DataSourceError(f"无法加载 {name} 的精细海岸线：" + "; ".join(errors))


def acquire_region(http, name, bounds):
    """Download, strictly validate, then atomically register a local region."""
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name) or name in COAST_REGIONS:
        raise DataSourceError("名称须为 1–64 位字母、数字、连字符或下划线，且不能覆盖内置区域")
    bounds = validate_bounds(bounds)
    if bounds[2] - bounds[0] > 3 or bounds[3] - bounds[1] > 3:
        raise DataSourceError("单区纬度和经度跨度均不能超过 3 度；请分区获取")
    # Match the exact request precision, including polygonization boundaries.
    bounds = validate_bounds(tuple(round(v, 4) for v in bounds))
    data = fetch_coastline(http, name, bounds)
    polygons = land_polygons(data, bounds)
    snapshot = {"version": 1, "name": name, "bounds": bounds, "data": data,
                "validated_at": datetime.now(timezone.utc).isoformat(),
                "source": "© OpenStreetMap contributors, ODbL; natural=coastline",
                "land_polygon_count": len(polygons)}
    folder = http.root / "coastline-regions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder,
                                         suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(snapshot, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return path, snapshot


def _parts(geometry):
    if geometry.is_empty:
        return []
    return list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]


def _key(point):
    return tuple(round(v, 11) for v in point)


def land_polygons(data, bounds):
    south, west, north, east = bounds
    region = box(west, south, east, north)
    lines, directions = [], set()
    for element in data.get("elements", []):
        if element.get("type") != "way" or element.get("tags", {}).get("natural") != "coastline":
            continue
        points = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
        if len(points) < 2:
            continue
        for line in _parts(LineString(points).intersection(region)):
            if line.geom_type != "LineString" or line.length <= 1e-12:
                continue
            lines.append(line)
            directions.update((_key(a), _key(b)) for a, b in zip(line.coords, list(line.coords)[1:]))
    if not lines:
        raise DataSourceError("区域没有海岸线，无法可靠判断海陆")
    faces, cuts, dangles, invalid = polygonize_full(unary_union([region.boundary, *lines]))
    if cuts.length + dangles.length + invalid.length > 1e-7:
        raise DataSourceError("海岸线存在未闭合或交叉片段，拒绝猜测海陆边界")
    land = []
    for face in faces.geoms:
        face = orient(face, sign=1)
        evidence = set()
        for ring in [face.exterior, *face.interiors]:
            for a, b in zip(ring.coords, list(ring.coords)[1:]):
                edge = (_key(a), _key(b))
                if edge in directions:
                    evidence.add(True)
                if edge[::-1] in directions:
                    evidence.add(False)
        if len(evidence) != 1:
            raise DataSourceError("海岸线方向不完整或相互矛盾，无法可靠判断海陆")
        if True in evidence:
            land.append(face)
    return land


class Coastline:
    def __init__(self, http):
        self.http = http
        self.regions = {}
        self.sources = {}
        self.bounds = dict(COAST_REGIONS)
        self.custom = {}
        for path in sorted((http.root / "coastline-regions").glob("*.json")):
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                name = snapshot["name"]
                if snapshot["version"] != 1 or name in self.bounds:
                    raise ValueError("版本或名称冲突")
                if not isinstance(snapshot["data"], dict) or not isinstance(snapshot["validated_at"], str):
                    raise ValueError("缺少有效海岸线快照或验证时间")
                self.bounds[name] = validate_bounds(snapshot["bounds"])
                self.custom[name] = snapshot
            except (ValueError, KeyError, TypeError, OSError) as exc:
                raise DataSourceError(f"精细海岸线注册文件损坏：{path}: {exc}") from exc

    def prepare(self, name):
        if name in self.regions:
            return self.regions[name]
        data = self.custom[name]["data"] if name in self.custom else fetch_coastline(self.http, name)
        polygons = land_polygons(data, self.bounds[name])
        projected = []
        # Exact spatial chunks avoid transforming an entire mainland ring for
        # every close-up frame. Intersections retain the original shoreline.
        chunks = []
        for polygon in polygons:
            west, south, east, north = polygon.bounds
            if east-west <= .25 and north-south <= .25:
                chunks.append(polygon)
                continue
            for x in range(math.floor(west*4), math.ceil(east*4)):
                for y in range(math.floor(south*4), math.ceil(north*4)):
                    for part in _parts(polygon.intersection(box(x/4,y/4,(x+1)/4,(y+1)/4))):
                        if part.geom_type == "Polygon" and not part.is_empty:
                            chunks.append(part)
        for polygon in chunks:
            rings = [[world_point(lat, lon) for lon, lat in ring.coords]
                     for ring in [polygon.exterior, *polygon.interiors]]
            xs, ys = zip(*rings[0])
            projected.append(((min(xs), min(ys), max(xs), max(ys)), rings))
        self.regions[name] = projected
        self.sources[name] = {"source": "OpenStreetMap natural=coastline; directed polygonization",
                              "bbox": self.bounds[name], "way_count": len(data["elements"]),
                              "land_polygon_count": len(polygons)}
        if name in self.custom:
            self.sources[name]["validated_at"] = self.custom[name]["validated_at"]
            self.sources[name]["storage"] = "local validated coastline snapshot"
        return projected

    def draw(self, image, center, zoom, ratio=2):
        cx, cy = world_point(*center)
        scale = 2**zoom * ratio
        width, height = image.size
        view = (cx-width/(2*scale), cy-height/(2*scale), cx+width/(2*scale), cy+height/(2*scale))
        def visible(bounds):
            return not (bounds[2] < view[0] or bounds[0] > view[2] or bounds[3] < view[1] or bounds[1] > view[3])
        def pixels(points):
            return [((x-cx)*scale+width/2, (y-cy)*scale+height/2) for x,y in points]
        draw = ImageDraw.Draw(image)
        for name, (south, west, north, east) in self.bounds.items():
            corners = (*world_point(north, west), *world_point(south, east))
            if not visible(corners):
                continue
            polygons = self.prepare(name)
            draw.rectangle([v for p in pixels([(corners[0],corners[1]),(corners[2],corners[3])]) for v in p], fill=WATER)
            for bounds, rings in polygons:
                if not visible(bounds):
                    continue
                draw.polygon(pixels(rings[0]), fill=PAPER)
                for hole in rings[1:]:
                    draw.polygon(pixels(hole), fill=WATER)
