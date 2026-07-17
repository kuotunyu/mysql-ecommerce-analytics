"""Stage B 分析 runner：執行 analysis/*.sql、落地聚合 CSV、產出報告圖表。

用法：
    uv run python scripts/run_analysis.py              # 全部主題
    uv run python scripts/run_analysis.py --themes 01,05
    uv run python scripts/run_analysis.py --list       # 列出查詢後退出
    uv run python scripts/run_analysis.py --no-figures # 只跑 SQL 不出圖

行為：
    * 每次必先執行 analysis/00_views.sql（CREATE OR REPLACE，冪等）
    * 解析各檔 `-- @name:` 標記逐查詢執行；`-- @nocsv` 標記者不落 CSV（如 9.3 萬列
      的 RFM 明細，僅在記憶體供圖）
    * 聚合結果寫 reports/tables/<檔>__<查詢代號>.csv（UTF-8-sig）
    * 圖表為具名函式（非通用 DSL）：殘月灰化、cohort 右截斷灰格、log 軸等
      都是客製繪圖邏輯。調色盤採 dataviz 已驗證 reference palette（light mode），
      分類色固定順序、單軸原則（不同量級一律上下雙 panel）。
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FuncFormatter
from tabulate import tabulate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = PROJECT_ROOT / "analysis"
FIG_DIR = PROJECT_ROOT / "reports" / "figures"
TABLE_DIR = PROJECT_ROOT / "reports" / "tables"

# ---- dataviz reference palette（light surface）----
SURFACE = "#fcfcfb"
INK1, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE = "#e1e0d9", "#c3c2b7"
C_BLUE, C_AQUA, C_YELLOW = "#2a78d6", "#1baf7a", "#eda100"
DELTA_GOOD, DELTA_BAD = "#006300", "#d03b3b"      # 正/負變化（成對出現、軸上有號）
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
CMAP_SEQ = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
CMAP_SEQ.set_bad(GRID)  # NaN（cohort 右截斷不可觀察格）＝灰

plt.rcParams.update({
    "font.sans-serif": ["Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif"],
    "font.family": "sans-serif",
    "axes.unicode_minus": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "text.color": INK1,
    "axes.labelcolor": INK2,
    "axes.titlecolor": INK1,
    "axes.titlesize": 12,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.edgecolor": BASELINE,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
})


def style_ax(ax, grid_axis="y"):
    ax.spines[["top", "right"]].set_visible(False)
    if grid_axis:
        ax.grid(axis=grid_axis, linewidth=0.8, color=GRID)
        ax.set_axisbelow(True)


def save_fig(fig, name):
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = FIG_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [fig] {path.relative_to(PROJECT_ROOT)}")


def millions(x, _):
    return f"{x / 1e6:.1f}M"


# ---------------------------------------------------------------- SQL 執行層

def connect():
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4",
    )


def parse_queries(text: str):
    """回傳 [(name, nocsv, sql)]；@name 之後至下一個分號結尾的語句為一查詢。"""
    queries, name, nocsv, buf = [], None, False, []

    def flush():
        nonlocal buf, name, nocsv
        stmt = "\n".join(buf).strip().rstrip(";").strip()
        if name and stmt:
            queries.append((name, nocsv, stmt))
        buf, name, nocsv = [], None, False

    for line in text.splitlines():
        m = re.match(r"--\s*@name:\s*(.+)", line)
        if m:
            flush()
            name = m.group(1).strip()
            continue
        if re.match(r"--\s*@nocsv", line):
            nocsv = True
            continue
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.strip().endswith(";"):
            flush()
    flush()
    return queries


def slug_of(name: str) -> str:
    return name.split()[0].replace("-", "_").lower()   # 'Q1' / '2-1' / '3-1b' → q1 / 2_1 / 3_1b


def to_df(cur) -> pd.DataFrame:
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    for c in df.columns:                    # Decimal → float（圖表計算用）
        converted = pd.to_numeric(df[c], errors="coerce")
        if not converted.isna().all() and converted.notna().sum() == df[c].notna().sum():
            df[c] = converted
    return df


def run_views(cur):
    text = (SQL_DIR / "00_views.sql").read_text(encoding="utf-8")
    stmts = [s.strip() for s in re.sub(r"^\s*--.*$", "", text, flags=re.M).split(";") if s.strip()]
    for s in stmts:
        cur.execute(s)
    print(f"[views] 00_views.sql：{len(stmts)} 個 view 已就緒")


def execute_file(cur, path: Path, results: dict, no_csv_output=False):
    stem = path.stem
    t_file = time.perf_counter()
    for name, nocsv, sql in parse_queries(path.read_text(encoding="utf-8")):
        t0 = time.perf_counter()
        cur.execute(sql)
        df = to_df(cur)
        dt = time.perf_counter() - t0
        results[(stem, slug_of(name))] = df
        print(f"\n--- {name}（{dt:.2f}s，{len(df):,} 列）---")
        with pd.option_context("display.max_columns", None):
            print(tabulate(df.head(20), headers=df.columns, tablefmt="github",
                           showindex=False, disable_numparse=True))
        if not nocsv and not no_csv_output:
            TABLE_DIR.mkdir(parents=True, exist_ok=True)
            out = TABLE_DIR / f"{stem}__{slug_of(name)}.csv"
            df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n[theme] {stem} 完成（{time.perf_counter() - t_file:.1f}s）")


# ---------------------------------------------------------------- 圖表層

def month_ticks(ax, yms, step=3):
    ax.set_xticks(range(0, len(yms), step))
    ax.set_xticklabels(yms[::step], rotation=45, ha="right", fontsize=8)


def fig_01_gmv_mom(r):
    df = r[("01_revenue", "q1")]
    yms = df["ym"].tolist()
    x = np.arange(len(df))
    full = df["month_flag"] == "full"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    ax1.plot(x, df["gmv"], color=GRID, linestyle="--", linewidth=1.5, zorder=1)
    ax1.plot(x[full], df.loc[full, "gmv"], color=C_BLUE, linewidth=2, marker="o",
             markersize=4, zorder=2)
    ax1.scatter(x[~full], df.loc[~full, "gmv"], color=MUTED, s=18, zorder=2)
    ax1.yaxis.set_major_formatter(FuncFormatter(millions))
    ax1.set_ylabel("GMV（R$）")
    ax1.set_title("月 GMV 趨勢與 MoM 成長率（灰點＝2016 稀疏期與 2018-09/10 殘月，不計成長率）")
    style_ax(ax1)
    mom = df["mom_pct"]
    colors = [DELTA_GOOD if v >= 0 else DELTA_BAD for v in mom.fillna(0)]
    ax2.bar(x[mom.notna()], mom.dropna(), color=[c for c, keep in zip(colors, mom.notna()) if keep],
            width=0.7)
    ax2.axhline(0, color=BASELINE, linewidth=1)
    ax2.set_ylabel("MoM %")
    style_ax(ax2)
    month_ticks(ax2, yms)
    save_fig(fig, "01_gmv_mom.png")


def fig_01_orders_aov(r):
    df = r[("01_revenue", "q1")]
    yms = df["ym"].tolist()
    x = np.arange(len(df))
    full = df["month_flag"] == "full"
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.bar(x, df["n_orders"], color=[C_BLUE if f else GRID for f in full], width=0.7)
    ax1.set_ylabel("訂單數")
    ax1.set_title("月訂單數與 AOV（灰＝非完整月）")
    style_ax(ax1)
    ax2.plot(x[full], df.loc[full, "aov"], color=C_AQUA, linewidth=2, marker="o", markersize=4)
    ax2.scatter(x[~full], df.loc[~full, "aov"], color=MUTED, s=18)
    ax2.set_ylabel("AOV（R$/單）")
    style_ax(ax2)
    month_ticks(ax2, yms)
    save_fig(fig, "01_orders_aov.png")


def fig_02_rm_distribution(r):
    df = r[("02_rfm", "2_1")]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    m = df["monetary"].astype(float)
    bins = np.logspace(np.log10(max(m.min(), 1)), np.log10(m.max()), 40)
    ax1.hist(m, bins=bins, color=C_BLUE)
    ax1.set_xscale("log")
    ax1.set_xlabel("客戶累計 GMV（R$，log 尺度）")
    ax1.set_ylabel("客戶數")
    ax1.set_title("M：消費金額分布")
    style_ax(ax1)
    ax2.hist(df["recency_days"].astype(float), bins=40, color=C_BLUE)
    ax2.set_xlabel("距最後一次購買天數（R 基準日＝資料內最大下單日）")
    ax2.set_title("R：流失時間分布")
    style_ax(ax2)
    save_fig(fig, "02_rm_distribution.png")


def fig_02_segments(r):
    df = r[("02_rfm", "2_3")]
    y = np.arange(len(df))[::-1]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    h = 0.38
    ax.barh(y + h / 2, df["pct_customers"], height=h, color=C_BLUE, label="人數佔比 %")
    ax.barh(y - h / 2, df["pct_gmv"], height=h, color=C_AQUA, label="GMV 貢獻佔比 %")
    for yi, (pc, pg) in zip(y, zip(df["pct_customers"], df["pct_gmv"])):
        ax.text(float(pc) + 0.4, yi + h / 2, f"{pc}", va="center", fontsize=8, color=INK2)
        ax.text(float(pg) + 0.4, yi - h / 2, f"{pg}", va="center", fontsize=8, color=INK2)
    ax.set_yticks(y)
    ax.set_yticklabels(df["segment"])
    ax.set_xlabel("%")
    ax.set_title("RFM 七分群：人數 vs GMV 貢獻（流失高價值＝人少錢多的喚回目標）")
    ax.legend(frameon=False)
    style_ax(ax, grid_axis="x")
    save_fig(fig, "02_segments.png")


def fig_02_rxm_heatmap(r):
    df = r[("02_rfm", "2_4")]
    pv = df.pivot(index="r_score", columns="m_score", values="n_customers").sort_index(ascending=False)
    fig, ax = plt.subplots(figsize=(6.5, 5))
    mesh = ax.pcolormesh(pv.values.astype(float), cmap=CMAP_SEQ, edgecolors=SURFACE, linewidth=2)
    for i in range(pv.shape[0]):
        for j in range(pv.shape[1]):
            v = pv.values[i, j]
            dark = v > np.nanmax(pv.values) * 0.55
            ax.text(j + 0.5, i + 0.5, f"{int(v):,}", ha="center", va="center",
                    fontsize=8, color=SURFACE if dark else INK2)
    ax.set_xticks(np.arange(5) + 0.5)
    ax.set_xticklabels([f"M{c}" for c in pv.columns])
    ax.set_yticks(np.arange(5) + 0.5)
    ax.set_yticklabels([f"R{i}" for i in pv.index])
    ax.set_xlabel("M 分數（5＝最高消費）")
    ax.set_ylabel("R 分數（5＝最近活躍）")
    ax.set_title("一次性客戶（F=1）的 R×M 地形圖（格值＝人數）")
    fig.colorbar(mesh, ax=ax, shrink=0.8, label="人數")
    style_ax(ax, grid_axis=None)
    save_fig(fig, "02_rxm_heatmap.png")


def fig_02_state_pareto(r):
    df = r[("02_rfm", "2_5")].head(10)
    x = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(x, df["pct_gmv"], color=C_BLUE, width=0.65, label="GMV 佔比 %")
    ax.plot(x, df["cum_pct_gmv"], color=C_AQUA, linewidth=2, marker="o", markersize=5,
            label="累積佔比 %")
    ax.axhline(80, color=MUTED, linewidth=1, linestyle="--")
    ax.text(len(df) - 0.4, 81.5, "80%", color=MUTED, fontsize=8, ha="right")
    ax.set_xticks(x)
    ax.set_xticklabels(df["customer_state"])
    ax.set_ylabel("%（同一尺度）")
    ax.set_title("州別 GMV Pareto（Top 10 州）")
    ax.legend(frameon=False)
    style_ax(ax)
    save_fig(fig, "02_state_pareto.png")


def fig_03_second_curve(r):
    df = r[("03_cohort", "3_1b")]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for color, (grp, g) in zip([C_BLUE, C_AQUA], df.groupby("cohort_year", sort=True)):
        ax.plot(g["month_age"], g["cum_second_pct"], color=color, linewidth=2,
                marker="o", markersize=5, label=str(grp))
        ax.text(6.1, float(g["cum_second_pct"].iloc[-1]), f'{g["cum_second_pct"].iloc[-1]}%',
                va="center", fontsize=8, color=color)
    ax.set_xlabel("首購後月數")
    ax.set_ylabel("累積二購率 %")
    ax.set_title("累積二購率曲線（限首購 ≤ 2018-02，全員 6 個月觀察窗）")
    ax.legend(frameon=False)
    style_ax(ax)
    save_fig(fig, "03_second_purchase_curve.png")


def fig_03_cohort_heatmap(r):
    df = r[("03_cohort", "3_1")]
    df = df[df["month_age"] >= 1]
    pv = df.pivot(index="cohort_m", columns="month_age", values="retention_pct")
    sizes = df.drop_duplicates("cohort_m").set_index("cohort_m")["n_cohort"]
    fig, ax = plt.subplots(figsize=(7, 7.5))
    data = np.ma.masked_invalid(pv.values.astype(float))
    mesh = ax.pcolormesh(data, cmap=CMAP_SEQ, vmin=0, vmax=1.5,
                         edgecolors=SURFACE, linewidth=2)
    for i in range(pv.shape[0]):
        for j in range(pv.shape[1]):
            v = pv.values[i, j]
            if not np.isnan(v):
                ax.text(j + 0.5, i + 0.5, f"{v:.1f}", ha="center", va="center",
                        fontsize=7, color=INK2 if v < 0.9 else SURFACE)
    ax.set_xticks(np.arange(pv.shape[1]) + 0.5)
    ax.set_xticklabels([f"+{c}月" for c in pv.columns])
    ax.set_yticks(np.arange(pv.shape[0]) + 0.5)
    ax.set_yticklabels([f"{str(i)[:4]}-{str(i)[4:]}（n={sizes[i]:,}）" for i in pv.index],
                       fontsize=7)
    ax.invert_yaxis()
    ax.set_title("Cohort 月留存率 %（色階上限 1.5%；灰格＝資料期外不可觀察）")
    fig.colorbar(mesh, ax=ax, shrink=0.7, label="留存率 %")
    style_ax(ax, grid_axis=None)
    save_fig(fig, "03_cohort_heatmap.png")


def fig_04_top15(r):
    df = r[("04_category", "4_1")][::-1]
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.barh(df["category"], df["gmv"], color=C_BLUE, height=0.65)
    for i, (g, p) in enumerate(zip(df["gmv"], df["pct_gmv"])):
        ax.text(float(g) + df["gmv"].max() * 0.01, i, f"{float(g)/1e6:.2f}M（{p}%）",
                va="center", fontsize=8, color=INK2)
    ax.xaxis.set_major_formatter(FuncFormatter(millions))
    ax.set_xlim(0, float(df["gmv"].max()) * 1.22)
    ax.set_xlabel("GMV（R$）")
    ax.set_title("Top 15 品類 GMV（標注＝金額與全站佔比）")
    style_ax(ax, grid_axis="x")
    save_fig(fig, "04_top15.png")


def fig_04_pareto(r):
    df = r[("04_category", "4_2")].head(30)
    x = np.arange(len(df))
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(x, df["pct_gmv"], color=C_BLUE, width=0.7, label="GMV 佔比 %")
    ax.plot(x, df["cum_pct_gmv"], color=C_AQUA, linewidth=2, marker="o", markersize=4,
            label="累積佔比 %")
    ax.axhline(80, color=MUTED, linewidth=1, linestyle="--")
    thresh = int(df[df["cum_pct_gmv"] >= 80].iloc[0]["rk"]) if (df["cum_pct_gmv"] >= 80).any() else None
    if thresh:
        ax.axvline(thresh - 1, color=MUTED, linewidth=1, linestyle=":")
        ax.text(thresh - 0.8, 92, f"前 {thresh} 個品類 ≈ 80% GMV", fontsize=8, color=INK2)
    ax.set_xticks(x)
    ax.set_xticklabels(df["category"], rotation=60, ha="right", fontsize=7)
    ax.set_ylabel("%（同一尺度）")
    ax.set_title("品類營收集中度 Pareto（前 30 個品類）")
    ax.legend(frameon=False)
    style_ax(ax)
    save_fig(fig, "04_pareto.png")


def fig_04_freight(r):
    df = r[("04_category", "4_4")].sort_values("freight_share_pct")
    baseline = float(df["overall_freight_share_pct"].iloc[0])
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.barh(df["category"], df["freight_share_pct"], color=C_BLUE, height=0.65)
    ax.axvline(baseline, color=MUTED, linewidth=1.2, linestyle="--")
    ax.text(baseline + 0.3, len(df) - 0.6, f"全站 {baseline}%", fontsize=8, color=MUTED)
    for i, (s, p) in enumerate(zip(df["freight_share_pct"], df["avg_item_price"])):
        ax.text(float(s) + 0.3, i, f"{s}%｜均價 {p:.0f}", va="center", fontsize=7.5, color=INK2)
    ax.set_xlim(0, float(df["freight_share_pct"].max()) * 1.3)
    ax.set_xlabel("運費佔 GMV %")
    ax.set_title("GMV Top 15 品類的運費結構（低單價品類運費侵蝕明顯）")
    style_ax(ax, grid_axis="x")
    save_fig(fig, "04_freight_by_category.png")


def fig_05_delivery_dist(r):
    d1, d2 = r[("05_logistics", "5_1")], r[("05_logistics", "5_2")]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.2))
    ax1.bar(d1["days_bin"], d1["pct"], color=C_BLUE, width=0.65)
    for i, p in enumerate(d1["pct"]):
        ax1.text(i, float(p) + 0.6, f"{p}%", ha="center", fontsize=8, color=INK2)
    ax1.set_ylabel("%")
    ax1.set_title("配送天數分布（下單→送達）")
    ax1.tick_params(axis="x", rotation=20)
    style_ax(ax1)
    # 早/晚到：polarity → diverging（早=藍、準時=灰中點、晚=紅）
    colors = [C_BLUE] * 3 + [MUTED] + [DELTA_BAD] * 3
    ax2.bar(d2["delay_bin"], d2["pct"], color=colors[: len(d2)], width=0.65)
    for i, p in enumerate(d2["pct"]):
        ax2.text(i, float(p) + 0.6, f"{p}%", ha="center", fontsize=8, color=INK2)
    ax2.set_title("vs 預估交期：早到（藍）/ 準時（灰）/ 晚到（紅）")
    ax2.tick_params(axis="x", rotation=20)
    style_ax(ax2)
    save_fig(fig, "05_delivery_distribution.png")


def fig_05_monthly(r):
    df = r[("05_logistics", "5_3")]
    yms = df["ym"].tolist()
    x = np.arange(len(df))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(x, df["avg_delivery_days"], color=C_BLUE, linewidth=2, marker="o", markersize=4)
    ax1.set_ylabel("平均配送天數")
    ax1.set_title("月度配送表現（2017-01 ~ 2018-08）")
    style_ax(ax1)
    ax2.plot(x, df["on_time_rate_pct"], color=C_AQUA, linewidth=2, marker="o", markersize=4)
    ax2.set_ylabel("準時率 %")
    style_ax(ax2)
    month_ticks(ax2, yms, step=2)
    save_fig(fig, "05_monthly_ontime.png")


def fig_05_state(r):
    df = r[("05_logistics", "5_4")]
    fig, ax = plt.subplots(figsize=(9, 8))
    y = np.arange(len(df))[::-1]
    ax.barh(y, df["avg_delivery_days"], color=C_BLUE, height=0.65)
    for yi, (d, ot) in zip(y, zip(df["avg_delivery_days"], df["on_time_rate_pct"])):
        ax.text(float(d) + 0.3, yi, f"{d} 天｜準時 {ot}%", va="center", fontsize=7.5, color=INK2)
    ax.set_yticks(y)
    ax.set_yticklabels(df["customer_state"], fontsize=8)
    ax.set_xlim(0, float(df["avg_delivery_days"].max()) * 1.35)
    ax.set_xlabel("平均配送天數")
    ax.set_title("州別配送天數與準時率（依天數排序）")
    style_ax(ax, grid_axis="x")
    save_fig(fig, "05_state_delivery.png")


def fig_05_stages(r):
    df = r[("05_logistics", "5_6")]
    segs = [("avg_approve_days", "核准", C_BLUE),
            ("avg_handling_days", "賣家出貨", C_AQUA),
            ("avg_transit_days", "物流運送", C_YELLOW)]
    fig, ax = plt.subplots(figsize=(9, 3.2))
    y = np.arange(len(df))[::-1]
    left = np.zeros(len(df))
    for col, label, color in segs:
        vals = df[col].astype(float).values
        ax.barh(y, vals, left=left, color=color, height=0.5, label=label,
                edgecolor=SURFACE, linewidth=2)
        for yi, (l, v) in zip(y, zip(left, vals)):
            ax.text(l + v / 2, yi, f"{v:.1f}", ha="center", va="center", fontsize=8, color=INK1)
        left += vals
    ax.set_yticks(y)
    ax.set_yticklabels(df["grp"])
    ax.set_xlabel("平均天數")
    ax.set_title("配送三階段拆解：晚到訂單的時間花在哪一段")
    ax.legend(frameon=False, ncol=3)
    style_ax(ax, grid_axis="x")
    save_fig(fig, "05_stage_breakdown.png")


def fig_06_score_comment(r):
    d1, d2 = r[("06_reviews", "6_1")], r[("06_reviews", "6_3")]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))
    ax1.bar(d1["review_score"].astype(str), d1["pct"], color=C_BLUE, width=0.6)
    for i, p in enumerate(d1["pct"]):
        ax1.text(i, float(p) + 1, f"{p}%", ha="center", fontsize=9, color=INK2)
    ax1.set_xlabel("評分")
    ax1.set_ylabel("%")
    ax1.set_title("評分分布（每單最新評論）")
    style_ax(ax1)
    ax2.bar(d2["review_score"].astype(str), d2["comment_rate_pct"], color=C_BLUE, width=0.6)
    for i, p in enumerate(d2["comment_rate_pct"]):
        ax2.text(i, float(p) + 1, f"{p}%", ha="center", fontsize=9, color=INK2)
    ax2.set_xlabel("評分")
    ax2.set_ylabel("留言率 %")
    ax2.set_title("留言率 × 評分（差評更愛寫字）")
    style_ax(ax2)
    save_fig(fig, "06_score_comment.png")


def fig_06_delay_score(r):
    df = r[("06_reviews", "6_2")]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    x = np.arange(len(df))
    ax.bar(x, df["avg_score"], color=C_BLUE, width=0.6)
    for i, (s, lr, n) in enumerate(zip(df["avg_score"], df["low_score_rate_pct"], df["n_orders"])):
        ax.text(i, float(s) + 0.08, f"{s}", ha="center", fontsize=10, color=INK1)
        ax.text(i, 0.25, f"1-2 分佔 {lr}%\nn={int(n):,}", ha="center", fontsize=8, color=SURFACE)
    ax.set_xticks(x)
    ax.set_xticklabels(df["delay_bin"])
    ax.set_ylim(0, 5.3)
    ax.set_ylabel("平均評分")
    ax.set_title("配送延遲 × 評分：晚 8 天以上，評分直接崩盤")
    style_ax(ax)
    save_fig(fig, "06_delay_vs_score.png")


def fig_06_lowscore_cat(r):
    df = r[("06_reviews", "6_4")].sort_values("low_score_rate_pct")
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.barh(df["category"], df["low_score_rate_pct"], color=C_BLUE, height=0.65)
    for i, (lr, n) in enumerate(zip(df["low_score_rate_pct"], df["n_orders"])):
        ax.text(float(lr) + 0.2, i, f"{lr}%（n={int(n):,}）", va="center", fontsize=7.5, color=INK2)
    ax.set_xlim(0, float(df["low_score_rate_pct"].max()) * 1.3)
    ax.set_xlabel("1-2 分訂單佔比 %")
    ax.set_title("品類低分率 Top 15（訂單數 ≥ 200）")
    style_ax(ax, grid_axis="x")
    save_fig(fig, "06_lowscore_category.png")


FIGURES = {
    "01_revenue":   [fig_01_gmv_mom, fig_01_orders_aov],
    "02_rfm":       [fig_02_rm_distribution, fig_02_segments, fig_02_rxm_heatmap, fig_02_state_pareto],
    "03_cohort":    [fig_03_second_curve, fig_03_cohort_heatmap],
    "04_category":  [fig_04_top15, fig_04_pareto, fig_04_freight],
    "05_logistics": [fig_05_delivery_dist, fig_05_monthly, fig_05_state, fig_05_stages],
    "06_reviews":   [fig_06_score_comment, fig_06_delay_score, fig_06_lowscore_cat],
}


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="Stage B 分析 runner")
    ap.add_argument("--themes", help="逗號分隔的主題前綴，如 01,05；預設全部")
    ap.add_argument("--list", action="store_true", help="列出全部查詢後退出")
    ap.add_argument("--no-figures", action="store_true", help="只跑 SQL 不出圖")
    args = ap.parse_args()

    files = sorted(p for p in SQL_DIR.glob("*.sql") if p.stem != "00_views")
    if args.themes:
        keys = [k.strip() for k in args.themes.split(",")]
        files = [f for f in files if any(f.stem.startswith(k) for k in keys)]

    if args.list:
        for f in files:
            for name, nocsv, _ in parse_queries(f.read_text(encoding="utf-8")):
                print(f"{f.stem}: {name}{'（nocsv）' if nocsv else ''}")
        return

    t0 = time.perf_counter()
    results: dict = {}
    conn = connect()
    try:
        with conn.cursor() as cur:
            run_views(cur)
            for f in files:
                execute_file(cur, f, results)
    finally:
        conn.close()

    if not args.no_figures:
        print("\n[figures]")
        for stem, fns in FIGURES.items():
            if any(k[0] == stem for k in results):
                for fn in fns:
                    fn(results)

    print(f"\n[done] 總耗時 {time.perf_counter() - t0:.1f} 秒")


if __name__ == "__main__":
    try:
        main()
    except pymysql.MySQLError as e:
        print(f"[SQL 錯誤] {e}", file=sys.stderr)
        sys.exit(1)
