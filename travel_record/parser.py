from __future__ import annotations

import re
from pathlib import Path

from .models import Leg, Place, Trip


class TripFormatError(ValueError):
    pass


_COORD_RE = re.compile(
    r"^(?P<name>.*?)\s*@\s*(?P<lat>[+-]?\d+(?:\.\d+)?)\s*,\s*(?P<lon>[+-]?\d+(?:\.\d+)?)\s*$"
)


def _compact_label(value: str, *, line_name: bool) -> str:
    """Turn compact ASCII slugs into readable labels without altering CJK names."""
    value = value.strip()
    if line_name:
        value = re.sub(r"\s*-\s*", " ", value)
    if value and value.isascii() and value == value.casefold():
        value = value.title()
        value = re.sub(r"\bJr\b", "JR", value)
    if value.casefold() in {"bus", "walk"}:
        return value.casefold()
    return value


def _parse_compact_routes(value: str, line_no: int) -> list[Leg]:
    if not (value.startswith("{") and value.endswith("}")):
        raise TripFormatError(f"第 {line_no} 行的紧凑行程必须用 {{...}} 包住")
    body = value[1:-1].strip()
    if not body:
        raise TripFormatError(f"第 {line_no} 行的紧凑行程不能为空")

    legs: list[Leg] = []
    for item_no, item in enumerate(body.split(";"), start=1):
        fields = [part.strip() for part in item.split(",")]
        if len(fields) == 4 and "@" in fields[2]:
            fields = [part.strip() for part in item.split(",", 2)]
        if len(fields) not in {3, 4} or not all(fields):
            raise TripFormatError(
                f"第 {line_no} 行第 {item_no} 段应为：线路名,起点,终点[,day/night]"
            )
        line_name, origin, destination = fields[:3]
        legs.append(
            Leg(
                when="",
                line=_compact_label(line_name, line_name=True),
                origin=_parse_place(_compact_label(origin, line_name=False), line_no),
                destination=_parse_place(_compact_label(destination, line_name=False), line_no),
                source_line=line_no,
                theme=_parse_theme(fields[3], line_no) if len(fields) == 4 else None,
            )
        )
    return legs


def _parse_theme(value: str, line_no: int = 0) -> str:
    aliases = {"day": "day", "night": "night", "日间": "day", "夜间": "night"}
    key = value.strip().casefold()
    if key not in aliases:
        raise TripFormatError(f"第 {line_no} 行的 theme 应为 day 或 night")
    return aliases[key]


def _parse_place(value: str, line_no: int) -> Place:
    value = value.strip()
    if not value:
        raise TripFormatError(f"第 {line_no} 行的地点不能为空")
    match = _COORD_RE.match(value)
    if not match:
        return Place(value)
    lat = float(match.group("lat"))
    lon = float(match.group("lon"))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise TripFormatError(f"第 {line_no} 行的经纬度超出范围")
    return Place(match.group("name").strip() or f"{lat:.5f},{lon:.5f}", lat, lon)


def _parse_size(value: str, line_no: int) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", value)
    if not match:
        raise TripFormatError(f"第 {line_no} 行的 size 应为 1280x720")
    width, height = map(int, match.groups())
    if width < 480 or height < 270 or width % 2 or height % 2:
        raise TripFormatError("视频宽高至少为 480x270，且都必须是偶数")
    return width, height


def _parse_bool(value: str, key: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "yes", "1", "on", "是", "开"}:
        return True
    if normalized in {"false", "no", "0", "off", "否", "关"}:
        return False
    raise TripFormatError(f"{key} 应为 true 或 false")


