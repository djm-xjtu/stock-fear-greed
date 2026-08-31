/* 个股情绪指数实验室 —— 前端逻辑 */
const LIVE = (window.LIVE_API || "").replace(/\/$/, "");
const STATIC = (window.STATIC_BASE || "./data").replace(/\/$/, "");
const $ = (s) => document.querySelector(s);
const fmt = (v, n = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(n));

const BANDS = [
  { max: 20, zh: "极度恐慌", c: "#ff4d5e" },
  { max: 40, zh: "恐慌", c: "#ff8a3d" },
  { max: 60, zh: "中性", c: "#d8c04a" },
  { max: 80, zh: "贪婪", c: "#3ddc84" },
  { max: 101, zh: "极度贪婪", c: "#12d6a0" },
];
const bandOf = (v) => BANDS.find((b) => v <= b.max) || BANDS[BANDS.length - 1];
const colorOf = (v) => (v === null || v === undefined ? "#6f7a95" : bandOf(v).c);

const AX = {
  axisLine: { lineStyle: { color: "rgba(140,160,210,.2)" } },
  axisLabel: { color: "#6f7a95", fontSize: 11 },
  splitLine: { lineStyle: { color: "rgba(140,160,210,.09)" } },
};
const TT = {
  backgroundColor: "rgba(12,17,30,.96)",
  borderColor: "rgba(140,160,210,.28)",
  textStyle: { color: "#e8ecf8", fontSize: 12 },
  extraCssText: "border-radius:11px;box-shadow:0 16px 40px rgba(0,0,0,.6);",
};

const state = { universe: [], cur: null, detail: {}, days: 380, charts: {}, live: false, from: null, to: null, bounds: ["",""], dip: null };

/* 价格格式化：KRW 无小数、带千分位（SKHY 用韩股主上市数据） */
const CUR = { USD: "$", KRW: "₩" };
function fmtPx(v, cur) {
  if (v === null || v === undefined) return "—";
  const sym = CUR[cur] || "";
  return sym + (cur === "KRW"
    ? Math.round(v).toLocaleString("en-US")
    : Number(v).toFixed(2));
}
const signed = (v, n = 1) => (v === null || v === undefined ? "—"
  : (v > 0 ? "+" : "") + Number(v).toFixed(n));
const pn = (v) => (v === null || v === undefined ? "" : v >= 0 ? "pos" : "neg");

function chart(id) {
  if (!state.charts[id]) state.charts[id] = echarts.init($("#" + id), null, { renderer: "canvas" });
  return state.charts[id];
}
window.addEventListener("resize", () => Object.values(state.charts).forEach((c) => c.resize()));

/* 数据获取：静态快照优先，配置了 LIVE_API 时先试实时接口（带超时），失败回落 */
const STATIC_MAP = {
  "/api/universe": "/universe.json",
  "/api/methodology": "/methodology.json",
  "/api/validation": "/validation.json",
  "/api/dip": "/dip.json",
};
function staticPath(path) {
  if (STATIC_MAP[path]) return STATIC + STATIC_MAP[path];
  const m = path.match(/^\/api\/stock\/(.+)$/);
  if (m) return STATIC + "/stock/" + m[1].toUpperCase() + ".json";
  return null;
}

