from __future__ import annotations

import math
import json
import multiprocessing
import os
import subprocess
import tempfile
import urllib.parse
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .cartography import AtlasPresentation, MapContext, PAPER, WATER, world_point
from .rail_background import UrbanRailNetwork
from .geo import haversine, point_at, polyline_lengths, prefix_path
from .models import ResolvedLeg, Trip
from .sources import OVERPASS_ENDPOINTS, HttpCache
from .theme import apply_theme, night_amount, ThemeDraw
from .coastline import Coastline


TILE_SIZE = 256
ZOOM_TRANSITION_SIDE_SECONDS = 1.0
NATURAL_EARTH_COUNTRIES_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_10m_admin_0_countries_jpn.geojson"
)
LandPolygon = tuple[
    bool,
    tuple[float, float, float, float],
    list[list[tuple[float, float]]],
]


def _smootherstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * value * (value * (value * 6 - 15) + 10)


def _zoom_transition(start: float, end: float, seconds_from_switch: float) -> float:
    """Change zoom only inside the one-second window on each side of a leg switch."""
    # Each half ends at rest. The optional station/transfer pause holds the
    # midpoint, so one uninterrupted easing curve would stop at peak velocity.
    side = seconds_from_switch / ZOOM_TRANSITION_SIDE_SECONDS
    eased = (
        0.5 * _smootherstep(side + 1)
        if side <= 0
        else 0.5 + 0.5 * _smootherstep(side)
    )
    return start + (end - start) * eased


def _leg_camera_zoom(
    index: int,
    elapsed_seconds: float,
    leg_seconds: float,
    target_zooms: list[float],
) -> float:
    # Finish the previous transition during the first second after the label changes.
    if index > 0 and elapsed_seconds < ZOOM_TRANSITION_SIDE_SECONDS:
        return _zoom_transition(
            target_zooms[index - 1], target_zooms[index], elapsed_seconds
        )

    # Start the next transition exactly one second before the next label change.
    seconds_until_switch = leg_seconds - elapsed_seconds
    if index + 1 < len(target_zooms) and seconds_until_switch < ZOOM_TRANSITION_SIDE_SECONDS:
        return _zoom_transition(
            target_zooms[index], target_zooms[index + 1], -seconds_until_switch
        )
    return target_zooms[index]


def _pause_camera_zoom(
    index: int,
    target_zooms: list[float],
) -> float:
    if index + 1 >= len(target_zooms):
        return target_zooms[index]
    # Keep the exact midpoint during an optional station pause. Zoom changes only
    # while the marker is moving: one second approaching and one second departing.
    return _zoom_transition(target_zooms[index], target_zooms[index + 1], 0.0)


def _overview_camera(
    progress: float,
    endpoint: tuple[float, float],
    final_center: tuple[float, float],
    start_zoom: float,
    end_zoom: float,
) -> tuple[tuple[float, float], float]:
    """Keep the endpoint on screen throughout the simultaneous pan and zoom.

    Interpolate its screen position, then solve for the projected map center.
    Interpolating latitude/longitude directly can throw the endpoint offscreen
    while the map is still zoomed into the last station.
    """
    if progress <= 0:
        return endpoint, start_zoom
    if progress >= 1:
        return final_center, end_zoom
    eased = _smootherstep(progress)
    zoom = start_zoom + (end_zoom-start_zoom)*eased
    sx, sy = world_point(*endpoint)
    ex, ey = world_point(*final_center)
    weight = eased * 2**(end_zoom-zoom)
    x, y = sx+(ex-sx)*weight, sy+(ey-sy)*weight
    center = (math.degrees(math.atan(math.sinh(math.pi*(1-2*y/256)))), x/256*360-180)
    return center, zoom


def _overview_duration(start_zoom: float, end_zoom: float, minimum: float, max_speed: float) -> float:
    # Quintic smootherstep has a peak derivative of 1.875 at its midpoint.
    return max(minimum, 1.875*abs(start_zoom-end_zoom)/max_speed)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size, index=0)
            except OSError:
                continue
    return ImageFont.load_default()


def _world_pixel(lat: float, lon: float, zoom: float) -> tuple[float, float]:
    scale = TILE_SIZE * 2**zoom
    lat = max(-85.05112878, min(85.05112878, lat))
    x = (lon + 180.0) / 360.0 * scale
    radians = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale
    return x, y


@dataclass(slots=True)
class MapScene:
    zoom: int
    left: float
    top: float
    image: Image.Image

    def pixel(self, coordinate: tuple[float, float]) -> tuple[float, float]:
        x, y = _world_pixel(coordinate[0], coordinate[1], self.zoom)
        return x - self.left, y - self.top


@dataclass(slots=True)
class Camera:
    scene: MapScene
    focus: tuple[float, float]
    scale: float
    width: int
    height: int

    def point(self, coordinate: tuple[float, float]) -> tuple[float, float]:
        scene_x, scene_y = self.scene.pixel(coordinate)
        return (
            (scene_x - self.focus[0]) * self.scale + self.width / 2,
            (scene_y - self.focus[1]) * self.scale + self.height / 2,
        )

    def frame(self) -> Image.Image:
        inverse_scale = 1.0 / self.scale
        affine = (
            inverse_scale,
            0.0,
            self.focus[0] - self.width * inverse_scale / 2,
            0.0,
            inverse_scale,
            self.focus[1] - self.height * inverse_scale / 2,
        )
        return self.scene.image.transform(
            (self.width, self.height),
            Image.Transform.AFFINE,
            affine,
            resample=Image.Resampling.BICUBIC,
        )


@dataclass(slots=True)
class FollowCamera:
    center: tuple[float, float]
    zoom: float
    width: int
    height: int

    def point(self, coordinate: tuple[float, float]) -> tuple[float, float]:
        center_x, center_y = _world_pixel(self.center[0], self.center[1], self.zoom)
        point_x, point_y = _world_pixel(coordinate[0], coordinate[1], self.zoom)
        return point_x - center_x + self.width / 2, point_y - center_y + self.height / 2


