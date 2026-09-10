// UI 冒烟测试:最小 DOM stub 跑 app.js 的核心流程(Node 环境)
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");

// ---------- 最小 DOM ----------
class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this._text = "";
    this.value = "";
    this.selected = false;
    this.disabled = false;
    this.title = "";
    this.className = "";
    this.style = { setProperty(k, v) { this[k] = v; } };
    this._cls = new Set();
    this.classList = {
      add: (...cs) => cs.forEach((c) => this._cls.add(c)),
      remove: (...cs) => cs.forEach((c) => this._cls.delete(c)),
      toggle: (c, force) => {
        const on = force !== undefined ? force : !this._cls.has(c);
        if (on) this._cls.add(c); else this._cls.delete(c);
        return on;
      },
      contains: (c) => this._cls.has(c),
    };
    this._handlers = {};
  }
  get textContent() {
    // 模拟 DOM:textContent 聚合子节点(文本节点/元素)
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(v) {
    this._text = String(v);
    if (v === "") this.children = [];   // 模拟 DOM:置空即清子节点
  }
  appendChild(c) { this.children.push(c); return c; }
  remove() {}
  click() {}
  addEventListener(ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); }
  fire(ev) { (this._handlers[ev] || []).forEach((f) => f({ target: this, stopPropagation() {} })); }
}
class FakeSelect extends FakeNode {
  constructor(id) { super("select"); this.id = id; this.selectedIndex = -1; this._value = ""; }
  get options() {
    // optgroup 展平成 option 列表(真实 DOM 的 select.options 语义)
    const out = [];
    const walk = (n) => n.children.forEach((c) => (c.tag === "optgroup" ? walk(c) : out.push(c)));
    walk(this);
    return out;
  }
  get length() { return this.options.length; }
  get value() {
    if (this._value && this.options.some((o) => String(o.value) === this._value)) return this._value;
    const opts = this.options;
    // 模拟浏览器:单选 select 有 option 时默认选中第一项
    if (this.selectedIndex < 0 && opts.length) return String(opts[0].value);
    const o = opts[this.selectedIndex];
    return o ? String(o.value) : "";
  }
  set value(v) { this._value = String(v); }
  querySelector() { return this.options[0] || null; }
}

const elements = {};
["selRegion", "selLine", "selStart", "selEnd"].forEach((id) => { elements[id] = new FakeSelect(id); });
["recordBody", "lineMeta", "toast"].forEach((id) => { elements[id] = new FakeNode("div"); elements[id].id = id; });
["btnSwap", "btnAdd", "btnExport", "btnCopy", "btnClear", "btnTheme"].forEach((id) => { elements[id] = new FakeNode("button"); elements[id].id = id; });
elements.recordCount = new FakeNode("span");
elements.emptyTip = new FakeNode("div");
elements.exportZh = new FakeNode("input");
elements.exportZh.checked = false;
// 本轮新增的元素
elements.subtitle = new FakeNode("div");
elements.lineSearch = new FakeNode("input");
elements.searchCount = new FakeNode("span");
elements.preview = new FakeNode("pre");
elements.dlgImport = new FakeNode("dialog");
elements.dlgImport.showModal = () => {};
elements.dlgImport.close = () => {};
elements.importText = new FakeNode("textarea");
["btnUndo", "btnImport", "btnImportCancel", "btnImportOk"].forEach((id) => { elements[id] = new FakeNode("button"); elements[id].id = id; });
["btnWalk", "btnBus", "btnTaxi"].forEach((id) => { elements[id] = new FakeNode("button"); elements[id].id = id; });
elements.btnExportTravel = new FakeNode("button");
elements.kwStart = new FakeNode("input");
elements.kwEnd = new FakeNode("input");
elements.kwHint = new FakeNode("div");
elements.sug = new FakeNode("div");

const documentStub = {
  getElementById: (id) => elements[id] || null,
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (t) => ({ _text: String(t), get textContent() { return this._text; } }),
  body: new FakeNode("body"),
  readyState: "loading",   // 让 app.js 走 addEventListener 分支,由测试显式调 init()
  addEventListener() {},
};
const localStorageStub = {
  _s: {},
  setItem(k, v) { this._s[k] = v; },
  getItem(k) { return k in this._s ? this._s[k] : null; },
};