async function getJSON(url, timeoutMs) {
  const ctl = new AbortController();
  const timer = timeoutMs ? setTimeout(() => ctl.abort(), timeoutMs) : null;
  try {
    const r = await fetch(url, { signal: ctl.signal, cache: "no-cache" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function api(path) {
  if (LIVE) {
    try {
      const d = await getJSON(LIVE + path, 2500);
      state.live = true;
      return d;
    } catch (e) {
      console.warn("实时接口不可用，回落静态快照：", path, String(e.message || e));
    }
  }
  const sp = staticPath(path);
  if (!sp) throw new Error("无对应静态数据：" + path);
  return getJSON(sp, 15000);
}

/* ---------------------------------------------------------------- 仪表盘 */
function renderGauge(d) {
  $("#gaugeSym").textContent = d.symbol + " · " + fmt(d.score) + " 分";
  $("#gaugeName").textContent = d.name + " · " + d.label;
  const c = colorOf(d.score);
  chart("gauge").setOption({
    animationDuration: 900,
    series: [{
      type: "gauge", startAngle: 205, endAngle: -25, min: 0, max: 100,
      radius: "108%", center: ["50%", "70%"], splitNumber: 5,
      axisLine: {
        lineStyle: {
          width: 17,
          color: [[0.2, "#ff4d5e"], [0.4, "#ff8a3d"], [0.6, "#d8c04a"], [0.8, "#3ddc84"], [1, "#12d6a0"]],
        },
      },
      pointer: { icon: "path://M2,0 L-2,0 L0,-62 Z", width: 7, length: "62%", offsetCenter: [0, 0],
        itemStyle: { color: c, shadowBlur: 14, shadowColor: c } },
      anchor: { show: true, size: 13, itemStyle: { color: c, borderWidth: 0 } },
      axisTick: { distance: -17, length: 4, lineStyle: { color: "rgba(255,255,255,.35)", width: 1 } },
      splitLine: { distance: -17, length: 17, lineStyle: { color: "rgba(7,10,18,.85)", width: 2 } },
      axisLabel: { distance: 20, color: "#6f7a95", fontSize: 10 },
      detail: {
        valueAnimation: true, offsetCenter: [0, "-13%"], fontSize: 46, fontWeight: 700,
        color: c, formatter: (v) => v.toFixed(0),
      },
      title: { offsetCenter: [0, "20%"], color: "#a3aec8", fontSize: 13 },
      data: [{ value: d.score, name: d.label }],
    }],
  }, true);

  const dir = d.score_20d_ago === null ? null : d.score - d.score_20d_ago;
  $("#vsRow").innerHTML = [
    ["多因子指数", fmt(d.score), c],
    ["greedyfear 口径", fmt(d.naive_range_score), colorOf(d.naive_range_score)],
    ["20日变化", dir === null ? "—" : (dir >= 0 ? "+" : "") + fmt(dir), dir === null ? "#6f7a95" : dir >= 0 ? "#3ddc84" : "#ff4d5e"],
  ].map(([k, v, col]) => `<div class="vs"><b style="color:${col}">${v}</b><span>${k}</span></div>`).join("");
}

/* ---------------------------------------------------------------- 看板 */
function sparkline(el, data, col) {
  const c = echarts.init(el, null, { renderer: "canvas" });
  c.setOption({
    animation: false, grid: { left: 0, right: 0, top: 3, bottom: 3 },
    xAxis: { type: "category", show: true, boundaryGap: false, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { show: false } },
    yAxis: { type: "value", show: false, min: 0, max: 100 },
    series: [
      { type: "line", data, symbol: "none", lineStyle: { width: 1.7, color: col },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: col + "55" }, { offset: 1, color: col + "02" }]) } },
      { type: "line", data: data.map(() => 50), symbol: "none",
        lineStyle: { width: 1, color: "rgba(255,255,255,.13)", type: "dashed" } },
    ],
  });
  return c;
}

function renderCards() {
  const box = $("#cards");
  box.innerHTML = state.universe.map((it) => {
    const b = bandOf(it.score);
    const delta = it.score - it.naive_range_score;
    const dCol = Math.abs(delta) < 5 ? "#6f7a95" : delta < 0 ? "#ff8a3d" : "#12d6a0";
    return `<div class="sc" data-sym="${it.symbol}">
      <div class="sc-bar" style="background:${b.c}"></div>
      <div class="sc-top">
        <div><div class="sc-sym">${it.symbol}</div><div class="sc-name">${it.name}</div></div>
        <div class="sc-px"><b>${fmtPx(it.price, it.currency)}</b>
          <span class="${it.change_pct >= 0 ? "up" : "down"}">${it.change_pct >= 0 ? "+" : ""}${it.change_pct.toFixed(2)}%</span></div>
      </div>
      <div class="sc-score">
        <b style="color:${b.c}">${fmt(it.score, 0)}</b>
        <span class="sc-lab" style="background:${b.c}1f;color:${b.c}">${it.label}</span>
      </div>
      <div class="spark" id="sp-${it.symbol}"></div>
      <div class="sc-meta">
        <span>单因子 ${fmt(it.naive_range_score, 0)}</span>
        <span class="sc-delta" style="color:${dCol}">Δ ${delta >= 0 ? "+" : ""}${delta.toFixed(0)}</span>
        <span>下行波动 ${fmt(it.downside_vol, 0)}%</span>
      </div>
    </div>`;
  }).join("");

  state.universe.forEach((it) => {
    const el = $("#sp-" + it.symbol);
    if (el && it.spark && it.spark.length) sparkline(el, it.spark, bandOf(it.score).c);
  });
  box.querySelectorAll(".sc").forEach((el) =>
    el.addEventListener("click", () => select(el.dataset.sym, true)));
  markActive();
}

function markActive() {
  document.querySelectorAll(".sc").forEach((e) => e.classList.toggle("on", e.dataset.sym === state.cur));
  document.querySelectorAll(".tab").forEach((e) => e.classList.toggle("on", e.dataset.sym === state.cur));
  const sel = $("#gaugePick");
  if (sel) sel.value = state.cur;
}

