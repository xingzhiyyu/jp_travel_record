#!/usr/bin/env python3
"""构建 transit-tool 的线路站点数据(data.js)。

数据源:复用 lvji 项目的 Overpass 缓存
  <lvji>/data/cache/rail_kansai_*.json      关西铁路 route relation(与 bbox 相交线路,含全程几何)
  <lvji>/data/cache/rail_kanto_*.json       关东铁路 route relation
  <lvji>/data/cache/stations_kansai_*.json  站点 node(名称/中文名/坐标,仅 bbox 内)
  <lvji>/data/cache/stations_kanto_*.json

缺口用 Overpass 补抓(结果缓存到 transit-tool/data/cache/,重复运行不重新请求):
  Q1  带 stop 成员但站名匹配不到的 node id → node(id:...); out;
  Q2  无 stop 成员的线路 relation → 线路几何 60m 范围内的 railway=station/halt node

输出: transit-tool/data.js  →  const TRANSIT_DATA = {...}
结构: regions[] → operators[] → lines[] → stops[[日文名, 中文名], ...](按线路方向有序)
"""
import glob
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_ROOT = os.path.dirname(HERE)
LVJI_ROOT = os.path.dirname(TOOL_ROOT)
sys.path.insert(0, LVJI_ROOT)

from data import station_zh as zh  # noqa: E402  lvji 的中文映射

LVJI_CACHE = os.path.join(LVJI_ROOT, "data", "cache")
TOOL_CACHE = os.path.join(TOOL_ROOT, "data", "cache")
OUT_PATH = os.path.join(TOOL_ROOT, "data.js")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
UA = "transit-tool-data-builder/1.0"

REGIONS = [
    ("kansai", "关西（大阪·神户·京都·奈良）"),
    ("kanto", "东京都市圈"),
]

# OSM 欠标/代表选举有损线路的人工站表兜底。
# 键:relation id(int)或归并名(group_key,str,更稳——代表选举换人也不失效);
# 值:正确的有序站名(日文),zh 在构建时从站名池/zh 映射补。
# 仅用于经核实 OSM 数据确实残缺的线路(不覆盖自动结果良好的 260+ 条)。
LINE_STOP_OVERRIDES = {
    # 京阪電気鉄道京阪本線・鴨東線(id=1921059):OSM relation 仅 22 个 node 成员,
    # 缺北端出町柳/南端淀屋橋/伏見桃山等;几何投影能找回这些站,但混入 JR 大阪城北詰噪声。
    # 站序按京阪官方 KH 站号(KH01 淀屋橋 … KH42 出町柳)排列,伏見桃山=KH29 在中書島与丹波橋之间。
    1921059: [
        "淀屋橋", "北浜", "天満橋", "京橋", "野江", "関目", "森小路", "千林",
        "滝井", "土居", "守口市", "西三荘", "門真市", "古川橋", "大和田", "萱島",
        "寝屋川市", "香里園", "光善寺", "枚方公園", "枚方市", "御殿山", "牧野",
        "樟葉", "橋本", "石清水八幡宮", "淀", "中書島", "伏見桃山", "丹波橋",
        "藤森", "墨染", "龍谷大前深草", "伏見稲荷", "鳥羽街道", "東福寺", "七条",
        "清水五条", "祇園四条", "三条", "神宮丸太町", "出町柳",
    ],
    # JR鶴見線:本線+弁天橋/海芝浦/大川三支线。OSM 按支线拆成 7 个 relation,
    # 代表选举只选了本線10站(扇町方向)relation,海芝浦支線(新芝浦/海芝浦)与
    # 大川支線(大川)整支丢失。主线 relation 10240098 的 21 个成员 node 恰含全部
    # 13 站,按 JR 官方站序排列(支线站紧跟其衔接站之后)。
    "鶴見線": [
        "鶴見", "国道", "鶴見小野", "弁天橋", "浅野", "新芝浦", "海芝浦",
        "安善", "大川", "武蔵白石", "浜川崎", "昭和", "扇町",
    ],
}

# ---------- 过滤规则 ----------
SERVICE_WORDS = (
    "普通", "快速", "急行", "特急", "区間", "通勤", "準急", "各停", "各駅停車",
    "ライナー", "回送", "直通", "シャトル", "新快速", "快速急行", "区間急行",
    "通勤急行", "通勤特急", "快速特急",
)
DROP_NAME_EXACT = {
    "ウエスタンリバー鉄道", "なにわ筋線", "中央新幹線", "大汐線",
    "Link Line to Kita-Kogane", "吹田 – 加島", "蹴上インクライン",
    "逢坂山トンネル", "長等山トンネル", "東海道－山陽新幹線大阪高架橋",
    "武蔵野南線", "高島線", "馬橋支線",  # JR 貨物线(无客运站表)
}
DROP_NAME_CONTAINS = ("貨物", "トンネル", "高架", "インクライン", "短絡", "臨海鉄道",
                      "Stopping service", "trackage", "Sotetsu")