// ---------- 加载 data.js + app.js ----------
const sandbox = {
  document: documentStub,
  localStorage: localStorageStub,
  confirm: () => true,
  console,
  URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} },
  Blob: class { constructor(parts) { this.text = parts.join(""); } },
  navigator: { clipboard: { writeText: async () => {} } },
  setTimeout, clearTimeout,
};
sandbox.window = sandbox;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(path.join(ROOT, "data.js"), "utf-8"), sandbox);
// data.js 用 var 声明,在 vm 顶层会自动挂到 sandbox 全局(等同浏览器的 window),
// app.js 经 window.TRANSIT_DATA 取值 —— 与真实浏览器一致,不再手动桥接
vm.runInContext(fs.readFileSync(path.join(ROOT, "app.js"), "utf-8"), sandbox);
const S = (expr) => vm.runInContext(expr, sandbox);

// ---------- 测试 ----------
let pass = 0, fail = 0;
const t = (name, cond) => { if (cond) pass++; else { fail++; console.error("  ✗ " + name); } };

console.log("[1] 数据加载");
const DATA = sandbox.TRANSIT_DATA;
t("TRANSIT_DATA 存在", !!DATA);
t("八个地区", DATA.regions.length === 8);
const nLines = DATA.regions.reduce((a, r) => a + r.operators.reduce((b, o) => b + o.lines.length, 0), 0);
const nStops = DATA.regions.reduce((a, r) => a + r.operators.reduce(
  (b, o) => b + o.lines.reduce((c, l) => c + l.stops.length, 0), 0), 0);
console.log(`    ${nLines} 条线路 / ${nStops} 个站记录 / ${DATA.regions.map((r) => r.name).join(" + ")}`);
t("线路数 > 500", nLines > 500);

console.log("[2] init + 下拉联动");
S("init()");
t("地区下拉有 8 项", elements.selRegion.options.length === 8);
t("线路下拉非空", elements.selLine.options.length > 50);
t("起点下拉非空", elements.selStart.options.length >= 2);
t("终点下拉 = 起点下拉", elements.selEnd.options.length === elements.selStart.options.length);
console.log(`    默认线路: ${elements.selLine.options[0].textContent} / ${elements.selStart.options.length} 站`);

console.log("[3] 选择 JR山手線 并添加记录");
elements.selRegion.value = "kanto";
S("populateLines()");
const yamanote = elements.selLine.options.find((o) => o.textContent.startsWith("JR山手线"));
t("能找到 JR山手線", !!yamanote);
elements.selLine.value = yamanote.value;
S("populateStops()");
const yStops = elements.selStart.options.map((o) => o.textContent);
console.log(`    山手線 ${yStops.length} 站: ${yStops[0]} -> ${yStops[yStops.length - 1]}`);
t("山手線 30 站", yStops.length === 30);
const shibuyaIdx = yStops.findIndex((s) => s.startsWith("涩谷"));
elements.selStart.selectedIndex = 0;
elements.selEnd.selectedIndex = shibuyaIdx;
S("addRecord()");
t("添加后 1 条记录", S("state.records.length") === 1);
t("记录线路名 = JR山手線", S("state.records[0].line") === "JR山手線");
t("记录起点 = 品川", S("state.records[0].startJa") === "品川");
t("记录终点 = 渋谷", S("state.records[0].endJa") === "渋谷");
t("起终点不同才允许添加", (() => {
  elements.selStart.selectedIndex = 0;
  elements.selEnd.selectedIndex = 0;
  const before = S("state.records.length");
  S("addRecord()");
  return S("state.records.length") === before;
})());

console.log("[4] 任意位置插入");
elements.selRegion.value = "kansai";
S("populateLines()");
const mido = elements.selLine.options.find((o) => o.textContent.startsWith("御堂筋线"));
t("能找到御堂筋線", !!mido);
elements.selLine.value = mido.value;
S("populateStops()");
elements.selStart.selectedIndex = 0;
elements.selEnd.selectedIndex = elements.selEnd.options.length - 1;
S("insertBelow(0)");  // 插到山手線记录下方
t("插入后 2 条记录", S("state.records.length") === 2);
t("第 2 条是御堂筋線", S("state.records[1].line") === "大阪御堂筋線");
t("御堂筋線起点 = 箕面萱野", S("state.records[1].startJa") === "箕面萱野");
t("御堂筋線终点 = 中百舌鳥", S("state.records[1].endJa") === "中百舌鳥");

