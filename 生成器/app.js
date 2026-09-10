"use strict";
/* 轨道行程记录生成器
 * 数据来自同目录 data.js(TRANSIT_DATA),双击 index.html 即可离线使用。
 * 记录自动保存在 localStorage,可导出为「序号,线路,起点,终点」的 txt。 */

const DATA = window.TRANSIT_DATA;
const STORE_KEY = "transit-tool-records-v1";

/* 标签段(非铁路):步行/公交/打车,导出为 lvji 关键字行 */
const MODES = {
  walk: { zh: "步行", colour: "#808080" },
  bus:  { zh: "公交", colour: "#4682B4" },
  taxi: { zh: "打车", colour: "#E8A522" },
};

const state = {
  records: [],    // {region, operator, line, lineZh, colour, startJa, startZh, endJa, endZh}
  exportZh: true,
  dark: false,
};

const $ = (id) => document.getElementById(id);
const els = {
  region: $("selRegion"), line: $("selLine"), start: $("selStart"), end: $("selEnd"),
  body: $("recordBody"), count: $("recordCount"), empty: $("emptyTip"),
  meta: $("lineMeta"),
  search: $("lineSearch"), searchCount: $("searchCount"),
};

/* ---------- 小工具 ---------- */
let toastTimer = null;
function toast(msg) {
  let t = $("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
}

function fmtStop(ja, zh) {
  const c = zh || ja;                 // 中文优先
  return (zh && zh !== ja) ? `${c}（${ja}）` : c;
}

function applyTheme() {
  document.body.classList.toggle("dark", state.dark);
  const b = $("btnTheme");
  if (b) b.textContent = state.dark ? "☀️ 白天" : "🌙 夜间";
}

function safeColour(c) {
  if (!c) return "";
  return /^#/.test(c) ? c : "#" + c;
}

/* ---------- 撤销(快照栈) ---------- */
const undoStack = [];
function snapshot() {
  undoStack.push(JSON.stringify(state.records));
  if (undoStack.length > 50) undoStack.shift();
}
function undo() {
  const snap = undoStack.pop();
  if (!snap) {
    toast("没有可撤销的操作");
    return;
  }
  state.records = JSON.parse(snap);
  render();
  toast("已撤销");
}

/* ---------- 下拉联动 ---------- */
function regionData() {
  return DATA.regions.find((r) => r.id === els.region.value) || DATA.regions[0];
}

function populateRegions() {
  els.region.textContent = "";
  DATA.regions.forEach((r) => {
    const o = document.createElement("option");
    o.value = r.id;
    o.textContent = r.name;
    els.region.appendChild(o);
  });
}

let lineIndex = [];   // [{el, og, hay}] 供搜索筛选

function populateLines() {
  els.line.textContent = "";
  lineIndex = [];
  const rd = regionData();
  rd.operators.forEach((op, oi) => {
    const og = document.createElement("optgroup");
    og.label = `${op.name}（${op.lines.length}）`;
    op.lines.forEach((ln, li) => {
      const o = document.createElement("option");
      o.value = `${oi}:${li}`;
      o.textContent = `${ln.zh || ln.name}（${ln.stops.length}站）`;
      og.appendChild(o);
      lineIndex.push({ el: o, og, hay: `${op.name} ${ln.name} ${ln.zh || ""}`.toLowerCase() });
    });
    if (og.children.length) els.line.appendChild(og);
  });
  applyLineFilter();
}

function applyLineFilter() {
  if (!els.search) return;
  const q = els.search.value.trim().toLowerCase();
  let n = 0;
  const ogs = new Map();
  lineIndex.forEach((it) => {
    const show = !q || it.hay.includes(q);
    it.el.style.display = show ? "" : "none";
    if (show) {
      n++;
      ogs.set(it.og, (ogs.get(it.og) || 0) + 1);
    }
  });
  lineIndex.forEach((it) => { it.og.style.display = ogs.has(it.og) ? "" : "none"; });
  if (els.searchCount) els.searchCount.textContent = q ? `${n} 条匹配` : "";
}

function currentLine() {
  const v = els.line.value;
  if (!v) return null;
  const [oi, li] = v.split(":").map(Number);
  const ops = regionData().operators;
  return ops && ops[oi] && ops[oi].lines[li] ? ops[oi].lines[li] : null;
}

function currentOperator() {
  const v = els.line.value;
  if (!v) return "";
  const [oi] = v.split(":").map(Number);
  const ops = regionData().operators;
  return ops && ops[oi] ? ops[oi].name : "";
}

function stopOption(s, i) {
  const o = document.createElement("option");
  o.value = i;
  o.textContent = fmtStop(s[0], s[1]);
  return o;
}

function populateStops(keep) {
  const ln = currentLine();
  els.start.textContent = "";
  els.end.textContent = "";
  els.meta.textContent = "";
  if (!ln) return;
  ln.stops.forEach((s, i) => {
    els.start.appendChild(stopOption(s, i));
    els.end.appendChild(stopOption(s, i));
  });
  const n = ln.stops.length;
  els.start.selectedIndex = keep && keep.start != null ? Math.min(keep.start, n - 1) : 0;
  els.end.selectedIndex = keep && keep.end != null
    ? Math.min(keep.end, n - 1) : Math.max(0, n - 1);
  // 起终点相同保护
  if (n > 1 && els.start.selectedIndex === els.end.selectedIndex) {
    els.end.selectedIndex = els.start.selectedIndex === n - 1 ? 0 : n - 1;
  }
  // 线路信息条(颜色点 + 站数)
  if (ln.colour) {
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = safeColour(ln.colour);
    els.meta.appendChild(dot);
  }
  els.meta.appendChild(document.createTextNode(
    `${currentOperator() || "—"} · ${ln.zh || ln.name} · 共 ${n} 站${ln.zh && ln.zh !== ln.name ? " · " + ln.name : ""}`));
}

/* ---------- 记录操作 ---------- */
function buildRecord() {
  const ln = currentLine();
  if (!ln) {
    toast("请先选择线路");
    return null;
  }
  const si = +els.start.value;
  const ei = +els.end.value;
  if (!(si >= 0) || !(ei >= 0)) {
    toast("请选择起点和终点");
    return null;
  }
  if (si === ei) {
    toast("起点和终点相同，请重新选择");
    return null;
  }
  return {
    region: regionData().id,
    operator: currentOperator(),
    line: ln.name,
    lineZh: ln.zh,
    colour: ln.colour,
    startJa: ln.stops[si][0],
    startZh: ln.stops[si][1],
    endJa: ln.stops[ei][0],
    endZh: ln.stops[ei][1],
  };
}

function isDuplicate(rec) {
  return state.records.some((r) => (r.kind || "") === (rec.kind || "")
    && r.line === rec.line && r.startJa === rec.startJa && r.endJa === rec.endJa);
}

function addRecord() {
  const rec = buildRecord();
  if (!rec) return;
  const dup = isDuplicate(rec);
  snapshot();
  state.records.push(rec);
  render();
  toast(dup ? "已有相同区段（仍已添加）" : "已添加到末尾");
}

/* 标签段:步行/公交/打车,起终点取输入框。
 * 都留空 = 接驳段(上一段终点 → 下一段起点,渲染时自动衔接,三种标签通用);
 * 只填终点 = 从上一段终点出发;只填起点无效。 */
function addKeyword(kind) {
  const s = ($("kwStart").value || "").trim();
  const e = ($("kwEnd").value || "").trim();
  if (s && !e) {
    toast("只填起点时无法确定方向：终点也要填，或把起点清空");
    return;
  }
  // 输入的站名对齐到真实站点(规范中/日名),渲染与回填都更稳;对不上保留原文
  const rs = stationExact.get(s) || null;
  const re = stationExact.get(e) || null;
  const rec = {
    kind, region: "", operator: "", line: "", lineZh: "", colour: null,
    startJa: rs ? rs.ja : (s || null),
    startZh: rs ? rs.zh : null,
    endJa: re ? re.ja : (e || null),
    endZh: re ? re.zh : null,
  };
  const dup = isDuplicate(rec);
  snapshot();
  state.records.push(rec);
  $("kwStart").value = "";
  $("kwEnd").value = "";
  render();
  const what = (!s && !e) ? `${MODES[kind].zh}接驳段（自动连上前后两段）` : `${MODES[kind].zh}段`;
  toast(dup ? `已有相同${what}（仍已添加）` : `已添加${what}`);
}

function insertBelow(i) {
  const rec = buildRecord();
  if (!rec) return;
  const dup = isDuplicate(rec);
  snapshot();
  state.records.splice(i + 1, 0, rec);
  render();
  toast(dup ? `已有相同区段（仍已插入到第 ${i + 2} 行）` : `已插入到第 ${i + 2} 行`);
}

function move(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= state.records.length) return;
  snapshot();
  [state.records[i], state.records[j]] = [state.records[j], state.records[i]];
  render();
}

