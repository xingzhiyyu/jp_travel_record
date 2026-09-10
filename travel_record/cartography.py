"""Quiet atlas styling and real OSM water/green-space geometry.

All geographic shapes come from the cached data services, never decorative
invented geography. UI drawing is supersampled separately from the map camera.
"""

from __future__ import annotations

import json
import math
import urllib.parse
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .geo import haversine, polyline_lengths, prefix_path
from .sources import OVERPASS_ENDPOINTS, HttpCache
from .theme import ThemeDraw, apply_theme


PAPER = "#F7F7F2"
WATER = "#C8DEE0"
GREEN = "#E1E9DC"
INK = "#203A42"
MUTED = "#6D8084"
BLUE = "#1677FF"
CONTEXT_REGIONS = {
    "kansai": (34.38, 135.15, 35.08, 135.86),
    "tokyo": (35.58, 139.62, 35.80, 139.94),
    # Yokohama's reclaimed port islands are represented by detailed land-use
    # polygons in OSM, not only by natural=coastline.
    "kanagawa": (35.20, 139.25, 35.60, 139.82),
}


def context_query(bounds: tuple[float, float, float, float]) -> bytes:
    bbox = ",".join(f"{value:.4f}" for value in bounds)
    query = (
        "[out:json][timeout:90];("
        f'way["natural"~"^(water|wood)$"]({bbox});'
        f'relation["natural"~"^(water|wood)$"]({bbox});'
        f'way["landuse"="forest"]({bbox});'
        f'relation["landuse"="forest"]({bbox});'
        f'way["leisure"="park"]({bbox});'
        f'relation["leisure"="park"]({bbox});'
        f'way["landuse"="industrial"]({bbox});'
        f'relation["landuse"="industrial"]({bbox});'
        f'way["waterway"~"^(river|canal)$"]({bbox});'
        ");out geom;"
    )
    return urllib.parse.urlencode({"data": query}).encode()


def fetch_context(http: HttpCache, name: str) -> dict:
    encoded = context_query(CONTEXT_REGIONS[name])
    for endpoint in OVERPASS_ENDPOINTS:
        payload = http.cached(
            endpoint, namespace="overpass-map-context", method="POST", data=encoded
        )
        if payload:
            return json.loads(payload)
    return http.json(
        OVERPASS_ENDPOINTS[0],
        namespace="overpass-map-context",
        method="POST",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=100,
        max_age=30 * 24 * 3600,
    )


def world_point(lat: float, lon: float) -> tuple[float, float]:
    lat = max(-85.05112878, min(85.05112878, lat))
    return (lon + 180) / 360 * 256, (
        1 - math.asinh(math.tan(math.radians(lat))) / math.pi
    ) * 128


def joined_rings(
    parts: list[list[tuple[float, float]]]
) -> list[list[tuple[float, float]]]:
    """Join relation member ways; do not close incomplete coastlines arbitrarily."""
    remaining = [part[:] for part in parts if len(part) >= 2]
    rings = []
    while remaining:
        ring = remaining.pop()
        while ring[0] != ring[-1]:
            for index, part in enumerate(remaining):
                if ring[-1] == part[0]:
                    ring.extend(part[1:])
                elif ring[-1] == part[-1]:
                    ring.extend(part[-2::-1])
                elif ring[0] == part[-1]:
                    ring = part[:-1] + ring
                elif ring[0] == part[0]:
                    ring = list(reversed(part[1:])) + ring
                else:
                    continue
                remaining.pop(index)
                break
            else:
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