/* ---------------------------------------------------------------- 因子条 */
const tip = $("#tip");
function showTip(html, ev) {
  tip.innerHTML = html;
  tip.style.opacity = 1;
  const pad = 14, w = 310;
  let x = ev.clientX + pad, y = ev.clientY + pad;
  if (x + w > window.innerWidth) x = ev.clientX - w - pad;
  if (y + 150 > window.innerHeight) y = ev.clientY - 150;
  tip.style.left = x + "px"; tip.style.top = Math.max(8, y) + "px";
}
const hideTip = () => (tip.style.opacity = 0);

function renderFactors(d) {
  $("#facBars").innerHTML = d.factors.map((f) => {
    const c = colorOf(f.score);
    return `<div class="fb" data-k="${f.key}">
      <div class="fb-top">
        <span><span class="fb-nm">${f.name}</span><span class="fb-w">权重 ${f.weight}%</span></span>
        <span><span class="fb-r">${f.raw_text}</span><span class="fb-v" style="color:${c}">${fmt(f.score, 0)}</span></span>
      </div>
      <div class="fb-track"><div class="fb-fill" style="width:${f.score || 0}%;background:linear-gradient(90deg,${c}66,${c})"></div>
        <div class="fb-mid"></div></div>
    </div>`;
  }).join("");

  $("#facBars").querySelectorAll(".fb").forEach((el) => {
    const f = d.factors.find((x) => x.key === el.dataset.k);
    el.addEventListener("mousemove", (ev) => showTip(
      `<b>${f.name} · ${f.name_en}</b>${f.desc}
       <div class="tr" style="margin-top:6px">原始值 ${f.raw_text} → 分位 ${fmt(f.score)} （权重 ${f.weight}%）</div>`, ev));
    el.addEventListener("mouseleave", hideTip);
  });

  chart("radar").setOption({
    animationDuration: 800,
    tooltip: { ...TT, trigger: "item" },
    radar: {
      indicator: d.factors.map((f) => ({ name: f.name, max: 100 })),
      radius: "66%", center: ["50%", "53%"], splitNumber: 4,
      axisName: { color: "#a3aec8", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(140,160,210,.13)" } },
      splitArea: { areaStyle: { color: ["rgba(255,255,255,.014)", "rgba(255,255,255,.03)"] } },
      axisLine: { lineStyle: { color: "rgba(140,160,210,.16)" } },
    },
    series: [{
      type: "radar", symbolSize: 5,
      data: [
        { value: d.factors.map(() => 50), name: "中性基准",
          lineStyle: { color: "rgba(255,255,255,.22)", type: "dashed", width: 1 },
          itemStyle: { color: "rgba(255,255,255,.3)" }, areaStyle: { color: "rgba(255,255,255,.03)" } },
        { value: d.factors.map((f) => f.score), name: d.symbol,
          lineStyle: { color: colorOf(d.score), width: 2.4 },
          itemStyle: { color: colorOf(d.score) },
          areaStyle: { color: colorOf(d.score) + "2e" } },
      ],
    }],
  }, true);
}

/* ---------------------------------------------------------------- 历史 */
/* 按当前区间设置切出可见历史；返回 [] 表示区间内无数据 */
function sliceHist(d) {
  if (state.from && state.to) {
    return d.history.filter((x) => x.d >= state.from && x.d <= state.to);
  }
  return state.days > 0 ? d.history.slice(-state.days) : d.history;
}

function updateHistSub(h) {
  const base = "灰色为 greedyfear 口径 —— 注意它长期贴顶，几乎不回落";
  const el = $("#histSub");
  if (!el) return;
  el.innerHTML = h.length
    ? base + ` · 当前区间 <b style="color:#a3aec8">${h[0].d} ~ ${h[h.length - 1].d}</b>（${h.length} 个交易日）`
    : `<b style="color:#ff8a3d">所选区间内没有数据</b>，可用范围 ${state.bounds[0]} ~ ${state.bounds[1]}`;
}

function renderHistory(d) {
  const h = sliceHist(d);
  updateHistSub(h);
  if (!h.length) { chart("histChart").clear(); chart("heat").clear(); return; }
  chart("histChart").setOption({
    animationDuration: 700,
    tooltip: {
      ...TT, trigger: "axis",
      axisPointer: { type: "cross", crossStyle: { color: "rgba(140,160,210,.4)" },
        lineStyle: { color: "rgba(140,160,210,.4)" } },
      formatter: (ps) => {
        let s = `<b>${ps[0].axisValue}</b><br/>`;
        ps.forEach((p) => {
          if (p.value === null) return;
          const suffix = p.seriesName === "收盘价" ? "" : " 分";
          s += `${p.marker}${p.seriesName} <b style="float:right;margin-left:16px">${Number(p.value).toFixed(p.seriesName === "收盘价" ? 2 : 1)}${suffix}</b><br/>`;
        });
        return s;
      },
    },
    legend: { data: ["多因子情绪指数", "52周区间位置", "收盘价"], top: 2, right: 4,
      textStyle: { color: "#a3aec8", fontSize: 11.5 }, itemWidth: 16, itemHeight: 9 },
    grid: { left: 46, right: 56, top: 40, bottom: 34 },
    xAxis: { type: "category", data: h.map((x) => x.d), boundaryGap: false, ...AX,
      axisLabel: { ...AX.axisLabel, formatter: (v) => v.slice(2, 7) } },
    yAxis: [
      { type: "value", min: 0, max: 100, name: "情绪分", nameTextStyle: { color: "#6f7a95", fontSize: 10.5 }, ...AX },
      { type: "value", name: "价格", scale: true, nameTextStyle: { color: "#6f7a95", fontSize: 10.5 },
        ...AX, splitLine: { show: false } },
    ],
    series: [
      { name: "多因子情绪指数", type: "line", data: h.map((x) => x.c), symbol: "none", smooth: 0.15,
        lineStyle: { width: 2.3, color: "#6ea8fe" },
        areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "rgba(110,168,254,.3)" }, { offset: 1, color: "rgba(110,168,254,0)" }]) },
        markLine: { silent: true, symbol: "none",
          data: [
            { yAxis: 80, lineStyle: { color: "rgba(18,214,160,.3)", type: "dashed" },
              label: { formatter: "极度贪婪 80", color: "#12d6a0", fontSize: 10, position: "insideEndTop" } },
            { yAxis: 20, lineStyle: { color: "rgba(255,77,94,.3)", type: "dashed" },
              label: { formatter: "极度恐慌 20", color: "#ff4d5e", fontSize: 10, position: "insideEndBottom" } },
          ] } },
      { name: "52周区间位置", type: "line", data: h.map((x) => x.naive), symbol: "none", smooth: 0.15,
        lineStyle: { width: 1.6, color: "#8b96b0", type: "dashed" } },
      { name: "收盘价", type: "line", yAxisIndex: 1, data: h.map((x) => x.close), symbol: "none",
        lineStyle: { width: 1.4, color: "rgba(169,123,255,.75)" } },
    ],
  }, true);
}