function backfill(i) {
  const r = state.records[i];
  if (!r) return;
  if (r.kind) {
    // 标签段:回填到标签输入框
    $("kwStart").value = r.startZh || r.startJa || "";
    $("kwEnd").value = r.endZh || r.endJa || "";
    toast("已回填到标签段输入框");
    return;
  }
  els.region.value = r.region;
  populateLines();
  const rd = regionData();
  let found = "";
  rd.operators.forEach((op, oi) => op.lines.forEach((ln, li) => {
    if (!found && ln.name === r.line && op.name === r.operator) found = `${oi}:${li}`;
  }));
  if (!found) {
    rd.operators.forEach((op, oi) => op.lines.forEach((ln, li) => {
      if (!found && ln.name === r.line) found = `${oi}:${li}`;
    }));
  }
  if (!found) {
    toast(`线路「${r.line}」已不在当前数据中`);
    return;
  }
  els.line.value = found;
  populateStops();
  const ln = currentLine();
  const si = ln.stops.findIndex((s) => s[0] === r.startJa);
  const ei = ln.stops.findIndex((s) => s[0] === r.endJa);
  if (si >= 0) els.start.selectedIndex = si;
  if (ei >= 0) els.end.selectedIndex = ei;
  save();
  toast("已回填到左侧选择器");
}

