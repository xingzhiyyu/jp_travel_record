#!/usr/bin/env python3
"""生成 transit-tool 的 data.js —— 数据源:data/source/japan_railways.sqlite

数据集为 2025-09 口径的全国铁路站表(598 线 / 10390 站, 含站序 sequence 与 prev/next 链)。
本脚本按既有 UI 结构(地区→运营商→线路→站点)重排:

  1. 范围:全国 8 地区(数据集 area_primary),UI 按 关西/东京圈/北海道/东北/中部/
     中国地方/四国/九州 组织;新干线随其 area 归区
  2. 剔除缆车/钢索/索道线(ケーブル/鋼索/索道/ロープウェイ 及 登山电车)
  3. 站序直接采用数据集 sequence 字段(官方口径, 支线按链化顺序)
  4. 中文站名:data/source/station_zh_pool.json(旧 OSM name:zh 池 749 条),
     无映射时保留日文原名 —— 与旧版 data.js 的兜底行为一致
  5. 线路中文名/配色:data/source/legacy_line_meta.json(旧版 277 线元数据),
     归一化匹配(去 JR 前缀/城市前缀), 无映射时 to_zh_extra 兜底转换
  6. 运营商名归一:数据集把同公司多线路拆成碎片 operator(如「叡山電鉄本/鞍馬」),
     按 OPERATOR_MAP 归并回公司名, 并沿用旧 UI 简称(近鉄/京急/阪急…)

已知修正:数据集把 箕面萱野(M16, 2024 开业) 只归入北大阪急行, 御堂筋线缺北延伸段,
此处为其头部补回 箕面萱野。

旧 OSM 管线(Overpass 抓取 + 几何投影排序)整体保留在 scripts/build_data_osm.py, 不再使用。
"""

import json
import os
import re
import sqlite3
import sys

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LVJI_ROOT = os.path.dirname(TOOL_DIR)
sys.path.insert(0, LVJI_ROOT)

from data import station_zh as zh  # noqa: E402  lvji 的中文映射(to_zh / LINE_NAME_ZH)

DB_PATH = os.path.join(TOOL_DIR, "data", "source", "japan_railways.sqlite")
OUT_PATH = os.path.join(TOOL_DIR, "data.js")
ZH_POOL_PATH = os.path.join(TOOL_DIR, "data", "source", "station_zh_pool.json")
LEGACY_META_PATH = os.path.join(TOOL_DIR, "data", "source", "legacy_line_meta.json")

REGIONS = {
    "kansai": "关西（大阪·神户·京都·奈良）",
    "kanto": "东京都市圈",
    "hokkaido": "北海道（札幌·函馆）",
    "tohoku": "东北（仙台·秋田·青森）",
    "chubu": "中部（名古屋·静冈·金泽）",
    "chugoku": "中国地方（冈山·广岛·山口）",
    "shikoku": "四国（高松·松山·高知）",
    "kyushu": "九州（福冈·鹿儿岛·那霸）",
}
AREA_TO_REGION = {
    "近畿": "kansai", "関東": "kanto", "北海道": "hokkaido", "東北": "tohoku",
    "中部": "chubu", "中国": "chugoku", "四国": "shikoku", "九州": "kyushu",
}

# 缆车/钢索类:按线路名剔除, 另附不带关键词的登山电车
CABLE_RE = re.compile(r"ケーブル|鋼索|索道|ロープウェイ")
CABLE_EXACT = {"御岳登山鉄道", "比叡山鉄道線", "高尾登山電鉄線",
               "青函トンネル竜飛斜坑線"}

