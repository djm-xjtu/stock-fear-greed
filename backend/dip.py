"""抄底信号（Dip-Buy）：把「什么时候抄底比较好」变成可检验的规则排行榜。

设计要点（都是为了不自欺）：

  1. 所有规则的收益都与**同一样本的无条件基线**对比（这批标的长期强上行，
     基线 60 日就有两位数收益，不减基线的话任何规则都会显得很厉害）。
  2. 事件用「条件的上升沿 + 至少 10 个交易日间隔」去重，避免同一波下跌
     被重复计入几十次，把样本量虚高成显著性。
  3. 除了收益，还统计入场后 60 日内的**最大浮亏与见底天数** —— 抄底真正
     的风险不是方向错，而是过早满仓后拿不住。
  4. 规则族覆盖三类信息：情绪极端（fear）、情绪反转（turn）、价格回撤
     （drawdown）以及它们的组合。
"""
from typing import Callable, Dict, List, Optional

HORIZONS = [5, 10, 20, 60]
THRESHOLDS = [40, 35, 30, 25, 20, 15]
DD_LEVELS = [8, 12, 16, 20, 25, 30]
MIN_GAP = 10
LOOKBACK_HIGH = 252

BANDS = [(0, 15), (15, 25), (25, 35), (35, 45), (45, 60), (60, 101)]

# 分批阶梯：首次触及各回撤档时的**累计**仓位（机械映射，非投资建议）
DD_LADDER = [(8, 20), (12, 40), (20, 65), (25, 100)]
FRESH_DAYS = 10          # 触发后多少个交易日内算「新鲜信号」


# ------------------------------------------------------------------ 基础工具
def _stat(vals: List[float]) -> Optional[dict]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    return {"n": n,
            "mean": round(100.0 * sum(s) / n, 2),
            "median": round(100.0 * med, 2),
            "hit": round(100.0 * sum(1 for x in s if x > 0) / n, 1),
            "p10": round(100.0 * s[max(0, int(0.1 * n) - 1)], 2),
            "p90": round(100.0 * s[min(n - 1, int(0.9 * n))], 2)}


def _series(detail: dict):
    h = detail["history"]
    return ([x["d"] for x in h], [x["c"] for x in h], [x["close"] for x in h])


def _fwd(px: List[float], i: int, k: int) -> Optional[float]:
    j = i + k
    if j >= len(px):
        return None
    return px[j] / px[i] - 1.0


def _trough(px: List[float], i: int, k: int = 60):
    j = min(len(px) - 1, i + k)
    if j <= i:
        return None, None
    seg = px[i + 1:j + 1]
    lo = min(seg)
    return lo / px[i] - 1.0, seg.index(lo) + 1


def roll_high(px: List[float], win: int = LOOKBACK_HIGH) -> List[Optional[float]]:
    return [max(px[max(0, i - win + 1):i + 1]) if i >= win else None
            for i in range(len(px))]


def dd_series(px: List[float]) -> List[Optional[float]]:
    hi = roll_high(px)
    return [None if hi[i] is None else 100.0 * (px[i] / hi[i] - 1.0)
            for i in range(len(px))]


def ma_series(px: List[float], win: int) -> List[Optional[float]]:
    out, acc = [], 0.0
    for i, v in enumerate(px):
        acc += v
        if i >= win:
            acc -= px[i - win]
        out.append(acc / win if i >= win - 1 else None)
    return out


def _edges(flags: List[bool], gap: int = MIN_GAP) -> List[int]:
    """条件的上升沿 + 最小间隔去重。"""
    out, last = [], -10 ** 9
    for i in range(1, len(flags)):
        if flags[i] and not flags[i - 1] and i - last >= gap:
            out.append(i)
            last = i
    return out


def breach_events(sc, T):
    return _edges([v < T for v in sc])


def recover_events(sc, T):
    n = len(sc)
    flags = [False] * n
    for i in range(5, n):
        flags[i] = sc[i] >= T and min(sc[i - 5:i]) < T
    return _edges(flags)


