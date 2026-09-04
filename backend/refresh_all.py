#!/usr/bin/env python3
"""每日一键更新：拉最新日线 -> 合并进 8 年快照 -> 重算静态 JSON。

用法：
    python3 sentiment-lab/backend/refresh_all.py

之后再执行 deploy_frontend 部署 sentiment-lab/frontend 即可（建议 stable_domain=true）。

数据源优先用 yfinance 包（pip install yfinance）；若环境里存在内部 yfinance 代理脚本
（inner_skills/yfinance/...），则回落到该脚本，便于在受限网络中使用。
"""
import datetime as dt
import glob
import gzip
import json
import math
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(HERE, "data")
MCP = os.path.join(ROOT, "inner_skills", "yfinance",
                   "mcp_yfinance_yahoo_finance.py")

# 展示 ticker -> 数据源 ticker（SK hynix 美股 SKHY 上市太晚，历史用韩股主上市）
SYMBOLS = ["SPY", "QQQ", "QLD", "VGT", "SOXX", "SMH", "USD", "TSM", "NVDA",
           "SKHY", "AVGO", "ANET", "MU", "GOOGL", "META", "GLD"]
SOURCE = {"SKHY": "000660.KS"}
BATCH = 4
LOOKBACK_DAYS = 400          # 只需补最近增量，历史由快照提供


def _finite_number(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False



def fetch_yf(tickers, start, end):
    """用 yfinance 包拉日线，返回 {ticker: [{date,open,high,low,close,volume}, ...]}。"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    out = {}
    for t in tickers:
        try:
            df = yf.Ticker(t).history(start=start, end=end, interval="1d",
                                      auto_adjust=False)
        except Exception as exc:                              # noqa: BLE001
            print("  !! {} 拉取失败：{}".format(t, exc))
            continue
        rows = []
        for ts, r in df.iterrows():
            price_cols = (r.get("Open"), r.get("High"), r.get("Low"), r.get("Close"))
            if not all(_finite_number(v) for v in price_cols):
                continue
            volume = r.get("Volume")
            rows.append({"date": ts.strftime("%Y-%m-%d"), "open": float(r["Open"]),
                         "high": float(r["High"]), "low": float(r["Low"]),
                         "close": float(r["Close"]),
                         "volume": float(volume) if _finite_number(volume) else 0.0})
        if rows:
            out[t.upper()] = rows
    return out or None


def call_mcp(tickers, start, end):
    payload = json.dumps({"tickers": tickers, "start_date": start,
                          "end_date": end})
    out = subprocess.run([sys.executable, MCP, payload], capture_output=True,
                         text=True, timeout=1200, cwd=ROOT)
    m = re.search(r'file:\s*"([^"]+)"', out.stdout or "")
    if not m:
        raise RuntimeError("MCP 未返回文件路径: {}".format(
            (out.stdout or out.stderr or "")[:300]))
    return m.group(1)


def merge(sym, rows):
    """把新 bars 覆盖 / 追加进 data/{SYM}.json.gz。返回 (新增数, 最新日期)。"""
    path = os.path.join(DATA, sym.upper() + ".json.gz")
    if not os.path.exists(path):
        raise RuntimeError("缺少基础快照 {}，请先全量拉取 8 年历史".format(sym))
    with gzip.open(path, "rt") as fh:
        rec = json.load(fh)
    b = rec["bars"]
    idx = {d: i for i, d in enumerate(b["d"])}
    added = 0
    for r in rows:
        d = r["date"]
        vals = (float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), float(r.get("volume") or 0.0))
        if not all(_finite_number(v) for v in vals[:4]):
            continue
        volume = vals[4] if _finite_number(vals[4]) else 0.0
        vals = (round(vals[0], 4), round(vals[1], 4), round(vals[2], 4),
                round(vals[3], 4), volume)
        if d in idx:
            i = idx[d]
            b["o"][i], b["h"][i], b["l"][i], b["c"][i], b["v"][i] = vals
        elif not b["d"] or d > b["d"][-1]:
            b["d"].append(d)
            for k, v in zip(("o", "h", "l", "c", "v"), vals):
                b[k].append(v)
            idx[d] = len(b["d"]) - 1
            added += 1
    with gzip.open(path, "wt") as fh:
        json.dump(rec, fh, separators=(",", ":"))
    return added, b["d"][-1]


def main():
    today = dt.date.today()
    start = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    end = (today + dt.timedelta(days=1)).isoformat()
    print("拉取窗口 {} ~ {}\n".format(start, end))

    fetch_list = [SOURCE.get(s, s) for s in SYMBOLS]
    back = {v: k for k, v in SOURCE.items()}

    got = {}
    yfres = fetch_yf(fetch_list, start, end)
    if yfres:
        for k, v in yfres.items():
            got[back.get(k, k)] = v
        print("[yfinance] 取到 {} 个标的\n".format(len(got)))

    for i in range(0, len(fetch_list), BATCH) if not got else []:
        batch = fetch_list[i:i + BATCH]
        print("[MCP] " + " ".join(batch))
        try:
            f = call_mcp(batch, start, end)
        except Exception as exc:                          # noqa: BLE001
            print("  !! 批次失败，跳过：{}".format(exc))
            continue
        for ent in json.load(open(f)):
            h = ent.get("history") or {}
            sym, rows = h.get("symbol"), (h.get("data") or [])
            if sym and rows:
                key = sym.upper()
                got[back.get(key, key)] = rows

    if not got:
        print("\n!! 未取到任何行情（yfinance 未安装且内部代理不可用），"
              "快照保持不变，仍会用现有数据重算。")

    print()
    stale = []
    for sym in SYMBOLS:
        rows = got.get(sym)
        if not rows:
            stale.append(sym)
            print("  %-5s 无新数据（沿用快照）" % sym)
            continue
        added, last = merge(sym, rows)
        print("  %-5s +%d 根新 K 线，最新 %s" % (sym, added, last))

    print("\n重算静态 JSON …")
    import build_static
    build_static.main()

    if stale:
        print("\n注意：以下标的本次未拿到新数据 -> " + ", ".join(stale))
    print("\n完成。下一步部署 frontend 目录（建议 stable_domain=true）。")


if __name__ == "__main__":
    main()
