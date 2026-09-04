import asyncio
import gzip
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from engine import (FACTOR_META, PCT_WINDOW, WEIGHTS, compute_factors, label_en,
                    label_of)

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("sentiment")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
BENCHMARK = "SPY"
DEFAULT_UNIVERSE = ["QQQ", "QLD", "VGT", "SOXX", "SMH", "USD", "TSM", "NVDA",
                    "SKHY", "AVGO", "ANET", "MU", "GOOGL", "META", "GLD"]

NAME_MAP = {
    "QQQ": "Invesco QQQ Trust · 纳指100",
    "QLD": "ProShares Ultra QQQ · 2倍做多纳指100",
    "VGT": "Vanguard Information Technology ETF · 信息技术ETF",
    "SOXX": "iShares Semiconductor ETF · 费半",
    "SMH": "VanEck Semiconductor ETF · 半导体ETF",
    "USD": "ProShares Ultra Semiconductors · 2倍做多半导体ETF",
    "TSM": "台积电 TSMC ADR",
    "NVDA": "英伟达 NVIDIA",
    "SKHY": "SK hynix（历史用韩股 000660.KS）",
    "AVGO": "博通 Broadcom",
    "ANET": "Arista Networks",
    "MU": "美光科技 Micron",
    "GOOGL": "Alphabet（谷歌）",
    "META": "Meta Platforms",
    "GLD": "SPDR Gold Shares · 黄金ETF",
    "SPY": "SPDR S&P 500 ETF Trust",
}

CALC_TTL = 60 * 20
LIVE_TTL = 60 * 10

_snapshots: Dict[str, dict] = {}
_live_cache: Dict[str, Tuple[float, Optional[dict]]] = {}
_calc_cache: Dict[str, Tuple[float, dict]] = {}
_locks: Dict[str, asyncio.Lock] = {}