/* ---------------------------------------------------------------- 热力图 */
function renderHeat(d) {
  const fh = d.factor_history;
  const keys = d.factors.map((f) => f.key);
  const names = d.factors.map((f) => f.name);
  const n = fh.d.length;
  const h = sliceHist(d);
  if (!h.length) { chart("heat").clear(); return; }
  const lo = h[0].d, hi = h[h.length - 1].d;
  let pool = [...Array(n).keys()].filter((i) => fh.d[i] >= lo && fh.d[i] <= hi);
  if (!pool.length) pool = [...Array(n).keys()];
  const step = Math.max(1, Math.ceil(pool.length / 200));
  const idxs = pool.filter((_, k) => k % step === 0);
  const data = [];
  idxs.forEach((ti, xi) => keys.forEach((k, yi) => {
    const v = fh[k][ti];
    if (v !== null && v !== undefined) data.push([xi, yi, v]);
  }));
  chart("heat").setOption({
    animation: false,
    tooltip: { ...TT, formatter: (p) =>
      `<b>${fh.d[idxs[p.value[0]]]}</b>${names[p.value[1]]}<br/><span class="tr">分位 ${p.value[2].toFixed(1)}</span>` },
    grid: { left: 104, right: 20, top: 12, bottom: 48 },
    xAxis: { type: "category", data: idxs.map((i) => fh.d[i]), ...AX, splitLine: { show: false },
      axisLabel: { ...AX.axisLabel, interval: Math.max(1, Math.floor(idxs.length / 9)),
        formatter: (v) => v.slice(0, 7) }, axisTick: { show: false } },
    yAxis: { type: "category", data: names, ...AX, splitLine: { show: false },
      axisLabel: { ...AX.axisLabel, fontSize: 11.5 }, axisTick: { show: false } },
    visualMap: {
      min: 0, max: 100, calculable: false, orient: "horizontal", left: "center", bottom: 4,
      itemWidth: 11, itemHeight: 108, textStyle: { color: "#6f7a95", fontSize: 10.5 },
      inRange: { color: ["#ff4d5e", "#ff8a3d", "#d8c04a", "#3ddc84", "#12d6a0"] },
      text: ["贪婪 100", "0 恐慌"],
    },
    series: [{ type: "heatmap", data, progressive: 4000,
      itemStyle: { borderWidth: 0 }, emphasis: { itemStyle: { borderColor: "#fff", borderWidth: 1 } } }],
  }, true);
}