# 公司全称 → 简称(用于显示名/归并)
COMPANY_SHORT = [
    ("近畿日本鉄道", "近鉄"), ("阪急電鉄", "阪急"), ("阪神電気鉄道", "阪神"),
    ("京阪電気鉄道", "京阪"), ("南海電気鉄道", "南海"), ("小田急電鉄", "小田急"),
    ("京王電鉄", "京王"), ("西武鉄道", "西武"), ("東武鉄道", "東武"),
    ("京浜急行電鉄", "京急"), ("京成電鉄", "京成"), ("東京急行電鉄", "東急"),
    ("新京成電鉄", "新京成"), ("相模鉄道", "相鉄"),
]

# operator 为空时按线路名前缀推断公司
OPERATOR_HINTS = [
    ("近畿日本鉄道", "近鉄"), ("南海電気鉄道", "南海"), ("阪急電鉄", "阪急"),
    ("阪神電気鉄道", "阪神"), ("京阪電気鉄道", "京阪"), ("能勢電鉄", "能勢電鉄"),
    ("泉北高速鉄道", "泉北高速"), ("北大阪急行", "北大阪急行"), ("水間鉄道", "水間鉄道"),
    ("大阪モノレール", "大阪モノレール"), ("京王電鉄", "京王"), ("小田急電鉄", "小田急"),
    ("西武鉄道", "西武"), ("東武鉄道", "東武"), ("京浜急行電鉄", "京急"),
    ("京成電鉄", "京成"), ("新京成電鉄", "新京成"), ("北総鉄道", "北総"),
    ("東京急行電鉄", "東急"), ("相模鉄道", "相鉄"), ("東京都交通局", "都営"),
    ("東京地下鉄", "東京メトロ"), ("横浜市交通局", "横浜市営地下鉄"),
    ("横浜高速鉄道", "横浜高速鉄道"), ("埼玉高速鉄道", "埼玉高速鉄道"),
    ("埼玉新都市交通", "埼玉新都市交通"), ("首都圏新都市鉄道", "つくばエクスプレス"),
    ("東葉高速鉄道", "東葉高速鉄道"), ("東京都", "都営"),
    ("北総", "北総鉄道"), ("新京成", "新京成電鉄"), ("京成", "京成"),
]

# operator 标签归一:英文/全称 → 统一简称(与线路显示风格一致)
OP_NORMALIZE = {
    "西日本旅客鉄道": "JR西日本", "東日本旅客鉄道": "JR東日本",
    "東海旅客鉄道": "JR東海", "九州旅客鉄道": "JR九州",
    "JR West": "JR西日本", "JR East": "JR東日本", "JR Central": "JR東海",
    "Tokyo Metro": "東京メトロ", "東京地下鉄": "東京メトロ",
    "大阪市高速電気軌道": "大阪メトロ",
    "Kintetsu Corporation": "近畿日本鉄道",
    "Seibu Railway Company, Ltd.": "西武鉄道",
    "Saitama New Urban Transit Co., Ltd.": "埼玉新都市交通",
    "Tokyo Waterfront Area Rapid Transit": "東京臨海高速鉄道",
}

# 繁体 name:zh → 简体补充映射(station_zh 之外,只在本脚本用)
EXTRA_ZH = {
    "狀": "状", "澀": "涩", "廣": "广", "萬": "万", "鐵": "铁", "國": "国",
    "場": "场", "澤": "泽", "卷": "卷", "發": "发", "樂": "乐", "嶋": "岛",
    "淺": "浅", "橋": "桥", "館": "馆", "驛": "站", "環": "环", "營": "营",
    "燒": "烧", "豐": "丰", "邊": "边", "帶": "带", "壽": "寿", "驗": "验",
    "龍": "龙", "龜": "龟", "馬": "马", "車": "车", "長": "长", "門": "门",
    "岡": "冈", "島": "岛", "線": "线", "驛": "站", "戶": "户", "縣": "县",
    "齋": "斋", "藤": "藤", "瀧": "泷", "卷": "卷", "絲": "丝", "覺": "觉",
}


def to_zh_extra(text: str) -> str:
    """station_zh 映射 + 本脚本繁简补充。"""
    out = []
    for ch in zh.to_zh(text):
        out.append(EXTRA_ZH.get(ch, ch))
    return "".join(out)


# ---------- Overpass(带文件缓存) ----------
def _cache_path(key: str) -> str:
    os.makedirs(TOOL_CACHE, exist_ok=True)
    import hashlib
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    return os.path.join(TOOL_CACHE, f"{key}_{h}.json")