function clearAll() {
  if (!state.records.length) return;
  if (!confirm(`确定清空全部 ${state.records.length} 条记录？`)) return;
  snapshot();
  state.records = [];
  render();
}

/* ---------- 表格渲染 ---------- */
function tdStop(ja, zh) {
  const tdEl = document.createElement("td");
  if (!ja && !zh) {
    tdEl.textContent = "—";
    tdEl.className = "muted-cell";
    return tdEl;
  }
  tdEl.textContent = zh || ja;           // 中文优先
  if (zh && zh !== ja) {
    const sp = document.createElement("span");
    sp.className = "stop-zh";
    sp.textContent = ja;                  // 日文作注音
    tdEl.appendChild(sp);
  }
  return tdEl;
}

function tdLine(r) {
  const tdEl = document.createElement("td");
  const wrap = document.createElement("span");
  wrap.className = "line-cell";
  if (r.kind) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.style.background = MODES[r.kind].colour;
    chip.textContent = MODES[r.kind].zh;
    wrap.appendChild(chip);
    tdEl.appendChild(wrap);
    return tdEl;
  }
  if (r.colour) {
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = safeColour(r.colour);
    wrap.appendChild(dot);
  }
  wrap.appendChild(document.createTextNode(r.lineZh || r.line));   // 中文优先
  if (r.lineZh && r.lineZh !== r.line) {
    const sp = document.createElement("span");
    sp.className = "stop-zh";
    sp.textContent = r.line;              // 日文作注音
    wrap.appendChild(sp);
  }
  tdEl.appendChild(wrap);
  return tdEl;
}

/* 单条记录昼夜切换:白天 ↔ 夜间 */
function flipRecordMode(i) {
  const r = state.records[i];
  if (!r) return;
  snapshot();
  r.mode = r.mode === "night" ? "day" : "night";
  render();
}

function tdOps(i) {
  const tdEl = document.createElement("td");
  tdEl.className = "ops";
  const mk = (label, title, fn, disabled) => {
    const b = document.createElement("button");
    b.className = "icon-btn";
    b.textContent = label;
    b.title = title;
    b.disabled = !!disabled;
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      fn();
    });
    tdEl.appendChild(b);
  };
  const r = state.records[i];
  mk(r.mode === "night" ? "🌙" : "☀️", "切换白天/夜间段", () => flipRecordMode(i));
  mk("↑", "上移一行", () => move(i, -1), i === 0);
  mk("↓", "下移一行", () => move(i, 1), i === state.records.length - 1);
  mk("⤵ 插入", "把当前选择的区段插入到这一行下方", () => insertBelow(i));
  mk("✕", "删除这一行", () => {
    snapshot();
    state.records.splice(i, 1);
    render();
  });
  return tdEl;
}

