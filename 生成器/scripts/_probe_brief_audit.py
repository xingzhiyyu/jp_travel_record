#!/usr/bin/env python3
"""简要审计:找出走 order/q3/geo 兜底分支或被 DROP 的线路里,可能漏站的。
对每条可疑线,用站名池在 50/100/150m 三个阈值下投影计数,对比 data.js 最终站数。"""
import os, sys, glob, json, math
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_data as bd  # noqa  main() 有 __main__ 守卫,import 安全

LVJI = bd.LVJI_CACHE
TOOL = bd.TOOL_ROOT

# 可疑线路名(来自 build 异常日志的 order/q3/geo/DROP)
SUSPECT = [
    "吉野線", "JR京都線", "中之島線", "和田岬線", "水間線",
    "神戸高速線", "南北線", "中央本線", "りんかい線", "羽沢線",
    "新京成線", "相鉄・JR直通線", "相鉄厚木線", "西武安比奈線", "大阪臨港線",
    "こまち", "はやて", "はやぶさ", "ふじさん", "やまびこ", "上越新幹線", "東北新幹線",
]

def load_rails():
    rels = []
    for pat in ("rail_kansai_*.json", "rail_kanto_*.json"):
        for f in glob.glob(os.path.join(LVJI, pat)):
            d = json.load(open(f))
            rels += [e for e in d.get("elements", []) if e.get("type") == "relation"]
    return rels

def name_of(rel):
    return (rel.get("tags") or {}).get("name", "")

def match_suspects(rels):
    out = {}
    for r in rels:
        nm = name_of(r)
        for s in SUSPECT:
            if s in nm:
                out.setdefault(s, []).append(r)
    return out

def proj_counts(rel, idx):
    segs = bd.relation_way_segments(rel)
    poly, anti = bd.chain_polyline(segs)
    if len(poly) < 2:
        return None
    xs, ys, cum, chunks = bd.build_line_index(poly, chunk=100)
    c = {50: 0, 100: 0, 150: 0}
    names_150 = []
    for slon, slat, sja, szh in idx.stations:
        arc, d = bd.project_indexed(xs, ys, cum, chunks, slon, slat)
        for thr in c:
            if d <= thr:
                c[thr] += 1
        if d <= 150:
            names_150.append((arc, d, sja))
    names_150.sort()
    return len(poly), anti, c, [n for _, _, n in names_150]

def main():
    print("加载站名池...")
    idx = bd.load_station_index()
    print("加载 rail relations...")
    rels = load_rails()
    print(f"  {len(rels)} 条 relation")
    smap = match_suspects(rels)

    for s in SUSPECT:
        rs = smap.get(s, [])
        if not rs:
            print(f"\n## {s}: 在缓存中未找到 relation")
            continue
        for r in rs:
            nm = name_of(r)
            tags = r.get("tags") or {}
            op = tags.get("operator", "")
            members = r.get("members", [])
            n_stop = sum(1 for m in members if m.get("role") == "stop")
            n_node = sum(1 for m in members if m.get("type") == "node")
            n_way = sum(1 for m in members if m.get("type") == "way")
            pc = proj_counts(r, idx)
            rid = r.get("id")
            print(f"\n## {nm}  (id={rid} operator={op})")
            print(f"   members: stop={n_stop} node={n_node} way={n_way}")
            if pc is None:
                print(f"   几何: 无可拼接折线(way 成员缺 geometry?)")
            else:
                npoly, anti, c, names = pc
                print(f"   几何: 折线{npoly}点 反接{anti}")
                print(f"   投影站数: 50m={c[50]} 100m={c[100]} 150m={c[150]}")
                print(f"   150m 站名({len(names)}): {' / '.join(names)}")

if __name__ == "__main__":
    main()