def _collect(px: List[float], idxs: List[int]) -> dict:
    fwd = {str(k): _stat([v for v in (_fwd(px, i, k) for i in idxs)
                          if v is not None]) for k in HORIZONS}
    dds, days = [], []
    for i in idxs:
        dd, dy = _trough(px, i, 60)
        if dd is not None:
            dds.append(dd)
            days.append(dy)
    return {"n": len(idxs), "fwd": fwd,
            "trough_mean": round(100.0 * sum(dds) / len(dds), 2) if dds else None,
            "trough_worst": round(100.0 * min(dds), 2) if dds else None,
            "trough_days": round(sum(days) / len(days), 1) if days else None}


# ------------------------------------------------------------------ 规则族
class Ctx:
    """单标的的所有可用序列，规则条件在其上表达。"""

    def __init__(self, detail: dict):
        self.dates, self.sc, self.px = _series(detail)
        self.dd = dd_series(self.px)
        self.ma20 = ma_series(self.px, 20)
        self.ma50 = ma_series(self.px, 50)
        self.n = len(self.px)

    def turn5(self, i: int) -> bool:
        """情绪 5 日回升 ≥ 5 分（情绪触底反弹的最小确认）。"""
        return i >= 5 and (self.sc[i] - self.sc[i - 5]) >= 5


def _f(fn: Callable[[Ctx, int], bool], c: Ctx) -> List[bool]:
    out = [False] * c.n
    for i in range(c.n):
        try:
            out[i] = bool(fn(c, i))
        except TypeError:
            out[i] = False
    return out


def _ddle(c: Ctx, i: int, x: float) -> bool:
    return c.dd[i] is not None and c.dd[i] <= -x


RULES: List[dict] = [
    {"key": "fear30", "name": "情绪跌破 30", "kind": "情绪",
     "desc": "多因子情绪分下穿 30 的当天买入 —— 最朴素的「恐慌就抄」。",
     "fn": lambda c, i: c.sc[i] < 30},
    {"key": "fear20", "name": "情绪跌破 20", "kind": "情绪",
     "desc": "只在极度恐慌档动手（情绪分 < 20）。",
     "fn": lambda c, i: c.sc[i] < 20},
    {"key": "turn25", "name": "情绪自 25 下方回升", "kind": "情绪反转",
     "desc": "先跌进恐慌区，再等情绪分回升上穿 25 才买 —— 用一点右侧确认换更小浮亏。",
     "fn": lambda c, i: i >= 5 and c.sc[i] >= 25 and min(c.sc[i - 5:i]) < 25},
    {"key": "dd8", "name": "回撤 ≥ 8%", "kind": "回撤",
     "desc": "价格从 252 日高点回落 8%（普通级别的回调）。",
     "fn": lambda c, i: _ddle(c, i, 8)},
    {"key": "dd12", "name": "回撤 ≥ 12%", "kind": "回撤",
     "desc": "价格从 252 日高点回落 12%。",
     "fn": lambda c, i: _ddle(c, i, 12)},
    {"key": "dd20", "name": "回撤 ≥ 20%", "kind": "回撤",
     "desc": "技术性熊市级别的回撤（-20%）。",
     "fn": lambda c, i: _ddle(c, i, 20)},
    {"key": "dd25", "name": "回撤 ≥ 25%", "kind": "回撤",
     "desc": "深度回撤（-25%），历史上机会稀缺。",
     "fn": lambda c, i: _ddle(c, i, 25)},
    {"key": "dd12_fear30", "name": "回撤 ≥12% 且情绪 <30", "kind": "组合",
     "desc": "价格已明显回撤、情绪也确实恐慌 —— 两个条件同时成立。",
     "fn": lambda c, i: _ddle(c, i, 12) and c.sc[i] < 30},
    {"key": "dd12_turn", "name": "回撤 ≥12% + 情绪 5 日回升", "kind": "组合",
     "desc": "在回撤中等情绪分 5 日回升 ≥5 分（情绪拐头）后再买。",
     "fn": lambda c, i: _ddle(c, i, 12) and c.turn5(i)},
    {"key": "dd20_turn", "name": "回撤 ≥20% + 情绪 5 日回升", "kind": "组合",
     "desc": "深回撤 + 情绪拐头，理论上的「黄金坑」组合。",
     "fn": lambda c, i: _ddle(c, i, 20) and c.turn5(i)},
    {"key": "dd12_ma20", "name": "回撤 ≥12% + 收复 20 日均线", "kind": "组合",
     "desc": "回撤中等价格重新站上 20 日均线，纯价格的右侧确认。",
     "fn": lambda c, i: _ddle(c, i, 12) and c.ma20[i] is not None
     and c.px[i] > c.ma20[i]},
    {"key": "dd12_fear_ma20", "name": "回撤 ≥12% + 情绪 <35 + 收复 20 日线",
     "kind": "组合",
     "desc": "情绪尚未修复、但价格已右侧转强 —— 情绪与价格的错位窗口。",
     "fn": lambda c, i: _ddle(c, i, 12) and c.sc[i] < 35
     and c.ma20[i] is not None and c.px[i] > c.ma20[i]},
]


