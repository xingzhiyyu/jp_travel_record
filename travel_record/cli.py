from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import TripFormatError, load_trip
from .renderer import VideoRenderer
from .sources import DataSourceError, HttpCache, TripResolver, resolved_manifest
from .coastline import acquire_region


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="travel-record", description="把格式化行程文本渲染为地图轨迹 MP4。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="解析文本并渲染视频")
    render.add_argument("input", type=Path, help="UTF-8 行程文本")
    render.add_argument("-o", "--output", type=Path, help="输出 MP4 路径")
    render.add_argument("--cache-dir", type=Path, default=Path(".cache/travel-record"))
    render.add_argument("--resolve-only", action="store_true", help="只生成解析后的 JSON，不编码视频")
    render.add_argument(
        "--workers",
        type=int,
        default=1,
        help="并行绘图进程数；建议 Apple Silicon 使用 4",
    )

    catalog = subparsers.add_parser("catalog", help="更新东京、京都和大阪都市圈线路目录")
    catalog.add_argument("-o", "--output", type=Path, default=Path("rail-catalog.json"))
    catalog.add_argument("--cache-dir", type=Path, default=Path(".cache/travel-record"))
    coast = subparsers.add_parser("coastline", help="获取并验证自定义区域精细海岸线，供后续渲染自动使用")
    coast.add_argument("name", help="本地区域名称，例如 hakodate")
    coast.add_argument("--bounds", type=float, nargs=4, required=True,
                       metavar=("SOUTH", "WEST", "NORTH", "EAST"))
    coast.add_argument("--cache-dir", type=Path, default=Path(".cache/travel-record"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "coastline":
            print(f"正在获取并验证 {args.name} 海岸线；优先复用缓存，公共服务可能需要数分钟。", flush=True)
            path, snapshot = acquire_region(HttpCache(args.cache_dir), args.name, args.bounds)
            print(f"已验证并注册精细海岸线：{path}；陆地多边形 {snapshot['land_polygon_count']} 个")
            print("后续 silhouette 渲染使用相同 --cache-dir 即自动加载；未启动视频渲染。")
            return 0
        resolver = TripResolver(args.cache_dir)
        if args.command == "catalog":
            count = resolver.export_catalog(args.output)
            print(f"已写入 {count} 条线路：{args.output}")
            return 0

        trip = load_trip(args.input)
        if args.workers < 1:
            raise TripFormatError("workers 必须至少为 1")
        output = args.output or args.input.with_suffix(".mp4")
        resolved = resolver.resolve_trip(trip)
        manifest_path = output.with_suffix(".resolved.json")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = resolved_manifest(trip, resolved)
        if not args.resolve_only:
            renderer = VideoRenderer(trip, args.cache_dir)
            preflight = renderer.preflight_sources(resolved)
            if preflight["sample_count"]:
                print(
                    f"底图预检完成：{preflight['sample_count']} 个沿途采样位置；开始编码。",
                    flush=True,
                )
            renderer.render(resolved, output, workers=args.workers)
            manifest["basemap"] = {
                "style": trip.basemap,
                "design": "travel-atlas" if trip.basemap == "silhouette" else trip.basemap,
                "preflight": preflight,
                "road_sources": renderer.basemap.road_sources,
                "context_sources": renderer.basemap.context.sources,
                "urban_rail_sources": renderer.basemap.rail_network.sources,
                "coastline_sources": renderer.basemap.coastline.sources,
            }
            overlay = renderer.basemap._road_overlay
            if overlay is not None:
                manifest["basemap"]["cached_raster_fallback"] = {
                    "used_tile_count": len(overlay.used_tiles),
                    "missing_tile_count": len(overlay.missing_tiles),
                    "note": "Major-road colors extracted from cached standard OSM tiles; approximate background only, not routing geometry.",
                }
            print(f"视频：{output}")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"解析数据：{manifest_path}")
        return 0
    except (TripFormatError, DataSourceError, RuntimeError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
