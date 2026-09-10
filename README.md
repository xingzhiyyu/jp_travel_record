# Travel Record

## 生成器与跨助手使用

`生成器/index.html` 可离线选站编辑行程。使用新增的 **导出视频行程** 按钮导出本项目格式；
但对于user来讲,可以直接用任何形式的方式输入,让AI自己清理数据
旧 TXT/复制按钮使用另一套格式，不能直接送入这里的解析器。
全国选站目录不代表所有线路已通过本项目地图寻路验证。

AI 助手交接：[执行流程](docs/WORKFLOW.md)、[可复制提示词](docs/ASSISTANT_PROMPT.md)。
准备上传仓库：[GitHub 文件清单](docs/GITHUB_UPLOAD.md)。助手入口为根目录 AGENTS.md。
个人行程、地图缓存与视频默认不进入版本控制。

把按顺序书写的旅行记录解析成一条连续地图轨迹，并导出 H.264 MP4。铁路段从 OpenStreetMap 公共交通关系中读取真实轨道、站名、方向和线路颜色。默认底图在关西、东海道和东京沿海区域使用 OpenStreetMap 精细海岸线，保留港池、码头和人工岛，叠加主要道路与城市轨道；区域外及日本定位小图使用 Natural Earth 概化陆地。也可把 `basemap` 改回 `osm` 使用完整浅色道路底图。蓝色圆形位置标记固定在屏幕中心，每一帧同时更新地图位置和浮点缩放级别。已经走过的线路持续保留；相邻输入端点不重合时平滑移动镜头，不补画未记录的接驳线。公交可选用 Google Routes API；步行和出租车优先使用公开 OSM 路由折线，服务不可用时才回退为简化线。

精细海岸线按 OSM 的海陆方向拼接，遇到断裂或方向矛盾会报错，不跨海湾直线补齐。数据首次下载后缓存在本地；视频清单的 `coastline_sources` 记录覆盖范围和来源。岸边不添加装饰色带。

## 运行

需要 Python 3.10 以上版本。项目使用 `uv` 管理运行环境：

```bash
uv sync
uv run travel-record render example_trip.txt -o output/example-trip.mp4
```

绘图是主要耗时。可用多个独立进程并行生成连续视频片段，再按原顺序无损拼接：

```bash
uv run travel-record render record.cleaned.txt -o output/record-full-60fps.mp4 --workers 4
```

`--workers` 默认为 `1`；Apple Silicon 建议先用 `4`。并行模式会明显提高瞬时功耗和内存占用，但不会改变帧率、画面顺序或动画时间轴。

每次渲染还会生成同名的 `.resolved.json`，其中包含使用到的站名、方向、颜色、线路折线、数据来源，以及北京首都国际机场（PEK）的地理位置。

## 输入格式

```text
title: 我的旅行
size: 1280x720
fps: 24
seconds_per_leg: 3
max_zoom_levels_per_second: 0.9
waypoint_pause_seconds: 0.55
ending_overview: true
ending_overview_seconds: 7
basemap: silhouette
locale: zh-CN

route:
2026-04-12 08:00 | JR Kobe Line | Osaka -> Sannomiya
2026-04-12 09:30 | bus | Sannomiya -> Kobe Harborland
2026-04-12 10:00 | walk | Kobe Harborland -> Meriken Park
```

- 固定写法是 `时间 | 线路名 | 起点 -> 终点`，按行从上到下播放。
- 也接受紧凑写法：`{线路名,起点,终点;线路名,起点,终点}`。例如
  `{kansai-airport-line,kansai airport,hineno;hanwa-line,hineno,tennoji;osaka-loop-line,tennoji,shin-imamiya}`。
- 紧凑写法中的连字符会自动变成易读名称。按基础设施线路记录时应在日根野、天王寺分段；若按一班贯通列车记录，可写
  `{kansai-airport-rapid,kansai airport,shin-imamiya}`。
- 可写多个途经点：`A -> B -> C` 会自动拆成两段。
- `bus`、`公交`、`巴士` 都统一显示为 `bus`；`walk`、`步行`、`徒歩` 都统一显示为 `walk`；`taxi`、`出租车`、`タクシー` 显示为 `taxi`。
- 其他交通段直接显示输入的线路名，例如 `JR Kobe Line`。
- 地名解析失败时可显式写经纬度：`My Place@35.0,135.0`。
- 普通路段只在换线前 1 秒至换线后 1 秒内改变比例尺；其余时间保持稳定，切换瞬间位于缩放曲线正中。若设置了到站停顿，停顿期间比例尺也固定在正中值，确保只在移动时缩放。`max_zoom_levels_per_second` 用于限制结尾总览的缩放速度。
- `basemap: silhouette` 使用旅行地图册风格：暖白陆地、灰蓝水系、浅绿公园与林地、低饱和度主干道路与城市轨道网；少量站名随视野显示。保留线路信息浮层、日本定位图、独立比例尺与署名，不再显示底部行程栏。地图与文字均经过抗锯齿处理。改成 `osm` 可恢复完整浅色道路地图。
- 城市轨道背景覆盖大阪—神户、京都及东京城区的已获取范围，包含 OSM 收录的运营铁路、地铁、轻轨、电车、单轨与缆索铁路，也包括地下线路。背景轨道采用淡化的线路颜色，实际旅行轨迹保持高亮；未知颜色使用中性色，不编造线路配色。具体范围、轨道数量和数据说明写入 `.resolved.json` 的 `urban_rail_sources`。
- 水系与绿地使用 OpenStreetMap 的真实几何，覆盖京阪核心区及东京示例行程所在的市区；非覆盖区不绘制这些细节。定位图中的蓝点表示当前地理位置，主画面的蓝圆圈仍固定在屏幕中心。
- 主干道路优先读取各数据服务的缓存。矢量数据不可用时，从本机已缓存的标准 OSM 瓦片提取道路颜色层作为近似背景（文字处可能有缺口），不会将它用于轨迹寻路；实际采用的来源写入解析文件。
- 可设置 `TRAVEL_RECORD_OFFLINE=1` 仅使用已有缓存。视频成功编码后才替换目标文件，中断时保留上一次完整成品。
- 播放时长会综合路程、输入的最短时长和缩放速度计算；`waypoint_pause_seconds` 控制到站停顿。
- 相邻段起终点不重合时，在到站停顿中以至少 1 秒平滑衔接镜头，蓝点仍固定屏幕中心。已走过的步行段沿实际步行道路画细虚线，铁路、公交和出租车画彩色实线；未记录的换乘接驳和未行驶部分不画线。轨迹无白色描边，线宽随比例尺变化（总览保留最细可见宽度，近景设置上限以免遮挡底图）。
- 主定位点为直径约 10 像素的小蓝点（720p），带细白边，无外圈光晕，减少遮挡周围线路；尺寸随画面分辨率缩放。
- `ending_overview` 开启后，蓝点锁定最终目的地的地理坐标，不再停在屏幕中心淡出。镜头同时拉远与平移，终点蓝点始终留在画面中，抵达画面与总览第一帧连续，信息浮层渐变为行程总结。`ending_overview_seconds` 是其最短时长，`max_zoom_levels_per_second` 限制整段动画的峰值缩放速度。