console.log("[5] 上移/删除/交换");
S("move(1, -1)");
t("上移后御堂筋線在第 1 行", S("state.records[0].line") === "大阪御堂筋線");
S("state.records.splice(0, 1)");
t("删除后剩 1 条", S("state.records.length") === 1);
const beforeStart = elements.selStart.selectedIndex;
const beforeEnd = elements.selEnd.selectedIndex;
elements.btnSwap.fire("click");
t("交换起终点", elements.selStart.selectedIndex === beforeEnd && elements.selEnd.selectedIndex === beforeStart);

console.log("[6] 导出文本格式(默认中文)");
const text = S("buildText()");
console.log("    " + JSON.stringify(text));
t("首行 = # 白天 标签", text.startsWith("# 白天\n"));
t("格式 = 线路 起点 → 终点(lvji 箭头格式)", text.split("\n")[1] === "JR山手线 品川 → 涩谷");

console.log("[7] 切换日文导出");
S("state.exportZh = false");
const textJa = S("buildText()");
console.log("    " + JSON.stringify(textJa));
t("日文线路/站名", textJa.split("\n")[1] === "JR山手線 品川 → 渋谷");

console.log("[8] localStorage 持久化 + 恢复");
// 设到已知状态:关西 / 御堂筋線 / 箕面萱野 -> なかもず
elements.selRegion.value = "kansai";
S("populateLines()");
elements.selLine.value = elements.selLine.options.find((o) => o.textContent.startsWith("御堂筋线")).value;
S("populateStops()");
S("save()");
const saved = JSON.parse(localStorageStub.getItem("transit-tool-records-v1"));
t("记录已保存", saved.records.length === 1);
t("选择已保存", !!saved.sel.region && saved.sel.region === "kansai");
// 模拟刷新:打乱后 restore
S("state.records = []");
elements.selStart.selectedIndex = -1;
elements.selEnd.selectedIndex = -1;
S("restore()");
t("restore 恢复记录", S("state.records.length") === 1);
t("restore 恢复起点(箕面萱野)", elements.selStart.options[elements.selStart.selectedIndex].textContent.startsWith("箕面萱野"));
t("restore 恢复终点(中百舌鳥)", elements.selEnd.options[elements.selEnd.selectedIndex].textContent.startsWith("中百舌鳥"));

console.log("[9] 回填(backfill)");
S("backfill(0)");
t("回填后选中站 = 品川", elements.selStart.options[elements.selStart.selectedIndex].textContent.startsWith("品川"));

console.log("[10] 清空");
S("clearAll()");
t("清空后 0 条", S("state.records.length") === 0);

console.log("[11] 京阪本線・鴨東線(回归:出町柳曾被 OSM 欠标 relation 丢失)");
elements.selRegion.value = "kansai";
S("populateLines()");
const keihan = elements.selLine.options.find((o) => o.textContent.startsWith("京阪本线"));
t("能找到京阪本線", !!keihan);
elements.selLine.value = keihan.value;
S("populateStops()");
const kStops = elements.selStart.options.map((o) => o.textContent);
console.log(`    京阪 ${kStops.length} 站: ${kStops[0]} -> ${kStops[kStops.length - 1]}`);
t("京阪 42 站(KH01-KH42)", kStops.length === 42);
t("京阪起点 = 淀屋桥", kStops[0].startsWith("淀屋桥"));
t("京阪终点 = 出町柳", kStops[kStops.length - 1].startsWith("出町柳"));
t("京阪含出町柳", kStops.some((s) => s.startsWith("出町柳")));
t("京阪含伏見桃山(京阪 KH29)", kStops.some((s) => s.includes("伏見桃山")));
t("伏見桃山位于中書島与丹波橋之间", (() => {
  const iC = kStops.findIndex((s) => s.includes("中書島"));
  const iF = kStops.findIndex((s) => s.includes("伏見桃山"));
  const iT = kStops.findIndex((s) => s.includes("丹波橋"));
  return iC >= 0 && iF >= 0 && iT >= 0 && iC < iF && iF < iT;
})());
t("京阪无 JR 噪声大阪城北詰", !kStops.some((s) => s.includes("大阪城北詰")));

