#!/usr/bin/env python3
"""每日一键更新：拉最新日线 -> 合并进 8 年快照 -> 重算静态 JSON。

用法：
    python3 sentiment-lab/backend/refresh_all.py

之后再执行 deploy_frontend 部署 sentiment-lab/frontend 即可（建议 stable_domain=true）。

数据源用 yfinance MCP 脚本（走服务端代理，不受 Yahoo 对沙箱 IP 的 429 限流影响）。
"""
import datetime as dt
import glob
import gzip
import json
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
SYMBOLS = ["SPY", "QQQ", "SOXX", "TSM", "NVDA", "SKHY", "AVGO", "ANET", "MU",
           "GOOGL", "META"]
SOURCE = {"SKHY": "000660.KS"}
BATCH = 4
LOOKBACK_DAYS = 400          # 只需补最近增量，历史由快照提供


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
        vals = (round(float(r["open"]), 4), round(float(r["high"]), 4),
                round(float(r["low"]), 4), round(float(r["close"]), 4),
                float(r["volume"] or 0))
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
    for i in range(0, len(fetch_list), BATCH):
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
        print("\n!! 所有批次都失败，快照保持不变，仍会用现有数据重算。")

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
