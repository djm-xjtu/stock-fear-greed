"""
多因子个股情绪指数（Multi-factor Single-Stock Fear & Greed Index）

设计目标：修正 greedyfear.com 只用「52周区间位置」的三个缺陷
  1. 信息量单一 —— 只含收盘价，无波动率 / 成交量 / 相对强度
  2. 记忆污染 —— 单根异常长影线永久扭曲 high52/low52
  3. 不可跨股比较 —— 高波动股与低波动股共用同一线性尺度

方法：7 个子因子，每个先算出「原始统计量」，再用该股票**自身**过去 2 年
      同一统计量的分布做滚动百分位（rolling percentile rank），映射到 0-100。
      自归一化 => 高波动股和低波动股的同一分数含义一致，可横向比较。
      滚动窗口只用历史数据 => 无未来函数（no lookahead bias）。

纯标准库实现（Python 3.8 兼容），无 numpy / pandas 依赖。
"""

import math
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------- 基础工具


def _sma(xs: Sequence[Optional[float]], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(xs)
    acc = 0.0
    cnt = 0
    for i, v in enumerate(xs):
        if v is None:
            continue
        acc += v
        cnt += 1
        if i >= n:
            old = xs[i - n]
            if old is not None:
                acc -= old
                cnt -= 1
        if cnt >= n:
            out[i] = acc / cnt
    return out


def _stdev(window: Sequence[float]) -> float:
    n = len(window)
    if n < 2:
        return 0.0
    m = sum(window) / n
    var = sum((x - m) ** 2 for x in window) / (n - 1)
    return math.sqrt(max(var, 0.0))


def _percentile_rank(window: Sequence[float], x: float) -> float:
    """x 在 window 分布中的百分位（0-100）。用 <= 计数，含并列一半修正。"""
    n = len(window)
    if n == 0:
        return 50.0
    below = 0
    equal = 0
    for v in window:
        if v < x:
            below += 1
        elif v == x:
            equal += 1
    return 100.0 * (below + 0.5 * equal) / n


def _rolling_pct(series: List[Optional[float]], window: int) -> List[Optional[float]]:
    """对每个时点，用其之前 window 个有效值的分布做百分位打分。"""
    out: List[Optional[float]] = [None] * len(series)
    for i, v in enumerate(series):
        if v is None:
            continue
        lo = max(0, i - window)
        hist = [s for s in series[lo:i] if s is not None]
        if len(hist) < max(60, window // 4):
            continue
        out[i] = _percentile_rank(hist, v)
    return out


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- 因子计算

TRADING_DAYS = 252
PCT_WINDOW = 504          # 2 年滚动百分位窗口
LOOKBACK_52W = 252
VOL_WIN = 21
MA_WIN = 125
RSI_WIN = 14
CMF_WIN = 21
RS_WIN = 60

WEIGHTS = {
    "momentum": 0.20,     # 波动率调整后的趋势强度
    "rel_strength": 0.18,  # 相对大盘超额收益
    "volatility": 0.16,   # 已实现波动率（反向）
    "money_flow": 0.14,   # 资金流
    "drawdown": 0.12,     # 距 52 周高点的 ATR 归一化回撤
    "range_pos": 0.10,    # 52 周区间位置（greedyfear 的唯一因子）
    "rsi": 0.10,          # 相对强弱指标
}

FACTOR_META = {
    "momentum": {
        "name": "趋势动量",
        "en": "Momentum",
        "desc": "价格相对 125 日均线的偏离，除以同期已实现波动率。波动率调整让高波动股与低波动股可比。",
        "raw_fmt": "{:+.2f}σ",
    },
    "rel_strength": {
        "name": "相对强度",
        "en": "Relative Strength",
        "desc": "过去 60 交易日相对 SPY 的超额收益。剥离大盘系统性涨跌，只留个股特有情绪。",
        "raw_fmt": "{:+.1%}",
    },
    "volatility": {
        "name": "下行波动（反向）",
        "en": "Downside Vol (inv.)",
        "desc": "21 日年化下行半标准差（只统计负收益），取反向映射。相比总波动率，"
                "它不会把财报跳空大涨误判成恐慌 —— 恐慌只体现在下跌的剧烈程度上。",
        "raw_fmt": "{:.1%}",
    },
    "money_flow": {
        "name": "资金流",
        "en": "Money Flow",
        "desc": "21 日蔡金资金流量（CMF）：用收盘价在日内区间的位置给成交量加权符号，衡量买卖压力。",
        "raw_fmt": "{:+.3f}",
    },
    "drawdown": {
        "name": "回撤压力",
        "en": "Drawdown Stress",
        "desc": "距 52 周高点的距离，用 ATR20 归一化。回答「跌了多少个真实波幅」而非「跌了多少百分比」。",
        "raw_fmt": "{:.1f} ATR",
    },
    "range_pos": {
        "name": "52周区间位置",
        "en": "52w Range Position",
        "desc": "greedyfear.com 使用的唯一因子。此处仅作 10% 权重的参考项保留，便于对照。",
        "raw_fmt": "{:.0f}/100",
    },
    "rsi": {
        "name": "RSI(14)",
        "en": "RSI(14)",
        "desc": "威尔德相对强弱指标，捕捉短周期超买 / 超卖状态。",
        "raw_fmt": "{:.1f}",
    },
}


def compute_factors(bars: Dict[str, List[float]],
                    bench_close: List[Optional[float]]) -> Dict[str, object]:
    """bars: {'ts','open','high','low','close','volume'} 已对齐清洗。"""
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    vol = bars["volume"]
    n = len(close)

    # --- 日收益 & 已实现波动率
    ret: List[Optional[float]] = [None] * n
    for i in range(1, n):
        if close[i - 1]:
            ret[i] = close[i] / close[i - 1] - 1.0

    rvol: List[Optional[float]] = [None] * n
    dvol: List[Optional[float]] = [None] * n     # 下行半标准差
    for i in range(n):
        if i < VOL_WIN:
            continue
        w = [r for r in ret[i - VOL_WIN + 1: i + 1] if r is not None]
        if len(w) >= VOL_WIN - 2:
            rvol[i] = _stdev(w) * math.sqrt(TRADING_DAYS)
            neg = [r for r in w if r < 0]
            # 半标准差：分母仍用全样本长度，避免「跌得少但每次都跌」被高估
            ss = sum(r * r for r in neg) / len(w)
            dvol[i] = math.sqrt(ss) * math.sqrt(TRADING_DAYS)

    # 60 日总波动率：仅用于动量的量纲归一化（比 21 日更稳，不易被单日跳空扰动）
    rvol60: List[Optional[float]] = [None] * n
    for i in range(n):
        if i < 60:
            continue
        w = [r for r in ret[i - 59: i + 1] if r is not None]
        if len(w) >= 55:
            rvol60[i] = _stdev(w) * math.sqrt(TRADING_DAYS)

    # --- 125 日均线 & 波动率调整动量
    ma = _sma(close, MA_WIN)
    momz: List[Optional[float]] = [None] * n
    for i in range(n):
        if ma[i] and rvol60[i] and rvol60[i] > 1e-9:
            dev = close[i] / ma[i] - 1.0
            momz[i] = dev / (rvol60[i] * math.sqrt(MA_WIN / TRADING_DAYS))

    # --- ATR20
    tr: List[Optional[float]] = [None] * n
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
    atr = _sma(tr, 20)

    # --- 52 周高低点
    hi52: List[Optional[float]] = [None] * n
    lo52: List[Optional[float]] = [None] * n
    for i in range(n):
        if i < LOOKBACK_52W - 1:
            continue
        seg_h = high[i - LOOKBACK_52W + 1: i + 1]
        seg_l = low[i - LOOKBACK_52W + 1: i + 1]
        hi52[i] = max(seg_h)
        lo52[i] = min(seg_l)

    # --- 回撤压力（ATR 归一化，负值，越接近 0 越贪婪）
    ddatr: List[Optional[float]] = [None] * n
    for i in range(n):
        if hi52[i] and atr[i] and atr[i] > 1e-9:
            ddatr[i] = (close[i] - hi52[i]) / atr[i]

    # --- 52 周区间位置（greedyfear 口径，用收盘价高低点更稳健地对照）
    rangepos: List[Optional[float]] = [None] * n
    for i in range(n):
        if hi52[i] is not None and lo52[i] is not None and hi52[i] > lo52[i]:
            rangepos[i] = _clamp((close[i] - lo52[i]) / (hi52[i] - lo52[i]) * 100.0)

    # --- RSI(14) 威尔德平滑
    rsi: List[Optional[float]] = [None] * n
    gain = loss = 0.0
    for i in range(1, n):
        ch = close[i] - close[i - 1]
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        if i <= RSI_WIN:
            gain += g
            loss += l
            if i == RSI_WIN:
                gain /= RSI_WIN
                loss /= RSI_WIN
                rsi[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
        else:
            gain = (gain * (RSI_WIN - 1) + g) / RSI_WIN
            loss = (loss * (RSI_WIN - 1) + l) / RSI_WIN
            rsi[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)

    # --- CMF(21) 蔡金资金流量
    mfv: List[Optional[float]] = [None] * n
    for i in range(n):
        rng = high[i] - low[i]
        if rng > 1e-12:
            mfv[i] = ((close[i] - low[i]) - (high[i] - close[i])) / rng * vol[i]
        else:
            mfv[i] = 0.0
    cmf: List[Optional[float]] = [None] * n
    for i in range(n):
        if i < CMF_WIN - 1:
            continue
        vs = sum(vol[i - CMF_WIN + 1: i + 1])
        if vs > 0:
            cmf[i] = sum(m or 0.0 for m in mfv[i - CMF_WIN + 1: i + 1]) / vs

    # --- 相对 SPY 的 60 日超额收益
    excess: List[Optional[float]] = [None] * n
    for i in range(RS_WIN, n):
        b0, b1 = bench_close[i - RS_WIN], bench_close[i]
        if b0 and b1 and close[i - RS_WIN]:
            excess[i] = (close[i] / close[i - RS_WIN] - 1.0) - (b1 / b0 - 1.0)

    # --- 滚动百分位打分
    s_mom = _rolling_pct(momz, PCT_WINDOW)
    s_rs = _rolling_pct(excess, PCT_WINDOW)
    s_vol_raw = _rolling_pct(dvol, PCT_WINDOW)
    s_vol = [None if v is None else 100.0 - v for v in s_vol_raw]   # 反向
    s_flow = _rolling_pct(cmf, PCT_WINDOW)
    s_dd = _rolling_pct(ddatr, PCT_WINDOW)
    s_range = rangepos                     # 已是 0-100 有界
    s_rsi = rsi                            # 已是 0-100 有界

    scores = {
        "momentum": s_mom,
        "rel_strength": s_rs,
        "volatility": s_vol,
        "money_flow": s_flow,
        "drawdown": s_dd,
        "range_pos": s_range,
        "rsi": s_rsi,
    }
    raws = {
        "momentum": momz,
        "rel_strength": excess,
        "volatility": dvol,
        "money_flow": cmf,
        "drawdown": ddatr,
        "range_pos": rangepos,
        "rsi": rsi,
    }

    # --- 合成（权重按可用因子重新归一化，缺失因子不拖累）
    composite: List[Optional[float]] = [None] * n
    for i in range(n):
        wsum = 0.0
        acc = 0.0
        for k, w in WEIGHTS.items():
            v = scores[k][i]
            if v is not None:
                acc += w * v
                wsum += w
        # 至少要有 80% 权重的因子可用才出分
        if wsum >= 0.8:
            composite[i] = acc / wsum

    return {"scores": scores, "raws": raws, "composite": composite,
            "hi52": hi52, "lo52": lo52, "rvol": rvol, "dvol": dvol, "atr": atr}


def label_of(score: float) -> str:
    if score <= 20:
        return "极度恐慌"
    if score <= 40:
        return "恐慌"
    if score <= 60:
        return "中性"
    if score <= 80:
        return "贪婪"
    return "极度贪婪"


def label_en(score: float) -> str:
    if score <= 20:
        return "Extreme Fear"
    if score <= 40:
        return "Fear"
    if score <= 60:
        return "Neutral"
    if score <= 80:
        return "Greed"
    return "Extreme Greed"
