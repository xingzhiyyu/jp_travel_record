# 给接手此仓库的编程助手

先读 README.md、docs/WORKFLOW.md。当前项目包含两个独立组件：
`生成器/` 是静态行程编辑器；`travel_record/` 是 Python 地图视频渲染器。

## 执行约定

- 保留用户原始输入，清洗另存文件，并逐项记录原文、修改、证据、不确定性。
- 使用实际代码、命令帮助和测试判断能力，不把 README 或此前聊天中的设想当成已实现。
- 先本地解析，再联网解析线路，检查 `.resolved.json`，最后出代表性预览。
- 用户只要求检查或预览时，不启动整片；用户明确要求完整版后持续完成，失败时保存诊断。
- 站名须区分运营商与地区。同名车站、岛屿、景点不能混用；不确定的旅行选择不能凭空补齐。
- 公交没有 Google API 密钥时是直线回退；步行、出租车也可能回退。报告采用的实际来源。
- 不把任何 API 密钥、用户记录、HTTP 缓存、视频或虚拟环境提交到 Git。
- 不发布或推送仓库，除非用户要求。用户提供的数据库须核实来源和再分发许可后再公开。
- 视频编码前后均保留 OSM / Natural Earth 署名，不编造几何或以装饰图替代海岸线。
- 回归命令：`python -m unittest discover -s tests -v`；生成器：`node 生成器/scripts/test_ui.js`。
- 可使用 uv 管理环境，或 Python 虚拟环境安装 `pip install -e .`；命令应适应实际操作系统。

## 已实现与未实现

已实现：`render`、`catalog`、`coastline NAME --bounds SOUTH WEST NORTH EAST`（获取并注册精细海岸区域）、`--resolve-only`、`--workers`、显式 day/night、步行虚线、中心蓝点、结尾地理坐标固定蓝点。
尚未实现：沿途所有站名显示开关、通用自动清洗命令、原生 preview 子命令、直接读取 resolved JSON 渲染、断点续渲染、NVENC 开关。
不要给用户虚构这些参数。预览可通过小型 Python 脚本调用现有 VideoRenderer 方法；内部接口修改时同步更新脚本。