/* ---------------------------------------------------------------- 抄底提示 */
function renderDip(dp) {
  state.dip = dp;
  $("#dipAsOf").textContent = "截止 " + (dp.symbols[0] || {}).as_of;

  const order = { act: 0, hold: 1, wait: 2 };
  const rows = [...dp.symbols].sort((a, b) => {
    const d = order[a.signal.tone] - order[b.signal.tone];
    return d !== 0 ? d : (a.drawdown_now ?? 0) - (b.drawdown_now ?? 0);
  });

  $("#dipTable").innerHTML = `<table><thead><tr>
      <th class="l">标的</th><th>现价</th><th>情绪分</th><th>距 252 日高点</th>
      <th>当前档位</th><th class="l">状态</th><th>建议累计仓位</th>
      <th>下一档触发价</th><th>该档历史 60 日</th>
    </tr></thead><tbody>` + rows.map((s) => {
    const sig = s.signal;
    const lv = s.levels.find((l) => l.x === sig.deepest);
    const nx = sig.next_x
      ? `-${sig.next_x}% @ ${fmtPx(sig.next_target, s.currency)}
         <span class="sub">还需 ${signed(sig.next_need)}%</span>` : "—";
    const hist = lv
      ? `<b class="${pn(lv.f60)}">${signed(lv.f60)}%</b>
         <span class="sub">胜率 ${fmt(lv.hit60, 0)}% · n=${lv.n} · 平均浮亏 ${fmt(lv.trough, 1)}%</span>`
      : "—";
    return `<tr data-sym="${s.symbol}">
      <td class="l"><span class="sym">${s.symbol}</span><span class="sub">${s.name}</span></td>
      <td class="num">${fmtPx(s.px, s.currency)}</td>
      <td class="num" style="color:${colorOf(s.score)}">${fmt(s.score, 0)}</td>
      <td class="num ${pn(s.drawdown_now)}">${fmt(s.drawdown_now, 1)}%</td>
      <td class="num">${sig.deepest ? "-" + sig.deepest + "%" : "—"}</td>
      <td class="l"><span class="badge ${sig.tone}">${sig.state}</span>
        <span class="sub">${sig.trend_note} · ${sig.mood_note}</span></td>
      <td><span class="lad"><b>${sig.ladder}%</b>
        <span class="lad-bar"><i style="width:${sig.ladder}%"></i></span></span></td>
      <td class="num">${nx}</td>
      <td class="num">${hist}</td>
    </tr>`;
  }).join("") + "</tbody></table>";

  $("#dipTable").querySelectorAll("tr[data-sym]").forEach((el) =>
    el.addEventListener("click", () => select(el.dataset.sym, false)));

  const act = rows.filter((r) => r.signal.tone === "act");
  $("#dipNote").innerHTML = act.length
    ? `可执行：<b>${act.map((r) => r.symbol + " -" + r.signal.deepest + "%").join("、")}</b>
       · 其余标的等下一档触发价`
    : "无新触发 · 等下一档触发价";

  renderRules(dp);
  renderEpisodes();
}

function renderEpisodes() {
  const dp = state.dip;
  if (!dp) return;
  const s = dp.symbols.find((x) => x.symbol === state.cur);
  const el = $("#epTable");
  $("#epSym").textContent = s ? s.symbol + " · " + s.name : "—";
  if (!s) { el.innerHTML = ""; return; }
  const eps = [...s.episodes].reverse();
  el.innerHTML = `<table><thead><tr>
      <th class="l">触发日</th><th>触发档</th><th>触发价</th><th>当日情绪分</th>
      <th>后 20 日</th><th>后 60 日</th><th>期间最大浮亏</th><th>见底天数</th>
    </tr></thead><tbody>` + eps.map((e) => `<tr>
      <td class="l">${e.d}</td>
      <td class="num">-${e.x}%</td>
      <td class="num">${fmtPx(e.px, s.currency)}</td>
      <td class="num" style="color:${colorOf(e.score)}">${fmt(e.score, 0)}</td>
      <td class="num ${pn(e.f20)}">${e.f20 === null ? "尚未满 20 日" : signed(e.f20) + "%"}</td>
      <td class="num ${pn(e.f60)}">${e.f60 === null ? "尚未满 60 日" : signed(e.f60) + "%"}</td>
      <td class="num neg">${e.dd === null ? "—" : fmt(e.dd, 1) + "%"}</td>
      <td class="num">${e.dd_days === null ? "—" : e.dd_days + " 日"}</td>
    </tr>`).join("") + "</tbody></table>";
}