class Basemap:
    def __init__(self, http: HttpCache, width: int, height: int, style: str = "osm") -> None:
        self.http = http
        self.width = width
        self.height = height
        self.style = style
        self.enabled = style != "none"
        self.tile_url = os.environ.get("TRAVEL_RECORD_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
        self._tiles: OrderedDict[tuple[int, int, int], Image.Image] = OrderedDict()
        self._scenes: OrderedDict[tuple[int, int, int, int, int], MapScene] = OrderedDict()
        self._land_polygons: list[LandPolygon] | None = None
        self._atlas_land: list | None = None
        self.context = MapContext(http)
        self.coastline = Coastline(http)
        self.rail_network = UrbanRailNetwork(http)
        self._road_cells: dict[
            tuple[str, int, int], list[tuple[str, list[tuple[float, float]]]]
        ] = {}
        self._projected_road_cells: dict[int, list] = {}
        self._road_overlay: CachedRoadOverlay | None = None
        self.road_sources: dict[str, str] = {}

    @staticmethod
    def _soft_map_style(tile: Image.Image) -> Image.Image:
        """Give standard OSM tiles a quiet, warm atlas look behind vivid routes."""
        styled = ImageEnhance.Color(tile.convert("RGB")).enhance(0.52)
        styled = ImageEnhance.Contrast(styled).enhance(0.90)
        styled = ImageEnhance.Brightness(styled).enhance(1.055)
        paper = Image.new("RGB", styled.size, "#F7F3E9")
        return Image.blend(styled, paper, 0.14)

    def _load_land_polygons(
        self,
    ) -> list[LandPolygon]:
        if self._land_polygons is not None:
            return self._land_polygons
        data = self.http.json(
            NATURAL_EARTH_COUNTRIES_URL,
            namespace="natural-earth",
            max_age=365 * 24 * 3600,
            timeout=60,
        )
        polygons = []
        for feature in data.get("features", []):
            properties = feature.get("properties", {})
            is_japan = any(
                properties.get(key) == "JPN"
                for key in ("ADM0_A3", "ISO_A3", "SOV_A3", "GU_A3")
            )
            geometry = feature.get("geometry") or {}
            coordinates = geometry.get("coordinates", [])
            if geometry.get("type") == "Polygon":
                candidates = [coordinates]
            elif geometry.get("type") == "MultiPolygon":
                candidates = coordinates
            else:
                continue
            for polygon in candidates:
                rings = [
                    [(float(point[1]), float(point[0])) for point in ring]
                    for ring in polygon
                    if len(ring) >= 3
                ]
                if not rings:
                    continue
                lats = [point[0] for point in rings[0]]
                lons = [point[1] for point in rings[0]]
                polygons.append(
                    (is_japan, (min(lats), min(lons), max(lats), max(lons)), rings)
                )
        self._land_polygons = polygons
        return polygons

    def _silhouette_frame(self, center: tuple[float, float], zoom: float) -> Image.Image:
        # Supersample geographic linework too: thin roads must not shimmer while
        # the camera pans or interpolates between fractional zoom levels.
        ratio = 2
        width, height = self.width * ratio, self.height * ratio
        image = Image.new("RGB", (width, height), WATER)
        draw = ImageDraw.Draw(image)
        center_x, center_y = world_point(*center)
        scale = 2**zoom * ratio
        view = (center_x - width/(2*scale), center_y - height/(2*scale),
                center_x + width/(2*scale), center_y + height/(2*scale))

        def pixels(points: list) -> list:
            return [((x-center_x)*scale+width/2, (y-center_y)*scale+height/2) for x,y in points]

        def visible(bounds: tuple) -> bool:
            return not (bounds[2] < view[0] or bounds[0] > view[2] or bounds[3] < view[1] or bounds[1] > view[3])

        if self._atlas_land is None:
            self._atlas_land = []
            for is_japan, (south, west, north, east), rings in self._load_land_polygons():
                a, b = world_point(north,west), world_point(south,east)
                self._atlas_land.append((is_japan, (*a,*b), [[world_point(*point) for point in ring] for ring in rings]))
        for is_japan, bounds, rings in self._atlas_land:
            if not visible(bounds):
                continue
            projected = [pixels(ring) for ring in rings]
            draw.polygon(projected[0], fill=PAPER if is_japan else "#E8EEE7")
            for hole in projected[1:]:
                draw.polygon(hole, fill=WATER)

        self.coastline.draw(image, center, zoom, ratio)
        self.context.draw(image, center, zoom, ratio)

        road_growth = max(0.0, min(1.5, (zoom - 12) / 3))
        road_styles = {
            "primary": ("#DBDCD2", 0.8 + road_growth * 0.6),
            "primary_link": ("#DBDCD2", 0.7),
            "trunk": ("#D5CBB0", 1.2 + road_growth * 0.7),
            "trunk_link": ("#D5CBB0", 0.8 + road_growth * 0.3),
            "motorway": ("#C7B795", 1.3 + road_growth * 0.8),
            "motorway_link": ("#C7B795", 0.8 + road_growth * 0.3),
        }
        road_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        road_draw = ImageDraw.Draw(road_layer)
        road_fade = max(0.0, min(1.0, (zoom - 11.3) / 1.2))
        roads = self._roads_for_view(center, zoom)
        if not roads and road_fade > 0:
            if self._road_overlay is None:
                self._road_overlay = CachedRoadOverlay(self.http, self.width, self.height)
            raster = self._road_overlay.frame(center, zoom).resize(image.size, Image.Resampling.BICUBIC)
            raster.putalpha(raster.getchannel("A").point(lambda a: round(a * road_fade)))
            image = Image.alpha_composite(image.convert("RGBA"), raster).convert("RGB")
        projected_roads = self._projected_road_cells.get(id(roads))
        if projected_roads is None and roads:
            projected_roads = []
            for road_class, road in roads:
                points = [_world_pixel(lat, lon, 0) for lat, lon in road]
                bounds = (min(p[0] for p in points), min(p[1] for p in points),
                          max(p[0] for p in points), max(p[1] for p in points))
                projected_roads.append((road_class, bounds, points))
            self._projected_road_cells[id(roads)] = projected_roads
        for road_class, bounds, road in projected_roads or []:
            if not visible(bounds):
                continue
            road_pixels = pixels(road)
            if len(road_pixels) < 2:
                continue
            color, road_width = road_styles.get(road_class, ("#DBDCD2", 1))
            road_draw.line(
                road_pixels,
                fill=(255, 255, 251, round(235 * road_fade)),
                width=round((road_width + 1.6) * ratio),
                joint="curve",
            )
            road_draw.line(
                road_pixels,
                fill=_rgba(color, round(235 * road_fade)),
                width=max(1, round(road_width * ratio)),
                joint="curve",
            )
        image = Image.alpha_composite(image.convert("RGBA"), road_layer).convert("RGB")
        self.rail_network.draw(image, center, zoom, ratio)
        return image.resize((self.width, self.height), Image.Resampling.LANCZOS)

    @staticmethod
    def _extract_roads(data: dict) -> list[tuple[str, list[tuple[float, float]]]]:
        roads = []
        for element in data.get("elements", []):
            road_class = str(element.get("tags", {}).get("highway", ""))
            geometry = element.get("geometry", [])
            points = [
                (float(point["lat"]), float(point["lon"]))
                for point in geometry
                if "lat" in point and "lon" in point
            ]
            if road_class and len(points) >= 2:
                roads.append((road_class, points))
        priority = {
            "primary": 0,
            "primary_link": 1,
            "trunk": 2,
            "trunk_link": 3,
            "motorway": 4,
            "motorway_link": 5,
        }
        return sorted(roads, key=lambda item: priority.get(item[0], 0))

    def _roads_for_view(
        self, center: tuple[float, float], zoom: float
    ) -> list[tuple[str, list[tuple[float, float]]]]:
        if zoom < 11.3:
            return []
        level, spacing, radius = "fine", 0.75, 0.55
        highway_pattern = "motorway|motorway_link|trunk|trunk_link|primary|primary_link"
        lat_index = round(center[0] / spacing)
        lon_index = round(center[1] / spacing)
        key = (level, lat_index, lon_index)
        if key in self._road_cells:
            return self._road_cells[key]

        cell_lat, cell_lon = lat_index * spacing, lon_index * spacing
        south, west = cell_lat - radius, cell_lon - radius
        north, east = cell_lat + radius, cell_lon + radius
        query = (
            "[out:json][timeout:120];"
            f'way["highway"~"^({highway_pattern})$"]'
            f"({south:.4f},{west:.4f},{north:.4f},{east:.4f});"
            "out geom;"
        )
        encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
        roads: list[tuple[str, list[tuple[float, float]]]] = []
        # Check every mirror's cache before attempting any network request.
        for endpoint in OVERPASS_ENDPOINTS:
            payload = self.http.cached(endpoint, namespace="overpass-major-roads", method="POST", data=encoded)
            if payload:
                roads = self._extract_roads(json.loads(payload))
                if roads:
                    self._road_cells[key] = roads
                    self.road_sources[str(key)] = "cached OpenStreetMap vector roads"
                    return roads
        for endpoint in reversed(OVERPASS_ENDPOINTS):
            try:
                data = self.http.json(
                    endpoint,
                    namespace="overpass-major-roads",
                    method="POST",
                    data=encoded,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    max_age=30 * 24 * 3600,
                    timeout=15,
                )
                roads = self._extract_roads(data)
                break
            except Exception:
                continue
        self._road_cells[key] = roads
        self.road_sources[str(key)] = (
            "OpenStreetMap vector roads" if roads else "cached OSM tile road-color extraction (approximate)"
        )
        return roads

    def zoom_for_path(self, path: list[tuple[float, float]]) -> int:
        lats = [point[0] for point in path]
        lons = [point[1] for point in path]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        if max_lat - min_lat < 0.002:
            middle = (min_lat + max_lat) / 2
            min_lat, max_lat = middle - 0.001, middle + 0.001
        if max_lon - min_lon < 0.002:
            middle = (min_lon + max_lon) / 2
            min_lon, max_lon = middle - 0.001, middle + 0.001
        usable_width, usable_height = self.width * 0.76, self.height * 0.64
        selected = 3
        for zoom in range(16, 2, -1):
            first = _world_pixel(max_lat, min_lon, zoom)
            second = _world_pixel(min_lat, max_lon, zoom)
            if abs(second[0] - first[0]) <= usable_width and abs(second[1] - first[1]) <= usable_height:
                selected = zoom
                break
        return selected

    def _tile(self, zoom: int, tile_x: int, tile_y: int) -> Image.Image | None:
        world_tiles = 2**zoom
        if tile_y < 0 or tile_y >= world_tiles:
            return None
        key = (zoom, tile_x % world_tiles, tile_y)
        if key in self._tiles:
            tile = self._tiles.pop(key)
            self._tiles[key] = tile
            return tile
        url = self.tile_url.format(z=zoom, x=key[1], y=key[2])
        try:
            payload = self.http.request(
                url,
                namespace="osm-tiles",
                headers={"Accept": "image/png"},
                max_age=7 * 24 * 3600,
                timeout=30,
            )
            tile = self._soft_map_style(Image.open(BytesIO(payload)))
        except Exception:
            return None
        self._tiles[key] = tile
        while len(self._tiles) > 320:
            self._tiles.popitem(last=False)
        return tile

    def _frame_at_tile_zoom(
        self, center: tuple[float, float], zoom: float, tile_zoom: int
    ) -> Image.Image:
        scale = 2 ** (zoom - tile_zoom)
        center_x, center_y = _world_pixel(center[0], center[1], tile_zoom)
        source_width = self.width / scale
        source_height = self.height / scale
        x0 = math.floor((center_x - source_width / 2 - 4) / TILE_SIZE)
        y0 = math.floor((center_y - source_height / 2 - 4) / TILE_SIZE)
        x1 = math.floor((center_x + source_width / 2 + 4) / TILE_SIZE)
        y1 = math.floor((center_y + source_height / 2 + 4) / TILE_SIZE)
        key = (tile_zoom, x0, y0, x1, y1)
        scene = self._scenes.get(key)
        if scene is None:
            left, top = x0 * TILE_SIZE, y0 * TILE_SIZE
            scene_width = (x1 - x0 + 1) * TILE_SIZE
            scene_height = (y1 - y0 + 1) * TILE_SIZE
            mosaic = self._fallback(scene_width, scene_height)
            for tile_y in range(y0, y1 + 1):
                for tile_x in range(x0, x1 + 1):
                    tile = self._tile(tile_zoom, tile_x, tile_y)
                    if tile is not None:
                        mosaic.paste(tile, ((tile_x - x0) * TILE_SIZE, (tile_y - y0) * TILE_SIZE))
            scene = MapScene(tile_zoom, left, top, mosaic)
            self._scenes[key] = scene
            while len(self._scenes) > 36:
                self._scenes.popitem(last=False)
        else:
            self._scenes.move_to_end(key)
        focus = scene.pixel(center)
        return Camera(scene, focus, scale, self.width, self.height).frame()

    def frame(self, center: tuple[float, float], zoom: float) -> Image.Image:
        zoom = max(3.0, min(18.0, zoom))
        if self.style == "silhouette":
            return self._silhouette_frame(center, zoom)
        if not self.enabled:
            return self._fallback(self.width, self.height)
        lower_zoom = max(3, min(18, math.floor(zoom)))
        upper_zoom = max(3, min(18, math.ceil(zoom)))
        lower = self._frame_at_tile_zoom(center, zoom, lower_zoom)
        if lower_zoom == upper_zoom:
            return lower
        upper = self._frame_at_tile_zoom(center, zoom, upper_zoom)
        blend = zoom - lower_zoom
        blend = blend * blend * (3 - 2 * blend)
        return Image.blend(lower, upper, blend)

    @staticmethod
    def _fallback(width: int, height: int) -> Image.Image:
        image = Image.new("RGB", (width, height), "#E8ECEA")
        draw = ImageDraw.Draw(image)
        spacing = 72
        for x in range(-height, width + height, spacing):
            draw.line((x, 0, x + height, height), fill="#D7DDDA", width=1)
        for x in range(0, width + height, spacing):
            draw.line((x, 0, x - height, height), fill="#DDE2E0", width=1)
        return image

    def prepare(self, path: list[tuple[float, float]]) -> MapScene:
        zoom = self.zoom_for_path(path)
        world_points = [_world_pixel(lat, lon, zoom) for lat, lon in path]
        margin_x, margin_y = self.width / 2 + 24, self.height / 2 + 24
        left = min(point[0] for point in world_points) - margin_x
        top = min(point[1] for point in world_points) - margin_y
        right = max(point[0] for point in world_points) + margin_x
        bottom = max(point[1] for point in world_points) + margin_y
        scene_width = max(self.width, math.ceil(right - left))
        scene_height = max(self.height, math.ceil(bottom - top))
        x0 = math.floor(left / TILE_SIZE)
        y0 = math.floor(top / TILE_SIZE)
        x1 = math.floor((left + scene_width - 1) / TILE_SIZE)
        y1 = math.floor((top + scene_height - 1) / TILE_SIZE)
        tile_count = (x1 - x0 + 1) * (y1 - y0 + 1)
        while tile_count > 64 and zoom > 3:
            zoom -= 1
            world_points = [_world_pixel(lat, lon, zoom) for lat, lon in path]
            left = min(point[0] for point in world_points) - margin_x
            top = min(point[1] for point in world_points) - margin_y
            right = max(point[0] for point in world_points) + margin_x
            bottom = max(point[1] for point in world_points) + margin_y
            scene_width = max(self.width, math.ceil(right - left))
            scene_height = max(self.height, math.ceil(bottom - top))
            x0 = math.floor(left / TILE_SIZE)
            y0 = math.floor(top / TILE_SIZE)
            x1 = math.floor((left + scene_width - 1) / TILE_SIZE)
            y1 = math.floor((top + scene_height - 1) / TILE_SIZE)
            tile_count = (x1 - x0 + 1) * (y1 - y0 + 1)

        result = self._fallback(scene_width, scene_height)
        if not self.enabled:
            return MapScene(zoom, left, top, result)
        world_tiles = 2**zoom
        failures = 0
        for tile_y in range(y0, y1 + 1):
            if tile_y < 0 or tile_y >= world_tiles:
                continue
            for tile_x in range(x0, x1 + 1):
                wrapped_x = tile_x % world_tiles
                url = self.tile_url.format(z=zoom, x=wrapped_x, y=tile_y)
                try:
                    payload = self.http.request(
                        url,
                        namespace="osm-tiles",
                        headers={"Accept": "image/png"},
                        max_age=7 * 24 * 3600,
                        timeout=30,
                    )
                    tile = self._soft_map_style(Image.open(BytesIO(payload)))
                    paste_x = int(tile_x * TILE_SIZE - left)
                    paste_y = int(tile_y * TILE_SIZE - top)
                    result.paste(tile, (paste_x, paste_y))
                except Exception:
                    failures += 1
        if failures == tile_count:
            result = self._fallback(scene_width, scene_height)
        return MapScene(zoom, left, top, result)


class CachedRoadOverlay(Basemap):
    """Best-effort roads from existing standard OSM tiles when vectors are unavailable.

    Only the characteristic motorway/trunk/primary fill colors are retained.
    This is a background approximation: labels can leave gaps, so it is never
    used for routing or the travel trajectory itself. It performs no downloads.
    """

    def __init__(self, http: HttpCache, width: int, height: int) -> None:
        super().__init__(http, width, height)
        self.missing_tiles: set[tuple[int, int, int]] = set()
        self.used_tiles: set[tuple[int, int, int]] = set()

    @staticmethod
    def _fallback(width: int, height: int) -> Image.Image:
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))

    @staticmethod
    def _road_colors(tile: Image.Image) -> Image.Image:
        result = Image.new("RGBA", tile.size, (0, 0, 0, 0))
        channels = tile.convert("RGB").split()
        for source, fill in (
            ((251, 214, 164), "#DBDCD2"),
            ((249, 178, 156), "#D5CBB0"),
            ((232, 146, 162), "#C7B795"),
        ):
            masks = [channel.point([round(255 * max(0, min(1, (12 - abs(v - ref)) / 5)))
                                    for v in range(256)])
                     for channel, ref in zip(channels, source)]
            mask = ImageChops.multiply(ImageChops.multiply(masks[0], masks[1]), masks[2])
            mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
            paint = Image.new("RGBA", tile.size, fill)
            paint.putalpha(mask.point(lambda a: round(a * 0.78)))
            result = Image.alpha_composite(result, paint)
        return result

    def _tile(self, zoom: int, tile_x: int, tile_y: int) -> Image.Image | None:
        if not 0 <= tile_y < 2**zoom:
            return None
        key = (zoom, tile_x % (2**zoom), tile_y)
        if key in self._tiles:
            self._tiles.move_to_end(key)
            return self._tiles[key]
        url = f"https://tile.openstreetmap.org/{zoom}/{key[1]}/{key[2]}.png"
        payload = self.http.cached(url, namespace="osm-tiles")
        if payload is None:
            self.missing_tiles.add(key)
            return None
        with Image.open(BytesIO(payload)) as source:
            tile = self._road_colors(source)
        self.used_tiles.add(key)
        self._tiles[key] = tile
        while len(self._tiles) > 320:
            self._tiles.popitem(last=False)
        return tile