console.log("[12] 白天/夜间主题");
S("applyTheme()");
t("默认白天", S("state.dark") === false);
t("按钮文案 = 🌙 夜间", elements.btnTheme.textContent === "🌙 夜间");
S("state.dark = true; applyTheme(); save()");
t("保存了 dark=true", JSON.parse(localStorageStub.getItem("transit-tool-records-v1")).dark === true);
S("state.records.push({line:'x', lineZh:'x', startJa:'a', startZh:'a', endJa:'b', endZh:'b', mode:'night'})");
t("夜间段导出首行 = # 夜间", S("buildText()").startsWith("# 夜间\n"));
S("state.records.pop()");
S("state.dark = false; restore()");
t("restore 恢复夜间", S("state.dark") === true);
S("applyTheme()");
t("夜间按钮文案 = ☀️ 白天", elements.btnTheme.textContent === "☀️ 白天");
S("state.dark = false; applyTheme(); save()");  // 还原默认,避免影响后续用例

console.log("[13] JR鶴見線(回归:代表选举只留本線,海芝浦/大川支线整支丢失)");
elements.selRegion.value = "kanto";
S("populateLines()");
const tsurumi = elements.selLine.options.find((o) => o.textContent.startsWith("JR鹤见线"));
t("能找到 JR鶴見線", !!tsurumi);
elements.selLine.value = tsurumi.value;
S("populateStops()");
const tStops = elements.selStart.options.map((o) => o.textContent);
console.log(`    鶴見線 ${tStops.length} 站: ${tStops[0]} -> ${tStops[tStops.length - 1]}`);
t("鶴見線 13 站(本線8+三支线)", tStops.length === 13);
t("起点 = 鶴見", tStops[0].startsWith("鶴見"));
t("含海芝浦支线 新芝浦/海芝浦", tStops.some((s) => s.includes("新芝浦")) && tStops.some((s) => s.includes("海芝浦")));
t("含大川支线 大川", tStops.some((s) => s.includes("大川")));
t("含弁天橋支线 弁天橋", tStops.some((s) => s.includes("弁天橋")));
t("本线末端 = 扇町", tStops[tStops.length - 1].startsWith("扇町"));

console.log("[14] 九州新幹線(全国 8 地区抽查)");
elements.selRegion.value = "kyushu";
S("populateLines()");
const kyushu = elements.selLine.options.find((o) => o.textContent.startsWith("九州新干线"));
t("能找到 九州新幹線", !!kyushu);
elements.selLine.value = kyushu.value;
S("populateStops()");
const jStops = elements.selStart.options.map((o) => o.textContent);
console.log(`    九州新幹線 ${jStops.length} 站: ${jStops[0]} -> ${jStops[jStops.length - 1]}`);
t("九州新幹線 12 站(博多-鹿児島中央)", jStops.length === 12);
t("起点 = 博多", jStops[0].startsWith("博多"));
t("终点 = 鹿児島中央", jStops[jStops.length - 1].includes("鹿児島中央"));

console.log("[15] 线路搜索筛选");
elements.selRegion.value = "kanto";
S("populateLines()");
const totalOpts = elements.selLine.options.length;
elements.lineSearch.value = "山手";
S("applyLineFilter()");
const visible = elements.selLine.options.filter((o) => o.style.display !== "none");
t("筛选后数量变少", visible.length >= 1 && visible.length < totalOpts);
t("结果都含 山手", visible.length >= 1 && visible.every((o) => o.textContent.includes("山手")));
t("显示匹配计数", elements.searchCount.textContent.includes("条匹配"));
t("optgroup 跟随隐藏/显示", elements.selLine.children.some((c) => c.style.display !== "none"));
elements.lineSearch.value = "";
S("applyLineFilter()");
t("清空后恢复全部", elements.selLine.options.every((o) => o.style.display !== "none"));

console.log("[16] 重复提醒 + 撤销");
S("populateStops()");
elements.selStart.selectedIndex = 0;
elements.selEnd.selectedIndex = elements.selEnd.options.length - 1;
S("addRecord()");
t("添加 1 条", S("state.records.length") === 1);
S("addRecord()");
t("重复添加到 2 条", S("state.records.length") === 2);
t("重复区段有提醒", elements.toast.textContent.includes("相同区段"));
S("undo()");
t("撤销后剩 1 条", S("state.records.length") === 1);
S("undo()");
t("再撤销后 0 条", S("state.records.length") === 0);