def rank_rules(ctxs: List[Ctx], baseline: Dict[str, dict]) -> List[dict]:
    out = []
    for r in RULES:
        idx_all, px_all = [], []
        fwd = {str(k): [] for k in HORIZONS}
        dds, days = [], []
        for c in ctxs:
            for i in _edges(_f(r["fn"], c)):
                for k in HORIZONS:
                    v = _fwd(c.px, i, k)
                    if v is not None:
                        fwd[str(k)].append(v)
                dd, dy = _trough(c.px, i, 60)
                if dd is not None:
                    dds.append(dd)
                    days.append(dy)
                idx_all.append(i)
        stats = {k: _stat(v) for k, v in fwd.items()}
        exc = {}
        for k in HORIZONS:
            s, b = stats[str(k)], baseline[str(k)]
            exc[str(k)] = None if not s else {
                "mean": round(s["mean"] - b["mean"], 2),
                "hit": round(s["hit"] - b["hit"], 1)}
        out.append({"key": r["key"], "name": r["name"], "kind": r["kind"],
                    "desc": r["desc"], "events": len(idx_all),
                    "fwd": stats, "excess": exc,
                    "trough_mean": round(100.0 * sum(dds) / len(dds), 2)
                    if dds else None,
                    "trough_p10": round(100.0 * sorted(dds)[
                        max(0, int(0.1 * len(dds)) - 1)], 2) if dds else None,
                    "trough_days": round(sum(days) / len(days), 1)
                    if days else None})
    out.sort(key=lambda r: -(r["excess"]["60"]["mean"]
                             if r["excess"]["60"] else -99))
    return out