# 数据集 operator → UI 运营商名(碎片归并 + 旧简称)
OPERATOR_MAP = {
    # 近畿
    "近畿日本鉄道": "近鉄",
    "大阪市高速電気軌道（Osaka Metro）": "大阪メトロ",
    "南海電気鉄道": "南海",
    "阪急電鉄": "阪急",
    "京阪電気鉄道": "京阪",
    "阪神電気鉄道": "阪神",
    "山陽電鉄本": "山陽電気鉄道", "山陽電鉄網干": "山陽電気鉄道",
    "叡山電鉄本": "叡山電鉄", "叡山電鉄鞍馬": "叡山電鉄",
    "能勢電鉄妙見": "能勢電鉄", "能勢電鉄日生": "能勢電鉄",
    "近江鉄道本": "近江鉄道", "近江鉄道多賀": "近江鉄道", "近江鉄道八日市": "近江鉄道",
    "神戸電鉄有馬": "神戸電鉄", "神戸電鉄粟生": "神戸電鉄",
    "神戸電鉄公園都市": "神戸電鉄", "神戸電鉄三田": "神戸電鉄",
    "京都丹後鉄道宮豊": "京都丹後鉄道", "京都丹後鉄道宮舞": "京都丹後鉄道",
    "京都丹後鉄道宮福": "京都丹後鉄道",
    "三岐鉄道三岐": "三岐鉄道", "三岐鉄道北勢": "三岐鉄道",
    "四日市あすなろう鉄道内部": "四日市あすなろう鉄道",
    "四日市あすなろう鉄道八王子": "四日市あすなろう鉄道",
    "伊勢鉄伊勢": "伊勢鉄道",
    "京都市烏丸": "京都市交通局", "京都市東西": "京都市交通局",
    "神戸市西神・山手": "神戸市交通局", "神戸市海岸": "神戸市交通局",
    "神戸高速鉄道東西": "神戸高速鉄道", "神戸高速神鉄": "神戸高速鉄道",
    "神戸新交通（六甲ライナー）": "神戸新交通", "ポートアイランド": "神戸新交通",
    "北港テクノポート": "大阪メトロ",
    "おおさか東": "JR西日本",
    "京福嵐山": "京福電気鉄道", "京福北野": "京福電気鉄道",
    "嵯峨野観光": "嵯峨野観光鉄道",
    "智頭急行智頭": "智頭急行",
    "わかやま電鉄貴志川": "わかやま電鉄",
    "北神急行": "北神急行電鉄",
    # 関東
    "東京地下鉄（Tokyo Metro）": "東京メトロ",
    "東京都交通局（都営地下鉄）": "東京都交通局",
    "東京都交通局（日暮里・舎人ライナー）": "東京都交通局",
    "東京都交通局（都電）": "東京都交通局",
    "東急電鉄（世田谷線）": "東急電鉄",
    "京浜急行電鉄": "京急", "京成電鉄": "京成", "西武鉄道": "西武",
    "東武鉄道": "東武", "小田急電鉄": "小田急", "京王電鉄": "京王",
    "相模鉄道": "相鉄", "相模鉄道・東京急行電鉄": "相鉄・東急",
    "つくばエクスプレス": "首都圏新都市鉄道",
    "東京りんかい": "東京臨海高速鉄道",
    "横浜市ブルーライン": "横浜市交通局", "グリーンライン": "横浜市交通局",
    "横浜みなとみらい": "横浜高速鉄道",
    "埼玉高速": "埼玉高速鉄道",
    "埼玉新都市交通（ニューシャトル）": "埼玉新都市交通",
    "いすみ": "いすみ鉄道",
    "ひたちなか海浜鉄道湊": "ひたちなか海浜鉄道",
    "上信電鉄上信": "上信電鉄", "上毛電鉄上毛": "上毛電気鉄道",
    "伊豆箱根鉄道大雄山": "伊豆箱根鉄道",
    "流鉄流山": "流鉄", "銚子電鉄": "銚子電気鉄道",
    "関東鉄道常総": "関東鉄道", "関東鉄道竜ヶ崎": "関東鉄道",
    "秩父本": "秩父鉄道", "東葉高速": "東葉高速鉄道",
    # 北海道
    "札幌市南北": "札幌市交通局", "札幌市東西": "札幌市交通局",
    "札幌市東豊": "札幌市交通局", "札幌市電": "札幌市交通局",
    # 東北
    "仙台市南北": "仙台市交通局", "仙台市東西": "仙台市交通局",
    "仙台空港": "仙台空港鉄道",
    "三陸鉄道リアス": "三陸鉄道", "会津鉄道会津": "会津鉄道",
    "山形鉄道フラワー長井": "山形鉄道",
    "弘南鉄道弘南": "弘南鉄道", "弘南鉄道大鰐": "弘南鉄道",
    "由利高原鉄道鳥海山ろく": "由利高原鉄道", "福島交通飯坂": "福島交通",
    "秋田内陸縦貫": "秋田内陸縦貫鉄道", "野岩鉄道会津鬼怒川": "野岩鉄道",
    # 中部
    "名古屋市名城": "名古屋市交通局", "名古屋市名港": "名古屋市交通局",
    "名古屋市東山": "名古屋市交通局", "名古屋市桜通": "名古屋市交通局",
    "名古屋市鶴舞": "名古屋市交通局", "名古屋市上飯田": "名古屋市交通局",
    "あおなみ": "名古屋臨海高速鉄道",
    "えちぜん鉄道三国芦原": "えちぜん鉄道", "えちぜん鉄道勝山永平寺": "えちぜん鉄道",
    "上田電鉄別所": "上田電鉄", "北越急行ほくほく": "北越急行",
    "北陸鉄道浅野川": "北陸鉄道", "北陸鉄道石川": "北陸鉄道",
    "のと鉄道七尾": "のと鉄道", "長良川鉄道越美南": "長良川鉄道",
    "大井川鉄道本": "大井川鉄道", "大井川鉄道井川": "大井川鉄道",
    "富山地方鉄道上滝": "富山地方鉄道", "富山地方鉄道不二越": "富山地方鉄道",
    "富山地方鉄道本": "富山地方鉄道", "富山地方鉄道立山": "富山地方鉄道",
    "富山地鉄市内": "富山地方鉄道",
    "樽見鉄道樽見": "樽見鉄道", "福井鉄道福武": "福井鉄道",
    "豊橋鉄道渥美": "豊橋鉄道", "長野電鉄長野": "長野電鉄",
    "松本電鉄上高地": "松本電鉄", "静岡鉄道静岡清水": "静岡鉄道",
    # 中国
    "岡山電軌東山": "岡山電気軌道", "岡山電軌清輝橋": "岡山電気軌道",
    "一畑電車北松江": "一畑電車", "一畑電車大社": "一畑電車",
    "水島臨海": "水島臨海鉄道", "若桜鉄道若桜": "若桜鉄道",
    "錦川鉄道錦川清流": "錦川鉄道",
    # 中部(补)
    "伊豆箱根鉄道駿豆": "伊豆箱根鉄道", "天竜浜名湖": "天竜浜名湖鉄道",
    "黒部峡谷": "黒部峡谷鉄道",
    # 四国
    "伊予鉄城北": "伊予鉄道", "伊予鉄城南": "伊予鉄道",
    "伊予鉄大手町": "伊予鉄道", "伊予鉄本町": "伊予鉄道",
    "伊予鉄横河原": "伊予鉄道", "伊予鉄花園": "伊予鉄道",
    "伊予鉄郡中": "伊予鉄道", "伊予鉄高浜": "伊予鉄道",
    "高松琴平電鉄志度": "高松琴平電鉄", "高松琴平電鉄琴平": "高松琴平電鉄",
    "高松琴平電鉄長尾": "高松琴平電鉄",
    "土佐くろしお鉄道中村": "土佐くろしお鉄道",
    "土佐くろしお鉄道宿毛": "土佐くろしお鉄道",
    "土佐くろしお鉄道阿佐": "土佐くろしお鉄道",
    "阿佐海岸": "阿佐海岸鉄道",
    # 九州
    "西日本鉄道": "西鉄",
    "福岡市七隈": "福岡市交通局", "福岡市空港": "福岡市交通局",
    "福岡市箱崎": "福岡市交通局",
    "平成筑豊鉄道伊田": "平成筑豊鉄道", "平成筑豊鉄道田川": "平成筑豊鉄道",
    "平成筑豊鉄道糸田": "平成筑豊鉄道",
    "長崎電軌本": "長崎電気軌道", "長崎電軌蛍茶屋支": "長崎電気軌道",
    "長崎電軌赤迫支": "長崎電気軌道", "長崎電軌大浦支": "長崎電気軌道",
    "長崎電軌桜町支": "長崎電気軌道",
    "熊本電鉄菊池": "熊本電気鉄道", "熊本電鉄藤崎": "熊本電気鉄道",
    "筑豊電鉄": "筑豊電気鉄道", "南阿蘇": "南阿蘇鉄道",
    "甘木鉄道甘木": "甘木鉄道",
    "北九州高速": "北九州モノレール",
    "沖縄都市モノレール（ゆいレール）": "沖縄都市モノレール",
}