console.log("[17] 导入行程文本");
S('importText("# 白天\\n1,JR山手线,品川,涩谷\\n2,九州新干线,博多,鹿児島中央\\n3,不存在线,甲,乙")');
t("导入 2 条(1 条未匹配)", S("state.records.length") === 2);
t("第 1 条 = JR山手線(ja 存档)", S("state.records[0].line") === "JR山手線");
t("中文站名能匹配(涩谷→渋谷)", S("state.records[0].endJa") === "渋谷");
t("第 2 条 = 九州新幹線", S("state.records[1].line") === "九州新幹線");
t("第 2 条起点 = 博多", S("state.records[1].startJa") === "博多");
t("统计含不衔接", elements.recordCount.textContent.includes("不衔接"));
t("预览已更新", elements.preview.textContent.startsWith("#"));
S("clearAll()");
t("清空后 0 条", S("state.records.length") === 0);

console.log("[18] 分组计数 / 副标题 / 线路信息条");
elements.selRegion.value = "kansai";
S("populateLines()");
const og0 = elements.selLine.children[0];
t("optgroup label 带线路数", typeof og0.label === "string" && og0.label.includes("（"));
t("副标题含线路数统计", /\d+ 条线路/.test(elements.subtitle.textContent));
const mido18 = elements.selLine.options.find((o) => o.textContent.startsWith("御堂筋线"));
t("能找到御堂筋線", !!mido18);
elements.selLine.value = mido18.value;
S("populateStops()");
t("信息条显示公司名", elements.lineMeta.textContent.includes("大阪メトロ"));

console.log("[19] 标签段(步行/公交/打车)");
elements.kwStart.value = "梅田";
elements.kwEnd.value = "新大阪";
S('addKeyword("walk")');
t("步行段已添加", S("state.records.length") === 1);
t("kind = walk", S("state.records[0].kind") === "walk");
t("导出步行段", S("buildText()").includes("步行 梅田 → 新大阪"));
elements.kwStart.value = "";
elements.kwEnd.value = "四条河原町";
S('addKeyword("bus")');
t("只填终点的公交段(接上段)", S("state.records.length") === 2);
t("公交段导出", S("buildText()").includes("公交 四条河原町"));
elements.kwStart.value = "甲";
elements.kwEnd.value = "";
S('addKeyword("taxi")');
t("只填起点被拒绝", S("state.records.length") === 2);
elements.kwStart.value = "";
elements.kwEnd.value = "";
S('addKeyword("taxi")');
t("裸打车接驳段已添加(三种标签通用)", S("state.records.length") === 3);
t("裸打车导出 = 打车", S("buildText()").endsWith("\n打车"));
elements.kwEnd.value = "大阪駅";
S('addKeyword("taxi")');
t("打车段已添加", S("state.records.length") === 4);
S("clearAll()");
elements.kwStart.value = "";
elements.kwEnd.value = "";
S('addKeyword("walk")');
t("裸步行(自动衔接)已添加", S("state.records.length") === 1);
t("裸步行导出 = 步行", S("buildText()").endsWith("\n步行"));
S("clearAll()");

console.log("[20] 导入:箭头格式 + 标签段 + 旧逗号格式");
S('importText("JR山手线 品川 → 涩谷\\n步行 梅田\\n打车 東京 → 横浜\\n步行\\n1,JR山手線,新宿,渋谷\\n3,不存在的线,甲,乙")');
t("导入 5 条(1 条未匹配)", S("state.records.length") === 5);
t("箭头行 → 记录(JR山手線)", S("state.records[0].line") === "JR山手線");
t("步行 梅田 → 终点-only 标签段", S("state.records[1].kind") === "walk" && S("state.records[1].endJa") === "梅田");
t("打车段 kind = taxi", S("state.records[2].kind") === "taxi");
t("打车起点 = 東京", S("state.records[2].startJa") === "東京");
t("裸步行导入", S("state.records[3].kind") === "walk" && !S("state.records[3].startJa"));
t("旧逗号格式兜底(JR山手線 新宿→渋谷)", S("state.records[4].startJa") === "新宿" && S("state.records[4].endJa") === "渋谷");
t("导出含标签行", S("buildText()").includes("步行 梅田"));
S("clearAll()");