/* ---------------------------------------------------------------- 规则回测表 */
function renderRules(dp) {
  const kc = { "回撤": "#12d6a0", "组合": "#6ea8fe", "情绪": "#ff4d5e", "情绪反转": "#ff8a3d" };
  const b = dp.pooled.baseline;
  const rows = dp.rules.map((r) => `<tr title="${r.desc}">
      <td class="l"><span class="sym">${r.name}</span></td>
      <td class="l"><span class="badge" style="background:${kc[r.kind]}1f;color:${kc[r.kind]}">${r.kind}</span></td>
      <td class="num">${r.fwd["20"].n}</td>
      <td class="num ${pn(r.fwd["20"].mean)}">${signed(r.fwd["20"].mean)}%</td>
      <td class="num ${pn(r.fwd["60"].mean)}">${signed(r.fwd["60"].mean)}%</td>
      <td class="num"><b class="${pn(r.excess["60"].mean)}">${signed(r.excess["60"].mean)}</b></td>
      <td class="num">${fmt(r.fwd["60"].hit, 0)}%</td>
      <td class="num neg">${fmt(r.trough_mean, 1)}%</td>
      <td class="num">${fmt(r.trough_days, 0)}</td>
    </tr>`).join("");
  $("#ruleTable").innerHTML = `<table><thead><tr>
      <th class="l">规则</th><th class="l">族</th><th>N</th><th>20D</th><th>60D</th>
      <th>EXC 60D</th><th>WIN 60D</th><th>MDD</th><th>见底日</th>
    </tr></thead><tbody>
      <tr class="base"><td class="l"><span class="sym">基线 · 任意一天买入</span></td><td class="l">—</td>
        <td class="num">${b["20"].n}</td><td class="num">${signed(b["20"].mean)}%</td>
        <td class="num">${signed(b["60"].mean)}%</td><td class="num">0.0</td>
        <td class="num">${fmt(b["60"].hit, 0)}%</td><td class="num">—</td><td class="num">—</td></tr>
      ${rows}</tbody></table>`;

  const win = dp.rules.filter((r) => r.excess["60"].mean > 0).length;
  const bands = Object.entries(dp.pooled.dd_bands)
    .map(([k, v]) => `${k} ${signed(v.stats["60"].mean)}%`).join(" · ");
  $("#ruleNote").innerHTML = `12 条规则中 <b>${win}</b> 条 EXC&gt;0，全部属回撤族；情绪族全部负超额。
    状态口径（当前处于该回撤区间，非首次触及）无效：${bands} ≈ 基线。`;
}

/* ---------------------------------------------------------------- 指标矩阵 */
const MX_KEY = "mx.cols.v1";
const FKEY = { momentum: "MOM", rel_strength: "RS", volatility: "DVOL-P",
  money_flow: "CMF", drawdown: "ATR-DD", range_pos: "52W-P", rsi: "RSI" };
const FTIP = { MOM: "趋势动量分位", RS: "相对 SPY 强度分位", "DVOL-P": "下行波动（反向）分位",
  CMF: "资金流 CMF 分位", "ATR-DD": "ATR 归一化回撤压力分位", "52W-P": "52 周区间位置分位",
  RSI: "RSI(14) 分位" };

function mxCols() {
  const C = [
    { k: "px", n: "PX", g: "核心", tip: "最新收盘价", v: (r) => r.price,
      c: (r) => fmtPx(r.price, r.currency) },
    { k: "chg", n: "CHG%", g: "核心", tip: "当日涨跌幅", v: (r) => r.change_pct,
      c: (r) => `<span class="${pn(r.change_pct)}">${signed(r.change_pct, 2)}</span>` },
    { k: "score", n: "MULTI", g: "核心", tip: "7 因子合成情绪分", v: (r) => r.score, tint: 1 },
    { k: "naive", n: "52W", g: "核心", tip: "greedyfear 口径：价格在 52 周区间位置",
      v: (r) => r.naive_range_score, tint: 1 },
    { k: "delta", n: "Δ", g: "核心", tip: "MULTI − 52W，越负说明单因子口径高估了贪婪",
      v: (r) => r.score - r.naive_range_score,
      c: (r) => `<span class="${pn(r.score - r.naive_range_score)}">${signed(r.score - r.naive_range_score, 0)}</span>` },
    { k: "d20", n: "20D Δ", g: "核心", tip: "情绪分 20 交易日变化",
      v: (r) => (r.score_20d_ago === null ? null : r.score - r.score_20d_ago),
      c: (r) => (r.score_20d_ago === null ? "—"
        : `<span class="${pn(r.score - r.score_20d_ago)}">${signed(r.score - r.score_20d_ago, 0)}</span>`) },
    { k: "pct", n: "SELF-P", g: "核心", tip: "当前情绪分在本标的过去 504 日分布中的分位",
      v: (r) => r.self_percentile, tint: 1 },
  ];
  Object.entries(FKEY).forEach(([k, n]) => C.push({
    k: "f_" + k, n, g: "因子", tip: FTIP[n], tint: 1,
    v: (r) => { const f = r.factors.find((x) => x.key === k); return f ? f.score : null; },
  }));
  C.push(
    { k: "dvol", n: "DVOL%", g: "风险", tip: "21 日下行半标准差（年化）", v: (r) => r.downside_vol,
      c: (r) => fmt(r.downside_vol, 0) },
    { k: "rvol", n: "RVOL%", g: "风险", tip: "21 日实现波动率（年化）", v: (r) => r.realized_vol,
      c: (r) => fmt(r.realized_vol, 0) },
    { k: "dd", n: "DD%", g: "抄底", tip: "距 252 日高点回撤", v: (r) => r.drawdown_now,
      c: (r) => `<span class="${pn(r.drawdown_now)}">${fmt(r.drawdown_now, 1)}</span>` },
    { k: "tier", n: "TIER", g: "抄底", tip: "已触发的最深回撤档",
      v: (r) => r.tier || 0, c: (r) => (r.tier ? "-" + r.tier + "%" : "—") },
    { k: "pos", n: "POS%", g: "抄底", tip: "机械阶梯的建议累计仓位", v: (r) => r.pos,
      c: (r) => `<span class="lad"><b>${r.pos}</b><span class="lad-bar"><i style="width:${r.pos}%"></i></span></span>` },
    { k: "tone", n: "SIG", g: "抄底", tip: "抄底信号新鲜度", v: (r) => ({ act: 2, hold: 1, wait: 0 }[r.tone]),
      c: (r) => `<span class="badge ${r.tone}">${{ act: "可执行", hold: "过期", wait: "观察" }[r.tone]}</span>` },
  );
  return C;
}

