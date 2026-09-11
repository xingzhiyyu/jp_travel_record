# 用其他agent接手整个流程

把仓库克隆到本地，用 AI 编程助手打开项目根目录，让它读取 `AGENTS.md` 和本文。
本流程只要求助手能读写项目、执行终端和在需要时联网，不依赖 Codex 专属工具。
助手若不能看图片，应输出图片供用户检查，并明确说明视觉检查未完成。

## 1. 环境与生成器

Python 3.10+；生成器本身只需浏览器。任选一种环境安装方式：

```sh
uv sync
uv run python -m unittest discover -s tests -v
```

没有 uv 时，创建 `.venv`，激活后运行 `python -m pip install -e .`。
Windows 激活路径是 `.venv\Scripts\Activate.ps1`，macOS/Linux 是 `.venv/bin/activate`。
之后将文中 `uv run python` 换成该虚拟环境的 `python`。

双击 `生成器/index.html`，选择线路/站点并排列行程。点击 **导出视频行程**，
得到当前 Python 渲染器可以解析的管道分隔文本，每段带 day/night。
此入口保留日文原名，避免中文译名额外引入歧义。留空的接驳只在相邻明确端点能确定时补齐，否则阻止导出。
旧“导出 TXT”和“复制文本”仍为 lvji 格式；本项目不能直接读取它们。
当前导入器也没有实现新版视频行程的往返导入，不要宣称已经支持。

**格式兼容不等于全国线路都能渲染。** 生成器有全国站名目录，渲染器重点支持关西/东京及东海道；
缺失线路关系必须查证、补齐别名和 OSM 关系后再用。站表 sequence 也不是可直接寻路的几何，尤其支线不能机械串联。

云端生成器只包含 `index.html`、`style.css`、`app.js`、`data.js` 和 README，直接使用预生成站表。
scripts/、data/source/ 可保留在本地，但不再由 Git 跟踪或上传；新克隆不附带这些目录。
本地存在测试脚本时可运行 `node 生成器/scripts/test_ui.js`；否则检查页面加载、选站和视频行程导出。
旧构建脚本仍依赖未随仓库提供的 `data.station_zh`，不要自动执行或寻找仓库外的旧项目缓存。
预生成 `data.js` 的来源与再分发许可仍需确认；移除源数据库并不消除衍生数据的许可问题。

## 2. 清洗与复核

输入另存为 `record.txt`，清洗另存 `record.cleaned.txt`，说明写 `output/cleaning-report.md`。
日夜取输入明确标记，不按系统时间猜测；条目顺序代表播放顺序，不需要序号。
标准格式示例：

```text
title: 我的旅行
size: 1280x720
fps: 24
basemap: silhouette
route:
未注明 | JR Kobe Line | Osaka -> Sannomiya | day
未注明 | walk | Sannomiya -> Meriken Park | night
```

清洗每项保留原始行号、原文、清洗结果、理由、来源链接、待确认事项。
明确区分 JR 宇治/京阪宇治、江ノ島站/江之岛、伏见稻荷站/稻荷站。
只补有唯一上下文解释的起終点。歧义问用户，独立可完成部分继续做。
中文名称映射优先使用生成器日文名称和项目别名；缺失站点核对运营商、地区、经纬度。
显式坐标用管道格式 `地点@纬度,经度`；紧凑格式逗号存在歧义，应避免。

先运行不联网的语法检查：

```sh
uv run python -c "from pathlib import Path; from travel_record.parser import load_trip; t=load_trip(Path('record.cleaned.txt')); print(len(t.legs), 'legs', t.width, t.height, t.fps)"
```

然后解析地图数据（首次会联网并发送地点名称或坐标）：

```sh
uv run python -m travel_record.cli render record.cleaned.txt -o output/checked.mp4 --resolve-only
```

这只写 `output/checked.resolved.json`，不会生成 MP4，也不会预热全部渲染底图。
检查每段 `source`、`path`、`osm_relation_id`、`stations` 和 `notes`，而不是只看退出码。
距离用 `travel_record.geo.polyline_lengths(path)[1]`，单位为米。
报告相邻段断点、各类回退数量、异常长步行、折返/远程道岔绕行、端点到轨道吸附偏差。
阈值仅用于提醒，不可自动删除真实绕行。`stations` 可能包含整个线路，不能当作本段全部实际经过站。

## 2.5 为新地区获取精细海岸线

使用[海岸线分析与获取提示词](COASTLINE_PROMPT.md)：先判断哪些镜头值得精细化，输出最小下载计划；获得获取授权后再下载与验证。
注意：追加自定义小区域目前不会替代内置大区域的下载，必须区分理想覆盖计划与实际请求范围。
程序入口是 `travel-record coastline`，实现位于 `travel_record/coastline.py`。
默认只有关西、东海道、东京三个精细区域；其他地方使用 Natural Earth 概化陆地。
这个命令可补充任意合法经纬度区域，不会自动推断行程范围，也不启动视频。

