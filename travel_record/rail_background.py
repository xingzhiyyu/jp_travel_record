"""The operating urban rail network, separate from the recorded itinerary."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass

from PIL import Image, ImageColor, ImageDraw

from .cartography import PAPER, world_point
from .sources import DataSourceError, HttpCache, OVERPASS_ENDPOINTS


RAIL_REGIONS = {
    "osaka_kobe": (34.30, 134.90, 34.92, 135.82),
    "kyoto": (34.82, 135.50, 35.25, 135.95),
    "tokyo_west": (35.45, 139.30, 35.98, 139.75),
    "tokyo_east": (35.45, 139.75, 35.98, 140.20),
}
RAIL_TYPES = {"rail", "subway", "light_rail", "tram", "monorail", "funicular"}


def rail_query(bounds: tuple, with_routes: bool = True) -> bytes:
    bbox = ",".join(f"{v:.4f}" for v in bounds)
    query = (
        "[out:json][timeout:60];"
        f'way["railway"~"^(rail|subway|light_rail|tram|monorail|funicular)$"]({bbox})->.tracks;'
        'rel(bw.tracks)["type"="route"]["route"~"^(train|subway|light_rail|tram|monorail|funicular|railway)$"]->.lines;'
        'rel(br.lines)["type"="route_master"]->.masters;'
        ".tracks out geom;.lines out body;.masters out body;"
    )
    if not with_routes:
        query = (
            "[out:json][timeout:45];"
            f'way["railway"~"^(rail|subway|light_rail|tram|monorail|funicular)$"]({bbox});out geom;'
        )
    return urllib.parse.urlencode({"data": query}).encode()


def fetch_rail_network(http: HttpCache, region: str) -> dict:
    encoded = rail_query(RAIL_REGIONS[region])
    for endpoint in OVERPASS_ENDPOINTS:
        payload = http.cached(
            endpoint, namespace="overpass-urban-rail", method="POST", data=encoded
        )
        if payload:
            data = json.loads(payload)
            if not data.get("remark"):
                return data
    # Geometry-only snapshots are a valid complete track background when the
    # much heavier reverse route lookup is unavailable. Color metadata is reused
    # from adjacent cached regions; unknown lines remain neutral, never invented.
    for endpoint in OVERPASS_ENDPOINTS:
        payload = http.cached(
            endpoint,
            namespace="overpass-urban-rail",
            method="POST",
            data=rail_query(RAIL_REGIONS[region], with_routes=False),
        )
        if payload:
            data = json.loads(payload)
            if not data.get("remark"):
                data["_geometry_only"] = True
                return data
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = http.json(
                endpoint,
                namespace="overpass-urban-rail",
                method="POST",
                data=encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=75,
                max_age=30 * 24 * 3600,
            )
            if data.get("remark"):
                raise DataSourceError(f"轨道数据未完整返回：{data['remark']}")
            return data
        except DataSourceError as exc:
            last_error = exc
    raise DataSourceError(f"无法读取 {region} 城市轨道网：{last_error}")


def tag_color(tags: dict) -> tuple | None:
    color = tags.get("colour", tags.get("color"))
    if not isinstance(color, str):
        return None
    try:
        return ImageColor.getrgb(color)[:3]
    except ValueError:
        return None


@dataclass
class RailWay:
    osm_id: int
    kind: str
    color: tuple
    points: list
    bounds: tuple
    service: bool


class UrbanRailNetwork:
    def __init__(self, http: HttpCache) -> None:
        self.http = http
        self.regions: dict[str, list[RailWay]] = {}
        self.sources: dict[str, dict] = {}
        self._relations: list | None = None

    def cached_relations(self) -> list:
        if self._relations is None:
            relations = {}
            for bounds in RAIL_REGIONS.values():
                for endpoint in OVERPASS_ENDPOINTS:
                    payload = self.http.cached(
                        endpoint,
                        namespace="overpass-urban-rail",
                        method="POST",
                        data=rail_query(bounds),
                    )
                    if payload:
                        data = json.loads(payload)
                        if not data.get("remark"):
                            relations.update(
                                {
                                    e["id"]: e
                                    for e in data.get("elements", [])
                                    if e.get("type") == "relation"
                                }
                            )
                            break
            self._relations = list(relations.values())
        return self._relations

    @staticmethod
    def extract(data: dict) -> list[RailWay]:
        relations = {
            item["id"]: item
            for item in data.get("elements", [])
            if item.get("type") == "relation"
        }
        inherited = {}
        for item in relations.values():
            if item.get("tags", {}).get("type") == "route_master":
                color = tag_color(item.get("tags", {}))
                if color:
                    for member in item.get("members", []):
                        if member.get("type") == "relation":
                            inherited[member["ref"]] = color
        colors = {}
        # Stable ordering prevents a shared track's tint changing between views.
        for osm_id, item in sorted(relations.items()):
            color = tag_color(item.get("tags", {})) or inherited.get(osm_id)
            if color:
                for member in item.get("members", []):
                    if member.get("type") == "way":
                        colors.setdefault(member["ref"], color)
        result, seen = [], set()
        for item in data.get("elements", []):
            tags = item.get("tags", {})
            if item.get("type") != "way" or tags.get("railway") not in RAIL_TYPES:
                continue
            if (
                tags.get("disused") == "yes"
                or tags.get("abandoned") == "yes"
                or item["id"] in seen
            ):
                continue
            points = [
                world_point(p["lat"], p["lon"])
                for p in item.get("geometry", [])
                if "lat" in p and "lon" in p
            ]
            if len(points) < 2:
                continue
            seen.add(item["id"])
            bounds = (
                min(p[0] for p in points),
                min(p[1] for p in points),
                max(p[0] for p in points),
                max(p[1] for p in points),
            )
            color = colors.get(item["id"]) or tag_color(tags) or (113, 133, 143)
            result.append(
                RailWay(
                    item["id"],
                    tags["railway"],
                    color,
                    points,
                    bounds,
                    tags.get("service") in {"yard", "siding", "spur"},
                )
            )
        return result

    def draw(
        self, canvas: Image.Image, center: tuple, zoom: float, ratio: int = 2
    ) -> None:
        if zoom <= 10.2:
            return
        cx, cy = world_point(*center)
        scale = 2**zoom * ratio
        width, height = canvas.size
        view = (
            cx - width / (2 * scale),
            cy - height / (2 * scale),
            cx + width / (2 * scale),
            cy + height / (2 * scale),
        )

        def visible(bounds):
            return not (
                bounds[2] < view[0]
                or bounds[0] > view[2]
                or bounds[3] < view[1]
                or bounds[1] > view[3]
            )

        ways, seen = [], set()
        for name, (south, west, north, east) in RAIL_REGIONS.items():
            if not visible((*world_point(north, west), *world_point(south, east))):
                continue
            if name not in self.regions:
                # An incomplete background must not silently masquerade as all lines.
                data = fetch_rail_network(self.http, name)
                self.regions[name] = self.extract(
                    {"elements": [*self.cached_relations(), *data.get("elements", [])]}
                )
                self.sources[name] = {
                    "source": "OpenStreetMap operating railway ways and route relation colors",
                    "bbox": RAIL_REGIONS[name],
                    "way_count": len(self.regions[name]),
                    "geometry_only_snapshot": bool(data.get("_geometry_only")),
                    "note": "Includes rail, subway, tram, light rail, monorail and funicular where mapped; uncolored ways use neutral ink.",
                }
            for way in self.regions[name]:
                if way.osm_id not in seen and visible(way.bounds):
                    ways.append(way)
                    seen.add(way.osm_id)
        layer = Image.new("RGBA", canvas.size)
        draw = ImageDraw.Draw(layer)
        t = min(1, (zoom - 10.2) / 2.0)
        fade = t * t * (3 - 2 * t)
        for way in sorted(ways, key=lambda w: (not w.service, w.osm_id)):
            points = [
                ((x - cx) * scale + width / 2, (y - cy) * scale + height / 2)
                for x, y in way.points
            ]
            weight = (
                0.65 if way.service else 1.25 if way.kind in {"subway", "tram"} else 1.5
            )
            weight += max(0, min(0.6, (zoom - 13) * 0.15))
            alpha = round((75 if way.service else 145) * fade)
            color = tuple(round(v * 0.58 + 247 * 0.42) for v in way.color)
            draw.line(
                points,
                fill=(*ImageColor.getrgb(PAPER), round(160 * fade)),
                width=round((weight + 1.1) * ratio),
                joint="curve",
            )
            draw.line(
                points,
                fill=(*color, alpha),
                width=max(1, round(weight * ratio)),
                joint="curve",
            )
        canvas.paste(
            Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")
        )