def overpass(query: str, key: str, retries: int = 3):
    p = _cache_path(key)
    if os.path.exists(p) and os.path.getsize(p) > 2:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    last_err = None
    for attempt in range(retries + 1):
        endpoint = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        try:
            print(f"[overpass] {key} (attempt {attempt + 1}) -> {endpoint}")
            body = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(endpoint, data=body,
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode())
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, p)
            return data
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"[overpass] {key} failed: {e}")
            time.sleep(6)
    print(f"[overpass] {key} 放弃,返回空结果({last_err})")
    return {"elements": []}


# ---------- 工具 ----------
def load_json(pattern):
    files = glob.glob(os.path.join(LVJI_CACHE, pattern))
    if not files:
        raise FileNotFoundError(f"找不到缓存: {pattern}")
    with open(files[0], encoding="utf-8") as f:
        return json.load(f)


DIRECTION_WORDS = ("外回り", "内回り", "上り", "下り", "右回り", "左回り", "方面", "方向")


def base_name(name: str) -> str:
    """去掉种别/方向/括号:'JR京都線・JR宝塚線 普通 (高槻 => 新三田)' → 'JR京都線・JR宝塚線'"""
    name = re.split(r"[（(]", name)[0].strip()
    while " " in name or "　" in name:
        head, _, tail = name.replace("　", " ").partition(" ")
        first = tail.split(" ")[0]
        if first in SERVICE_WORDS or first in DIRECTION_WORDS or "・" not in head:
            name = head if (first in SERVICE_WORDS or first in DIRECTION_WORDS) else name
            break
        name = head
    return name


def clean_stop_name(name: str) -> str:
    """stop_position node 名字清理:'東京 5番線'/'新大阪駅' → '東京'/'新大阪'。"""
    name = re.sub(r"\s*\d+番線$", "", name)
    name = re.sub(r"\s*[一二三四五六七八九十]+番線$", "", name)
    name = re.sub(r"\s*(上り|下り)$", "", name)
    name = re.sub(r"\s*ホーム\d+.*$", "", name)
    name = name.strip()
    if name.endswith("駅") and len(name) > 2:
        name = name[:-1]
    return name


def display_name(name: str) -> str:
    """UI 显示名:基础名 + 去 ' : :方向'、'/直通段'、公司前缀、方向/种别尾巴。"""
    n = base_name(name)
    n = re.split(r"\s*:\s*", n)[0].strip()
    n = n.split("/")[0].strip()
    if n.startswith("Subway "):
        n = n[len("Subway "):]
    for pre in ("Osaka Metor", "Osaka Metro"):  # 大阪地铁的英文/错拼前缀
        if n.startswith(pre):
            n = n[len(pre):]
    n = re.sub(r"(外回り|内回り|[上下]り|各駅(停車)?)$", "", n)
    n = re.sub(r"J[A-Z]$", "", n)  # JR 线路代号尾缀:JR中央線JC → JR中央線
    for full, short in COMPANY_SHORT:
        if n.startswith(full):
            rest = n[len(full):].lstrip(" 　")
            if rest.startswith(short):
                rest = rest[len(short):]
            n = short + rest
            break
    return n


def group_key(name: str) -> str:
    """归并 key:显示名去空格/JR 前缀,让 'JR常磐線' 与 '常磐線'、'山手線' 各版本归并。"""
    b = display_name(name).replace(" ", "").replace("　", "")
    if b.startswith("JR"):
        b = b[2:]
    return b


def is_noise(tags, name: str) -> bool:
    base = base_name(name)
    if base in DROP_NAME_EXACT or name in DROP_NAME_EXACT:
        return True
    if any(w in name for w in DROP_NAME_CONTAINS):
        return True
    if base.split(" ")[0] in SERVICE_WORDS:
        return True
    if any(w in base for w in ("快速", "ライナー", "特急", "急行", "準急", "区急")):
        return True
    # 跨线直通种别 relation:'北陸本線・湖西線・東海道本線・山陽本線 新快速'
    bare = re.split(r"[（(]", name)[0].replace("　", " ")
    parts = bare.split(" ")
    if len(parts) > 1 and parts[-1] in SERVICE_WORDS and "・" in base:
        return True
    if tags.get("usage") == "freight" or tags.get("railway:traffic_mode") == "freight":
        return True
    if tags.get("construction") or tags.get("proposed"):
        return True
    return False


def operator_of(tags, name: str) -> str:
    op = tags.get("operator:short") or tags.get("operator") or ""
    op = op.split(";")[0].strip()
    op = re.sub(r"株式会社$", "", op)
    op = OP_NORMALIZE.get(op, op)
    for full, short in COMPANY_SHORT:
        if op.startswith(full):
            op = short + op[len(full):]
            break
    if op:
        return op
    for prefix, short in OPERATOR_HINTS:
        if name.startswith(prefix):
            return short
    if name.startswith("JR"):
        return "JR"
    return "其他"