console.log("[21] 站名联想补全 + 衔接提示");
t("站名索引已构建", S("stationIndex.length") > 5000);
const sug = S('stationSuggestions("梅")');
t("输入 梅 有联想(≤10 条)", Array.isArray(sug) && sug.length > 0 && sug.length <= 10);
t("联想项带线路上下文", !!sug[0].ja && !!sug[0].operator && !!sug[0].line);
elements.kwStart.value = "梅";
S('showSug($("kwStart"))');
t("下拉打开", elements.sug.classList.contains("open"));
t("下拉渲染条目", elements.sug.children.length === sug.length);
S("pickSug(0)");
t("选中后回填输入框(中文优先)", elements.kwStart.value.startsWith("梅"));
t("选中后下拉收起", !elements.sug.classList.contains("open"));
elements.kwStart.value = "东京";
elements.kwEnd.value = "品川";
S('addKeyword("taxi")');
t("输入中文名对齐规范站名(東京/东京)", S("state.records[0].startJa") === "東京" && S("state.records[0].startZh") === "东京");
S("state.exportZh = true");   // [7] 之后一直处于日文导出,还原后再断言中文导出
t("导出用中文名", S("buildText()").includes("打车 东京 → 品川"));
elements.kwStart.value = "";
elements.kwEnd.value = "";
S("updateKwHint()");
t("留空时提示衔接上一段终点", elements.kwHint.textContent.includes("上一段终点"));
elements.kwStart.value = "梅田";
elements.kwEnd.value = "新大阪";
S("updateKwHint()");
t("起终点都填则提示隐藏", elements.kwHint.style.display === "none");
S("clearAll()");

console.log("[22] 每条记录白天/夜间切换");
S('state.records.push({line:"JR山手線", lineZh:"JR山手线", startJa:"品川", startZh:"品川", endJa:"渋谷", endZh:"涩谷"})');
S('state.records.push({line:"大阪御堂筋線", lineZh:"御堂筋线", startJa:"梅田", startZh:"梅田", endJa:"なんば", endZh:"なんば", mode:"night"})');
const txt22 = S("buildText()");
console.log("    " + JSON.stringify(txt22));
t("首段白天标签", txt22.split("\n")[0] === "# 白天");
t("白天段导出", txt22.split("\n")[1] === "JR山手线 品川 → 涩谷");
t("模式变化处插入 # 夜间", txt22.split("\n")[2] === "# 夜间");
t("夜间段导出", txt22.split("\n")[3] === "御堂筋线 梅田 → なんば");
S("flipRecordMode(0)");
t("切换第 1 条为夜间", S("state.records[0].mode") === "night");
t("切换后首行 = # 夜间", S("buildText()").startsWith("# 夜间\n"));
t("同为夜间只出现一个模式行", S("buildText()").split("\n").filter((l) => l === "# 夜间").length === 1);
S("flipRecordMode(0)");
t("切回白天", S("state.records[0].mode") === "day");
S("clearAll()");
S('importText("# 夜间\\nJR山手线 品川 → 涩谷\\n# 白天\\n御堂筋线 梅田 → なんば")');
t("导入 2 条(带模式)", S("state.records.length") === 2);
t("模式行作用于其后各段(夜间)", S("state.records[0].mode") === "night");
t("模式行中途切换(白天)", S("state.records[1].mode") === "day");
S("clearAll()");

console.log("[23] 当前视频渲染器导出");
S('state.records = [{line:"JR山手線",startJa:"品川",endJa:"渋谷"}, {kind:"walk",mode:"night"}, {line:"銀座線",startJa:"渋谷",endJa:"銀座",mode:"night"}]');
const videoText = S("buildTravelText()");
t("原名与白天标记", videoText.includes("未注明 | JR山手線 | 品川 -> 渋谷 | day"));
t("接驳明确补齐", videoText.includes("未注明 | walk | 渋谷 -> 渋谷 | night"));
t("不输出旧日夜注释", !videoText.includes("# 夜间"));
const cp = require("child_process");
if (process.env.TRAVEL_RECORD_TEST_PYTHON) {
  const parsed = cp.spawnSync(process.env.TRAVEL_RECORD_TEST_PYTHON,
    ["-c", "import sys; from travel_record.parser import parse_trip; t=parse_trip(sys.stdin.read()); assert len(t.legs)==3; assert [x.theme for x in t.legs]==['day','night','night']; assert t.legs[1].mode=='walk'"],
    {input:videoText, encoding:"utf8", cwd:path.join(ROOT,"..")});
  t("真实 Python 解析器兼容", parsed.status === 0);
  if (parsed.status !== 0) console.error(parsed.stderr);
}
S('state.records = [{kind:"walk"}]');
let missingRejected = false;
try { S("buildTravelText()"); } catch (_) { missingRejected = true; }
t("阻止无法确定端点的导出", missingRejected);

console.log(`\n结果: ${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
