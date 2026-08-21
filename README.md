# Multi-Factor Single-Stock Fear &amp; Greed Index

用 7 个互补因子重建**个股级别**的恐慌/贪婪指数，并把「什么时候抄底」做成可回测的规则表。

线上 Demo：**https://djm-xjtu.github.io/stock-fear-greed/** （纯静态，数据随页面打包，`gh-pages` 分支）

![sections](https://img.shields.io/badge/sections-看板%20·%20指标矩阵%20·%20抄底%20·%20拆解-6ea8fe)

## 为什么重做

greedyfear.com 的个股分数只有一个输入 —— 当前价在 52 周区间的位置：

```text
score = round((price - low52) / (high52 - low52) * 100)
```

问题：① 没有波动率 / 成交量 / 相对强度；② 52 周极值是极值统计量，一次闪崩锁定分母一整年；
③ 长期上行的科技股几乎永远贴近 52 周高点，指标失去区分度。

## 指数构成

| 因子 | 权重 | 说明 |
|---|---|---|
| 趋势动量 MOM | 20% | 多周期动量合成 |
| 相对强度 RS | 18% | 相对 SPY 的超额走势 |
| 下行波动（反向） DVOL | 16% | 21 日下行半标准差，避免把财报跳空大涨误判成恐慌 |
| 资金流 CMF | 14% | Chaikin Money Flow |
| 回撤压力 ATR-DD | 12% | ATR 归一化的回撤位置 |
| 52 周区间位置 | 10% | 即 greedyfear 的唯一因子 |
| RSI(14) | 10% | 超买超卖 |

每个因子先算原始统计量，再在**该标的自身过去 504 个交易日**的同类分布中取百分位，最后加权合成 0–100。
只使用历史窗口 ⇒ 无未来函数；自归一化 ⇒ 跨标的可比。

## 抄底规则回测

12 条候选规则（情绪极端 / 情绪反转 / 价格回撤 / 组合）在 10 个标的、约 6 年日线上做事件回测，
条件上升沿触发 + 10 交易日去重，所有收益都减去**同期无条件基线**：

- 基线：任意一天买入，未来 60 交易日平均 +11.0%、胜率 69%
- 情绪族全部负超额（情绪分跌破 30：+6.5%，−4.5pp）
- 回撤族全部正超额（首次触及 −25%：+17.5%，+6.5pp）
- 状态口径（「当前正处于某回撤区间」）无效，优势只集中在**首次触及**的那几天
- 等确认（情绪回升 / 收复 20 日线）降低收益但降低浮亏

据此给出机械阶梯：−8%→20%、−12%→40%、−20%→65%、−25%→100%，每档只在首次触及时执行一次。

## 标的池

QQQ、SOXX、TSM、NVDA、SKHY、AVGO、ANET、MU、GOOGL、META（SPY 仅作相对强度基准）。

> SKHY（SK hynix）美股上市时间过短，历史序列采用韩股主上市 `000660.KS`（KRW 计价，交易日与美股不完全重叠）。

## 目录结构

```text
backend/
  engine.py             因子计算与滚动分位合成
  dip.py                抄底规则回测（事件定义 / 基线对比 / 阶梯信号）
  main.py               FastAPI 接口（/api/universe /api/stock /api/dip ...）
  build_static.py       预计算静态 JSON 到 frontend/data
  refresh_all.py        每日增量更新（拉日线 → 合并快照 → 重算静态 JSON）
  bootstrap_snapshot.py 新增标的时全量生成快照
  data/*.json.gz        8 年日线快照
frontend/
  index.html            看板 / 指标矩阵 / 抄底 / 拆解
  app.js styles.css     渲染逻辑与样式（ECharts 本地打包）
  data/*.json           预计算结果（零后端依赖）
```

## 本地运行

```bash
# 纯静态查看
cd frontend && python3 -m http.server 5173      # http://127.0.0.1:5173

# 重算静态数据
cd backend && python3 build_static.py

# 带接口的后端（可选）
cd backend && pip install -r requirements.txt && python3 main.py
```

每日增量更新（优先用 `yfinance` 包，失败才回落到代理脚本）：

```bash
pip install yfinance
cd backend && python3 refresh_all.py     # 拉最近 400 天 → 合并快照 → 重算 frontend/data
```

新增标的时先全量建快照：

```bash
cd backend && python3 bootstrap_snapshot.py <yfinance导出的json> [--map 000660.KS=SKHY]
```

## 免责声明

本仓库为方法论与可视化演示，**不构成投资建议**。已知局限：

1. 未纳入期权隐含波动率与 put/call 偏斜，情绪的「预期维度」缺失；
2. 回测为样本内池化统计，未扣交易成本与滑点，未做多重检验；
3. 10 个标的集中在 AI 算力链且过去 6 年整体强上行，结论不可外推到小盘、周期或下行趋势资产。