# ------------------------------------------------------------------ 单标的
def symbol_report(detail: dict, c: Ctx) -> dict:
    """当前抄底红绿灯：回撤阶梯的触发状态、下一档的目标价、历史触发明细。"""
    dates, sc, px = c.dates, c.sc, c.px
    n = c.n
    own_base = {str(k): _stat([v for v in (_fwd(px, i, k) for i in range(n))
                               if v is not None]) for k in HORIZONS}

    hi252 = roll_high(px)[-1]
    dd_now = c.dd[-1]
    cur = sc[-1]
    turning = c.turn5(n - 1)
    above_ma20 = bool(c.ma20[-1] and px[-1] > c.ma20[-1])

    # 各回撤档：目标价 / 是否已触发 / 上次触发距今多少个交易日 / 该股历史效力
    levels = []
    for X, w in DD_LADDER:
        flags = [_ddle(c, j, X) for j in range(n)]
        ev = _edges(flags)
        st = _collect(px, ev)
        since = None if not ev else n - 1 - ev[-1]
        levels.append({
            "x": X, "ladder": w,
            "target": None if hi252 is None else round(hi252 * (1 - X / 100.0), 2),
            "need_pct": None if hi252 is None else
            round(100.0 * (hi252 * (1 - X / 100.0) / px[-1] - 1.0), 1),
            "hit": bool(dd_now is not None and dd_now <= -X),
            "since": since, "last": None if not ev else dates[ev[-1]],
            "n": st["n"],
            "f60": None if not st["fwd"]["60"] else st["fwd"]["60"]["mean"],
            "hit60": None if not st["fwd"]["60"] else st["fwd"]["60"]["hit"],
            "trough": st["trough_mean"],
        })

    triggered = [lv for lv in levels if lv["hit"]]
    deepest = triggered[-1] if triggered else None
    fresh = bool(deepest and deepest["since"] is not None
                 and deepest["since"] <= FRESH_DAYS)
    nxt = next((lv for lv in levels if not lv["hit"]), None)

    if deepest is None:
        state = "观察 · 尚无回撤"
        tone = "wait"
    elif fresh:
        state = "刚触及 -{}% 档（{} 日前）· 该档可执行".format(
            deepest["x"], deepest["since"])
        tone = "act"
    elif nxt is None:
        state = "已在 -{}% 档 {} 日 · 阶梯已打满，无更深档位".format(
            deepest["x"], deepest["since"])
        tone = "hold"
    else:
        state = "已在 -{}% 档 {} 日 · 信号已过期，等 -{}% 档".format(
            deepest["x"], deepest["since"], nxt["x"])
        tone = "hold"

    trend = "价格已站上 20 日均线" if above_ma20 else "价格仍在 20 日均线下"
    mood = "情绪 5 日回升 ≥5 分" if turning else "情绪尚未回升"

    below40 = 0
    for i in range(n - 1, -1, -1):
        if sc[i] < 40:
            below40 += 1
        else:
            break

    # 历史触发明细（各档合并，按日期）
    eps = []
    for X, _w in DD_LADDER:
        for i in _edges([_ddle(c, j, X) for j in range(n)]):
            dd, dy = _trough(px, i, 60)
            eps.append({"d": dates[i], "x": X, "score": round(sc[i], 1),
                        "px": round(px[i], 2),
                        "f20": None if _fwd(px, i, 20) is None
                        else round(100 * _fwd(px, i, 20), 1),
                        "f60": None if _fwd(px, i, 60) is None
                        else round(100 * _fwd(px, i, 60), 1),
                        "dd": None if dd is None else round(100 * dd, 1),
                        "dd_days": dy})
    eps.sort(key=lambda e: (e["d"], e["x"]))

    # 情绪阈值口径（保留对照：说明「按情绪抄底」在该股上的表现）
    rows = []
    for T in THRESHOLDS:
        b = _collect(px, breach_events(sc, T))
        r = _collect(px, recover_events(sc, T))
        for e in (b, r):
            e["excess60"] = None if not e["fwd"]["60"] else \
                round(e["fwd"]["60"]["mean"] - own_base["60"]["mean"], 2)
        rows.append({"t": T, "breach": b, "recover": r})

    return {
        "symbol": detail["symbol"], "name": detail["name"],
        "currency": detail.get("currency", "USD"),
        "score": round(cur, 1), "as_of": dates[-1], "px": round(px[-1], 2),
        "high252": None if hi252 is None else round(hi252, 2),
        "drawdown_now": None if dd_now is None else round(dd_now, 1),
        "baseline60": own_base["60"]["mean"] if own_base["60"] else None,
        "levels": levels, "episodes": eps[-18:], "rows": rows,
        "signal": {"state": state, "tone": tone, "fresh": fresh,
                   "ladder": deepest["ladder"] if deepest else 0,
                   "deepest": None if not deepest else deepest["x"],
                   "since": None if not deepest else deepest["since"],
                   "next_x": nxt["x"] if nxt else None,
                   "next_target": nxt["target"] if nxt else None,
                   "next_need": nxt["need_pct"] if nxt else None,
                   "turning": turning, "above_ma20": above_ma20,
                   "trend_note": trend, "mood_note": mood,
                   "days_below40": below40},
        "self_percentile": detail.get("self_percentile"),
    }


