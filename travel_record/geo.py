from __future__ import annotations

import heapq
import math
import unicodedata
from collections import defaultdict
from typing import Iterable


EARTH_RADIUS_M = 6_371_008.8


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("station", "").replace(" stn", "").replace("駅", "")
    return "".join(char for char in value if char.isalnum())


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def decode_google_polyline(encoded: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    index = lat = lon = 0
    while index < len(encoded):
        deltas: list[int] = []
        for _ in range(2):
            result = shift = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        points.append((lat / 1e5, lon / 1e5))
    return points


def polyline_lengths(path: list[tuple[float, float]]) -> tuple[list[float], float]:
    cumulative = [0.0]
    for first, second in zip(path, path[1:]):
        cumulative.append(cumulative[-1] + haversine(first, second))
    return cumulative, cumulative[-1]


def point_at(path: list[tuple[float, float]], fraction: float) -> tuple[tuple[float, float], float]:
    if len(path) < 2:
        return path[0], 0.0
    cumulative, total = polyline_lengths(path)
    target = max(0.0, min(1.0, fraction)) * total
    index = max(0, min(len(path) - 2, next((i - 1 for i, v in enumerate(cumulative) if v >= target), len(path) - 2)))
    span = cumulative[index + 1] - cumulative[index]
    local = 0.0 if span == 0 else (target - cumulative[index]) / span
    lat = path[index][0] + (path[index + 1][0] - path[index][0]) * local
    lon = path[index][1] + (path[index + 1][1] - path[index][1]) * local
    mean_lat = math.radians((path[index][0] + path[index + 1][0]) / 2)
    screen_dx = (path[index + 1][1] - path[index][1]) * math.cos(mean_lat)
    screen_dy = -(path[index + 1][0] - path[index][0])
    bearing = math.atan2(screen_dy, screen_dx)
    return (lat, lon), bearing


def prefix_path(path: list[tuple[float, float]], fraction: float) -> list[tuple[float, float]]:
    if fraction <= 0:
        return [path[0]]
    if fraction >= 1:
        return path[:]
    point, _ = point_at(path, fraction)
    cumulative, total = polyline_lengths(path)
    target = fraction * total
    result = [coord for coord, distance in zip(path, cumulative) if distance < target]
    result.append(point)
    return result


def _perpendicular_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    if start == end:
        return haversine(point, start)
    ref_lat = math.radians((start[0] + end[0]) / 2)
    scale_x = math.cos(ref_lat) * 111_320
    scale_y = 110_540
    px, py = (point[1] - start[1]) * scale_x, (point[0] - start[0]) * scale_y
    ex, ey = (end[1] - start[1]) * scale_x, (end[0] - start[0]) * scale_y
    t = max(0.0, min(1.0, (px * ex + py * ey) / (ex * ex + ey * ey)))
    return math.hypot(px - t * ex, py - t * ey)


def simplify(path: list[tuple[float, float]], tolerance_m: float = 35.0) -> list[tuple[float, float]]:
    if len(path) <= 2:
        return path[:]
    max_distance = 0.0
    max_index = 0
    for index in range(1, len(path) - 1):
        distance = _perpendicular_distance(path[index], path[0], path[-1])
        if distance > max_distance:
            max_distance, max_index = distance, index
    if max_distance <= tolerance_m:
        return [path[0], path[-1]]
    left = simplify(path[: max_index + 1], tolerance_m)
    right = simplify(path[max_index:], tolerance_m)
    return left[:-1] + right


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, value: int) -> int:
        self.parent.setdefault(value, value)
        if self.parent[value] != value:
            self.parent[value] = self.find(self.parent[value])
        return self.parent[value]

    def union(self, first: int, second: int) -> None:
        left, right = self.find(first), self.find(second)
        if left != right:
            self.parent[right] = left


def graph_path(
    edges: Iterable[tuple[int, int]],
    coordinates: dict[int, tuple[float, float]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    adjacency: dict[int, list[tuple[int, float]]] = defaultdict(list)
    union = UnionFind()
    for first, second in edges:
        if first not in coordinates or second not in coordinates:
            continue
        distance = haversine(coordinates[first], coordinates[second])
        adjacency[first].append((second, distance))
        adjacency[second].append((first, distance))
        union.union(first, second)
    if not adjacency:
        return [start, end]

    groups: dict[int, list[int]] = defaultdict(list)
    for node in adjacency:
        groups[union.find(node)].append(node)
    viable = [nodes for nodes in groups.values() if len(nodes) >= 2]
    component = min(
        viable,
        key=lambda nodes: min(haversine(start, coordinates[node]) for node in nodes)
        + min(haversine(end, coordinates[node]) for node in nodes),
    )
    # A station may have one stop_position on each of two parallel tracks.
    # Snapping each endpoint to only its nearest node can select opposite tracks;
    # the graph then takes a long trip to a remote crossover and doubles back.
    # Treat all nearby rail nodes as valid snaps and minimise the complete
    # station-to-station journey instead.
    snap_radius = 140.0
    start_gaps = {node: haversine(start, coordinates[node]) for node in component}
    end_gaps = {node: haversine(end, coordinates[node]) for node in component}
    start_nodes = [node for node, gap in start_gaps.items() if gap <= snap_radius]
    end_nodes = [node for node, gap in end_gaps.items() if gap <= snap_radius]
    if not start_nodes:
        start_nodes = [min(component, key=start_gaps.__getitem__)]
    if not end_nodes:
        end_nodes = [min(component, key=end_gaps.__getitem__)]

    distances = {node: start_gaps[node] for node in start_nodes}
    previous: dict[int, int] = {}
    queue = [(distance, node) for node, distance in distances.items()]
    heapq.heapify(queue)
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances.get(node):
            continue
        for neighbor, weight in adjacency[node]:
            candidate = distance + weight
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))
    reachable_ends = [node for node in end_nodes if node in distances]
    if not reachable_ends:
        return [start, end]
    end_node = min(reachable_ends, key=lambda node: distances[node] + end_gaps[node])
    node_path = [end_node]
    while node_path[-1] in previous:
        node_path.append(previous[node_path[-1]])
    node_path.reverse()
    result = [start]
    result.extend(coordinates[node] for node in node_path)
    result.append(end)
    return simplify(result)
