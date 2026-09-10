from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Place:
    name: str
    lat: float | None = None
    lon: float | None = None

    @property
    def coordinate(self) -> tuple[float, float] | None:
        if self.lat is None or self.lon is None:
            return None
        return self.lat, self.lon


@dataclass(slots=True)
class Leg:
    when: str
    line: str
    origin: Place
    destination: Place
    source_line: int
    theme: str | None = None

    @property
    def mode(self) -> str:
        key = self.line.strip().casefold()
        if key in {"walk", "walking", "步行", "徒歩"}:
            return "walk"
        if key in {"bus", "公交", "公交车", "巴士", "バス"}:
            return "bus"
        if key in {"taxi", "出租车", "计程车", "タクシー"}:
            return "taxi"
        return "rail"

    @property
    def display_line(self) -> str:
        return self.mode if self.mode in {"walk", "bus", "taxi"} else self.line


@dataclass(slots=True)
class Trip:
    title: str
    legs: list[Leg]
    width: int = 1280
    height: int = 720
    fps: int = 24
    seconds_per_leg: float = 3.0
    max_zoom_levels_per_second: float = 0.9
    waypoint_pause_seconds: float = 0.55
    ending_overview_seconds: float = 7.0
    ending_overview: bool = True
    end_hold_seconds: float = 1.0
    basemap: str = "osm"
    locale: str = "en"
    source_path: Path | None = None
    theme: str = "day"
    theme_transition_seconds: float = 1.5


@dataclass(slots=True)
class Station:
    name: str
    lat: float
    lon: float
    aliases: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolvedLeg:
    leg: Leg
    path: list[tuple[float, float]]
    color: str
    source: str
    relation_id: int | None = None
    direction_from: str | None = None
    direction_to: str | None = None
    stations: list[Station] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