## 日间 / 夜间模式

在每段行程末尾添加可选的第四个字段 `day` 或 `night`：

```text
theme: day
theme_transition_seconds: 1.5
route:
{JR Kobe Line,Osaka,Sannomiya,day}
{walk,Sannomiya,Meriken Park,night}
{walk,Meriken Park,Hanakuma}
```

普通格式也支持：`19:00 | walk | Sannomiya -> Meriken Park | night`。

- 不填第四字段时，沿用上一段的模式；首段使用顶部 `theme`，默认 `day`。原来的三字段输入无需修改。
- `night` 从该段开始切换，之后持续夜间；写 `day` 可切回日间。顶部 `theme: night` 可使整段影片从夜间开始。
- `theme_transition_seconds` 控制切换时长，默认 1.5 秒，范围 0–10 秒；0 为立即切换，过长时自动限制在该交通段内。过渡从新段开始，不提前变暗，也不改变移动和缩放节奏。
- 夜间同时调整陆地、水体、道路、文字、信息卡及署名的明暗对比，保留鲜明线路色和小蓝点。结尾总览沿用最后一段的模式。
- 过渡不再混合明暗反相的底图；陆地、水体和道路维持明暗层次。文字按背景对比度选择深色或浅色字，在临界点切换文字明暗，不经过看不清的灰字阶段；步行虚线也单独保证可见性。
- 这是明确指定的显示模式，不会根据 `19:00`、系统时间或日落自动推断，避免没有日期/时区时误判。`day`/`night` 也可写为“日间”/“夜间”；其他值会报错。
- 可运行 `uv run travel-record render example_day_night.txt -o output/day-night-demo.mp4` 查看日夜切换示例。示例不代表原始记录的真实旅行时间。

## 公交路径与示例渲染

个人行程可另存为 `record.cleaned.txt`，原文件保持不变。这两个个人文件和 output/ 不随仓库发布；初次使用可先运行上面的 example_trip.txt。运行自己的清洗记录：

```bash
uv run travel-record render record.cleaned.txt -o output/record.mp4
```

若要使用 Google Maps 公交信息，先启用 Google Routes API 并设置环境变量：

```bash
export GOOGLE_MAPS_API_KEY="你的密钥"
uv run travel-record render trip.txt
```

程序只保留 Google 返回结果中的 BUS 交通步骤；接驳步行不会画成公交线。未设置密钥时，公交段会回退为端点之间的简化直线，并在 `.resolved.json` 中注明。步行使用 `routing.openstreetmap.de` 的步行路由，出租车使用 OSRM 驾车路由；可通过 `TRAVEL_RECORD_FOOT_ROUTER_URL`、`TRAVEL_RECORD_DRIVING_ROUTER_URL` 替换服务地址。两者不可用时会保留简化线并注明原因。

## 线路目录

内置了东海道新干线、JR Kyoto Line、JR Kobe Line、东京地铁银座线和 JR 山手线的常用别名。其他京都—大阪都市圈和东京都市圈线路会从 OpenStreetMap 自动发现。也可以导出当前线路目录：

```bash
uv run travel-record catalog -o output/rail-catalog.json
```

目录记录线路名、起讫方向、颜色、运营方和 OSM 关系 ID；实际使用的线路会在解析文件中额外附带站点列表。

## 数据与署名

- 地图底图、铁路几何和公共交通标签：[© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)，ODbL。
- 陆地剪影：[Natural Earth](https://www.naturalearthdata.com/downloads/10m-cultural-vectors/)，公有领域。
- 公交可选数据：[Google Routes API 公交路线](https://developers.google.com/maps/documentation/routes/transit-route)；需要用户自己的 API 密钥和已启用计费的项目。
- 步行与出租车道路折线：基于 OpenStreetMap 数据的公开路由服务；首次请求会发送该段的两个端点坐标，并被本地缓存。
- 程序遵守 [OpenStreetMap 瓦片使用政策](https://operations.osmfoundation.org/policies/tiles/)，缓存瓦片至少七天，并在视频中保留署名。