def parse_trip(text: str, source_path: Path | None = None) -> Trip:
    settings: dict[str, str] = {}
    route_items: list[Leg | tuple[int, str]] = []
    in_routes = False

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.casefold() in {"route:", "routes:", "行程:", "轨迹:"}:
            in_routes = True
            continue
        if line.startswith("{"):
            route_items.extend(_parse_compact_routes(line, line_no))
            in_routes = True
            continue
        if not in_routes and ":" in line and "|" not in line:
            key, value = line.split(":", 1)
            settings[key.strip().casefold()] = value.strip()
            continue
        if "|" in line:
            route_items.append((line_no, line.lstrip("- ").strip()))
            in_routes = True
            continue
        raise TripFormatError(f"无法识别第 {line_no} 行：{raw}")

    if not route_items:
        raise TripFormatError(
            "没有找到行程。请使用：时间 | 线路名 | 起点 -> 终点，"
            "或 {线路名,起点,终点;...}"
        )

    legs: list[Leg] = []
    for item in route_items:
        if isinstance(item, Leg):
            legs.append(item)
            continue
        line_no, line = item
        fields = [part.strip() for part in line.split("|")]
        if len(fields) not in {3, 4} or not all(fields):
            raise TripFormatError(f"第 {line_no} 行应为：时间 | 线路名 | 路径 [| day/night]")
        when, line_name, route = fields[:3]
        theme = _parse_theme(fields[3], line_no) if len(fields) == 4 else None
        stops = [part.strip() for part in re.split(r"\s*(?:->|→|＞|>)\s*", route) if part.strip()]
        if len(stops) < 2:
            raise TripFormatError(f"第 {line_no} 行至少需要起点和终点")
        for index, (origin, destination) in enumerate(zip(stops, stops[1:])):
            child_when = when if index == 0 else f"{when} · {index + 1}"
            legs.append(
                Leg(
                    when=child_when,
                    line=line_name,
                    origin=_parse_place(origin, line_no),
                    destination=_parse_place(destination, line_no),
                    source_line=line_no,
                    theme=theme,
                )
            )

    default_theme = _parse_theme(settings.get("theme", "day"))
    current_theme = default_theme
    for leg in legs:
        current_theme = leg.theme or current_theme
        leg.theme = current_theme
    width, height = _parse_size(settings.get("size", "1280x720"), 0)
    try:
        fps = int(settings.get("fps", "24"))
        seconds = float(settings.get("seconds_per_leg", "3"))
        max_zoom_speed = float(settings.get("max_zoom_levels_per_second", "0.9"))
        waypoint_pause = float(settings.get("waypoint_pause_seconds", "0.55"))
        ending_overview_seconds = float(settings.get("ending_overview_seconds", "7"))
        end_hold = float(settings.get("end_hold_seconds", "1"))
        theme_transition = float(settings.get("theme_transition_seconds", "1.5"))
    except ValueError as exc:
        raise TripFormatError("fps 和各项时长、速度设置（包括 theme_transition_seconds）必须是数字") from exc
    if not 1 <= fps <= 60:
        raise TripFormatError("fps 必须在 1 到 60 之间")
    if not 0 <= theme_transition <= 10:
        raise TripFormatError("theme_transition_seconds 必须为 0–10 秒")
    if not 0.5 <= seconds <= 60 or not 0 <= end_hold <= 30:
        raise TripFormatError("每段时长须为 0.5–60 秒，结尾停留须为 0–30 秒")
    if not 0.1 <= max_zoom_speed <= 4:
        raise TripFormatError("max_zoom_levels_per_second 必须在 0.1 到 4 之间")
    if not 0 <= waypoint_pause <= 10 or not 1 <= ending_overview_seconds <= 60:
        raise TripFormatError("到站停顿须为 0–10 秒，结尾总览须为 1–60 秒")

    return Trip(
        title=settings.get("title", source_path.stem if source_path else "Travel Record"),
        legs=legs,
        width=width,
        height=height,
        fps=fps,
        seconds_per_leg=seconds,
        max_zoom_levels_per_second=max_zoom_speed,
        waypoint_pause_seconds=waypoint_pause,
        ending_overview_seconds=ending_overview_seconds,
        ending_overview=_parse_bool(settings.get("ending_overview", "true"), "ending_overview"),
        end_hold_seconds=end_hold,
        basemap=settings.get("basemap", "osm").casefold(),
        locale=settings.get("locale", "en"),
        source_path=source_path,
        theme=default_theme,
        theme_transition_seconds=theme_transition,
    )


def load_trip(path: Path) -> Trip:
    return parse_trip(path.read_text(encoding="utf-8"), path)