class MapContext:
    def __init__(self, http: HttpCache) -> None:
        self.http = http
        self.regions: dict[str, list] = {}
        self.sources: dict[str, str] = {}

    @staticmethod
    def extract(data: dict) -> list:
        result = []
        for element in data.get("elements", []):
            tags = element.get("tags", {})
            kind = "water" if tags.get("natural") == "water" else "green"
            if tags.get("waterway") in {"river", "canal"}:
                kind = "river"
            elif tags.get("landuse") in {
                "industrial", "commercial", "retail", "residential", "railway"
            }:
                kind = "land"

            def coordinates(geometry: list) -> list:
                return [
                    world_point(p["lat"], p["lon"])
                    for p in geometry
                    if "lat" in p and "lon" in p
                ]

            outer, inner = [], []
            if element.get("type") == "relation":
                for member in element.get("members", []):
                    target = inner if member.get("role") == "inner" else outer
                    target.append(coordinates(member.get("geometry", [])))
                outer, inner = joined_rings(outer), joined_rings(inner)
            else:
                points = coordinates(element.get("geometry", []))
                if len(points) >= 2 and (
                    kind == "river" or (len(points) >= 4 and points[0] == points[-1])
                ):
                    outer = [points]
            for ring in outer:
                bounds = (
                    min(p[0] for p in ring),
                    min(p[1] for p in ring),
                    max(p[0] for p in ring),
                    max(p[1] for p in ring),
                )
                holes = [
                    hole
                    for hole in inner
                    if bounds[0] <= hole[0][0] <= bounds[2]
                    and bounds[1] <= hole[0][1] <= bounds[3]
                ]
                result.append((kind, bounds, ring, holes))
        return sorted(
            result,
            key=lambda item: {"land": 0, "green": 1, "water": 2, "river": 3}[item[0]],
        )

    def draw(
        self,
        canvas: Image.Image,
        center: tuple[float, float],
        zoom: float,
        ratio: int = 2,
    ) -> None:
        if zoom <= 10.7:
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
        layer = Image.new("RGBA", canvas.size)
        draw = ImageDraw.Draw(layer)
        fade = min(1, (zoom - 10.7) / 1.7)

        def pixel(point: tuple) -> tuple:
            return (
                (point[0] - cx) * scale + width / 2,
                (point[1] - cy) * scale + height / 2,
            )

        for name, (south, west, north, east) in CONTEXT_REGIONS.items():
            a, b = world_point(north, west), world_point(south, east)
            if b[0] < view[0] or a[0] > view[2] or b[1] < view[1] or a[1] > view[3]:
                continue
            if name not in self.regions:
                try:
                    self.regions[name] = self.extract(fetch_context(self.http, name))
                    self.sources[name] = (
                        "OpenStreetMap water, woodland and park geometries"
                    )
                except Exception:
                    self.regions[name] = []
                    self.sources[name] = "unavailable; omitted"
            for kind, bounds, ring, holes in self.regions[name]:
                if (
                    bounds[2] < view[0]
                    or bounds[0] > view[2]
                    or bounds[3] < view[1]
                    or bounds[1] > view[3]
                ):
                    continue
                points = [pixel(p) for p in ring]
                if kind == "river":
                    draw.line(
                        points,
                        fill=WATER,
                        width=max(ratio, round(1.2 * ratio * 2 ** max(0, zoom - 14))),
                        joint="curve",
                    )
                else:
                    fill = WATER if kind == "water" else GREEN if kind == "green" else PAPER
                    draw.polygon(points, fill=fill)
                    for hole in holes:
                        draw.polygon([pixel(p) for p in hole], fill=PAPER)
        if fade < 1:
            layer.putalpha(layer.getchannel("A").point(lambda a: round(a * fade)))
        canvas.paste(
            Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")
        )


@lru_cache(maxsize=128)
def atlas_font(
    size: int, bold: bool = False, cjk: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [("/System/Library/Fonts/Hiragino Sans GB.ttc", 1 if bold else 0)]
        if cjk
        else []
    ) + [
        ("/System/Library/Fonts/Avenir Next.ttc", 2 if bold else 7),
        (
            (
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
                if bold
                else "/System/Library/Fonts/Supplemental/Arial.ttf"
            ),
            0,
        ),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ]
    for path, index in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=index)
            except OSError:
                continue
    return ImageFont.load_default()


