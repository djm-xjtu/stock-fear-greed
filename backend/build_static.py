"""把情绪指数结果预计算成静态 JSON，随前端一起部署。

动机：FaaS 后端域名（*.cn-east-fn.bytedance.net）是内网域名，用户浏览器无法直连，
表现为请求挂起 + Provisional headers are shown + Failed to fetch。
静态化后页面零后端依赖，任何网络都能打开。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as M                                            # noqa: E402
from engine import FACTOR_META, WEIGHTS                      # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "frontend", "data")


def write(rel, obj):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    return os.path.getsize(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    bench = M.load_snapshot(M.BENCHMARK)
    details = {}
    total = 0

    for sym in M.DEFAULT_UNIVERSE:
        snap = M.load_snapshot(sym)
        if snap is None:
            print("  skip", sym)
            continue
        d = M.build_detail(snap, bench)
        details[sym] = d
        n = write("stock/{}.json".format(sym), d)
        total += n
        print("  %-6s %6.1f (%s)  naive %5.1f  %6.1f KB"
              % (sym, d["score"], d["label"], d["naive_range_score"], n / 1024))

    items = []
    for sym, r in details.items():
        items.append({
            "symbol": r["symbol"], "name": r["name"], "price": r["price"],
            "currency": r.get("currency", "USD"),
            "change_pct": r["change_pct"], "score": r["score"],
            "label": r["label"], "label_en": r["label_en"],
            "naive_range_score": r["naive_range_score"],
            "score_20d_ago": r["score_20d_ago"],
            "self_percentile": r["self_percentile"],
            "realized_vol": r["realized_vol"], "downside_vol": r["downside_vol"],
            "as_of": r["as_of"], "data_source": r["data_source"],
            "factors": [{"key": f["key"], "name": f["name"], "score": f["score"],
                         "raw_text": f["raw_text"]} for f in r["factors"]],
            "spark": [h["c"] for h in r["history"][-120:]],
        })
    items.sort(key=lambda x: -(x["score"] if x["score"] is not None else 0))
    total += write("universe.json", {"items": items, "errors": [],
                                     "generated_at": 0})

    total += write("methodology.json", {
        "weights": {k: round(v * 100, 1) for k, v in WEIGHTS.items()},
        "factors": [dict([("key", k)] + list(FACTOR_META[k].items()))
                    for k in WEIGHTS],
        "pct_window_days": 504, "benchmark": M.BENCHMARK,
        "universe": M.DEFAULT_UNIVERSE,
    })

    # 复用后端的分档验证逻辑（同步执行内部 _run）
    bands = ["0-20", "20-40", "40-60", "60-80", "80-100"]
    bmap = dict(zip(bench["bars"]["d"], bench["bars"]["close"]))
    fwd = 20
    acc = {}
    for sym in M.DEFAULT_UNIVERSE:
        snap = M.load_snapshot(sym)
        if snap is None:
            continue
        d = M.build_detail(snap, bench, history_days=1400)
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
                acc.setdefault((tag, bands[min(4, int(val // 20))]), []).append(exc)

    val = {"fwd_days": fwd, "universe": M.DEFAULT_UNIVERSE,
           "benchmark": M.BENCHMARK}
    for tag in ("multi", "naive"):
        tot = sum(len(acc.get((tag, b), [])) for b in bands)
        rows = []
        for b in bands:
            v = acc.get((tag, b), [])
            rows.append({
                "band": b, "n": len(v),
                "share": round(100.0 * len(v) / tot, 1) if tot else 0.0,
                "mean_excess": round(100.0 * sum(v) / len(v), 2) if v else None,
                "hit_rate": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)
                if v else None,
            })
        val[tag] = {"rows": rows, "total": tot,
                    "max_band_share": max(r["share"] for r in rows)}
    total += write("validation.json", val)

    # ---------------- 抄底信号：长历史（2400 日）重算一次 ----------------
    import dip
    long_details = []
    for sym in M.DEFAULT_UNIVERSE:
        snap = M.load_snapshot(sym)
        if snap is None:
            continue
        long_details.append(M.build_detail(snap, bench, history_days=2400))
    dp = dip.build(long_details)
    n = write("dip.json", dp)
    total += n
    print("\n抄底信号 %.0f KB · %d 个标的" % (n / 1024, len(dp["symbols"])))
    for s in dp["symbols"]:
        sig = s["signal"]
        print("  %-5s 分 %5.1f  回撤 %6s%%  仓位阶梯 %3d%%  %s"
              % (s["symbol"], s["score"], s["drawdown_now"], sig["ladder"],
                 sig["state"]))

    print("\n静态数据总计 %.0f KB -> %s" % (total / 1024, os.path.abspath(OUT)))


if __name__ == "__main__":
    main()