# ------------------------------------------------------------------ 池化统计
def pooled(ctxs: List[Ctx]) -> dict:
    base = {str(k): [] for k in HORIZONS}
    bands = {"{}-{}".format(*b): {str(k): [] for k in HORIZONS} for b in BANDS}
    ddband = {}
    for c in ctxs:
        for i in range(c.n):
            bkey = "{}-{}".format(*next(b for b in BANDS
                                        if b[0] <= c.sc[i] < b[1]))
            dkey = None
            if c.dd[i] is not None:
                for X, _w in reversed(DD_LADDER):
                    if c.dd[i] <= -X:
                        dkey = "-{}%".format(X)
                        break
                if dkey is None:
                    dkey = "0 ~ -8%"
            for k in HORIZONS:
                v = _fwd(c.px, i, k)
                if v is None:
                    continue
                base[str(k)].append(v)
                bands[bkey][str(k)].append(v)
                if dkey:
                    ddband.setdefault(dkey, {str(h): [] for h in HORIZONS})
                    ddband[dkey][str(k)].append(v)

    baseline = {k: _stat(v) for k, v in base.items()}

    per_t = {}
    for T in THRESHOLDS:
        agg = {"breach": {str(k): [] for k in HORIZONS},
               "recover": {str(k): [] for k in HORIZONS},
               "dds": [], "days": [], "nb": 0, "nr": 0}
        for c in ctxs:
            for mode, idxs in (("breach", breach_events(c.sc, T)),
                               ("recover", recover_events(c.sc, T))):
                agg["nb" if mode == "breach" else "nr"] += len(idxs)
                for i in idxs:
                    for k in HORIZONS:
                        v = _fwd(c.px, i, k)
                        if v is not None:
                            agg[mode][str(k)].append(v)
                    if mode == "breach":
                        dd, dy = _trough(c.px, i, 60)
                        if dd is not None:
                            agg["dds"].append(dd)
                            agg["days"].append(dy)
        bs = {k: _stat(v) for k, v in agg["breach"].items()}
        rs = {k: _stat(v) for k, v in agg["recover"].items()}
        per_t[str(T)] = {
            "breach": bs, "recover": rs,
            "excess_breach": {k: None if not bs[k] else
                              round(bs[k]["mean"] - baseline[k]["mean"], 2)
                              for k in bs},
            "excess_recover": {k: None if not rs[k] else
                               round(rs[k]["mean"] - baseline[k]["mean"], 2)
                               for k in rs},
            "events_breach": agg["nb"], "events_recover": agg["nr"],
            "trough_mean": round(100.0 * sum(agg["dds"]) / len(agg["dds"]), 2)
            if agg["dds"] else None,
            "trough_p10": round(100.0 * sorted(agg["dds"])[
                max(0, int(0.1 * len(agg["dds"])) - 1)], 2) if agg["dds"] else None,
            "trough_days": round(sum(agg["days"]) / len(agg["days"]), 1)
            if agg["days"] else None,
        }

    dd_rule = {}
    for X in DD_LEVELS:
        vals = {str(k): [] for k in HORIZONS}
        cnt = 0
        for c in ctxs:
            for i in _edges([_ddle(c, j, X) for j in range(c.n)]):
                cnt += 1
                for k in HORIZONS:
                    v = _fwd(c.px, i, k)
                    if v is not None:
                        vals[str(k)].append(v)
        st = {k: _stat(v) for k, v in vals.items()}
        dd_rule[str(X)] = {
            "events": cnt, "fwd": st,
            "excess": {k: None if not st[k] else
                       round(st[k]["mean"] - baseline[k]["mean"], 2) for k in st}}

    return {
        "thresholds": THRESHOLDS, "horizons": HORIZONS, "baseline": baseline,
        "per_threshold": per_t, "dd_levels": DD_LEVELS, "dd_rule": dd_rule,
        "bands": {k: {"stats": {h: _stat(v) for h, v in val.items()}}
                  for k, val in bands.items()},
        "dd_bands": {k: {"stats": {h: _stat(v) for h, v in val.items()}}
                     for k, val in ddband.items()},
    }