class AtlasCanvas:
    """Two-times drawing with screen-space coordinates for crisp video labels."""

    def __init__(self, size: tuple[int, int], night: float = 0) -> None:
        self.size = size
        self.image = Image.new("RGBA", (size[0] * 2, size[1] * 2))
        self.draw = ThemeDraw(ImageDraw.Draw(self.image), night)

    def line(self, points: list | tuple, fill, width: float = 1) -> None:
        self.draw.line(
            [(x * 2, y * 2) for x, y in points],
            fill=fill,
            width=max(1, round(width * 2)),
            joint="curve",
        )

    def circle(
        self, x: float, y: float, radius: float, fill, outline=None, width: float = 1
    ) -> None:
        self.draw.ellipse(
            ((x - radius) * 2, (y - radius) * 2, (x + radius) * 2, (y + radius) * 2),
            fill=fill,
            outline=outline,
            width=max(1, round(width * 2)),
        )

    def rectangle(
        self, box: tuple, fill, radius: float = 0, outline=None, width: float = 1
    ) -> None:
        self.draw.rounded_rectangle(
            tuple(v * 2 for v in box),
            radius=round(radius * 2),
            fill=fill,
            outline=outline,
            width=max(1, round(width * 2)),
        )

    def polygon(self, points: list, fill) -> None:
        self.draw.polygon([(x * 2, y * 2) for x, y in points], fill=fill)

    def text(
        self,
        xy: tuple,
        text: str,
        size: float,
        fill=INK,
        bold: bool = False,
        anchor: str = "lt",
        halo: bool = False,
    ) -> None:
        font = atlas_font(
            max(1, round(size * 2)), bold, any(ord(c) > 0x2FFF for c in text)
        )
        self.draw.text(
            (xy[0] * 2, xy[1] * 2),
            text,
            font=font,
            fill=fill,
            anchor=anchor,
            stroke_width=3 if halo else 0,
            stroke_fill=PAPER if halo else None,
        )

    def measure(self, text: str, size: float, bold: bool = False) -> float:
        return (
            atlas_font(
                max(1, round(size * 2)), bold, any(ord(c) > 0x2FFF for c in text)
            ).getlength(text)
            / 2
        )

    def fit(self, text: str, size: float, width: float, bold: bool = False) -> str:
        if self.measure(text, size, bold) <= width:
            return text
        while text and self.measure(text + "…", size, bold) > width:
            text = text[:-1]
        return text + "…"

    def composite(self, background: Image.Image) -> Image.Image:
        foreground = self.image.resize(self.size, Image.Resampling.LANCZOS)
        return Image.alpha_composite(background.convert("RGBA"), foreground).convert(
            "RGB"
        )


def tint(color: str, opacity: int) -> tuple[int, int, int, int]:
    return (*ImageColor.getrgb(color), max(0, min(255, opacity)))


