from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from difflib import SequenceMatcher
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from typing import Any

from PIL import ImageColor

from .geo import decode_google_polyline, graph_path, haversine, normalize_name
from .models import Leg, Place, ResolvedLeg, Station, Trip


OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
REGION_BBOXES = {
    "tokyo": (35.50, 139.45, 35.90, 140.00),
    "kanto": (35.20, 139.25, 35.90, 140.10),
    "osaka": (34.45, 135.20, 34.90, 135.80),
    "kyoto": (34.82, 135.50, 35.18, 135.92),
}


def _line_core(value: str) -> str:
    value = str(value)
    for separator in ("(", "（", ":", "：", "→", "->", "=>"):
        value = value.split(separator, 1)[0]
    result = normalize_name(value)
    for generic in (
        "tokyometro",
        "tokyosubway",
        "jrwest",
        "jreast",
        "jrcentral",
        "railway",
        "subway",
        "train",
        "line",
        "東京メトロ",
        "東京都営",
        "大阪メトロ",
        "osakametro",
        "京都市営地下鉄",
        "地下鉄",
    ):
        result = result.replace(normalize_name(generic), "")
    if result.endswith("線") and len(result) > 1:
        result = result[:-1]
    return result


class DataSourceError(RuntimeError):
    pass


def _route_color(value: Any, fallback: str = "#2463A8") -> str:
    """Normalize OSM's hex and named colour tags to a renderer-safe hex value."""
    raw = str(value or fallback).strip()
    candidate = raw if raw.startswith("#") else raw.lstrip("#")
    if not candidate.startswith("#"):
        try:
            red, green, blue = ImageColor.getrgb(candidate)
            return f"#{red:02X}{green:02X}{blue:02X}"
        except ValueError:
            return fallback
    if len(candidate) in {4, 7}:
        try:
            int(candidate[1:], 16)
            return candidate
        except ValueError:
            pass
    return fallback


class HttpCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.offline = os.environ.get("TRAVEL_RECORD_OFFLINE", "").casefold() in {"1", "true", "yes"}
        self.user_agent = os.environ.get(
            "TRAVEL_RECORD_USER_AGENT",
            "TravelRecord/0.1 (local video renderer; https://www.openstreetmap.org/copyright)",
        )

    def _path(self, namespace: str, key: str, extension: str = ".bin") -> Path:
        digest = sha256(key.encode("utf-8")).hexdigest()
        folder = self.root / namespace
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{digest}{extension}"

    def request(
        self,
        url: str,
        *,
        namespace: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        max_age: float | None = None,
        timeout: float = 120,
    ) -> bytes:
        cache_key = f"{method}\n{url}\n{data!r}"
        path = self._path(namespace, cache_key)
        if path.exists() and (self.offline or max_age is None or time.time() - path.stat().st_mtime <= max_age):
            return path.read_bytes()
        if self.offline:
            raise DataSourceError(f"离线缓存中没有此地图数据：{url}")
        request_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        request_headers.update(headers or {})
        request = urllib.request.Request(url, data=data, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            if path.exists():
                return path.read_bytes()
            raise DataSourceError(f"无法访问地图数据：{url}: {exc}") from exc
        path.write_bytes(payload)
        return payload

    def cached(self, url: str, *, namespace: str, method: str = "GET", data: bytes | None = None) -> bytes | None:
        """Read existing data without contacting a server, including stale fallback data."""
        path = self._path(namespace, f"{method}\n{url}\n{data!r}")
        return path.read_bytes() if path.exists() else None

    def json(self, url: str, **kwargs: Any) -> Any:
        payload = self.request(url, **kwargs)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            preview = payload[:240].decode("utf-8", errors="replace")
            raise DataSourceError(f"地图服务返回了非 JSON 数据：{preview}") from exc


def _resource_json(name: str) -> dict[str, Any]:
    return json.loads(files("travel_record").joinpath("data", name).read_text(encoding="utf-8"))


class PlaceResolver:
    def __init__(self, http: HttpCache) -> None:
        self.http = http
        self.known: dict[str, tuple[float, float]] = {}
        self.canonical: dict[str, str] = {}
        for item in _resource_json("places.json")["places"]:
            coordinate = (float(item["lat"]), float(item["lon"]))
            for alias in [item["name"], *item.get("aliases", [])]:
                self.known[normalize_name(alias)] = coordinate
                self.canonical[normalize_name(alias)] = item["name"]
        self._last_nominatim_request = 0.0

    def remember(self, name: str, coordinate: tuple[float, float]) -> None:
        self.known[normalize_name(name)] = coordinate

    def resolve(self, place: Place, bias: str | None = None) -> tuple[float, float]:
        if place.coordinate is not None:
            self.remember(place.name, place.coordinate)
            return place.coordinate
        key = normalize_name(place.name)
        if key in self.known:
            return self.known[key]
        query = place.name if not bias else f"{place.name}, {bias}"
        params = urllib.parse.urlencode(
            {"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 0}
        )
        wait = 1.05 - (time.monotonic() - self._last_nominatim_request)
        if wait > 0:
            time.sleep(wait)
        data = self.http.json(
            f"https://nominatim.openstreetmap.org/search?{params}",
            namespace="nominatim",
            max_age=365 * 24 * 3600,
            timeout=30,
        )
        self._last_nominatim_request = time.monotonic()
        if not data:
            raise DataSourceError(
                f"找不到地点“{place.name}”。可写成 {place.name}@35.0,135.0 明确指定坐标。"
            )
        coordinate = (float(data[0]["lat"]), float(data[0]["lon"]))
        self.remember(place.name, coordinate)
        return coordinate


class OSMRailSource:
    def __init__(self, http: HttpCache, places: PlaceResolver) -> None:
        self.http = http
        self.places = places
        self.seed = _resource_json("line_aliases.json")["lines"]
        self.seed_by_relation: dict[int, dict[str, Any]] = {}
        for line in self.seed:
            for relation_id in line["relations"]:
                self.seed_by_relation[int(relation_id)] = line

    def _relation_metadata(self, relation_id: int) -> dict[str, Any]:
        try:
            data = self.http.json(
                f"https://api.openstreetmap.org/api/0.6/relation/{relation_id}.json",
                namespace="osm-relations-meta",
                max_age=30 * 24 * 3600,
            )
        except DataSourceError as metadata_error:
            # A completed render cache often has relation/full but not the
            # smaller metadata endpoint.  Reuse it in offline mode instead of
            # falsely declaring a known line unavailable.
            try:
                data = self._relation_full(relation_id)
            except DataSourceError:
                raise metadata_error
        relation = next(item for item in data["elements"] if item["type"] == "relation")
        return {
            "id": relation_id,
            "tags": relation.get("tags", {}),
            "members": relation.get("members", []),
        }

    def _overpass(self, query: str) -> dict[str, Any]:
        encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
        last_error: Exception | None = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                return self.http.json(
                    endpoint,
                    namespace="overpass",
                    method="POST",
                    data=encoded,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    max_age=14 * 24 * 3600,
                    timeout=150,
                )
            except DataSourceError as exc:
                last_error = exc
        raise DataSourceError(f"Overpass 暂时不可用：{last_error}")

    def discover_catalog(self) -> list[dict[str, Any]]:
        discovered: dict[int, dict[str, Any]] = {}
        for bbox in REGION_BBOXES.values():
            south, west, north, east = bbox
            query = (
                "[out:json][timeout:120];"
                "("
                f'rel["type"="route_master"]["route"~"train|subway|light_rail|tram"]'
                f"({south},{west},{north},{east});"
                f'rel["type"="route"]["route"~"train|subway|light_rail|tram|railway"]'
                f"({south},{west},{north},{east});"
                ");out ids tags center;"
            )
            for item in self._overpass(query).get("elements", []):
                if item.get("type") == "relation":
                    discovered[int(item["id"])] = {"id": int(item["id"]), "tags": item.get("tags", {})}
        for relation_id in self.seed_by_relation:
            if relation_id not in discovered:
                try:
                    discovered[relation_id] = self._relation_metadata(relation_id)
                except DataSourceError:
                    continue
        return list(discovered.values())

    @staticmethod
    def _candidate_names(item: dict[str, Any], seed: dict[str, Any] | None = None) -> list[str]:
        tags = item.get("tags", {})
        values = [
            tags.get("name"),
            tags.get("name:en"),
            tags.get("name:ja"),
            tags.get("name:zh"),
            tags.get("short_name"),
            tags.get("ref"),
        ]
        if seed:
            values.extend([seed["canonical"], *seed.get("aliases", [])])
        return [str(value) for value in values if value]

    def _score(self, query: str, item: dict[str, Any], leg: Leg) -> int:
        relation_id = int(item["id"])
        seed = self.seed_by_relation.get(relation_id)
        target = normalize_name(query)
        target_core = _line_core(query)
        score = 0
        for name in self._candidate_names(item, seed):
            candidate = normalize_name(name)
            candidate_core = _line_core(name)
            if target == candidate:
                score = max(score, 100)
            elif target and (target in candidate or candidate in target):
                score = max(score, 72)
            if target_core and candidate_core:
                if target_core == candidate_core:
                    score = max(score, 96)
                elif target_core in candidate_core or candidate_core in target_core:
                    score = max(score, 84)
                else:
                    similarity = SequenceMatcher(None, target_core, candidate_core).ratio()
                    if similarity >= 0.78:
                        score = max(score, round(similarity * 88))
        tags = item.get("tags", {})
        origin, destination = leg.origin.name, leg.destination.name
        from_name, to_name = str(tags.get("from", "")), str(tags.get("to", ""))
        if from_name and self._equivalent_place_names(origin, from_name):
            score += 16
        if to_name and self._equivalent_place_names(destination, to_name):
            score += 16
        if from_name and self._equivalent_place_names(destination, from_name):
            score -= 4
        return score

    def _equivalent_place_names(self, first: str, second: str) -> bool:
        left, right = normalize_name(first), normalize_name(second)
        if left and right and (left == right or left in right or right in left):
            return True
        left_coord = self.places.known.get(left)
        right_coord = self.places.known.get(right)
        if left_coord and right_coord:
            from .geo import haversine

            return haversine(left_coord, right_coord) < 900
        return False

    def select_relation(self, leg: Leg) -> dict[str, Any]:
        query_key = normalize_name(leg.line)
        seeded_ids: list[int] = []
        for line in self.seed:
            names = [line["canonical"], *line.get("aliases", [])]
            if any(normalize_name(name) == query_key for name in names):
                seeded_ids.extend(int(value) for value in line["relations"])
        candidates: list[dict[str, Any]] = []
        for relation_id in seeded_ids:
            try:
                candidates.append(self._relation_metadata(relation_id))
            except DataSourceError:
                continue
        if seeded_ids and not candidates:
            raise DataSourceError(
                f"已识别线路“{leg.line}”，但其线路数据尚不可用。"
                "请联网加载该线路数据；不会用相似名称的其他线路代替。"
            )
        if not candidates:
            candidates = self.discover_catalog()
        ranked = sorted(((self._score(leg.line, item, leg), item) for item in candidates), reverse=True, key=lambda pair: pair[0])
        if not ranked or ranked[0][0] < 65:
            raise DataSourceError(
                f"找不到铁路线路“{leg.line}”。请使用 catalog 命令查看线路名称，或改用精确的 OSM 线路名称。"
            )
        selected = ranked[0][1]
        if selected.get("tags", {}).get("type") == "route_master":
            full_master = self._relation_metadata(int(selected["id"]))
            children = []
            for member in full_master.get("members", []):
                if member.get("type") != "relation":
                    continue
                try:
                    children.append(self._relation_metadata(int(member["ref"])))
                except DataSourceError:
                    continue
            child_ranked = sorted(
                ((self._score(leg.line, item, leg), item) for item in children),
                reverse=True,
                key=lambda pair: pair[0],
            )
            if not child_ranked:
                raise DataSourceError(f"线路“{leg.line}”没有可用的方向关系。")
            return child_ranked[0][1]
        return selected

    def _relation_full(self, relation_id: int) -> dict[str, Any]:
        return self.http.json(
            f"https://api.openstreetmap.org/api/0.6/relation/{relation_id}/full.json",
            namespace="osm-relations-full",
            max_age=30 * 24 * 3600,
            timeout=180,
        )

    @staticmethod
    def _station_aliases(tags: dict[str, Any]) -> list[str]:
        return [
            str(tags[key])
            for key in ("name", "name:en", "name:ja", "name:zh", "short_name", "official_name")
            if tags.get(key)
        ]

    def _extract_stations(
        self,
        relation: dict[str, Any],
        nodes: dict[int, dict[str, Any]],
        ways: dict[int, dict[str, Any]],
    ) -> list[Station]:
        stations: list[Station] = []
        seen: set[str] = set()
        direct_members = relation.get("members", [])
        preferred_ids = [
            int(member["ref"])
            for member in direct_members
            if member.get("type") == "node"
            and member.get("role", "") in {"", "stop", "stop_entry_only", "stop_exit_only", "platform"}
        ]
        candidates = [nodes[node_id] for node_id in preferred_ids if node_id in nodes]
        candidates.extend(
            node
            for node in nodes.values()
            if node.get("tags", {}).get("railway") in {"station", "halt", "stop"}
            or node.get("tags", {}).get("public_transport") in {"station", "stop_position", "platform"}
        )
        for node in candidates:
            tags = node.get("tags", {})
            aliases = self._station_aliases(tags)
            if not aliases or "lat" not in node or "lon" not in node:
                continue
            key = normalize_name(aliases[0])
            if key in seen:
                continue
            seen.add(key)
            stations.append(Station(aliases[0], float(node["lat"]), float(node["lon"]), aliases[1:]))
        return stations

    @staticmethod
    def _match_station(name: str, stations: list[Station]) -> Station | None:
        # Verified spelling error in OSM relation 1864758 (新今宮).
        corrections = {normalize_name("Shin-Imaimiya"): normalize_name("Shin-Imamiya")}
        target = normalize_name(name)
        target = corrections.get(target, target)
        ranked: list[tuple[int, Station]] = []
        for station in stations:
            score = 0
            for alias in [station.name, *station.aliases]:
                candidate = normalize_name(alias)
                candidate = corrections.get(candidate, candidate)
                if candidate == target:
                    score = max(score, 100)
                elif target and target in candidate:
                    score = max(score, 70 - abs(len(target) - len(candidate)))
            ranked.append((score, station))
        if not ranked:
            return None
        score, station = max(ranked, key=lambda pair: pair[0])
        return station if score >= 55 else None

    def _validate_service_direction(self, leg, relation, nodes):
        """Validate ordered PTv2 stop members, never infrastructure station pools.

        Matching stop order is necessary, not proof of correct track geometry.
        In particular it cannot verify loops, directional tracks or crossovers.
        """
        tags = relation.get("tags", {})
        result = {"status": "unverified", "track_geometry": "unverified"}
        if tags.get("type") != "route" or tags.get("route") != "train" or str(tags.get("public_transport:version")) != "2":
            result["reason"] = "No ordered PTv2 train service stops"
            return result
        stops = []
        for member in relation.get("members", []):
            if member.get("type") != "node" or member.get("role") not in {"stop", "stop_entry_only", "stop_exit_only"}:
                continue
            node = nodes.get(member["ref"], {})
            aliases = self._station_aliases(node.get("tags", {}))
            if aliases and "lat" in node and "lon" in node:
                stops.append(Station(aliases[0], node["lat"], node["lon"], aliases[1:]))
        origins = [i for i, station in enumerate(stops) if self._match_station(leg.origin.name, [station])]
        destinations = [i for i, station in enumerate(stops) if self._match_station(leg.destination.name, [station])]
        if len(origins) != 1 or len(destinations) != 1 or origins == destinations:
            result["reason"] = "Missing or ambiguous endpoint in ordered service stops"
            return result
        if origins[0] > destinations[0]:
            raise DataSourceError(
                f"线路关系 {relation.get('id')} 的停站顺序与“{leg.origin.name} → {leg.destination.name}”相反。"
                "拒绝反向套用服务轨道；请查找对应方向的关系并核对上下行分离、环线和道岔。不能改成步行。"
            )
        result.update(status="stop_order_matches", reason="Input endpoints follow PTv2 stop order")
        return result

    def resolve(self, leg: Leg) -> ResolvedLeg:
        selected = self.select_relation(leg)
        relation_id = int(selected["id"])
        data = self._relation_full(relation_id)
        elements = data.get("elements", [])
        nodes = {int(item["id"]): item for item in elements if item.get("type") == "node"}
        ways = {int(item["id"]): item for item in elements if item.get("type") == "way"}
        relation = next(
            item for item in elements if item.get("type") == "relation" and int(item["id"]) == relation_id
        )
        tags = relation.get("tags", {})
        direction_validation = self._validate_service_direction(leg, relation, nodes)
        stations = self._extract_stations(relation, nodes, ways)

        start_station = self._match_station(leg.origin.name, stations)
        end_station = self._match_station(leg.destination.name, stations)
        start = (
            (start_station.lat, start_station.lon)
            if start_station
            else self.places.resolve(leg.origin, tags.get("network:en") or "Japan")
        )
        end = (
            (end_station.lat, end_station.lon)
            if end_station
            else self.places.resolve(leg.destination, tags.get("network:en") or "Japan")
        )
        self.places.remember(leg.origin.name, start)
        self.places.remember(leg.destination.name, end)

        direct_way_ids = {
            int(member["ref"])
            for member in relation.get("members", [])
            if member.get("type") == "way" and "platform" not in member.get("role", "")
        }
        coordinates = {
            node_id: (float(node["lat"]), float(node["lon"]))
            for node_id, node in nodes.items()
            if "lat" in node and "lon" in node
        }
        edges: list[tuple[int, int]] = []
        for way_id in direct_way_ids:
            way = ways.get(way_id)
            if not way:
                continue
            way_tags = way.get("tags", {})
            if way_tags.get("public_transport") == "platform" or way_tags.get("railway") == "platform":
                continue
            way_nodes = [int(value) for value in way.get("nodes", [])]
            edges.extend(zip(way_nodes, way_nodes[1:]))

        rail_node_ids = {node_id for edge in edges for node_id in edge if node_id in coordinates}
        if rail_node_ids:
            start_gap = min(haversine(start, coordinates[node_id]) for node_id in rail_node_ids)
            end_gap = min(haversine(end, coordinates[node_id]) for node_id in rail_node_ids)
            if start_gap > 3000 or end_gap > 3000:
                missing = leg.origin.name if start_gap >= end_gap else leg.destination.name
                raise DataSourceError(
                    f"线路“{leg.line}”不经过“{missing}”。请按实际线路分段，"
                    "或使用贯通列车的服务名称。"
                )
        path = graph_path(edges, coordinates, start, end)

        seed = self.seed_by_relation.get(relation_id, {})
        color = _route_color(tags.get("colour") or seed.get("color"))
        return ResolvedLeg(
            leg=leg,
            path=path,
            color=color,
            source="OpenStreetMap railway relation",
            relation_id=relation_id,
            direction_from=leg.origin.name,
            direction_to=leg.destination.name,
            stations=stations,
            notes=["方向检查仅核对列车停站顺序；上下行实际轨道、环线和道岔尚未验证，连通性与里程不能替代这些检查。"],
            details={
                "direction_validation": direction_validation,
                "canonical_line": seed.get("canonical"),
                "osm_name": tags.get("name"),
                "osm_name_en": tags.get("name:en"),
                "operator": tags.get("operator:en") or tags.get("operator"),
                "network": tags.get("network:en") or tags.get("network"),
                "osm_direction": {"from": tags.get("from"), "to": tags.get("to")},
            },
        )


class GoogleTransitSource:
    def __init__(self, http: HttpCache) -> None:
        self.http = http
        self.api_key = os.environ.get("GOOGLE_MAPS_API_KEY")

    def bus_path(
        self, start: tuple[float, float], end: tuple[float, float], locale: str
    ) -> tuple[list[tuple[float, float]], dict[str, Any]] | None:
        if not self.api_key:
            return None
        body = json.dumps(
            {
                "origin": {"location": {"latLng": {"latitude": start[0], "longitude": start[1]}}},
                "destination": {"location": {"latLng": {"latitude": end[0], "longitude": end[1]}}},
                "travelMode": "TRANSIT",
                "transitPreferences": {"allowedTravelModes": ["BUS"], "routingPreference": "LESS_WALKING"},
                "languageCode": locale,
                "units": "METRIC",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        fields = ",".join(
            [
                "routes.legs.steps.polyline.encodedPolyline",
                "routes.legs.steps.travelMode",
                "routes.legs.steps.transitDetails",
            ]
        )
        data = self.http.json(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            namespace="google-routes",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": fields,
            },
            max_age=24 * 3600,
            timeout=60,
        )
        legs = data.get("routes", [{}])[0].get("legs", [])
        points: list[tuple[float, float]] = []
        lines: list[str] = []
        for route_leg in legs:
            for step in route_leg.get("steps", []):
                if step.get("travelMode") != "TRANSIT" or not step.get("transitDetails"):
                    continue
                transit = step["transitDetails"]
                vehicle = transit.get("transitLine", {}).get("vehicle", {}).get("type")
                if vehicle != "BUS":
                    continue
                encoded = step.get("polyline", {}).get("encodedPolyline")
                if encoded:
                    decoded = decode_google_polyline(encoded)
                    if points and decoded and points[-1] == decoded[0]:
                        points.extend(decoded[1:])
                    else:
                        points.extend(decoded)
                name = transit.get("transitLine", {}).get("nameShort") or transit.get("transitLine", {}).get("name")
                if name and name not in lines:
                    lines.append(name)
        if len(points) < 2:
            return None
        return points, {"operator_lines": lines}


class OpenStreetMapRouteSource:
    """Optional public-road geometry for walking and taxi legs.

    The two endpoints remain the canonical record; this layer only improves the
    visual polyline.  A failed public router is deliberately non-fatal.
    """

    def __init__(self, http: HttpCache) -> None:
        self.http = http
        self.foot_url = os.environ.get(
            "TRAVEL_RECORD_FOOT_ROUTER_URL",
            "https://routing.openstreetmap.de/routed-foot/route/v1/driving",
        ).rstrip("/")
        self.driving_url = os.environ.get(
            "TRAVEL_RECORD_DRIVING_ROUTER_URL",
            "https://router.project-osrm.org/route/v1/driving",
        ).rstrip("/")

    def _route(
        self, base_url: str, start: tuple[float, float], end: tuple[float, float], namespace: str
    ) -> tuple[list[tuple[float, float]], dict[str, Any]] | None:
        coordinates = f"{start[1]:.7f},{start[0]:.7f};{end[1]:.7f},{end[0]:.7f}"
        query = urllib.parse.urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
        try:
            data = self.http.json(f"{base_url}/{coordinates}?{query}", namespace=namespace,
                                  max_age=90 * 24 * 3600, timeout=60)
        except DataSourceError:
            return None
        route = data.get("routes", [{}])[0]
        geometry = route.get("geometry", {}).get("coordinates", [])
        points = [(float(lat), float(lon)) for lon, lat in geometry if len((lon, lat)) == 2]
        if len(points) < 2:
            return None
        return points, {"distance_meters": route.get("distance"), "duration_seconds": route.get("duration")}

    def walking_path(self, start: tuple[float, float], end: tuple[float, float]):
        return self._route(self.foot_url, start, end, "osm-foot-routes")

    def taxi_path(self, start: tuple[float, float], end: tuple[float, float]):
        return self._route(self.driving_url, start, end, "osm-driving-routes")


class TripResolver:
    def __init__(self, cache_dir: Path) -> None:
        self.http = HttpCache(cache_dir)
        self.places = PlaceResolver(self.http)
        self.rail = OSMRailSource(self.http, self.places)
        self.google = GoogleTransitSource(self.http)
        self.router = OpenStreetMapRouteSource(self.http)

    def resolve_leg(self, leg: Leg, locale: str = "en") -> ResolvedLeg:
        if leg.mode == "rail":
            return self.rail.resolve(leg)
        start = self.places.resolve(leg.origin)
        end = self.places.resolve(leg.destination)
        if leg.mode == "walk":
            routed = self.router.walking_path(start, end)
            if routed:
                path, details = routed
                return ResolvedLeg(
                    leg=leg,
                    path=path,
                    color="#6B7280",
                    source="OpenStreetMap foot routing",
                    details=details,
                )
            return ResolvedLeg(
                leg=leg,
                path=[start, end],
                color="#6B7280",
                source="straight walking guide",
                notes=["步行段不请求道路路径，仅显示简化方向。"],
            )
        if leg.mode == "taxi":
            routed = self.router.taxi_path(start, end)
            if routed:
                path, details = routed
                return ResolvedLeg(
                    leg=leg,
                    path=path,
                    color="#C9982B",
                    source="OpenStreetMap driving routing",
                    details=details,
                )
            return ResolvedLeg(
                leg=leg,
                path=[start, end],
                color="#C9982B",
                source="straight taxi guide",
                notes=["出租车段不请求道路路径，仅显示简化方向。"],
            )
        google = self.google.bus_path(start, end, locale)
        if google:
            path, details = google
            return ResolvedLeg(
                leg=leg,
                path=path,
                color="#D97706",
                source="Google Routes API transit steps",
                details=details,
            )
        return ResolvedLeg(
            leg=leg,
            path=[start, end],
            color="#D97706",
            source="straight bus fallback",
            notes=["未设置 GOOGLE_MAPS_API_KEY；公交段使用端点直线。"],
        )

    def resolve_trip(self, trip: Trip) -> list[ResolvedLeg]:
        return [self.resolve_leg(leg, trip.locale) for leg in trip.legs]

    def export_catalog(self, destination: Path) -> int:
        items = self.rail.discover_catalog()
        rows = []
        for item in sorted(items, key=lambda value: (value.get("tags", {}).get("name:en", ""), value["id"])):
            tags = item.get("tags", {})
            rows.append(
                {
                    "osm_relation_id": item["id"],
                    "name": tags.get("name"),
                    "name_en": tags.get("name:en"),
                    "name_ja": tags.get("name:ja"),
                    "name_zh": tags.get("name:zh"),
                    "from": tags.get("from"),
                    "to": tags.get("to"),
                    "color": tags.get("colour"),
                    "route": tags.get("route"),
                    "network": tags.get("network:en") or tags.get("network"),
                    "operator": tags.get("operator:en") or tags.get("operator"),
                }
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lines": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(rows)


def resolved_manifest(trip: Trip, legs: list[ResolvedLeg]) -> dict[str, Any]:
    known_places = _resource_json("places.json")["places"]
    return {
        "title": trip.title,
        "video": {
            "width": trip.width,
            "height": trip.height,
            "fps": trip.fps,
            "seconds_per_leg": trip.seconds_per_leg,
            "max_zoom_levels_per_second": trip.max_zoom_levels_per_second,
            "waypoint_pause_seconds": trip.waypoint_pause_seconds,
            "ending_overview_seconds": trip.ending_overview_seconds,
            "ending_overview": trip.ending_overview,
            "theme": trip.theme,
            "theme_transition_seconds": trip.theme_transition_seconds,
        },
        "known_places": known_places,
        "legs": [
            {
                "when": item.leg.when,
                "line": item.leg.display_line,
                "theme": item.leg.theme or trip.theme,
                "requested_line": item.leg.line,
                "mode": item.leg.mode,
                "origin": item.leg.origin.name,
                "destination": item.leg.destination.name,
                "color": item.color,
                "source": item.source,
                "osm_relation_id": item.relation_id,
                "direction": {"from": item.direction_from, "to": item.direction_to},
                "stations": [asdict(station) for station in item.stations],
                "path": [[round(lat, 7), round(lon, 7)] for lat, lon in item.path],
                "notes": item.notes,
                "details": item.details,
            }
            for item in legs
        ],
        "attribution": [
            "Map data © OpenStreetMap contributors (ODbL)",
            "Google Routes API is used only when GOOGLE_MAPS_API_KEY is configured.",
        ],
    }