let dragIdx = null;   // 拖拽排序:被拖行的下标

function render() {
  els.body.textContent = "";
  let disc = 0;   // 与下一段起点不衔接的数量(标签段不参与检查)
  state.records.forEach((r, i) => {
    const next = state.records[i + 1];
    if (next && !r.kind && !next.kind && next.startJa !== r.endJa) disc++;
    const tr = document.createElement("tr");
    if (r.colour) {
      tr.classList.add("has-colour");
      tr.style.setProperty("--line-colour", safeColour(r.colour));
    }
    tr.draggable = true;
    tr.addEventListener("dragstart", (ev) => {
      dragIdx = i;
      tr.classList.add("dragging");
      try { ev.dataTransfer.setData("text/plain", String(i)); } catch (e) { /* 非 DOM 环境 */ }
    });
    tr.addEventListener("dragend", () => tr.classList.remove("dragging"));
    tr.addEventListener("dragover", (ev) => {
      ev.preventDefault();
      tr.classList.add("drop-target");
    });
    tr.addEventListener("dragleave", () => tr.classList.remove("drop-target"));
    tr.addEventListener("drop", (ev) => {
      ev.preventDefault();
      tr.classList.remove("drop-target");
      if (dragIdx == null || dragIdx === i) return;
      snapshot();
      const [moved] = state.records.splice(dragIdx, 1);
      state.records.splice(i, 0, moved);
      dragIdx = null;
      render();
    });
    const idx = document.createElement("td");
    idx.className = "idx";
    idx.textContent = i + 1;
    tr.appendChild(idx);
    tr.appendChild(tdLine(r));
    tr.appendChild(tdStop(r.startJa, r.startZh));
    const endTd = tdStop(r.endJa, r.endZh);
    if (next && !r.kind && !next.kind && next.startJa !== r.endJa) {
      const b = document.createElement("span");
      b.className = "badge-disc";
      b.textContent = "⚡";
      b.title = "与下一段起点不同（渲染时会产生位置跳跃）";
      endTd.appendChild(b);
    }
    tr.appendChild(endTd);
    tr.appendChild(tdOps(i));
    tr.title = "点击回填到左侧选择器";
    tr.addEventListener("click", () => backfill(i));
    els.body.appendChild(tr);
  });
  els.count.textContent = state.records.length
    ? `共 ${state.records.length} 条` + (disc ? ` · ${disc} 处不衔接` : "")
    : "";
  els.empty.style.display = state.records.length ? "none" : "";
  updatePreview();
  updateKwHint();
  save();
}

/* ---------- 导出 ---------- */
function buildText() {
  // lvji 行程格式:每行一段 ——「线路名 起点 → 终点」或关键字行(步行/公交/打车);
  // 各记录的昼夜模式变化处插入 # 白天 / # 夜间 模式行(lvji 逐段识别)
  const lines = [];
  let cur = null;
  state.records.forEach((r) => {
    const zh = state.exportZh;
    const s = zh ? (r.startZh || r.startJa) : r.startJa;
    const e = zh ? (r.endZh || r.endJa) : r.endJa;
    const m = r.mode === "night" ? "夜间" : "白天";
    if (m !== cur) {
      lines.push(`# ${m}`);
      cur = m;
    }
    if (r.kind) {
      const kw = MODES[r.kind].zh;
      if (!s && !e) lines.push(kw);                    // 裸标签:自动衔接前后两段
      else if (!s) lines.push(`${kw} ${e}`);           // 只给终点:起点接上一段
      else lines.push(`${kw} ${s} → ${e}`);
    } else {
      const line = zh ? (r.lineZh || r.line) : r.line;
      lines.push(`${line} ${s} → ${e}`);
    }
  });
  return lines.join("\n");
}