class AtlasPresentation:
    def __init__(self, trip, basemap) -> None:
        self.trip, self.basemap = trip, basemap
        self.u = min(trip.width / 1280, trip.height / 720)
        self._locator_cache: dict = {}
        self._distances: dict[int, float] = {}
        self._ending_tickets: dict = {}
        self.night = 0.0

    def distance(self, leg) -> float:
        if id(leg) not in self._distances:
            self._distances[id(leg)] = polyline_lengths(leg.path)[1]
        return self._distances[id(leg)]

    @staticmethod
    def distance_label(meters: float) -> str:
        return f"{meters/1000:.1f} km" if meters >= 1000 else f"{round(meters):,} m"

    def dashed(self, c: AtlasCanvas, points: list, color, width: float) -> None:
        # Carry the dash phase through vertices to prevent tiny fragmented dots.
        phase, dash, period = 0.0, 5 * self.u, 10 * self.u
        for start, end in zip(points, points[1:]):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            cursor = 0.0
            while cursor < length:
                visible = phase < dash
                step = min(length - cursor, (dash if visible else period) - phase)
                if visible and step > 0:
                    c.line(
                        [
                            (
                                start[0] + dx * cursor / length,
                                start[1] + dy * cursor / length,
                            ),
                            (
                                start[0] + dx * (cursor + step) / length,
                                start[1] + dy * (cursor + step) / length,
                            ),
                        ],
                        color,
                        width,
                    )
                cursor += step
                phase = (phase + step) % period

    def routes(
        self,
        c: AtlasCanvas,
        legs: list,
        index: int,
        progress: float,
        camera,
        complete=False,
    ) -> None:
        u = self.u
        # Geographic scaling, with a fine overview floor for legibility and a
        # close-up cap to keep the itinerary from obscuring nearby map details.
        route_width = max(1.4, min(5.0, 3.5 * 2 ** ((camera.zoom - 15) / 2))) * u
        # Transfers and walking move the camera without drawing a guessed route.
        for n, leg in enumerate(legs if complete else legs[: index + 1]):
            points = [camera.point(p) for p in leg.path]
            active = complete or n < index
            if not active:
                points = [camera.point(p) for p in prefix_path(leg.path, progress)]
            if leg.leg.mode == "walk":
                self.dashed(c, points, tint(MUTED, 190), max(0.6 * u, route_width * 0.65))
                continue
            if len(points) >= 2:
                c.line(points, tint(leg.color, 240 if n == index else 215), route_width)

    def marker(
        self, c: AtlasCanvas, opacity: int = 255, point: tuple | None = None
    ) -> None:
        x, y = (
            point if point is not None else (self.trip.width / 2, self.trip.height / 2)
        )
        u = self.u
        c.circle(x, y, 5 * u, tint(BLUE, opacity), tint("#FFFFFF", opacity), 1.25 * u)

    def labels(
        self, c: AtlasCanvas, legs: list, index: int, camera, complete=False
    ) -> None:
        u = self.u
        candidates = []
        if not complete:
            leg = legs[index]
            candidates = [
                (leg.leg.origin.name, leg.path[0], INK),
                (leg.leg.destination.name, leg.path[-1], leg.color),
            ]
        candidates += (
            [
                (legs[0].leg.origin.name, legs[0].path[0], INK),
                (legs[-1].leg.destination.name, legs[-1].path[-1], BLUE),
            ]
            if complete
            else []
        )
        if camera.zoom < 11:
            for leg in legs:
                for station in leg.stations:
                    name = next(
                        (
                            alias
                            for alias in station.aliases
                            if alias in {
                                "Kobe", "Kyoto", "Nagoya", "Shizuoka",
                                "Shin-Yokohama", "Tokyo",
                            }
                        ),
                        None,
                    )
                    if name:
                        candidates.append((name, (station.lat, station.lon), MUTED))
        occupied = [
            (24 * u, 24 * u, 438 * u, 198 * u),
            (self.trip.width - 208 * u, 24 * u, self.trip.width - 24 * u, 206 * u),
        ]
        accepted = []
        for name, coordinate, color in candidates:
            x, y = camera.point(coordinate)
            # Tokyo Station and Haneda are close at the final national view,
            # but both labels carry useful, distinct meaning.
            separation = 20 if complete and name == "Tokyo" else 45
            if any(math.hypot(x - a, y - b) < separation * u for a, b in accepted):
                continue
            if not (
                24 * u < x < self.trip.width - 24 * u
                and 24 * u < y < self.trip.height - 66 * u
            ):
                continue
            c.circle(x, y, 4.5 * u, PAPER, color, 2 * u)
            accepted.append((x, y))
            label = c.fit(name, 12 * u, 180 * u, True)
            tw = c.measure(label, 12 * u, True)
            endpoint_label = complete and coordinate == legs[-1].path[-1]
            side_gap = (28 if endpoint_label else 11) * u
            for dx, dy in (
                (side_gap, -19 * u),
                (side_gap, 10 * u),
                (-tw - side_gap, -19 * u),
                (-tw - side_gap, 10 * u),
            ):
                left, top = x + dx, y + dy
                box = (left - 3 * u, top - 3 * u, left + tw + 3 * u, top + 16 * u)
                if (
                    box[0] < 20 * u
                    or box[2] > self.trip.width - 20 * u
                    or box[1] < 20 * u
                    or box[3] > self.trip.height - 60 * u
                ):
                    continue
                if any(
                    not (box[2] < a or box[0] > d or box[3] < b or box[1] > e)
                    for a, b, d, e in occupied
                ):
                    continue
                if (
                    not complete
                    and abs(x - self.trip.width / 2) < 52 * u
                    and abs(y - self.trip.height / 2) < 45 * u
                ):
                    continue
                c.text((left, top), label, 12 * u, INK, bold=True, halo=True)
                occupied.append(box)
                break

    def paper_panel(self, c: AtlasCanvas, box: tuple, radius: float = 14) -> None:
        u = self.u
        x0, y0, x1, y1 = box
        c.rectangle(
            (x0 - 2 * u, y0 + 3 * u, x1 + 2 * u, y1 + 7 * u), tint(INK, 5), radius * u
        )
        c.rectangle((x0, y0 + 2 * u, x1, y1 + 4 * u), tint(INK, 8), radius * u)
        c.rectangle(
            box,
            (255, 255, 252, 245),
            radius * u,
            outline=(224, 232, 228, 210),
            width=0.7 * u,
        )

    def ticket(
        self, c: AtlasCanvas, legs: list, index: int, complete: bool = False
    ) -> None:
        u = self.u
        x, y, w, h = 32 * u, 32 * u, 394 * u, 153 * u
        self.paper_panel(c, (x, y, x + w, y + h))
        leg = legs[index]
        title = self.trip.title if complete else leg.leg.display_line
        color = BLUE if complete else leg.color
        kicker = (
            "JOURNEY COMPLETE"
            if complete
            else f"{leg.leg.mode.upper()}   /   {index+1:02d} — {len(legs):02d}"
        )
        c.line([(x + 24 * u, y + 25 * u), (x + 42 * u, y + 25 * u)], color, 3 * u)
        c.text((x + 51 * u, y + 19 * u), kicker, 10.5 * u, MUTED, bold=True)
        if not complete and leg.leg.when:
            c.text(
                (x + w - 24 * u, y + 19 * u),
                leg.leg.when.rsplit(" ", 1)[-1],
                11 * u,
                MUTED,
                anchor="rt",
            )
        size = 25 * u
        while size > 19 * u and c.measure(title, size, True) > w - 48 * u:
            size -= u
        c.text(
            (x + 24 * u, y + 47 * u),
            c.fit(title, size, w - 48 * u, True),
            size,
            INK,
            bold=True,
        )
        first = legs[0].leg.origin.name if complete else leg.leg.origin.name
        last = legs[-1].leg.destination.name if complete else leg.leg.destination.name
        direction = f"{first}   to   {last}"
        c.text(
            (x + 24 * u, y + 85 * u),
            c.fit(direction, 14 * u, w - 48 * u),
            14 * u,
            MUTED,
        )
        c.line(
            [(x + 24 * u, y + 112 * u), (x + w - 24 * u, y + 112 * u)],
            "#E5EBE7",
            0.8 * u,
        )
        distance = (
            sum(self.distance(item) for item in legs)
            if complete
            else self.distance(leg)
        )
        c.text(
            (x + 24 * u, y + 124 * u),
            self.distance_label(distance),
            11 * u,
            INK,
            bold=True,
        )
        note = f"{len(legs)} connected legs" if complete else "TRAVEL RECORD"
        c.text((x + w - 24 * u, y + 124 * u), note, 10 * u, MUTED, anchor="rt")

    def locator(self, c: AtlasCanvas, legs: list, coordinate: tuple) -> None:
        u = self.u
        x, y, w, h = self.trip.width - 198 * u, 32 * u, 166 * u, 166 * u
        self.paper_panel(c, (x, y, x + w, y + h))
        c.text((x + 18 * u, y + 17 * u), "JAPAN", 10.5 * u, MUTED, bold=True)
        # The tiny N is an orientation cue, not a replacement for the blue marker.
        c.text((x + w - 19 * u, y + 17 * u), "N", 9.5 * u, MUTED, anchor="rt")
        key = (id(legs), round(w), round(h))
        if key not in self._locator_cache:
            mini = AtlasCanvas((round(w), round(h - 40 * u)))
            a, b = world_point(46, 128), world_point(30, 147)
            scale = min((w - 30 * u) / (b[0] - a[0]), (h - 54 * u) / (b[1] - a[1]))

            def project(point):
                p = world_point(*point)
                return (p[0] - (a[0] + b[0]) / 2) * scale + w / 2, (
                    p[1] - (a[1] + b[1]) / 2
                ) * scale + (h - 40 * u) / 2

            for (
                is_japan,
                (south, west, north, east),
                rings,
            ) in self.basemap._load_land_polygons():
                if not is_japan or north < 29 or west > 147:
                    continue
                mini.polygon([project(p) for p in rings[0]], "#D9E3DD")
            for leg in legs:
                mini.line([project(p) for p in leg.path], "#76999F", 1.4 * u)
            self._locator_cache[key] = (mini.image, project)
        miniature, project = self._locator_cache[key]
        c.image.alpha_composite(apply_theme(miniature, self.night), (round(x * 2), round((y + 37 * u) * 2)))
        px, py = project(coordinate)
        if 0 < px < w and 0 < py < h - 40 * u:
            c.circle(x + px, y + 37 * u + py, 4 * u, BLUE, "#FFFFFF", 1.3 * u)

    def map_notes(self, c: AtlasCanvas, camera) -> None:
        u = self.u
        x, y = 32 * u, self.trip.height - 54 * u
        mpp = 156543.03392 * math.cos(math.radians(camera.center[0])) / 2**camera.zoom
        target = 104 * u * mpp
        power = 10 ** math.floor(math.log10(max(target, 0.001)))
        meters = max(
            candidate * power
            for candidate in (1, 2, 5, 10)
            if candidate * power <= target
        )
        bar = meters / mpp
        label = f"{meters/1000:g} km" if meters >= 1000 else f"{meters:g} m"
        c.rectangle(
            (x - 10 * u, y - 8 * u, x + max(bar, 70 * u) + 10 * u, y + 31 * u),
            tint(PAPER, 235),
            radius=6 * u,
        )
        c.text((x, y), label, 11 * u, INK, bold=True)
        by = y + 22 * u
        c.line([(x, by), (x + bar, by)], INK, 2 * u)
        c.line([(x + bar / 2, by), (x + bar, by)], "#A8B8B7", 2 * u)
        for tick in (x, x + bar):
            c.line([(tick, by - 3 * u), (tick, by + 3 * u)], INK, 1 * u)
        if self.trip.basemap != "none":
            c.text(
                (self.trip.width - 32 * u, self.trip.height - 18 * u),
                "© OpenStreetMap contributors · Natural Earth",
                10 * u,
                MUTED,
                halo=True,
                anchor="rt",
            )

    def frame(
        self, legs: list, index: int, progress: float, camera, background: Image.Image
    ) -> Image.Image:
        c = AtlasCanvas(background.size, self.night)
        self.routes(c, legs, index, progress, camera)
        self.labels(c, legs, index, camera)
        self.marker(c)
        self.ticket(c, legs, index)
        self.locator(c, legs, camera.center)
        self.map_notes(c, camera)
        return c.composite(background)

    def overview(
        self, legs: list, progress: float, camera, background: Image.Image
    ) -> Image.Image:
        if progress <= 0:
            return self.frame(legs, len(legs) - 1, 1, camera, background)
        c = AtlasCanvas(background.size, self.night)
        self.routes(c, legs, len(legs) - 1, 1, camera, True)
        self.labels(c, legs, len(legs) - 1, camera, True)
        self.marker(c, point=camera.point(legs[-1].path[-1]))
        # The last leg card changes gently into the overview summary.
        key = (id(legs), self.night)
        if key not in self._ending_tickets:
            start, end = AtlasCanvas(background.size, self.night), AtlasCanvas(background.size, self.night)
            self.ticket(start, legs, len(legs) - 1)
            self.ticket(end, legs, len(legs) - 1, True)
            self._ending_tickets[key] = (start.image, end.image)
        start, end = self._ending_tickets[key]
        phase = max(0, min(1, (progress - 0.04) / 0.12))
        phase = phase * phase * (3 - 2 * phase)
        c.image.alpha_composite(Image.blend(start, end, phase))
        self.locator(c, legs, legs[-1].path[-1])
        self.map_notes(c, camera)
        return c.composite(background)