function renderMatrix(dp) {
  const COLS = mxCols();
  const dmap = {};
  dp.symbols.forEach((s) => (dmap[s.symbol] = s));
  const rows = state.universe.map((it) => {
    const d = dmap[it.symbol] || {};
    return { ...it, drawdown_now: d.drawdown_now,
      tier: d.signal ? d.signal.deepest : null,
      pos: d.signal ? d.signal.ladder : 0,
      tone: d.signal ? d.signal.tone : "wait" };
  });

  let show = null;
  try { show = JSON.parse(localStorage.getItem(MX_KEY)); } catch (e) { show = null; }
  if (!Array.isArray(show)) show = COLS.filter((c) => c.k !== "rvol" && c.k !== "px").map((c) => c.k);
  let sort = { k: "score", dir: -1 };

  const save = () => { try { localStorage.setItem(MX_KEY, JSON.stringify(show)); } catch (e) {} };

  function draw() {
    const cs = COLS.filter((c) => show.includes(c.k));
    const col = COLS.find((c) => c.k === sort.k) || COLS[2];
    const rs = [...rows].sort((a, b) => {
      const va = col.v(a), vb = col.v(b);
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      return (va - vb) * sort.dir;
    });
    $("#mxTable").innerHTML = `<table><thead><tr><th class="l">SYM</th>` +
      cs.map((c) => `<th data-k="${c.k}" title="${c.tip}" class="sortable${sort.k === c.k ? " sorted" : ""}">
        ${c.n}${sort.k === c.k ? (sort.dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("") +
      `</tr></thead><tbody>` + rs.map((r) => `<tr data-sym="${r.symbol}"${r.symbol === state.cur ? ' class="on"' : ""}>
        <td class="l"><span class="sym">${r.symbol}</span></td>` +
        cs.map((c) => {
          const v = c.v(r);
          if (c.tint) {
            const col2 = colorOf(v);
            return `<td class="num tint" style="background:${col2}20;color:${col2}">${fmt(v, 0)}</td>`;
          }
          return `<td class="num">${c.c ? c.c(r) : fmt(v, 1)}</td>`;
        }).join("") + `</tr>`).join("") + `</tbody></table>`;

    $("#mxTable").querySelectorAll("th.sortable").forEach((th) =>
      th.addEventListener("click", () => {
        sort = { k: th.dataset.k, dir: sort.k === th.dataset.k ? -sort.dir : -1 };
        draw();
      }));
    $("#mxTable").querySelectorAll("tr[data-sym]").forEach((tr) =>
      tr.addEventListener("click", () => select(tr.dataset.sym, false)));
  }

  $("#mxToggles").innerHTML = ["核心", "因子", "风险", "抄底"].map((g) =>
    `<span class="tg-group"><i class="tg-g">${g}</i>` +
    COLS.filter((c) => c.g === g).map((c) =>
      `<button class="tg${show.includes(c.k) ? " on" : ""}" data-k="${c.k}" title="${c.tip}">${c.n}</button>`).join("") +
    `</span>`).join("");

  const sync = () => {
    $("#mxToggles").querySelectorAll(".tg").forEach((b) =>
      b.classList.toggle("on", show.includes(b.dataset.k)));
    save();
    draw();
  };
  $("#mxToggles").querySelectorAll(".tg").forEach((b) =>
    b.addEventListener("click", () => {
      const k = b.dataset.k;
      show = show.includes(k) ? show.filter((x) => x !== k) : [...show, k];
      sync();
    }));
  $("#mxAll").addEventListener("click", () => { show = COLS.map((c) => c.k); sync(); });
  $("#mxCore").addEventListener("click", () => {
    show = COLS.filter((c) => c.g === "核心" || c.g === "抄底").map((c) => c.k); sync(); });
  $("#mxFac").addEventListener("click", () => {
    show = ["score"].concat(COLS.filter((c) => c.g === "因子").map((c) => c.k)); sync(); });

  $("#mxLegend").innerHTML = `<span>MULTI 7 因子合成</span><span>52W 区间位置</span>
    <span>SELF-P 自身历史分位</span><span>MOM 动量</span><span>RS 相对强度</span>
    <span>DVOL-P 下行波动分位</span><span>CMF 资金流</span><span>ATR-DD 回撤压力</span>
    <span>DD% 距 252 日高点</span><span>POS% 建议累计仓位</span>`;
  state.mxDraw = draw;
  draw();
}

/* ---------------------------------------------------------------- 选择 */
async function select(sym, scroll) {
  state.cur = sym;
  markActive();
  let d = state.detail[sym];
  if (!d) {
    d = await api("/api/stock/" + sym);
    state.detail[sym] = d;
  }
  renderGauge(d);
  renderFactors(d);
  renderHistory(d);
  renderHeat(d);
  renderEpisodes();
  if (state.mxDraw) state.mxDraw();
  if (scroll) $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------------------------------------------------------- 启动 */
async function boot() {
  $("#apiLink").href = STATIC + "/universe.json";
  try {
    const u = await api("/api/universe");
    state.universe = u.items;
    renderCards();

    $("#gaugePick").innerHTML = u.items
      .map((i) => `<option value="${i.symbol}">${i.symbol}</option>`).join("");
    $("#symTabs").innerHTML = u.items
      .map((i) => `<button class="tab" data-sym="${i.symbol}">${i.symbol}</button>`).join("");
    $("#symTabs").querySelectorAll(".tab").forEach((el) =>
      el.addEventListener("click", () => select(el.dataset.sym, false)));
    $("#gaugePick").addEventListener("change", (e) => select(e.target.value, false));

    const asOf = u.items[0].as_of;
    $("#asOf").textContent = asOf;
    $("#boardAsOf").textContent = "截止 " + asOf;
    $("#srcMode").textContent = state.live ? "实时接口" : "静态快照";

    await select(u.items[0].symbol, false);

    /* --- 时间区间：预设 chip + 自定义日期 --- */
    const allDates = state.detail[state.cur].history.map((x) => x.d);
    state.bounds = [allDates[0], allDates[allDates.length - 1]];
    const fromEl = $("#dFrom"), toEl = $("#dTo");
    [fromEl, toEl].forEach((el) => { el.min = state.bounds[0]; el.max = state.bounds[1]; });

    const redraw = () => {
      renderHistory(state.detail[state.cur]);
      renderHeat(state.detail[state.cur]);
    };
    const clearActive = () =>
      $("#rangeChips").querySelectorAll(".chip").forEach((x) => x.classList.remove("active"));

    $("#rangeChips").querySelectorAll(".chip[data-d]").forEach((el) =>
      el.addEventListener("click", () => {
        clearActive();
        el.classList.add("active");
        state.days = +el.dataset.d;
        state.from = state.to = null;
        fromEl.classList.remove("bad"); toEl.classList.remove("bad");
        redraw();
      }));

    const applyCustom = () => {
      let a = fromEl.value, b = toEl.value;
      fromEl.classList.toggle("bad", !a);
      toEl.classList.toggle("bad", !b);
      if (!a || !b) return;
      if (a > b) { const t = a; a = b; b = t; fromEl.value = a; toEl.value = b; }
      state.from = a; state.to = b;
      clearActive();
      $("#dApply").classList.add("active");
      redraw();
    };
    $("#dApply").addEventListener("click", applyCustom);
    [fromEl, toEl].forEach((el) => el.addEventListener("change", () => {
      if (fromEl.value && toEl.value) applyCustom();
    }));
    $("#dReset").addEventListener("click", () => {
      fromEl.value = ""; toEl.value = "";
      fromEl.classList.remove("bad"); toEl.classList.remove("bad");
      state.from = state.to = null; state.days = 380;
      clearActive();
      $('#rangeChips .chip[data-d="380"]').classList.add("active");
      redraw();
    });

    const dp = await api("/api/dip");
    renderDip(dp);
    renderMatrix(dp);
  } catch (e) {
    $("#cards").innerHTML =
      `<div class="card" style="grid-column:1/-1"><b style="color:#ff8a3d">数据加载失败</b>
       <p style="color:#a3aec8;font-size:13px;margin-top:6px">${String(e.message || e)}</p></div>`;
    console.error(e);
  }
}
boot();