def line_zh_name(tags, name: str) -> str:
    zh_name = tags.get("name:zh-Hans") or tags.get("name:zh")
    if name in zh.LINE_NAME_ZH:
        return zh.LINE_NAME_ZH[name]
    if zh_name:
        return to_zh_extra(zh_name)
    return zh.LINE_NAME_ZH.get(base_name(name)) or to_zh_extra(name)


# ---------- 站点索引 ----------
class StationIndex:
    """站名池:stations 缓存(bbox 内,带中文名)+ 补抓站。网格加速最近匹配。"""

    CELL = 0.008

    def __init__(self):
        self.stations = []  # (lon, lat, name, zh_name, dist_ok)
        self.grid = {}

    def add(self, lon, lat, name, zh_name):
        self.stations.append((lon, lat, name, zh_name))
        i = len(self.stations) - 1
        self.grid.setdefault((int(lat // self.CELL), int(lon // self.CELL)), []).append(i)

    def nearest(self, lon, lat, radius_m=150.0):
        """返回 (index, dist_m) 或 (None, inf)。"""
        key = (int(lat // self.CELL), int(lon // self.CELL))
        # 网格单元 ~0.008° ≈ 890m,半径 150m 时查 3x3 足够;更大半径自动扩圈
        rng = max(1, int(radius_m / 800.0) + 1)
        best, bd = None, float("inf")
        for dx in range(-rng, rng + 1):
            for dy in range(-rng, rng + 1):
                for i in self.grid.get((key[0] + dx, key[1] + dy), []):
                    s = self.stations[i]
                    d = math.hypot(s[0] - lon, s[1] - lat) * 111000.0
                    if d < bd:
                        bd, best = d, i
        if best is not None and bd <= radius_m:
            return best, bd
        return None, float("inf")


def load_station_index() -> StationIndex:
    """只收铁路站(railway=station/halt),排除 bus_stop,避免公交站污染站序。"""
    idx = StationIndex()
    seen = set()
    for pat, _ in REGIONS:
        data = load_json(f"stations_{pat}_*.json")
        for el in data.get("elements", []):
            t = el.get("tags", {})
            if t.get("railway") not in ("station", "halt"):
                continue
            name = t.get("name")
            if not name or "lat" not in el:
                continue
            z = t.get("name:zh-Hans") or t.get("name:zh")
            key = (round(el["lon"], 6), round(el["lat"], 6))
            if key in seen:
                continue
            seen.add(key)
            idx.add(el["lon"], el["lat"], name, to_zh_extra(z) if z else zh.station_zh(name))
    return idx


def add_overpass_nodes(idx: StationIndex, data):
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = t.get("name")
        if not name or "lat" not in el:
            continue
        z = t.get("name:zh-Hans") or t.get("name:zh")
        idx.add(el["lon"], el["lat"], name, to_zh_extra(z) if z else zh.station_zh(name))


# ---------- relation 几何/成员提取 ----------
def relation_way_segments(rel):
    segs = []
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("geometry"):
            seg = [(p["lon"], p["lat"]) for p in m["geometry"] if p]
            if len(seg) >= 2:
                segs.append(seg)
    return segs


def relation_stop_points(rel):
    """role=stop 的 node 坐标,按 relation 顺序。"""
    out = []
    for m in rel.get("members", []):
        if (m.get("type") == "node" and m.get("role") == "stop"
                and m.get("lat") is not None):
            out.append((m["lon"], m["lat"], m.get("ref")))
    return out


def chain_polyline(segs, max_gap_m=600.0):
    """way 段贪心拼接为一条折线,返回 (polyline, 反向衔接次数)。

    - route relation 常混入孤立侧线/车库线:多轮「最长未用段起步建链」取主径。
    - 双轨长线(新幹線等)的平行轨道会让纯最近邻贪心在上下行间 ping-pong
      (实测東海道新幹線折线 1265km vs 实际 515km,弧长序全乱)—— 扩展时
      优先与链端方向顺向的候选段,无顺向候选才允许逆向(计入反向次数,
      调用方据此判断弧长是否可信)。
    - 近邻查找用网格加速(数千段时全扫描 O(n²) 要跑分钟级)。
    """
    if not segs:
        return [], 0
    r = 6378137.0

    def mx(lon):
        return math.radians(lon) * r

    def my(lat):
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * r

    cell = max_gap_m
    grid = {}  # (cx, cy) -> [(seg_i, end_i, x, y)]
    for i, s in enumerate(segs):
        for e, p in ((0, s[0]), (1, s[-1])):
            x, y = mx(p[0]), my(p[1])
            grid.setdefault((int(x // cell), int(y // cell)), []).append((i, e, x, y))
    limit = max_gap_m * max_gap_m

    def candidates(x, y, used):
        cx, cy = int(x // cell), int(y // cell)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, e, px, py in grid.get((cx + dx, cy + dy), ()):
                    if i in used:
                        continue
                    d = (px - x) ** 2 + (py - y) ** 2
                    if d < limit:
                        out.append((i, e, d))
        return out

    def grow(si):
        used = {si}
        cur = list(segs[si])
        head_parts = []  # 依次前插的段,最后逆序拼接
        anti = 0

        def pick(x, y, vx, vy):
            """最近的顺向候选;无顺向才取逆向。返回 (i, e, rev) 或 None。"""
            cs = candidates(x, y, used)
            if not cs:
                return None
            vn = math.hypot(vx, vy)
            vxu, vyu = (vx / vn, vy / vn) if vn > 0 else (0.0, 0.0)
            scored = []
            for i, e, d in cs:
                s = segs[i]
                fx, fy = mx(s[-1][0]) - mx(s[0][0]), my(s[-1][1]) - my(s[0][1])
                if e == 1:  # 接在对方端点上,行进方向取反
                    fx, fy = -fx, -fy
                fn = math.hypot(fx, fy)
                cosv = (vxu * fx + vyu * fy) / fn if fn > 0 else 0.0
                scored.append((cosv < -0.2, d, i, e))
            scored.sort()
            rev, _d, i, e = scored[0]
            return i, e, rev

        def extend_tail():
            nonlocal anti
            while True:
                x, y = mx(cur[-1][0]), my(cur[-1][1])
                k = max(0, len(cur) - 4)
                hit = pick(x, y, x - mx(cur[k][0]), y - my(cur[k][1]))
                if hit is None:
                    return
                bi, be, rev = hit
                used.add(bi)
                if rev:
                    anti += 1
                seg = segs[bi]
                cur.extend(seg[1:] if be == 0 else seg[::-1][1:])

        def extend_head():
            nonlocal anti
            while True:
                if head_parts:
                    p = head_parts[-1]
                    nxt = p[1] if len(p) >= 2 else (
                        head_parts[-2][0] if len(head_parts) >= 2 else cur[0])
                else:
                    p = None
                    nxt = cur[1]
                hx, hy = mx((p[0] if p else cur[0])[0]), my((p[0] if p else cur[0])[1])
                hit = pick(hx, hy, hx - mx(nxt[0]), hy - my(nxt[1]))
                if hit is None:
                    return
                bi, be, rev = hit
                used.add(bi)
                if rev:
                    anti += 1
                seg = segs[bi]
                # 对方末端贴链头 → 正向前插;对方首端贴链头 → 逆向前插
                head_parts.append(seg[:-1] if be == 1 else seg[::-1][:-1])

        extend_tail()
        extend_head()
        poly = [p for part in reversed(head_parts) for p in part] + cur
        return poly, used, anti

    unused = set(range(len(segs)))
    best = []
    best_anti = 0
    for si in sorted(range(len(segs)), key=lambda i: -len(segs[i])):
        if si not in unused:
            continue
        poly, used, anti = grow(si)
        if len(poly) > len(best):
            best = poly
            best_anti = anti
        unused -= used
    return best, best_anti


def project_onto(poly, lon, lat):
    """点投影到折线,返回 (弧长 m, 最近距离 m)。(小折线用)"""
    xs, ys, cum, segs = build_line_index(poly, chunk=len(poly))
    return project_indexed(xs, ys, cum, segs, lon, lat)


def build_line_index(poly, chunk=100):
    """预计算折线墨卡托坐标 + 分段 bbox 索引。"""
    r = 6378137.0
    xs = [math.radians(p[0]) * r for p in poly]
    ys = [math.log(math.tan(math.pi / 4 + math.radians(p[1]) / 2)) * r for p in poly]
    cum = [0.0]
    for i in range(1, len(xs)):
        cum.append(cum[-1] + math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]))
    segs = []
    for s in range(0, len(poly) - 1, chunk):
        e = min(len(poly) - 1, s + chunk)
        segs.append((s, e, min(xs[s:e + 1]), min(ys[s:e + 1]),
                     max(xs[s:e + 1]), max(ys[s:e + 1])))
    return xs, ys, cum, segs


def project_indexed(xs, ys, cum, segs, lon, lat):
    """带分段 bbox 预筛的投影:站距线 ≤60m 时距段 bbox 也 ≤60m,PAD 80m 精确不漏。"""
    r = 6378137.0
    px = math.radians(lon) * r
    py = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * r
    best_s, best_d = 0.0, float("inf")
    pad = 80.0
    for s, e, minx, miny, maxx, maxy in segs:
        if px < minx - pad or px > maxx + pad or py < miny - pad or py > maxy + pad:
            continue
        for i in range(s + 1, e + 1):
            x1, y1 = xs[i - 1], ys[i - 1]
            x2, y2 = xs[i], ys[i]
            dx, dy = x2 - x1, y2 - y1
            seg2 = dx * dx + dy * dy
            if seg2 == 0:
                continue
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg2))
            qx, qy = x1 + t * dx, y1 + t * dy
            d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 < best_d:
                best_d = d2
                best_s = cum[i - 1] + t * math.sqrt(seg2)
    return best_s, math.sqrt(best_d)


# ---------- 主流程 ----------
def main():
    print("[1/6] 加载 lvji 缓存 ...")
    rail = {rid: load_json(f"rail_{rid}_*.json") for rid, _ in REGIONS}
    station_idx = load_station_index()
    print(f"      站名池: {len(station_idx.stations)} 个站 node")
    # 站名→中文名 映射(供 LINE_STOP_OVERRIDES 兜底线路补 zh)
    name_zh = {}
    for _lo, _la, sja, szh in station_idx.stations:
        if sja and szh and sja not in name_zh:
            name_zh[sja] = szh

    # ---- 收集 relation,过滤噪声,按归并 key 分组 ----
    print("[2/6] 解析线路 relation ...")
    groups = {}  # (region, group_key) -> [(rel, has_stop, stop_count)]
    for rid, _ in REGIONS:
        for rel in rail[rid].get("elements", []):
            if rel.get("type") != "relation":
                continue
            tags = rel.get("tags", {})
            name = tags.get("name")
            if not name:
                continue
            if is_noise(tags, name):
                continue
            gkey = group_key(name)
            if not gkey:
                continue
            stops = relation_stop_points(rel)
            groups.setdefault((rid, gkey), []).append((rel, len(stops) > 0, len(stops)))

    # ---- 每组选代表:优先带 stop,其次站多/成员多 ----
    reps = {}  # (region, group_key) -> (rel, has_stop)
    for key, lst in groups.items():
        lst.sort(key=lambda x: (not x[1], -x[2], -len(x[0].get("members", []))))
        rel, has_stop, _ = lst[0]
        reps[key] = (rel, has_stop)

    # ---- 跨 region 去重:同 (operator, 归并名) 只留先出现的 region(東海道新幹線等) ----
    region_order = [r[0] for r in REGIONS]
    seen_global = set()
    for key in sorted(reps.keys(), key=lambda k: region_order.index(k[0])):
        rel, _ = reps[key]
        tags = rel.get("tags", {})
        gk = (operator_of(tags, tags.get("name", "")), key[1])
        if gk in seen_global:
            del reps[key]
            continue
        seen_global.add(gk)

    n_with = sum(1 for _, h in reps.values() if h)
    print(f"      {len(reps)} 条线路(带 stop 成员 {n_with} / 无 stop {len(reps) - n_with})")

    # ---- Q1: 带 stop 的缺名/模糊匹配 node 补抓 ----
    print("[3/6] 匹配站名,收集缺口 ...")
    miss_ids = set()
    for key, (rel, has_stop) in reps.items():
        if not has_stop:
            continue
        for lon, lat, ref in relation_stop_points(rel):
            i, d = station_idx.nearest(lon, lat, 150.0)
            # 30m 内视为高置信匹配;30-150m 是大站综合体的模糊地带
            # (新宿的 stop node 会误配到「新線新宿」),抓回 node 本身的名字优先
            if ref and (i is None or d > 30.0):
                miss_ids.add(ref)
    print(f"      站名缺失/模糊 node: {len(miss_ids)} 个")
    miss_ids = sorted(miss_ids)
    for bi in range(0, len(miss_ids), 800):
        batch = miss_ids[bi:bi + 800]
        q = ("[out:json][timeout:180];node(id:"
             + ",".join(map(str, batch)) + ");out;")
        add_overpass_nodes(station_idx, overpass(q, f"q1v2_missing_{bi // 800}"))
    # 再匹配一次,统计剩余缺口
    still = 0
    for key, (rel, has_stop) in reps.items():
        if not has_stop:
            continue
        for lon, lat, ref in relation_stop_points(rel):
            i, _ = station_idx.nearest(lon, lat, 150.0)
            if i is None:
                still += 1
    print(f"      补抓后仍缺: {still} 个(stop node 无名或已删除,将跳过)")

    # ---- Q3: stop 稀疏的 relation,补抓其 node 成员的 tags(站序救援) ----
    # relation 成员 node 是绘图者标注的属于本线的停靠点,天然排除邻线站;
    # 缺 stop 成员的线路(山手線/新幹線/京阪本線等)靠它拿完整站表。
    print("[4/6] stop 稀疏线路:补抓成员 node ...")
    q3_rels = [rel for (rel, _h) in reps.values()
               if len(relation_stop_points(rel)) < 5]
    q3_ids = sorted({m["ref"] for rel in q3_rels
                     for m in rel.get("members", [])
                     if m.get("type") == "node" and m.get("ref")})
    print(f"      {len(q3_rels)} 条线路 / {len(q3_ids)} 个成员 node")
    q3_nodes = {}  # node_id -> (lon, lat, tags)
    for bi in range(0, len(q3_ids), 800):
        batch = q3_ids[bi:bi + 800]
        q = ("[out:json][timeout:180];node(id:"
             + ",".join(map(str, batch)) + ");out;")
        for el in overpass(q, f"q3_member_{bi // 800}").get("elements", []):
            if "lat" in el:
                q3_nodes[el["id"]] = (el["lon"], el["lat"], el.get("tags", {}))
    n_q3_stops = sum(1 for _lon, _lat, t in q3_nodes.values()
                     if t.get("railway") in ("stop_position", "stop", "station", "halt")
                     and t.get("name"))
    print(f"      其中带名字的停靠点 node: {n_q3_stops} 个")

    # ---- 生成每条线路的有序站表 ----
    # 站序:way 几何拼折线 → 站点投影按弧长排序(relation 成员顺序不保证几何顺序)。
    # 折线断裂导致投影覆盖率不足时退回 relation 原始顺序;stop 稀疏的线路用
    # Q3 成员 node 补站表 —— relation 的成员 node 天然属于本线,排除邻线/并行线站。
    print("[5/6] 生成站序 ...")
    stats = {"proj": 0, "proj_q3": 0, "q3": 0, "order": 0, "geo": 0, "drop": 0, "override": 0}
    anomalies = []
    regions_out = []
    for rid, rname in REGIONS:
        operators = {}
        for (rrid, gkey), (rel, has_stop) in sorted(reps.items()):
            if rrid != rid:
                continue
            tags = rel.get("tags", {})
            name = tags.get("name")
            stops_raw = relation_stop_points(rel)

            # stop node → 站名池匹配(relation 顺序)
            matched = []  # [(ja, zh), ...]
            for lon, lat, ref in stops_raw:
                i, _ = station_idx.nearest(lon, lat, 150.0)
                if i is not None:
                    s = station_idx.stations[i]
                    matched.append((s[2], s[3] or s[2]))

            # Q3 成员 node —— stop 稀疏线路的主要站源(带 relation 成员序号)
            q3_matched = []  # [(member_idx, lon, lat, ja, zh), ...]
            if len(stops_raw) < 5:
                for k, m in enumerate(rel.get("members", [])):
                    info = q3_nodes.get(m.get("ref")) if m.get("type") == "node" else None
                    if not info:
                        continue
                    lon, lat, mt = info
                    if mt.get("railway") not in ("stop_position", "stop", "station", "halt"):
                        continue
                    nm = clean_stop_name(mt.get("name", ""))
                    if not nm:
                        continue
                    z = mt.get("name:zh-Hans") or mt.get("name:zh")
                    q3_matched.append((k, lon, lat, nm,
                                       to_zh_extra(z) if z else zh.station_zh(nm)))

            # 投影站序
            candidates = []  # (arc, dist, ja, zh, prio) prio: 0=stop node 1=Q3
            proj_stop = 0
            segs = relation_way_segments(rel)
            poly, anti = chain_polyline(segs)
            if len(poly) >= 2:
                xs, ys, cum, chunks = build_line_index(poly, chunk=100)
                for lon, lat, ref in stops_raw:
                    i, _ = station_idx.nearest(lon, lat, 150.0)
                    if i is None:
                        continue
                    s = station_idx.stations[i]
                    arc, d = project_indexed(xs, ys, cum, chunks, lon, lat)
                    if d <= 150.0:
                        candidates.append((arc, d, s[2], s[3] or s[2], 0))
                        proj_stop += 1
                for _k, lon, lat, nm, zn in q3_matched:
                    arc, d = project_indexed(xs, ys, cum, chunks, lon, lat)
                    if d <= 300.0:
                        candidates.append((arc, d, nm, zn, 1))

            # 站表来源决策(clean = 折线反向衔接 ≤2,弧长序可信):
            # ① 折线干净且 stop/Q3 投影覆盖足 → 弧长序
            # ② stop 成员池匹配 ≥2 → stop 成员顺序兜底
            # ③ Q3 成员站充足 → relation 成员顺序兜底(mapper 站序)
            # ④ 折线两侧 50m 站名池兜底
            proj_q3 = len(candidates) - proj_stop
            clean = len(poly) >= 2 and anti <= 2
            override = (LINE_STOP_OVERRIDES.get(rel.get("id"))
                        or LINE_STOP_OVERRIDES.get(group_key(name)))
            if override:
                # 人工站表:跳过投影/成员兜底,直接按给定顺序(zh 从站名池/映射补)
                candidates = [(k, 0.0, nm,
                               to_zh_extra(name_zh.get(nm)) if name_zh.get(nm) else zh.station_zh(nm),
                               0) for k, nm in enumerate(override)]
                stats["override"] += 1
            elif clean and len(matched) >= 2 and proj_stop >= max(2, len(matched) - 2):
                candidates.sort(key=lambda c: (c[0], c[4]))
                stats["proj"] += 1
            elif clean and proj_q3 >= 2:
                candidates.sort(key=lambda c: (c[0], c[4]))
                stats["proj_q3"] += 1
            elif len(matched) >= 2:
                candidates = [(k, 0.0, ja, cn, 0)
                              for k, (ja, cn) in enumerate(matched)]
                stats["order"] += 1
                anomalies.append(f"    order {name}: 池{len(matched)} 投影{proj_stop} 折线{len(poly)}点 反接{anti}")
            elif len(q3_matched) >= 2:
                pos = {}
                for k, m in enumerate(rel.get("members", [])):
                    if m.get("type") == "node":
                        pos[m.get("ref")] = k
                seq = []
                for lon, lat, ref in stops_raw:
                    i, _ = station_idx.nearest(lon, lat, 150.0)
                    if i is not None:
                        s = station_idx.stations[i]
                        seq.append((pos.get(ref, 10 ** 9), s[2], s[3] or s[2]))
                seq.extend((k, nm, zn) for k, _lo, _la, nm, zn in q3_matched)
                seq.sort(key=lambda t: t[0])
                candidates = [(j, 0.0, ja, cn, 1) for j, (_p, ja, cn) in enumerate(seq)]
                stats["q3"] += 1
                anomalies.append(f"    q3    {name}: 成员站 {len(seq)} 反接{anti}")
            else:
                # 无成员站源:折线两侧 50m 内的站名池站兜底(几何匹配版 Q2)
                if len(poly) >= 2:
                    xs, ys, cum, chunks = build_line_index(poly, chunk=100)
                    for slon, slat, sja, szh in station_idx.stations:
                        arc, d = project_indexed(xs, ys, cum, chunks, slon, slat)
                        if d <= 50.0:
                            candidates.append((arc, d, sja, szh or sja, 2))
                if len(candidates) >= 2:
                    candidates.sort(key=lambda c: (c[0], c[4]))
                    stats["geo"] += 1
                    anomalies.append(f"    geo   {name}: 折线站 {len(candidates)}")
                else:
                    stats["drop"] += 1
                    anomalies.append(
                        f"    DROP  {name}: stop{len(stops_raw)} 池{len(matched)} "
                        f"q3{len(q3_matched)} 折线{len(poly)}点")
                    continue

            seen = set()
            dedup = []
            for arc, d, ja, cn, prio in candidates:
                ja = clean_stop_name(ja)
                if not ja or ja in seen:
                    continue
                seen.add(ja)
                dedup.append((ja, cn))
            # 环线首尾同名去尾
            if len(dedup) > 2 and dedup[0][0] == dedup[-1][0]:
                dedup.pop()
            if len(dedup) < 2:
                stats["drop"] += 1
                anomalies.append(f"    DROP  {name}: 去重后不足 2 站")
                continue
            op = operator_of(tags, name)
            disp = display_name(name)
            colour = (tags.get("colour") or "").strip()
            if not re.match(r"^#?[0-9a-fA-F]{3}$|^#?[0-9a-fA-F]{6}$", colour):
                colour = ""
            operators.setdefault(op, []).append({
                "name": disp,
                "zh": line_zh_name(tags, disp),
                "colour": colour,
                "stops": dedup,
            })
        op_list = [{"name": op, "lines": lines}
                   for op, lines in sorted(operators.items(), key=lambda kv: -sum(len(l["stops"]) for l in kv[1]))]
        regions_out.append({"id": rid, "name": rname, "operators": op_list})
    print(f"      站表来源: 投影 {stats['proj']} / 稀疏投影 {stats['proj_q3']} / "
          f"成员顺序 {stats['q3']} / stop原始顺序 {stats['order']} / "
          f"折线站 {stats['geo']} / 人工兜底 {stats['override']} / 丢弃 {stats['drop']}")
    if anomalies:
        print(f"      异常线路 {len(anomalies)} 条:")
        print("\n".join(anomalies))

    # ---- 输出 ----
    print("[6/6] 写 data.js ...")
    n_lines = sum(len(o["lines"]) for r in regions_out for o in r["operators"])
    n_stops = sum(len(l["stops"]) for r in regions_out for o in r["operators"] for l in o["lines"])
    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "regions": regions_out,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("// 自动生成:scripts/build_data.py 生成于 " + out["generated"] + "\n")
        f.write("// 结构: regions[] -> operators[] -> lines[] -> stops[[日文,中文],...]\n")
        f.write("var TRANSIT_DATA = ")
        f.write(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")
    size = os.path.getsize(OUT_PATH) / 1024
    print(f"完成:{n_lines} 条线路 / {n_stops} 个站记录 / {size:.0f} KB -> {OUT_PATH}")
    for r in regions_out:
        rn = sum(len(o["lines"]) for o in r["operators"])
        print(f"  {r['name']}: {rn} 条线路, {len(r['operators'])} 家公司")


if __name__ == "__main__":
    main()