# 站表修正:{线路名: {"prepend": [站名]}} —— 顺序仍按数据集, 修正只动头部
LINE_FIXES = {
    # 数据集把 箕面萱野(M16) 只归入北大阪急行, 御堂筋线缺 2024 北延伸段
    "大阪御堂筋線": {"prepend": ["箕面萱野"]},
}

# 运营商修正:{线路名: 正确公司} —— 数据集把大阪单轨误记在 Osaka Metro 名下
LINE_OPERATOR_FIX = {
    "大阪モノレール": "大阪モノレール",
    "大阪モノレール彩都線": "大阪モノレール",
}

# 线路改名:{数据集名: (显示名, 中文名)} —— 直通线按常用口径显示
LINE_RENAME = {
    "相模鉄道・東京急行電鉄": ("相鉄・東急直通線", "相铁・东急直通线"),
}

# 城市前缀:归一化匹配旧线路元数据时剥离(大阪御堂筋線 → 御堂筋線 等)
CITY_PREFIXES = ("大阪市高速電気軌道", "東京メトロ", "大阪", "神戸市", "京都市",
                 "横浜市", "東京都")

# 繁体 name:zh → 简体补充映射(station_zh 之外, 只在本脚本用)
EXTRA_ZH = {
    "狀": "状", "澀": "涩", "廣": "广", "萬": "万", "鐵": "铁", "國": "国",
    "場": "场", "澤": "泽", "卷": "卷", "發": "发", "樂": "乐", "嶋": "岛",
    "淺": "浅", "橋": "桥", "館": "馆", "驛": "站", "環": "环", "營": "营",
    "燒": "烧", "豐": "丰", "邊": "边", "帶": "带", "壽": "寿", "驗": "验",
    "龍": "龙", "龜": "龟", "馬": "马", "車": "车", "長": "长", "門": "门",
    "岡": "冈", "島": "岛", "線": "线", "戶": "户", "縣": "县",
    "齋": "斋", "藤": "藤", "瀧": "泷", "絲": "丝", "覺": "觉",
    # 全国化后新增线名常用字(to_zh 未覆盖)
    "幹": "干", "陸": "陆", "鶴": "鹤", "瀬": "濑", "飯": "饭",
    "桟": "栈", "蛍": "萤", "篠": "筱", "沢": "泽",
    "見": "见", "緑": "绿", "渋": "涩", "栄": "荣", "児": "儿",
    "竜": "龙", "亀": "龟", "諏": "诹", "訪": "访", "飾": "饰",
    "淵": "渊", "鷺": "鹭", "窪": "洼", "溝": "沟", "穂": "穗",
    "曽": "曾", "薩": "萨", "塩": "盐",
}