def _lock(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


# --------------------------------------------------------------- 快照数据

def load_snapshot(symbol: str) -> Optional[dict]:
    """读取随服务打包的 8 年日线快照（离线兜底 / 主数据源）。"""
    key = symbol.upper()
    if key in _snapshots:
        return _snapshots[key]
    path = os.path.join(DATA_DIR, key + ".json.gz")
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt") as fh:
            rec = json.load(fh)
    except Exception as exc:                              # noqa: BLE001
        LOGGER.warning("snapshot load failed %s: %s", key, exc)
        return None
    b = rec["bars"]
    out = {
        "symbol": rec.get("symbol", key).upper(),
        "name": NAME_MAP.get(key, rec.get("name") or key),
        "currency": rec.get("currency", "USD"),
        "exchange": rec.get("exchange", ""),
        "bars": {"d": b["d"], "open": b["o"], "high": b["h"], "low": b["l"],
                 "close": b["c"], "volume": b["v"]},
        "source": "snapshot",
    }
    _snapshots[key] = out
    return out


def available_symbols() -> List[str]:
    if not os.path.isdir(DATA_DIR):
        return []
    return sorted(f[:-8] for f in os.listdir(DATA_DIR) if f.endswith(".json.gz"))


# --------------------------------------------------------------- 实时数据

async def fetch_live(client: httpx.AsyncClient, symbol: str,
                     rng: str = "8y") -> Optional[dict]:
    """尝试从 Yahoo 拉取最新行情。失败返回 None（由快照兜底）。"""
    key = symbol.upper()
    now = time.time()
    hit = _live_cache.get(key)
    if hit and now - hit[0] < LIVE_TTL:
        return hit[1]

    for host in YAHOO_HOSTS:
        try:
            r = await client.get("{}/v8/finance/chart/{}".format(host, key),
                                 params={"range": rng, "interval": "1d"},
                                 headers={"User-Agent": UA,
                                          "Accept": "application/json"})
            if r.status_code != 200:
                continue
            payload = r.json()
        except Exception:                                 # noqa: BLE001
            continue
        chart = (payload or {}).get("chart") or {}
        results = chart.get("result") or []
        if chart.get("error") or not results:
            continue
        res = results[0]
        meta = res.get("meta") or {}
        ts = res.get("timestamp") or []
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        o, h, l, c, v = (q.get("open") or [], q.get("high") or [],
                         q.get("low") or [], q.get("close") or [],
                         q.get("volume") or [])
        bars = {"d": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        for i in range(min(len(ts), len(c))):
            if c[i] is None or h[i] is None or l[i] is None or o[i] is None:
                continue
            bars["d"].append(time.strftime("%Y-%m-%d", time.gmtime(int(ts[i]))))
            bars["open"].append(float(o[i]))
            bars["high"].append(float(h[i]))
            bars["low"].append(float(l[i]))
            bars["close"].append(float(c[i]))
            bars["volume"].append(float(v[i]) if i < len(v) and v[i] is not None else 0.0)
        if len(bars["close"]) < 30:
            continue
        out = {
            "symbol": meta.get("symbol", key),
            "name": NAME_MAP.get(key) or meta.get("shortName") or key,
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("fullExchangeName", ""),
            "price": meta.get("regularMarketPrice"),
            "bars": bars,
            "source": "yahoo",
        }
        _live_cache[key] = (now, out)
        return out

    _live_cache[key] = (now, None)
    return None


def merge_bars(snap: Optional[dict], live: Optional[dict]) -> dict:
    """以快照为基底，用实时数据覆盖 / 追加尾部。"""
    if snap is None and live is None:
        raise HTTPException(status_code=502, detail="无可用行情数据")
    if snap is None:
        return live
    if live is None:
        return snap

    base = {k: list(v) for k, v in snap["bars"].items()}
    idx = {d: i for i, d in enumerate(base["d"])}
    lb = live["bars"]
    for i, d in enumerate(lb["d"]):
        row = (lb["open"][i], lb["high"][i], lb["low"][i], lb["close"][i],
               lb["volume"][i])
        if d in idx:
            j = idx[d]
            base["open"][j], base["high"][j], base["low"][j] = row[0], row[1], row[2]
            base["close"][j], base["volume"][j] = row[3], row[4]
        elif not base["d"] or d > base["d"][-1]:
            base["d"].append(d)
            base["open"].append(row[0])
            base["high"].append(row[1])
            base["low"].append(row[2])
            base["close"].append(row[3])
            base["volume"].append(row[4])
            idx[d] = len(base["d"]) - 1

    out = dict(snap)
    out["bars"] = base
    out["price"] = live.get("price")
    out["source"] = "yahoo+snapshot"
    return out


def _align_bench(sym_dates: List[str], bench: dict) -> List[Optional[float]]:
    bmap = dict(zip(bench["bars"]["d"], bench["bars"]["close"]))
    out: List[Optional[float]] = []
    last = None
    for d in sym_dates:
        v = bmap.get(d)
        if v is not None:
            last = v
        out.append(last)
    return out


# --------------------------------------------------------------- 组装结果

def _fmt_raw(key: str, val: Optional[float]) -> str:
    if val is None:
        return "—"
    try:
        return FACTOR_META[key]["raw_fmt"].format(val)
    except Exception:                                     # noqa: BLE001
        return "{:.2f}".format(val)


def build_detail(sym_raw: dict, bench_raw: dict, history_days: int = 900) -> dict:
    bars = sym_raw["bars"]
    bench_close = _align_bench(bars["d"], bench_raw)
    f = compute_factors(bars, bench_close)

    comp = f["composite"]
    idx = None
    for i in range(len(comp) - 1, -1, -1):
        if comp[i] is not None:
            idx = i
            break
    if idx is None:
        raise HTTPException(
            status_code=422,
            detail="{} 历史数据不足，至少需要约 3 年日线".format(sym_raw["symbol"]))

    close_now = bars["close"][idx]
    live_price = sym_raw.get("price") or close_now
    prev = bars["close"][idx - 1] if idx > 0 else close_now
    change_pct = (live_price / prev - 1.0) * 100.0 if prev else 0.0

    factors = []
    for key, w in sorted(WEIGHTS.items(), key=lambda kv: -kv[1]):
        sc = f["scores"][key][idx]
        rw = f["raws"][key][idx]
        meta = FACTOR_META[key]
        factors.append({
            "key": key, "name": meta["name"], "name_en": meta["en"],
            "desc": meta["desc"], "weight": round(w * 100, 1),
            "score": None if sc is None else round(sc, 1),
            "label": None if sc is None else label_of(sc),
            "raw": None if rw is None else round(rw, 6),
            "raw_text": _fmt_raw(key, rw),
        })

    start = max(0, idx + 1 - history_days)
    hist = []
    for i in range(start, idx + 1):
        if comp[i] is None:
            continue
        naive = f["scores"]["range_pos"][i]
        hist.append({"d": bars["d"][i], "c": round(comp[i], 1),
                     "naive": None if naive is None else round(naive, 1),
                     "close": round(bars["close"][i], 2)})

    keys = list(WEIGHTS.keys())
    step = max(1, (idx + 1 - start) // 260)
    fac_hist = {"d": []}
    for k in keys:
        fac_hist[k] = []
    for i in range(start, idx + 1, step):
        if comp[i] is None:
            continue
        fac_hist["d"].append(bars["d"][i])
        for k in keys:
            v = f["scores"][k][i]
            fac_hist[k].append(None if v is None else round(v, 1))

    score = round(comp[idx], 1)
    naive_now = f["scores"]["range_pos"][idx]
    naive_now = None if naive_now is None else round(naive_now, 1)

    prev_score = None
    for j in range(max(0, idx - 20), idx):
        if comp[j] is not None:
            prev_score = round(comp[j], 1)
            break

    # 分数在自身历史中的分位，回答「现在这个情绪相对该股自己算不算极端」
    hist_scores = [c for c in comp[max(0, idx - PCT_WINDOW):idx] if c is not None]
    self_pct = None
    if hist_scores:
        below = sum(1 for x in hist_scores if x < comp[idx])
        self_pct = round(100.0 * below / len(hist_scores), 1)

    return {
        "symbol": sym_raw["symbol"], "name": sym_raw["name"],
        "currency": sym_raw.get("currency", "USD"),
        "exchange": sym_raw.get("exchange", ""),
        "data_source": sym_raw.get("source", "snapshot"),
        "price": round(float(live_price), 2),
        "change_pct": round(change_pct, 2),
        "as_of": bars["d"][idx],
        "score": score, "label": label_of(score), "label_en": label_en(score),
        "score_20d_ago": prev_score, "self_percentile": self_pct,
        "naive_range_score": naive_now,
        "high52": round(f["hi52"][idx], 2) if f["hi52"][idx] else None,
        "low52": round(f["lo52"][idx], 2) if f["lo52"][idx] else None,
        "realized_vol": round(f["rvol"][idx] * 100, 1) if f["rvol"][idx] else None,
        "downside_vol": round(f["dvol"][idx] * 100, 1) if f["dvol"][idx] else None,
        "factors": factors, "history": hist, "factor_history": fac_hist,
        "bars_used": len(bars["close"]),
    }


async def get_detail(symbol: str) -> dict:
    key = symbol.upper()
    now = time.time()
    hit = _calc_cache.get(key)
    if hit and now - hit[0] < CALC_TTL:
        return hit[1]

    async with _lock(key):
        hit = _calc_cache.get(key)
        if hit and time.time() - hit[0] < CALC_TTL:
            return hit[1]

        snap = load_snapshot(key)
        bench_snap = load_snapshot(BENCHMARK)
        async with httpx.AsyncClient(timeout=8.0, trust_env=False,
                                     follow_redirects=True) as client:
            live, bench_live = await asyncio.gather(
                fetch_live(client, key), fetch_live(client, BENCHMARK))

        if snap is None and live is None:
            raise HTTPException(
                status_code=404,
                detail="未找到 {} 的行情数据（数据源限流或代码无效）".format(key))

        sym_raw = merge_bars(snap, live)
        bench_raw = merge_bars(bench_snap, bench_live)
        loop = asyncio.get_event_loop()
        detail = await loop.run_in_executor(None, build_detail, sym_raw, bench_raw)
        _calc_cache[key] = (time.time(), detail)
        return detail


# --------------------------------------------------------------- 路由

app = FastAPI(title="Single-Stock Sentiment Lab",
              docs_url="/api/docs", redoc_url="/api/redoc",
              openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.get("/api")
def index_handler():
    return {"service": "single-stock-sentiment-lab", "ok": True,
            "symbols": available_symbols()}


@app.get("/api/v1/ping")
async def ping_handler():
    return "Ping healthcheck"


@app.get("/api/methodology")
async def methodology():
    return {
        "weights": {k: round(v * 100, 1) for k, v in WEIGHTS.items()},
        "factors": [dict([("key", k)] + list(FACTOR_META[k].items()))
                    for k in WEIGHTS],
        "pct_window_days": PCT_WINDOW,
        "benchmark": BENCHMARK,
        "universe": DEFAULT_UNIVERSE,
        "bands": [
            {"max": 20, "zh": "极度恐慌", "en": "Extreme Fear"},
            {"max": 40, "zh": "恐慌", "en": "Fear"},
            {"max": 60, "zh": "中性", "en": "Neutral"},
            {"max": 80, "zh": "贪婪", "en": "Greed"},
            {"max": 100, "zh": "极度贪婪", "en": "Extreme Greed"},
        ],
    }


@app.get("/api/universe")
async def universe(symbols: Optional[str] = Query(None)):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] \
        if symbols else list(DEFAULT_UNIVERSE)
    syms = syms[:16]
    results = await asyncio.gather(*[get_detail(s) for s in syms],
                                   return_exceptions=True)
    items, errors = [], []
    for s, r in zip(syms, results):
        if isinstance(r, Exception):
            errors.append({"symbol": s, "error": str(getattr(r, "detail", r))})
            continue
        items.append({
            "symbol": r["symbol"], "name": r["name"], "price": r["price"],
            "currency": r.get("currency", "USD"),
            "change_pct": r["change_pct"], "score": r["score"],
            "label": r["label"], "label_en": r["label_en"],
            "naive_range_score": r["naive_range_score"],
            "score_20d_ago": r["score_20d_ago"],
            "self_percentile": r["self_percentile"],
            "realized_vol": r["realized_vol"],
            "downside_vol": r["downside_vol"], "as_of": r["as_of"],
            "data_source": r["data_source"],
            "factors": [{"key": f["key"], "name": f["name"], "score": f["score"],
                         "raw_text": f["raw_text"]} for f in r["factors"]],
            "spark": [h["c"] for h in r["history"][-120:]],
        })
    items.sort(key=lambda x: -(x["score"] if x["score"] is not None else 0))
    return {"items": items, "errors": errors, "generated_at": int(time.time())}


@app.get("/api/validation")
async def validation(fwd: int = Query(20, ge=5, le=60)):
    """样本内验证：把两套指数按分档统计未来 fwd 日相对 SPY 的超额收益 + 分档占比。

    目的不是宣称择时能力，而是回答两个可检验的问题：
      1. 分布是否健康 —— 一个半辈子待在「极度贪婪」的指数没有区分度
      2. 极端读数是否携带信息 —— 极端档的未来超额收益 / 胜率是否显著异于中间档
    """
    key = "validation_{}".format(fwd)
    now = time.time()
    hit = _calc_cache.get(key)
    if hit and now - hit[0] < 3600:
        return hit[1]

    async with _lock(key):
        hit = _calc_cache.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]

        def _run():
            bench = load_snapshot(BENCHMARK)
            bmap = dict(zip(bench["bars"]["d"], bench["bars"]["close"]))
            bands = ["0-20", "20-40", "40-60", "60-80", "80-100"]

            def band_of(x):
                return bands[min(4, int(x // 20))]

            acc = {}
            for s in DEFAULT_UNIVERSE:
                snap = load_snapshot(s)
                if snap is None:
                    continue
                d = build_detail(snap, bench, history_days=1400)
                h = d["history"]
                dates = [x["d"] for x in h]
                close = [x["close"] for x in h]
                for i in range(len(h) - fwd):
                    b0, b1 = bmap.get(dates[i]), bmap.get(dates[i + fwd])
                    if not b0 or not b1:
                        continue
                    exc = (close[i + fwd] / close[i] - 1.0) - (b1 / b0 - 1.0)
                    for tag, val in (("multi", h[i]["c"]), ("naive", h[i]["naive"])):
                        if val is None:
                            continue
                        acc.setdefault((tag, band_of(val)), []).append(exc)

            out = {}
            for tag in ("multi", "naive"):
                total = sum(len(acc.get((tag, b), [])) for b in bands)
                rows = []
                for b in bands:
                    v = acc.get((tag, b), [])
                    rows.append({
                        "band": b, "n": len(v),
                        "share": round(100.0 * len(v) / total, 1) if total else 0.0,
                        "mean_excess": round(100.0 * sum(v) / len(v), 2) if v else None,
                        "hit_rate": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)
                        if v else None,
                    })
                shares = [r["share"] for r in rows]
                out[tag] = {
                    "rows": rows, "total": total,
                    # 分布集中度：最大档占比越高 => 区分度越差
                    "max_band_share": max(shares) if shares else None,
                }
            return {"fwd_days": fwd, "universe": DEFAULT_UNIVERSE,
                    "benchmark": BENCHMARK, **out}

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, _run)
        _calc_cache[key] = (time.time(), res)
        return res


@app.get("/api/dip")
async def dip_signals():
    """抄底提示：情绪阈值 / 反转确认 / 回撤三类规则的历史效力 + 当前红绿灯。"""
    key = "dip_v1"
    hit = _calc_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]

    async with _lock(key):
        hit = _calc_cache.get(key)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]

        def _run():
            import dip
            bench = load_snapshot(BENCHMARK)
            details = []
            for s in DEFAULT_UNIVERSE:
                snap = load_snapshot(s)
                if snap is None:
                    continue
                details.append(build_detail(snap, bench, history_days=2400))
            return dip.build(details)

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, _run)
        _calc_cache[key] = (time.time(), res)
        return res


@app.get("/api/stock/{symbol}")
async def stock(symbol: str):
    if not symbol or len(symbol) > 12:
        raise HTTPException(status_code=400, detail="非法代码")
    return await get_detail(symbol)


# ------------------------------------------------------------------ 入口
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config("main:app", port=port, log_level="info", host=None)
    server = uvicorn.Server(config)
    server.run()

