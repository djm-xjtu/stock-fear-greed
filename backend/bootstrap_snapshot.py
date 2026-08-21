#!/usr/bin/env python3
"""从 yfinance MCP 输出文件全量生成 data/{SYM}.json.gz 快照（新增标的时用）。

用法：
    python3 bootstrap_snapshot.py <mcp_json> [<mcp_json> ...] [--map SRC=DST,...]

--map 用于「数据源 ticker 与展示 ticker 不同」的情况，例如 SK hynix 的美股
SKHY 2026 年才上市、历史不足，改用韩股主上市 000660.KS 的日线：
    --map 000660.KS=SKHY
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

CURRENCY = {"SKHY": "KRW"}
EXCHANGE = {"SKHY": "KRX (000660.KS)"}


def main(argv):
    files, mapping = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--map":
            i += 1
            for pair in argv[i].split(","):
                src, dst = pair.split("=")
                mapping[src.strip().upper()] = dst.strip().upper()
        else:
            files.append(a)
        i += 1

    os.makedirs(DATA, exist_ok=True)
    for f in files:
        for ent in json.load(open(f)):
            h = ent.get("history") or {}
            rows = h.get("data") or []
            src = (h.get("symbol") or "").upper()
            if not src or not rows:
                continue
            sym = mapping.get(src, src)
            name = ((ent.get("stock_info") or {}).get("name") or sym)
            if name == "N/A":
                name = sym
            bars = {"d": [], "o": [], "h": [], "l": [], "c": [], "v": []}
            for r in rows:
                if r.get("close") is None:
                    continue
                bars["d"].append(r["date"])
                bars["o"].append(round(float(r["open"]), 4))
                bars["h"].append(round(float(r["high"]), 4))
                bars["l"].append(round(float(r["low"]), 4))
                bars["c"].append(round(float(r["close"]), 4))
                bars["v"].append(float(r.get("volume") or 0))
            rec = {"symbol": sym, "name": name,
                   "currency": CURRENCY.get(sym, "USD"),
                   "exchange": EXCHANGE.get(sym, ""),
                   "source_ticker": src, "bars": bars}
            path = os.path.join(DATA, sym + ".json.gz")
            with gzip.open(path, "wt") as fh:
                json.dump(rec, fh, separators=(",", ":"))
            print("%-6s <- %-10s %d bars  %s -> %s  %.0f KB"
                  % (sym, src, len(bars["d"]), bars["d"][0], bars["d"][-1],
                     os.path.getsize(path) / 1024))


if __name__ == "__main__":
    main(sys.argv[1:])
