"""每小时自动更新入口：拉最新行情 -> 合并进快照 -> 重算静态 JSON。

用法（在仓库根的 backend 目录下）：
    python3 update.py

之后再执行一次 deploy_frontend（stable_domain=true）即可让线上页面刷新。

说明：本指数基于**日线**计算，因此盘中每小时刷新实际改变的是「当日那根未收盘的 K 线」
（收盘价用最新成交价代替）。美股交易时段为北京时间 21:30–04:00，其余时间刷新不会有变化。
"""
import glob
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
# 仓库根 -> 找到 inner_skills/yfinance 的 MCP 脚本
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
MCP = os.path.join(ROOT, "inner_skills", "yfinance",
                   "mcp_yfinance_yahoo_finance.py")
MCP_OUT = os.path.join(ROOT, "mcp_outputs")

SYMBOLS = ["SPY", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
           "TSM", "QQQ", "SOXX"]
BATCH = 3          # 每批 3 个，避免上游限流


def fetch(batch, start, end):
    """调用 MCP yfinance 拉取一批标的的日线，返回 {sym: [bar,...]}。"""
    payload = json.dumps({"tickers": batch, "start_date": start, "end_date": end})
    r = subprocess.run([sys.executable, MCP, payload], capture_output=True,
                       text=True, timeout=900)
    if r.returncode != 0:
        print("  ! MCP 调用失败:", (r.stderr or r.stdout)[:300])
        return {}
    name = "yfinance_" + "_".join(batch) + ".json"
    path = os.path.join(MCP_OUT, name)
    if not os.path.exists(path):
        cand = sorted(glob.glob(os.path.join(MCP_OUT, "yfinance_*.json")),
                      key=os.path.getmtime)
        if not cand:
            print("  ! 找不到 MCP 输出文件")
            return {}
        path = cand[-1]
    out = {}
    for ent in json.load(open(path)):
        h = ent.get("history") or {}
        if h.get("symbol") and h.get("data"):
            out[h["symbol"].upper()] = h["data"]
    return out


def merge(sym, rows):
    """把新行情覆盖 / 追加进快照，返回 (新增, 覆盖) 条数。"""
    p = os.path.join(DATA, sym + ".json.gz")
    if not os.path.exists(p):
        print("  ! 快照不存在:", sym)
        return 0, 0
    with gzip.open(p, "rt") as fh:
        rec = json.load(fh)
    b = rec["bars"]
    idx = {d: i for i, d in enumerate(b["d"])}
    added = updated = 0
    for r in rows:
        d = r["date"]
        vals = (round(float(r["open"]), 4), round(float(r["high"]), 4),
                round(float(r["low"]), 4), round(float(r["close"]), 4),
                float(r["volume"] or 0))
        if d in idx:
            i = idx[d]
            if abs(b["c"][i] - vals[3]) > 1e-6:
                updated += 1
            b["o"][i], b["h"][i], b["l"][i], b["c"][i], b["v"][i] = vals
        elif not b["d"] or d > b["d"][-1]:
            b["d"].append(d)
            for k, v in zip(("o", "h", "l", "c", "v"), vals):
                b[k].append(v)
            idx[d] = len(b["d"]) - 1
            added += 1
    with gzip.open(p, "wt") as fh:
        json.dump(rec, fh, separators=(",", ":"))
    return added, updated


def main():
    end = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.utcnow() - timedelta(days=120)).strftime("%Y-%m-%d")
    print("拉取区间 %s ~ %s" % (start, end))

    got, tot_a, tot_u = 0, 0, 0
    for i in range(0, len(SYMBOLS), BATCH):
        batch = SYMBOLS[i:i + BATCH]
        rows = fetch(batch, start, end)
        for sym in batch:
            if sym not in rows:
                print("  %-6s 未取到数据（保留原快照）" % sym)
                continue
            a, u = merge(sym, rows[sym])
            tot_a += a
            tot_u += u
            got += 1
            print("  %-6s 最新 %s  新增 %d 覆盖 %d"
                  % (sym, rows[sym][-1]["date"], a, u))

    print("\n%d/%d 个标的更新成功，新增 %d 根、修正 %d 根 K 线"
          % (got, len(SYMBOLS), tot_a, tot_u))

    if got == 0:
        print("全部标的均未取到数据，跳过重算（线上仍展示上一版快照，不会白屏）")
        return 1

    print("\n重算静态 JSON …")
    import build_static
    build_static.main()
    print("\n完成。接下来执行 deploy_frontend（stable_domain=true）发布。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