先从 `.resolved.json` 的真实路径和镜头视野选取沿海范围，包含画面余量。以函馆为例：

```sh
uv run travel-record coastline hakodate --bounds 41.65 140.50 41.95 140.95
```

四个数字严格依次为 **南、西、北、东**，不是常见的西南东北顺序。
单区经度和纬度跨度各不超过 3 度，纬度限制在 ±85°，不支持跨日期变更线。
长行程分区串行获取，优先港区小样；内陆空区域不注册，不根据空响应猜测整区是陆地。
区域名只能用字母、数字、`-`、`_`，不能覆盖内置区域名称。

命令复用 Overpass HTTP 缓存，下载完整 way 几何，裁剪到边界，按 OSM“陆地在左”规则
严格多边形化。断裂、交叉、方向冲突、空响应或服务器 remark 均不能注册为成功数据。
验证通过后原子写入 `.cache/travel-record/coastline-regions/hakodate.json`，包括原始几何、
bbox、来源、验证时间和多边形数量；同名成功更新会替换旧快照，失败保留旧快照。
验证时间不是 OSM 编辑时间；下载结果可能来自已有 HTTP 缓存。

后续 `basemap: silhouette` 渲染自动读取同一缓存目录中的注册区域，多进程同样生效。
使用自定义 `--cache-dir` 时，获取和渲染两条命令必须保持一致。新增区域本身无需再次联网，
但其他底图层仍可能下载。默认区域先绘制、自定义区域按文件名排序后绘制；重叠区采用后绘制数据，
应尽量使用相邻分区并核对接缝。扩展覆盖不会改变日本定位小图的概化数据，也不影响 `basemap: osm`。

先检查海岸几何和少量真实渲染帧，再继续整片流程；检查港池、岛屿、站点及分区边缘。
注册成功不代表地理与画面已人工复核。`natural=coastline` 不含所有河流、湖泊和独立港内水面，
这些需要单独的水体层，不能把未覆盖水面用装饰图替代。
失败保存错误和范围，不无限重试公共服务。数据缓存不入 Git，成片保留 OSM / Natural Earth 署名。

## 3. 先预览

全片可能耗时很久。先挑换乘、日夜切换、步道、港区、长铁路段与结尾各出核对帧；
需要验证移动/缩放时出短片。推荐 640×360、24fps 的独立预览输入，保留原始成片设置。
选择少量有问题的连续段，避免为预览渲染完整 178 段。
用现有 `VideoRenderer._target_zooms`、`_frame_specs`、`_render_spec` 生成整段时间轴上的抽样帧，
不要单独猜测 zoom 或忽略前后段造成错误预览。直接调用内部方法时先读代码确认签名。
也可做一份短行程用正常 `render` 出短片；说明短行程与全片上下文可能有差异。

核对：蓝点中心、站名可读、换乘连续、切换前后各一秒缩放、海陆边界、步行虚线、
日夜过渡可读、结尾蓝点固定目的地。视觉通过不代表地理准确，关键位置仍需对照数据来源。

## 4. 正式渲染与交付

用户要求完整版后执行：

```sh
uv run python -m travel_record.cli render record.cleaned.txt -o output/final.mp4 --workers 4
```

默认渲染单进程，`4` 是可调整的建议，不是所有电脑的最佳值；先小样实测。
并行按连续帧分片编码，最终按顺序合并。日志没有逐帧进度，片段体积不能推断百分比或剩余时间。
不要仅凭“和另一片一样大”报告快完成。估时根据同分辨率/帧率/场景小样，给范围并注明首次下载因素。
`TRAVEL_RECORD_OFFLINE=1` 只有完整缓存时适用；Windows PowerShell 用 `$env:TRAVEL_RECORD_OFFLINE='1'`，
macOS/Linux 可用命令前缀。缺缓存时不要强行离线。

成功条件：正式 MP4 存在、完整解码退出码为 0、分辨率/fps/时长符合设置、关键帧和转场复核。
用 `imageio_ffmpeg.get_ffmpeg_exe()` 获取项目自带 FFmpeg 路径，通过 subprocess 参数列表调用：
`ffmpeg -v error -i output/final.mp4 -map 0:v:0 -f null -`；用 `ffmpeg -hide_banner -i ...` 查看元数据
（只查看输入没有输出时非零退出不等于视频损坏）。
交付视频路径、解析 JSON、清洗说明与已知回退。不要把片段文件当成成片。
用户停止时终止本次主进程及其子进程，不重启，不删旧成片。失败保留日志，不无限重试昂贵渲染。

## 当前能力边界

`show_through_stations` 只是此前的功能建议，尚未实现，不能写进输入并声称生效。
没有自动清洗、preview、断点续渲染、直接 JSON 渲染或 NVENC 参数。
Mac 字体路径有 Linux 后备，但 CJK 字体可用性必须在目标机抽帧检查。
生成器全国选站不意味着这些区域具备精细底图缓存或已验证的寻路。