def _draw_dashed(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], fill: str, width: int) -> None:
    dash, gap = 12.0, 8.0
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        cursor = 0.0
        while cursor < length:
            final = min(length, cursor + dash)
            draw.line(
                (
                    start[0] + dx * cursor / length,
                    start[1] + dy * cursor / length,
                    start[0] + dx * final / length,
                    start[1] + dy * final / length,
                ),
                fill=fill,
                width=width,
            )
            cursor += dash + gap


def _rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    clean = hex_color.lstrip("#")
    if len(clean) == 3:
        clean = "".join(value * 2 for value in clean)
    try:
        red, green, blue = int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 36, 99, 168
    return red, green, blue, alpha


class VideoRenderer:
    def __init__(self, trip: Trip, cache_dir: Path) -> None:
        self.trip = trip
        self.cache_dir = cache_dir
        self.http = HttpCache(cache_dir)
        self.basemap = Basemap(self.http, trip.width, trip.height, trip.basemap)
        self.atlas = AtlasPresentation(trip, self.basemap)
        self.font_title = _font(max(24, trip.height // 24), bold=True)
        self.font_line = _font(max(20, trip.height // 30), bold=True)
        self.font_regular = _font(max(15, trip.height // 42))
        self.font_small = _font(max(12, trip.height // 54))
        self._durations: dict[int, float] = {}
        self._night = 0.0

    def _leg_duration(self, leg: ResolvedLeg) -> float:
        if id(leg) not in self._durations:
            km = polyline_lengths(leg.path)[1] / 1000
            factor = {"rail": 0.76, "bus": 0.88, "walk": 1.0, "taxi": 0.88}[leg.leg.mode]
            self._durations[id(leg)] = max(self.trip.seconds_per_leg, min(9.5, 2.2 + factor * math.log1p(km)))
        return self._durations[id(leg)]

    def _frame(self, legs, index, progress, camera_zoom, center_override=None):
        current = legs[index].leg.theme or self.trip.theme
        previous = (legs[index - 1].leg.theme or self.trip.theme) if index else current
        duration = self._leg_duration(legs[index])
        night = night_amount(previous, current, progress * duration,
                             min(self.trip.theme_transition_seconds, duration))
        self._night = self.atlas.night = night
        return self._day_frame(legs, index, progress, camera_zoom, center_override)

    def _overview_frame(self, legs, *args, **kwargs):
        self._night = self.atlas.night = float((legs[-1].leg.theme or self.trip.theme) == "night")
        return self._day_overview_frame(legs, *args, **kwargs)

    def _render_spec(self, legs: list[ResolvedLeg], spec: tuple) -> Image.Image:
        kind = spec[0]
        if kind == "leg":
            _, index, progress, zoom, center = spec
            return self._frame(legs, index, progress, zoom, center_override=center)
        if kind == "overview":
            _, progress, start_center, end_center, start_zoom, end_zoom = spec
            return self._overview_frame(
                legs, progress, start_center, end_center, start_zoom, end_zoom
            )
        raise RuntimeError(f"未知画面任务：{kind}")

    def _frame_specs(
        self, legs: list[ResolvedLeg], target_zooms: list[float]
    ) -> list[tuple]:
        specs: list[tuple] = []
        opening = ("leg", 0, 0.0, target_zooms[0], None)
        specs.extend(
            [opening] * round(self.trip.waypoint_pause_seconds * self.trip.fps)
        )
        last_spec = opening
        for index, _leg in enumerate(legs):
            leg_seconds = self._leg_duration(legs[index])
            frames_per_leg = max(2, round(leg_seconds * self.trip.fps))
            for frame_index in range(frames_per_leg):
                progress = frame_index / (frames_per_leg - 1)
                elapsed_seconds = progress * leg_seconds
                zoom = _leg_camera_zoom(
                    index, elapsed_seconds, leg_seconds, target_zooms
                )
                last_spec = ("leg", index, progress, zoom, None)
                specs.append(last_spec)

            pause_frames = round(
                self.trip.waypoint_pause_seconds * self.trip.fps
            )
            transfer = (
                index + 1 < len(legs)
                and haversine(legs[index].path[-1], legs[index + 1].path[0]) > 0.1
            )
            if transfer:
                pause_frames = max(pause_frames, round(self.trip.fps))
            pause_zoom = _pause_camera_zoom(index, target_zooms)
            for pause_index in range(pause_frames):
                center = None
                if transfer:
                    progress = pause_index / max(1, pause_frames - 1)
                    progress = progress * progress * (3 - 2 * progress)
                    center, _ = point_at(
                        [legs[index].path[-1], legs[index + 1].path[0]], progress
                    )
                last_spec = ("leg", index, 1.0, pause_zoom, center)
                specs.append(last_spec)

        if self.trip.ending_overview:
            all_points = [point for leg in legs for point in leg.path]
            overview_center = (
                (min(point[0] for point in all_points) + max(point[0] for point in all_points)) / 2,
                (min(point[1] for point in all_points) + max(point[1] for point in all_points)) / 2,
            )
            overview_zoom = float(self.basemap.zoom_for_path(all_points))
            overview_seconds = _overview_duration(
                target_zooms[-1],
                overview_zoom,
                self.trip.ending_overview_seconds,
                self.trip.max_zoom_levels_per_second,
            )
            overview_frames = max(2, round(overview_seconds * self.trip.fps))
            for frame_index in range(overview_frames):
                progress = frame_index / (overview_frames - 1)
                last_spec = (
                    "overview",
                    progress,
                    legs[-1].path[-1],
                    overview_center,
                    target_zooms[-1],
                    overview_zoom,
                )
                specs.append(last_spec)
        specs.extend([last_spec] * round(self.trip.end_hold_seconds * self.trip.fps))
        return specs

    def _label(self, draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, anchor: str = "la") -> None:
        x, y = xy
        bbox = draw.textbbox((x, y), text, font=self.font_small, anchor=anchor, stroke_width=2)
        draw.rounded_rectangle((bbox[0] - 5, bbox[1] - 3, bbox[2] + 5, bbox[3] + 3), radius=5, fill=(250, 248, 240, 225))
        draw.text((x, y), text, font=self.font_small, fill="#14223A", anchor=anchor)

    def _day_frame(
        self,
        legs: list[ResolvedLeg],
        index: int,
        progress: float,
        camera_zoom: float,
        center_override: tuple[float, float] | None = None,
    ) -> Image.Image:
        leg = legs[index]
        total = len(legs)
        eased = progress * progress * (3 - 2 * progress)
        marker_coord, _ = point_at(leg.path, eased)
        if center_override is not None:
            marker_coord = center_override
        camera = FollowCamera(marker_coord, camera_zoom, self.trip.width, self.trip.height)
        image = apply_theme(self.basemap.frame(marker_coord, camera_zoom).convert("RGBA"), self._night)
        if self.trip.basemap == "silhouette":
            return self.atlas.frame(legs, index, eased, camera, image)
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ThemeDraw(ImageDraw.Draw(overlay), self._night)
        line_width = max(1, round(max(1.4, min(5, 3.5 * 2 ** ((camera_zoom - 15) / 2))) * self.trip.height / 720))
        minimal = self.trip.basemap == "silhouette"

        # Keep the full traveled history visible. Future routes stay hidden until reached.
        for route_index, route in enumerate(legs[: index + 1]):
            traveled = route.path if route_index < index else prefix_path(route.path, eased)
            active_path = [camera.point(point) for point in traveled]
            if route.leg.mode == "walk":
                _draw_dashed(draw, active_path, "#75808D", max(1, round(line_width * 0.65)))
            elif len(active_path) >= 2:
                draw.line(active_path, fill=_rgba(route.color), width=line_width, joint="curve")

        path_pixels = [camera.point(point) for point in leg.path]

        start_px, end_px = path_pixels[0], path_pixels[-1]
        for x, y, fill in ((*start_px, "#14223A"), (*end_px, leg.color)):
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#FAF8F0", outline=fill, width=4)
        if not minimal:
            self._label(draw, (start_px[0] + 12, start_px[1] - 10), leg.leg.origin.name)
            self._label(draw, (end_px[0] + 12, end_px[1] + 12), leg.leg.destination.name)

        marker_x, marker_y = self.trip.width / 2, self.trip.height / 2
        radius = max(3, round(5 * self.trip.height / 720))
        draw.ellipse(
            (marker_x - radius, marker_y - radius, marker_x + radius, marker_y + radius),
            fill="#1677FF",
            outline="#FFFFFF",
            width=1,
        )

        if minimal:
            draw.text(
                (28, 24),
                leg.leg.display_line,
                font=self.font_line,
                fill="#14223A",
                stroke_width=3,
                stroke_fill="#F3EBDD",
            )
            draw.text(
                (28, 56),
                f"{leg.leg.origin.name}  →  {leg.leg.destination.name}",
                font=self.font_small,
                fill="#425466",
                stroke_width=2,
                stroke_fill="#F3EBDD",
            )
        else:
            header_height = max(112, self.trip.height // 5)
            draw.rounded_rectangle(
                (24, 22, min(self.trip.width - 24, 720), header_height),
                radius=18,
                fill=(16, 29, 49, 232),
            )
            draw.text((48, 40), self.trip.title, font=self.font_title, fill="#FAF8F0")
            chip_y = 78
            draw.rounded_rectangle((48, chip_y, 70, chip_y + 22), radius=6, fill=_rgba(leg.color))
            draw.text((82, chip_y - 2), leg.leg.display_line, font=self.font_line, fill="#FAF8F0")
            direction = f"{leg.leg.origin.name}  →  {leg.leg.destination.name}"
            draw.text((48, chip_y + 34), direction, font=self.font_regular, fill="#D8E1EA")
            draw.text(
                (min(self.trip.width - 48, 688), 46),
                f"{index + 1} / {total}",
                font=self.font_regular,
                fill="#B9C6D3",
                anchor="ra",
            )
            draw.text(
                (min(self.trip.width - 48, 688), 76),
                leg.leg.when,
                font=self.font_small,
                fill="#B9C6D3",
                anchor="ra",
            )

        if self.trip.basemap == "silhouette":
            attribution = "Land: Natural Earth · Roads: © OpenStreetMap contributors"
        elif self.trip.basemap != "none":
            attribution = "© OpenStreetMap contributors"
        else:
            attribution = "Travel Record"
        draw.text(
            (self.trip.width - 12, self.trip.height - 22),
            attribution,
            font=self.font_small,
            fill="#14223A",
            stroke_width=3,
            stroke_fill="#FAF8F0",
            anchor="ra",
        )
        self._scale_bar(draw, camera_zoom, 1.0, marker_coord[0])
        return Image.alpha_composite(image, overlay).convert("RGB")

    def _scale_bar(self, draw: ImageDraw.ImageDraw, zoom: float, scale: float, latitude: float) -> None:
        meters_per_pixel = 156543.03392 * math.cos(math.radians(latitude)) / (2**zoom * scale)
        target_distance = 132 * meters_per_pixel
        power = 10 ** math.floor(math.log10(max(target_distance, 0.001)))
        nice_distance = power
        for multiple in (1, 2, 5, 10):
            candidate = multiple * power
            if candidate <= target_distance:
                nice_distance = candidate
        bar_width = nice_distance / meters_per_pixel
        x0, y = 28.0, self.trip.height - 46.0
        x1 = x0 + bar_width
        label = f"{nice_distance / 1000:g} km" if nice_distance >= 1000 else f"{nice_distance:g} m"
        draw.line((x0, y, x1, y), fill="#FAF8F0", width=7)
        draw.line((x0, y, x1, y), fill="#14223A", width=3)
        draw.line((x0, y - 6, x0, y + 6), fill="#14223A", width=3)
        draw.line((x1, y - 6, x1, y + 6), fill="#14223A", width=3)
        draw.text(
            ((x0 + x1) / 2, y - 9),
            label,
            font=self.font_small,
            fill="#14223A",
            stroke_width=3,
            stroke_fill="#FAF8F0",
            anchor="ms",
        )

    def _day_overview_frame(
        self,
        legs: list[ResolvedLeg],
        progress: float,
        start_center: tuple[float, float],
        end_center: tuple[float, float],
        start_zoom: float,
        end_zoom: float,
    ) -> Image.Image:
        center, zoom = _overview_camera(progress, start_center, end_center, start_zoom, end_zoom)
        camera = FollowCamera(center, zoom, self.trip.width, self.trip.height)
        image = apply_theme(self.basemap.frame(center, zoom).convert("RGBA"), self._night)
        if self.trip.basemap == "silhouette":
            return self.atlas.overview(legs, progress, camera, image)
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ThemeDraw(ImageDraw.Draw(overlay), self._night)
        line_width = max(1, round(max(1.4, min(5, 3.5 * 2 ** ((zoom - 15) / 2))) * self.trip.height / 720))
        minimal = self.trip.basemap == "silhouette"

        for leg in legs:
            pixels = [camera.point(point) for point in leg.path]
            if leg.leg.mode == "walk":
                _draw_dashed(draw, pixels, "#75808D", max(1, round(line_width * 0.65)))
            else:
                draw.line(pixels, fill=_rgba(leg.color, 225), width=line_width, joint="curve")

        first_px = camera.point(legs[0].path[0])
        last_px = camera.point(legs[-1].path[-1])
        for x, y, fill in ((*first_px, "#14223A"), (*last_px, "#1677FF")):
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#FAF8F0", outline=fill, width=4)
        if not minimal:
            self._label(draw, (first_px[0] + 12, first_px[1] - 10), legs[0].leg.origin.name)
            self._label(draw, (last_px[0] + 12, last_px[1] + 12), legs[-1].leg.destination.name)

        marker_alpha = 255
        if marker_alpha:
            marker_x, marker_y = camera.point(legs[-1].path[-1])
            radius = max(3, round(5 * self.trip.height / 720))
            draw.ellipse(
                (marker_x - radius, marker_y - radius, marker_x + radius, marker_y + radius),
                fill=(22, 119, 255, marker_alpha),
                outline=(255, 255, 255, marker_alpha),
                width=1,
            )

        if minimal:
            draw.text(
                (28, 24),
                "完整行程",
                font=self.font_line,
                fill="#14223A",
                stroke_width=3,
                stroke_fill="#F3EBDD",
            )
        else:
            header_height = max(112, self.trip.height // 5)
            draw.rounded_rectangle(
                (24, 22, min(self.trip.width - 24, 720), header_height),
                radius=18,
                fill=(16, 29, 49, 232),
            )
            draw.text((48, 40), self.trip.title, font=self.font_title, fill="#FAF8F0")
            draw.rounded_rectangle((48, 78, 70, 100), radius=6, fill="#1677FF")
            draw.text((82, 76), "完整行程", font=self.font_line, fill="#FAF8F0")
            draw.text(
                (48, 112),
                f"{legs[0].leg.origin.name}  →  {legs[-1].leg.destination.name}",
                font=self.font_regular,
                fill="#D8E1EA",
            )

        if self.trip.basemap == "silhouette":
            attribution = "Land: Natural Earth · Roads: © OpenStreetMap contributors"
        elif self.trip.basemap != "none":
            attribution = "© OpenStreetMap contributors"
        else:
            attribution = "Travel Record"
        draw.text(
            (self.trip.width - 12, self.trip.height - 22),
            attribution,
            font=self.font_small,
            fill="#14223A",
            stroke_width=3,
            stroke_fill="#FAF8F0",
            anchor="ra",
        )
        self._scale_bar(draw, zoom, 1.0, center[0])
        return Image.alpha_composite(image, overlay).convert("RGB")

    def _render_parallel_segments(
        self,
        legs: list[ResolvedLeg],
        specs: list[tuple],
        staging_path: Path,
        ffmpeg: str,
        workers: int,
    ) -> None:
        worker_count = max(1, min(workers, len(specs)))
        chunk_size = math.ceil(len(specs) / worker_count)
        with tempfile.TemporaryDirectory(
            prefix=f".{staging_path.stem}-parts-", dir=staging_path.parent
        ) as directory:
            root = Path(directory)
            tasks = []
            segment_paths = []
            for index, start in enumerate(range(0, len(specs), chunk_size)):
                segment = root / f"segment-{index:03d}.mp4"
                segment_paths.append(segment)
                tasks.append(
                    (
                        self.trip,
                        self.cache_dir,
                        legs,
                        specs[start : start + chunk_size],
                        segment,
                    )
                )
            context = multiprocessing.get_context("spawn")
            with context.Pool(processes=worker_count) as pool:
                pool.map(_parallel_segment_worker, tasks)

            concat_path = root / "segments.txt"
            rows = []
            for path in segment_paths:
                escaped = str(path.resolve()).replace("'", "'\\''")
                rows.append(f"file '{escaped}'")
            concat_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_path),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(staging_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if result.returncode:
                stderr = result.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"并行视频拼接失败：{stderr[-1200:]}")

    def _target_zooms(self, legs: list[ResolvedLeg]) -> list[float]:
        requested_zooms = []
        for leg in legs:
            requested = float(self.basemap.zoom_for_path(leg.path))
            _, distance = polyline_lengths(leg.path)
            # Regional trains are followed by the camera; they do not need the
            # whole inter-city leg visible at once.  Keep useful city detail on
            # the Kyoto-Nara and southern Kanto approaches while preserving the
            # wide Tokaido Shinkansen view.
            if (
                leg.leg.mode == "rail"
                and "shinkansen" not in leg.leg.line.casefold()
                and distance >= 15_000
            ):
                requested = max(requested, 12.5)
            requested_zooms.append(requested)

        target_zooms = [requested_zooms[0]]
        for requested in requested_zooms[1:]:
            previous = target_zooms[-1]
            change = max(-4.0, min(4.0, requested - previous))
            target_zooms.append(previous + change)
        return target_zooms

    def render(
        self, legs: list[ResolvedLeg], destination: Path, workers: int = 1
    ) -> None:
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError("缺少视频编码依赖；请先运行 uv sync") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        fd, staging_name = tempfile.mkstemp(prefix=f".{destination.stem}-", suffix=".mp4", dir=destination.parent)
        os.close(fd)
        staging_path = Path(staging_name)
        target_zooms = self._target_zooms(legs)

        specs = self._frame_specs(legs, target_zooms)
        if workers > 1:
            try:
                self._render_parallel_segments(
                    legs, specs, staging_path, ffmpeg, workers
                )
                os.replace(staging_path, destination)
                return
            except BaseException:
                staging_path.unlink(missing_ok=True)
                raise

        command = _raw_video_command(ffmpeg, self.trip, staging_path)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            assert process.stdin is not None
            _write_frame_specs(process, self, legs, specs)
            process.stdin.close()
            stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
            return_code = process.wait()
            if return_code:
                raise RuntimeError(f"视频编码失败：{stderr[-1200:]}")
            os.replace(staging_path, destination)
        except BaseException:
            process.kill()
            process.wait()
            staging_path.unlink(missing_ok=True)
            raise


def _raw_video_command(ffmpeg: str, trip: Trip, destination: Path) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-nostats",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{trip.width}x{trip.height}",
        "-r",
        str(trip.fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(destination),
    ]


def _write_frame_specs(
    process: subprocess.Popen,
    renderer: VideoRenderer,
    legs: list[ResolvedLeg],
    specs: list[tuple],
) -> None:
    assert process.stdin is not None
    previous_spec = None
    previous_payload = None
    for spec in specs:
        if spec != previous_spec:
            previous_payload = renderer._render_spec(legs, spec).tobytes()
            previous_spec = spec
        assert previous_payload is not None
        process.stdin.write(previous_payload)


def _parallel_segment_worker(args: tuple) -> str:
    trip, cache_dir, legs, specs, destination = args
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError("缺少视频编码依赖；请先运行 uv sync") from exc
    renderer = VideoRenderer(trip, cache_dir)
    process = subprocess.Popen(
        _raw_video_command(imageio_ffmpeg.get_ffmpeg_exe(), trip, destination),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _write_frame_specs(process, renderer, legs, specs)
        process.stdin.close()
        stderr = (
            process.stderr.read().decode("utf-8", errors="replace")
            if process.stderr
            else ""
        )
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"并行分段编码失败：{stderr[-1200:]}")
        return str(destination)
    except BaseException:
        process.kill()
        process.wait()
        destination.unlink(missing_ok=True)
        raise