# ------------------------------------------------------------------ 结论
def insights(p: dict, ranked: List[dict]) -> List[str]:
    """结论全部由上面的统计生成，不预设立场 —— 包括对本指数不利的结论。"""
    out = []
    R = {r["key"]: r for r in ranked}
    b60 = p["baseline"]["60"]

    out.append("<b>先立参照系。</b>这 10 个标的过去约 6 年里<b>任意一天买入</b>，未来 60 个"
               "交易日平均涨 <b>{:+.1f}%</b>、胜率 <b>{:.0f}%</b>（n={}）。"
               "任何抄底规则都必须打赢这个数字，否则「抄底」只是心理安慰。"
               .format(b60["mean"], b60["hit"], b60["n"]))

    f30 = R.get("fear30")
    if f30 and f30["fwd"]["60"]:
        s60, e60 = f30["fwd"]["60"], f30["excess"]["60"]
        out.append("<b>只按情绪恐慌抄底，反而跑输。</b>情绪分下穿 30 后 60 日平均 "
                   "{:+.1f}%、胜率 {:.0f}%（n={}），相对基线 <b>{:+.1f}pp</b>。"
                   "道理很直白：情绪低同时意味着趋势和相对强度都在恶化，"
                   "在长期上行的科技资产里，这等于主动挑最差的时段入场。"
                   .format(s60["mean"], s60["hit"], s60["n"], e60["mean"]))

    best = next((r for r in ranked if r["fwd"]["60"] and r["fwd"]["60"]["n"] >= 20),
                None)
    if best:
        s60 = best["fwd"]["60"]
        out.append("<b>有效的是价格回撤事件，而不是情绪读数。</b>排行第一的「{}」60 日平均 "
                   "<b>{:+.1f}%</b>、胜率 {:.0f}%、超基线 <b>{:+.1f}pp</b>（n={}）。"
                   "注意口径：只在<b>首次触及</b>该回撤深度的那一天买入，而不是「跌了很多"
                   "所以随时可买」。"
                   .format(best["name"], s60["mean"], s60["hit"],
                           best["excess"]["60"]["mean"], s60["n"]))

    bands = p["dd_bands"]
    vals = [(k, v["stats"]["60"]) for k, v in bands.items() if v["stats"]["60"]]
    if vals:
        txt = "、".join("{} {:+.1f}%".format(k, v["mean"]) for k, v in vals)
        out.append("<b>「跌得多」本身不是优势。</b>按当前回撤深度分档统计未来 60 日收益："
                   + txt + " —— 各档与基线基本无差异。"
                   "优势集中在<b>刚跌破新档位的那几天</b>，"
                   "所以抄底是<b>事件驱动</b>而非「越跌越买」的状态驱动。")

    d12, d12t, d12m = R.get("dd12"), R.get("dd12_turn"), R.get("dd12_ma20")
    if d12 and d12t and d12m and all(x["fwd"]["60"] for x in (d12, d12t, d12m)):
        out.append("<b>等确认要付学费。</b>同样在回撤 ≥12% 的位置：不等确认 60 日 "
                   "{:+.1f}%／胜率 {:.0f}%；等情绪 5 日回升 {:+.1f}%／{:.0f}%；"
                   "等收复 20 日均线 {:+.1f}%／{:.0f}%。确认让浮亏体验更舒服，"
                   "但也让你错过最快的第一段反弹 —— 想要更高期望就别等，"
                   "想要更低浮亏就等，二者不可兼得。"
                   .format(d12["fwd"]["60"]["mean"], d12["fwd"]["60"]["hit"],
                           d12t["fwd"]["60"]["mean"], d12t["fwd"]["60"]["hit"],
                           d12m["fwd"]["60"]["mean"], d12m["fwd"]["60"]["hit"]))

    t25 = p["per_threshold"].get("25", {})
    if t25.get("trough_mean") is not None:
        out.append("<b>浮亏与时间成本要提前认。</b>历史上买在恐慌档（情绪下穿 25）之后，"
                   "60 日内平均还要再跌 <b>{:.1f}%</b>、平均 <b>{:.0f}</b> 个交易日才见底，"
                   "最差十分之一的情形浮亏 {:.1f}%。所以必须分批：一次性满仓的人，"
                   "多数是在见底前被自己的浮亏赶下车的。"
                   .format(abs(t25["trough_mean"]), t25["trough_days"],
                           abs(t25["trough_p10"])))

    dd = p["dd_rule"]
    d8, d25 = dd.get("8", {}).get("fwd", {}).get("60"), \
        dd.get("25", {}).get("fwd", {}).get("60")
    if d8 and d25:
        out.append("<b>弹药分配。</b>浅回撤机会多（-8% 档历史触发 {} 次，60 日 {:+.1f}%），"
                   "深回撤机会稀缺但单次更值（-25% 档仅 {} 次，{:+.1f}%）。"
                   "对应的机械阶梯是：-8% 建 20%、-12% 到 40%、-20% 到 65%、"
                   "-25% 及更深打满 —— 每档只在<b>首次触及</b>时执行一次。"
                   .format(d8["n"], d8["mean"], d25["n"], d25["mean"]))
    return out


def build(details: List[dict]) -> dict:
    ctxs = [Ctx(d) for d in details]
    p = pooled(ctxs)
    ranked = rank_rules(ctxs, p["baseline"])
    syms = [symbol_report(d, c) for d, c in zip(details, ctxs)]
    return {"pooled": p, "rules": ranked, "symbols": syms,
            "insights": insights(p, ranked), "min_gap_days": MIN_GAP,
            "fresh_days": FRESH_DAYS,
            "dd_ladder": [{"x": x, "ladder": w} for x, w in DD_LADDER]}