def to_zh_extra(text: str) -> str:
    """station_zh 映射 + 本脚本繁简补充。"""
    out = []
    for ch in zh.to_zh(text):
        out.append(EXTRA_ZH.get(ch, ch))
    return "".join(out)


def normalize_key(name: str) -> str:
    """线路名归一化:去 JR 前缀与城市前缀, 用于匹配旧元数据。"""
    n = name.strip()
    if n.startswith("JR"):
        n = n[2:]
    changed = True
    while changed:
        changed = False
        for pre in CITY_PREFIXES:
            if n.startswith(pre) and len(n) > len(pre) + 1:
                n = n[len(pre):]
                changed = True
    return n


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_cable(line_name: str) -> bool:
    return bool(CABLE_RE.search(line_name)) or line_name in CABLE_EXACT


def region_of(area: str) -> str | None:
    """数据集 area_primary → UI 地区;8 地区全收。"""
    return AREA_TO_REGION.get(area)


def main():
    zh_pool = load_json(ZH_POOL_PATH)
    legacy = load_json(LEGACY_META_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    lines = conn.execute(
        "SELECT line_name, operator, area_primary, prefectures, station_count, "
        "is_shinkansen FROM lines"
    ).fetchall()

    regions = {rid: {"id": rid, "name": name, "operators": {}}
               for rid, name in REGIONS.items()}
    stats = {"kept": 0, "cable": 0, "out": 0, "empty": 0}

    for row in lines:
        name = row["line_name"]
        rid = region_of(row["area_primary"])
        if rid is None:
            stats["out"] += 1
            continue
        if is_cable(name):
            stats["cable"] += 1
            continue

        stations = conn.execute(
            "SELECT station_name FROM stations WHERE line_name=? ORDER BY sequence",
            (name,),
        ).fetchall()
        stops = [[s["station_name"], zh_pool.get(s["station_name"]) or s["station_name"]]
                 for s in stations]
        for extra in LINE_FIXES.get(name, {}).get("prepend", []):
            stops.insert(0, [extra, zh_pool.get(extra) or extra])
        if not stops:
            stats["empty"] += 1
            continue

        meta = legacy.get(name) or legacy.get(normalize_key(name)) or {}
        if name in LINE_RENAME:
            name, fixed_zh = LINE_RENAME[name]
            meta = {"zh": fixed_zh}
        line = {
            "name": name,
            "zh": to_zh_extra(meta.get("zh") or zh.LINE_NAME_ZH.get(name)
                              or zh.LINE_NAME_ZH.get(normalize_key(name)) or name),
            "colour": meta.get("colour") or None,
            "stops": stops,
        }
        op_name = LINE_OPERATOR_FIX.get(name) \
            or OPERATOR_MAP.get(row["operator"], row["operator"])
        operators = regions[rid]["operators"]
        operators.setdefault(op_name, []).append(line)
        stats["kept"] += 1

    conn.close()

    data = {"generated": __import__("datetime").date.today().isoformat(), "regions": []}
    for rid, rname in REGIONS.items():
        ops = [{"name": op, "lines": sorted(lst, key=lambda l: l["name"])}
               for op, lst in regions[rid]["operators"].items()]
        ops.sort(key=lambda o: (-len(o["lines"]), o["name"]))
        data["regions"].append({"id": rid, "name": rname, "operators": ops})

    n_lines = sum(len(o["lines"]) for r in data["regions"] for o in r["operators"])
    n_stops = sum(len(l["stops"]) for r in data["regions"] for o in r["operators"]
                  for l in o["lines"])

    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(f"// 自动生成:scripts/build_data.py(源:data/source/japan_railways.sqlite,"
                f"{data['generated']})— 勿手改\n")
        f.write(f"var TRANSIT_DATA = {body};\n")

    print(f"[build] 保留 {stats['kept']} 线 / {n_stops} 站;"
          f"剔除缆车 {stats['cable']}, 范围外 {stats['out']}, 空线路 {stats['empty']}")
    for r in data["regions"]:
        n_l = sum(len(o["lines"]) for o in r["operators"])
        print(f"  {r['name']}: {len(r['operators'])} 社 / {n_l} 线")


if __name__ == "__main__":
    main()