function buildTravelText() {
  // Export original station names, preserving explicit per-leg day/night.
  // A bare transfer is resolvable only between two explicit neighbouring stops.
  const records = state.records;
  const lines = ["title: 我的旅行", "size: 1280x720", "fps: 24",
    "basemap: silhouette", "theme: day", "route:"];
  records.forEach((r, i) => {
    const start = r.startJa || (i > 0 ? records[i - 1].endJa : "");
    const end = r.endJa || (r.kind && i + 1 < records.length ? records[i + 1].startJa : "");
    if (!start || !end) throw new Error(`第 ${i + 1} 段缺少明确起终点，请补齐后导出`);
    const line = r.kind || r.line;
    if (!line || [line, start, end].some(s => /[|\r\n]/.test(s))) {
      throw new Error(`第 ${i + 1} 段名称为空或含格式分隔符，请修改`);
    }
    lines.push(`未注明 | ${line} | ${start} -> ${end} | ${r.mode === "night" ? "night" : "day"}`);
  });
  return lines.join("\n");
}

function exportTxt(travelFormat = false) {
  if (!state.records.length) {
    toast("没有记录可导出");
    return;
  }
  let text;
  try { text = travelFormat ? buildTravelText() : buildText(); }
  catch (error) { toast(error.message); return; }
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  a.href = URL.createObjectURL(blob);
  a.download = `行程记录_${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
  toast(`已导出 ${state.records.length} 条记录`);
}

async function copyText() {
  const text = buildText();
  if (!text) {
    toast("没有记录可复制");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制到剪贴板");
  } catch (e) {
    // file:// 下 clipboard API 可能不可用,退回 execCommand
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    ta.remove();
    toast(ok ? "已复制到剪贴板" : "复制失败，请使用导出 TXT");
  }
}

/* ---------- 预览 / 导入 ---------- */
function updatePreview() {
  const pv = $("preview");
  if (pv) pv.textContent = buildText() || "（暂无记录）";
}

function findSegment(lineName, startName, endName) {
  for (const r of DATA.regions) {
    for (const op of r.operators) {
      for (const ln of op.lines) {
        if (ln.name !== lineName && ln.zh !== lineName) continue;
        const si = ln.stops.findIndex((s) => s[0] === startName || s[1] === startName);
        const ei = ln.stops.findIndex((s) => s[0] === endName || s[1] === endName);
        if (si >= 0 && ei >= 0 && si !== ei) {
          return {
            region: r.id, operator: op.name, line: ln.name, lineZh: ln.zh, colour: ln.colour,
            startJa: ln.stops[si][0], startZh: ln.stops[si][1],
            endJa: ln.stops[ei][0], endZh: ln.stops[ei][1],
          };
        }
      }
    }
  }
  return null;
}

/* 与 lvji parse.py 同规则:『線名 [起点] → 终点』或『線名 终点』 */
function parseArrowLine(text) {
  const m = text.match(/^(.*?)(?:\s+(.*?))?\s*(?:→|->)\s*(.+)$/);
  if (m && m[1].trim()) {
    return { line: m[1].trim(), start: (m[2] || "").trim() || null, end: m[3].trim() };
  }
  const parts = text.split(/\s+/);
  if (parts.length === 2) return { line: parts[0], start: null, end: parts[1] };
  return null;
}

const KEYWORD_KIND = {
  walk: "walk", "步行": "walk", "徒步": "walk",
  bus: "bus", "公交": "bus", "巴士": "bus",
  taxi: "taxi", "打车": "taxi", "出租车": "taxi",
};

function resolveStationIn(name) {
  for (const r of DATA.regions) {
    for (const op of r.operators) {
      for (const ln of op.lines) {
        const si = ln.stops.findIndex((s) => s[0] === name || s[1] === name);
        if (si >= 0) return { region: r.id, operator: op.name, ln, si };
      }
    }
  }
  return null;
}

function makeKeywordRecord(keyword, rest) {
  const kind = KEYWORD_KIND[keyword];
  let startName = null;
  let endName = null;
  if (rest) {
    // 关键字段 rest 没有线路名:箭头左边即起点(与 lvji parse.py 关键字分支同规则)
    const am = rest.match(/^(.*?)\s*(?:→|->|——)\s*(.+)$/);
    if (am) {
      startName = am[1].trim() || null;
      endName = am[2].trim() || null;
    } else {
      endName = rest.trim() || null;
    }
  }
  const rec = {
    kind, region: "", operator: "", line: "", lineZh: "", colour: null,
    startJa: startName, startZh: null, endJa: endName, endZh: null,
  };
  // 尝试把站名对齐到真实线路(便于回填);对不上就保留原文,渲染端自己模糊匹配
  const tryFill = (side, name) => {
    const hit = resolveStationIn(name);
    if (hit) {
      rec.region = hit.region;
      rec.operator = hit.operator;
      rec.line = hit.ln.name;
      rec.lineZh = hit.ln.zh || "";
      rec[side + "Ja"] = hit.ln.stops[hit.si][0];
      rec[side + "Zh"] = hit.ln.stops[hit.si][1];
    }
  };
  if (startName) tryFill("start", startName);
  if (endName) tryFill("end", endName);
  return rec;
}

function importText(text) {
  const rows = String(text || "").split(/\r?\n/);
  const found = [];
  const failed = [];
  let pendingMode = null;   // # 白天/# 夜间 作用于其后导入的记录
  const add = (rec) => {
    if (pendingMode) rec.mode = pendingMode;
    found.push(rec);
  };
  rows.forEach((raw) => {
    const line = raw.trim();
    if (!line) return;
    if (line.startsWith("#")) {
      const c = line.replace(/^#+\s*/, "").trim();
      if (c === "白天" || c === "夜间") pendingMode = c === "夜间" ? "night" : "day";
      return;   // 标题行忽略
    }
    // 标签段:步行/公交/打车(walk/bus/taxi)开头
    const kw = line.match(/^(walk|步行|徒步|bus|公交|巴士|taxi|打车|出租车)(?:\s+(.*))?$/);
    if (kw) {
      add(makeKeywordRecord(kw[1], (kw[2] || "").trim()));
      return;
    }
    // 箭头格式:线路名 [起点] → 终点
    if (line.includes("→") || line.includes("->")) {
      const spec = parseArrowLine(line);
      const hit = spec && findSegment(spec.line, spec.start || "", spec.end);
      if (hit) add(hit);
      else failed.push(line);
      return;
    }
    // 旧逗号格式兜底:序号,线路,起点,终点(或 线路,起点,终点)
    const parts = line.split(/[，,]/).map((s) => s.trim()).filter(Boolean);
    if (parts.length >= 3) {
      const [lnName, stName, enName] = parts.length >= 4 ? parts.slice(1, 4) : parts.slice(0, 3);
      const hit = findSegment(lnName, stName, enName);
      if (hit) add(hit);
      else failed.push(`${lnName},${stName},${enName}`);
    } else {
      failed.push(line);
    }
  });
  if (!found.length) {
    toast(failed.length ? `没有匹配的记录（${failed.length} 条无法识别）` : "没有可识别的内容");
    return;
  }
  snapshot();
  state.records = state.records.concat(found);
  render();
  toast(`导入 ${found.length} 条` + (failed.length ? `，${failed.length} 条未匹配` : ""));
}

/* ---------- 标签段站名补全 ---------- */
let stationIndex = [];           // 去重后的全部站点 [{ja, zh, region, operator, line}]
const stationExact = new Map();  // 中/日文名 → {ja, zh},供输入框精确对齐

function buildStationIndex() {
  const seen = new Map();
  DATA.regions.forEach((r) => r.operators.forEach((op) => op.lines.forEach((ln) => {
    ln.stops.forEach((s) => {
      const ja = s[0];
      const zh = s[1] || "";
      const prev = seen.get(ja);
      if (!prev) seen.set(ja, { ja, zh, region: r.name, operator: op.name, line: ln.zh || ln.name });
      else if (zh && !prev.zh) prev.zh = zh;
    });
  })));
  stationIndex = Array.from(seen.values());
  stationExact.clear();
  stationIndex.forEach((st) => {
    if (!stationExact.has(st.ja)) stationExact.set(st.ja, { ja: st.ja, zh: st.zh || st.ja });
    if (st.zh && !stationExact.has(st.zh)) stationExact.set(st.zh, { ja: st.ja, zh: st.zh });
  });
}

/* 前缀命中优先,其次包含,最多 10 条 */
function stationSuggestions(q) {
  if (!q) return [];
  const ql = q.toLowerCase();
  const hits = [];
  for (const st of stationIndex) {
    let score = -1;
    if (st.zh === q || st.ja === q) score = 0;
    else if (st.zh.startsWith(q) || st.ja.startsWith(q)) score = 1;
    else if ((st.zh || st.ja).toLowerCase().includes(ql)
             || st.ja.toLowerCase().includes(ql)) score = 2;
    if (score < 0) continue;
    hits.push([score, (st.zh || st.ja).length, st]);
  }
  hits.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return hits.slice(0, 10).map((x) => x[2]);
}

let sugInput = null;   // 当前补全针对的输入框
let sugList = [];
let sugActive = -1;

function hideSug() {
  const box = $("sug");
  if (box) box.classList.remove("open");
  sugInput = null;
  sugList = [];
  sugActive = -1;
}

function renderSug() {
  const box = $("sug");
  if (!box) return;
  box.textContent = "";
  sugList.forEach((st, i) => {
    const item = document.createElement("div");
    item.className = "sug-item" + (i === sugActive ? " active" : "");
    item.textContent = st.zh || st.ja;                 // 中文优先
    if (st.ja !== (st.zh || st.ja)) {
      const ja = document.createElement("span");
      ja.className = "sug-ja";
      ja.textContent = st.ja;                          // 日文注音
      item.appendChild(ja);
    }
    const meta = document.createElement("span");
    meta.className = "sug-meta";
    meta.textContent = `${st.operator} ${st.line} · ${st.region}`;
    item.appendChild(meta);
    item.addEventListener("mousedown", (ev) => {   // mousedown 抢在 blur 之前
      ev.preventDefault();
      pickSug(i);
    });
    box.appendChild(item);
  });
  box.classList.toggle("open", sugList.length > 0);
}

function showSug(input) {
  sugInput = input;
  sugList = stationSuggestions((input.value || "").trim());
  sugActive = sugList.length ? 0 : -1;
  renderSug();
}

function pickSug(i) {
  const st = sugList[i];
  if (st && sugInput) {
    sugInput.value = st.zh || st.ja;
    updateKwHint();
  }
  hideSug();
}

/* 输入框下方的衔接提示:让「留空自动衔接」看得见 */
function updateKwHint() {
  const hint = $("kwHint");
  if (!hint) return;
  const s = ($("kwStart").value || "").trim();
  const e = ($("kwEnd").value || "").trim();
  const parts = [];
  if (!s) {
    const last = state.records.length ? state.records[state.records.length - 1] : null;
    const lastEnd = last ? (last.endZh || last.endJa) : "";
    parts.push(lastEnd
      ? `起点留空＝衔接上一段终点「${lastEnd}」`
      : "起点留空＝衔接上一段终点（当前暂无上一段）");
  }
  if (!e) {
    parts.push(s
      ? "终点未填＝请填终点，或把起终点都清空生成接驳段"
      : "起终点都留空＝接驳段，自动连上上一段终点与下一段起点");
  }
  hint.textContent = parts.join("；");
  hint.style.display = parts.length ? "" : "none";
}

/* ---------- 持久化 ---------- */
function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({
      records: state.records,
      exportZh: state.exportZh,
      dark: state.dark,
      sel: {
        region: els.region.value,
        line: els.line.value,
        start: els.start.value,
        end: els.end.value,
      },
    }));
  } catch (e) { /* 隐私模式等场景忽略 */ }
}

function restore() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { saved = null; }
  if (!saved) {
    // 无存档:跟随系统深色偏好
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      state.dark = true;
    }
    return;
  }
  state.records = Array.isArray(saved.records) ? saved.records : [];
  state.exportZh = !!saved.exportZh;
  state.dark = typeof saved.dark === "boolean" ? saved.dark : false;
  $("exportZh").checked = state.exportZh;
  const sel = saved.sel || {};
  if (sel.region && els.region.querySelector(`option[value="${sel.region}"]`)) {
    els.region.value = sel.region;
    populateLines();
  }
  if (sel.line && els.line.querySelector(`option[value="${sel.line}"]`)) {
    els.line.value = sel.line;
    populateStops();
    if (sel.start != null && els.start.length) {
      els.start.selectedIndex = Math.min(+sel.start, els.start.length - 1);
    }
    if (sel.end != null && els.end.length) {
      els.end.selectedIndex = Math.min(+sel.end, els.end.length - 1);
    }
  }
}

/* ---------- 初始化 ---------- */
function init() {
  populateRegions();
  populateLines();
  populateStops();
  buildStationIndex();
  restore();
  $("exportZh").checked = state.exportZh;
  applyTheme();
  render();

  // 顶栏副标题:随数据自动统计
  const nLines = DATA.regions.reduce((a, r) => a + r.operators.reduce((b, o) => b + o.lines.length, 0), 0);
  const nStops = DATA.regions.reduce((a, r) => a + r.operators.reduce(
    (b, o) => b + o.lines.reduce((c, l) => c + l.stops.length, 0), 0), 0);
  const sub = $("subtitle");
  if (sub) sub.textContent = `日本全国 ${DATA.regions.length} 地区 · ${nLines} 条线路 · ${nStops} 站`;

  els.region.addEventListener("change", () => {
    populateLines();
    populateStops();
    save();
  });
  els.line.addEventListener("change", () => {
    populateStops();
    save();
  });
  if (els.search) {
    els.search.addEventListener("input", applyLineFilter);
  }
  $("btnSwap").addEventListener("click", () => {
    const a = els.start.selectedIndex;
    els.start.selectedIndex = els.end.selectedIndex;
    els.end.selectedIndex = a;
    save();
  });
  $("btnAdd").addEventListener("click", addRecord);
  $("btnWalk").addEventListener("click", () => addKeyword("walk"));
  $("btnBus").addEventListener("click", () => addKeyword("bus"));
  $("btnTaxi").addEventListener("click", () => addKeyword("taxi"));
  // 标签段输入框:站名联想 + 衔接提示
  [$("kwStart"), $("kwEnd")].forEach((inp) => {
    inp.addEventListener("input", () => { updateKwHint(); showSug(inp); });
    inp.addEventListener("focus", () => showSug(inp));
    inp.addEventListener("blur", () => setTimeout(hideSug, 120));
    inp.addEventListener("keydown", (ev) => {
      if (!$("sug") || !$("sug").classList.contains("open")) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        if (sugList.length) {
          sugActive = (sugActive + (ev.key === "ArrowDown" ? 1 : sugList.length - 1)) % sugList.length;
          renderSug();
        }
      } else if (ev.key === "Enter") {
        if (sugActive >= 0) {
          ev.stopPropagation();
          ev.preventDefault();
          pickSug(sugActive);
        }
      } else if (ev.key === "Escape") {
        hideSug();
      }
    });
  });
  document.addEventListener("click", (ev) => {
    const box = $("sug");
    if (box && box.classList.contains("open") && ev.target
        && ev.target !== $("kwStart") && ev.target !== $("kwEnd")
        && !(box.contains && box.contains(ev.target))) {
      hideSug();
    }
  });
  $("btnExport").addEventListener("click", () => exportTxt());
  $("btnExportTravel").addEventListener("click", () => exportTxt(true));
  $("btnCopy").addEventListener("click", copyText);
  $("btnClear").addEventListener("click", clearAll);
  $("btnUndo").addEventListener("click", undo);
  $("btnImport").addEventListener("click", () => {
    const d = $("dlgImport");
    if (d && typeof d.showModal === "function") d.showModal();
    else toast("当前浏览器不支持弹窗导入");
  });
  $("btnImportCancel").addEventListener("click", () => {
    const d = $("dlgImport");
    if (d && d.close) d.close();
  });
  $("btnImportOk").addEventListener("click", () => {
    const d = $("dlgImport");
    const ta = $("importText");
    if (ta) {
      importText(ta.value);
      ta.value = "";
    }
    if (d && d.close) d.close();
  });
  $("exportZh").addEventListener("change", (e) => {
    state.exportZh = e.target.checked;
    updatePreview();
    save();
  });
  $("btnTheme").addEventListener("click", () => {
    state.dark = !state.dark;
    applyTheme();
    updatePreview();
    save();
  });
  // 快捷键:Enter 添加 / ⌘(Ctrl)+Z 撤销
  document.addEventListener("keydown", (e) => {
    const tag = e.target && e.target.tagName;
    if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === "z") {
      if (tag === "INPUT" || tag === "TEXTAREA") return;   // 不抢占输入框自己的撤销
      e.preventDefault();
      undo();
      return;
    }
    if (e.key === "Enter" && [els.region, els.line, els.start, els.end].includes(e.target)) {
      addRecord();
    }
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
