"""探勘一：RFM KMeans 分群。

用法：
    uv run python mining/rfm_clustering.py

流程（階層式兩層）：
    1. SQL 取每位自然人（customer_unique_id）的原始 R/F/M（delivered、GMV 口徑，
       與 sql/07 同一套口徑；R 基準日=資料內最大下單日）
    2. 特徵工程：M 先 log1p（重尾分布直接標準化會被極端值綁架）→ StandardScaler
    3. 【主分群】KMeans k=2..8 掃描，silhouette（10K 抽樣）選 k。
       實測結果 k=2 勝出且切線正是 F=1 vs F>=2——非監督方法重新發現了
       「97% 一次客」的二元結構，與 SQL 側 F 固定分箱決策互相印證。
    4. 【子分群】k=2 只回答了「誰是回頭客」；對 97% 的 F=1 主群再以 R×M 兩維
       做第二層掃描（k=2..6 silhouette 選 k），產出有行動意義的商業分群。
    5. 輸出：兩層畫像表＋規則式商業命名 → results/mining/rfm_clusters.csv、
       rfm_clusters_f1.csv；silhouette 曲線＋F=1 子群散點圖 →
       results/figs/rfm_clusters.png

誠實聲明：F 高度退化（97% F=1）導致全量分群結構由 F 主導、子分群由 R×M 主導；
silhouette 全程如實列出；random_state 固定，結果可重現。
"""

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from tabulate import tabulate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = PROJECT_ROOT / "results" / "mining" / "rfm_clusters.csv"
OUT_CSV_F1 = PROJECT_ROOT / "results" / "mining" / "rfm_clusters_f1.csv"
OUT_FIG = PROJECT_ROOT / "results" / "figs" / "rfm_clusters.png"
SEED = 42

# dataviz reference palette（light）
SURFACE, INK1, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
CAT = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]

plt.rcParams.update({
    "font.sans-serif": ["Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif"],
    "font.family": "sans-serif", "axes.unicode_minus": False,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK1, "axes.labelcolor": INK2, "axes.titlecolor": INK1,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": "#c3c2b7",
    "grid.color": GRID, "grid.linewidth": 0.8,
})

RFM_SQL = """
WITH anchor AS (
    SELECT MAX(order_purchase_timestamp) AS anchor_ts
    FROM orders WHERE order_status = 'delivered'
)
SELECT c.customer_unique_id,
       DATEDIFF((SELECT anchor_ts FROM anchor), MAX(o.order_purchase_timestamp)) AS recency_days,
       COUNT(DISTINCT o.order_id) AS frequency,
       SUM(g.gmv)                 AS monetary
FROM orders o
JOIN customers   c ON c.customer_id = o.customer_id
JOIN v_order_gmv g ON g.order_id    = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY c.customer_unique_id
"""


def fetch_rfm() -> pd.DataFrame:
    load_dotenv(PROJECT_ROOT / ".env")
    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(RFM_SQL)
            df = pd.DataFrame(cur.fetchall(),
                              columns=["customer_unique_id", "recency_days", "frequency", "monetary"])
    finally:
        conn.close()
    df["recency_days"] = df["recency_days"].astype(int)
    df["frequency"] = df["frequency"].astype(int)
    df["monetary"] = df["monetary"].astype(float)
    return df


def name_cluster(row, r_terciles, m_terciles, f_aware=True) -> str:
    """規則式命名。F 優先（回頭客是本資料集最稀有的身分），R/M 以群「中位數」
    對照母體三分位（偏態分布下平均值會誤導命名）。"""
    if f_aware and row["avg_frequency"] >= 1.5:
        return "回頭客群"
    r, m = row["med_recency_days"], row["med_monetary"]
    r_part = "近期" if r <= r_terciles[0] else ("中期" if r <= r_terciles[1] else "流失")
    m_part = "高消費" if m >= m_terciles[1] else ("中消費" if m >= m_terciles[0] else "低消費")
    return f"{r_part}{m_part}一次客" if f_aware else f"{r_part}{m_part}"


def scan_kmeans(Xs, k_range):
    scores, labelings = {}, {}
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=10, random_state=SEED)
        labels = km.fit_predict(Xs)
        scores[k] = silhouette_score(Xs, labels, sample_size=10_000, random_state=SEED)
        labelings[k] = labels
        print(f"  k={k}: silhouette={scores[k]:.4f}")
    best_k = max(scores, key=scores.get)
    return best_k, scores, labelings[best_k]


def profile_clusters(df, r_terciles, m_terciles, f_aware):
    prof = df.groupby("cluster").agg(
        n_customers=("customer_unique_id", "count"),
        avg_recency_days=("recency_days", "mean"),
        med_recency_days=("recency_days", "median"),
        avg_frequency=("frequency", "mean"),
        avg_monetary=("monetary", "mean"),
        med_monetary=("monetary", "median"),
        total_gmv=("monetary", "sum"),
    ).reset_index()
    prof["pct_customers"] = (prof["n_customers"] / prof["n_customers"].sum() * 100).round(1)
    prof["pct_gmv"] = (prof["total_gmv"] / prof["total_gmv"].sum() * 100).round(1)
    prof["name"] = prof.apply(name_cluster, axis=1, args=(r_terciles, m_terciles, f_aware))
    if prof["name"].duplicated().any():
        prof["name"] = prof["name"] + "#" + prof["cluster"].astype(str)
    return prof.sort_values("n_customers", ascending=False)


def save_profile(prof, path):
    out = prof[["cluster", "name", "n_customers", "pct_customers", "avg_recency_days",
                "med_recency_days", "avg_frequency", "avg_monetary", "med_monetary",
                "pct_gmv"]].round(
        {"avg_recency_days": 0, "med_recency_days": 0, "avg_frequency": 2,
         "avg_monetary": 2, "med_monetary": 2})
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    print(tabulate(out, headers=out.columns, tablefmt="github", showindex=False))
    return out


def main():
    df = fetch_rfm()
    print(f"[data] {len(df):,} 位自然人客戶（delivered）")
    r_terciles = df["recency_days"].quantile([1 / 3, 2 / 3]).values
    m_terciles = df["monetary"].quantile([1 / 3, 2 / 3]).values

    # ---- 第一層：全量 R/F/M ----
    X = df[["recency_days", "frequency", "monetary"]].copy()
    X["monetary"] = np.log1p(X["monetary"])
    Xs = StandardScaler().fit_transform(X)
    print("[scan-1] 全量 R/F/M，k=2..8（silhouette 10K 抽樣）")
    best_k, scores_full, labels = scan_kmeans(Xs, range(2, 9))
    df["cluster"] = labels
    print(f"[pick-1] best k={best_k}（silhouette={scores_full[best_k]:.4f}）")
    print("\n[主分群畫像]")
    prof_full = profile_clusters(df, r_terciles, m_terciles, f_aware=True)
    save_profile(prof_full, OUT_CSV)

    # ---- 第二層：F=1 主群（97%）的 R×M 子分群 ----
    f1 = df[df["frequency"] == 1].copy()
    Xf = f1[["recency_days", "monetary"]].copy()
    Xf["monetary"] = np.log1p(Xf["monetary"])
    Xfs = StandardScaler().fit_transform(Xf)
    print(f"\n[scan-2] F=1 子集（{len(f1):,} 人）R×M，k=2..6")
    best_k2, scores_f1, labels_f1 = scan_kmeans(Xfs, range(2, 7))
    f1["cluster"] = labels_f1
    print(f"[pick-2] best k={best_k2}（silhouette={scores_f1[best_k2]:.4f}）")
    r_ter_f1 = f1["recency_days"].quantile([1 / 3, 2 / 3]).values
    m_ter_f1 = f1["monetary"].quantile([1 / 3, 2 / 3]).values
    print("\n[F=1 子分群畫像]")
    prof_f1 = profile_clusters(f1, r_ter_f1, m_ter_f1, f_aware=False)
    save_profile(prof_f1, OUT_CSV_F1)

    # ---- 圖：兩層 silhouette 曲線 + F=1 子群 R×M 散點 ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.8),
                                   gridspec_kw={"width_ratios": [1, 2]})
    ax1.plot(list(scores_full), list(scores_full.values()), color=CAT[0], linewidth=2,
             marker="o", markersize=5, label="全量 R/F/M")
    ax1.plot(list(scores_f1), list(scores_f1.values()), color=CAT[1], linewidth=2,
             marker="o", markersize=5, label="F=1 子集 R×M")
    ax1.scatter([best_k], [scores_full[best_k]], color=CAT[5], s=70, zorder=3)
    ax1.scatter([best_k2], [scores_f1[best_k2]], color=CAT[5], s=70, zorder=3)
    ax1.set_xlabel("k")
    ax1.set_ylabel("silhouette（10K 抽樣）")
    ax1.set_title(f"k 掃描：全量 k={best_k}、F=1 子集 k={best_k2}")
    ax1.legend(frameon=False, fontsize=8)
    ax1.grid(axis="y", linewidth=0.8)
    ax1.spines[["top", "right"]].set_visible(False)

    plot_df = f1.sample(n=min(15_000, len(f1)), random_state=SEED)
    name_of = dict(zip(prof_f1["cluster"], prof_f1["name"]))
    n_of = dict(zip(prof_f1["cluster"], prof_f1["n_customers"]))
    for i, cl in enumerate(prof_f1["cluster"]):
        g = plot_df[plot_df["cluster"] == cl]
        ax2.scatter(g["recency_days"], g["monetary"], s=6, alpha=0.45, color=CAT[i % len(CAT)],
                    label=f"{name_of[cl]}（{n_of[cl]:,}）")
    ax2.set_yscale("log")
    ax2.set_xlabel("Recency（距最後購買天數）")
    ax2.set_ylabel("Monetary（R$，log）")
    ax2.set_title(f"F=1 主群（97%）的 R×M 子分群（k={best_k2}，抽樣 15K 點）")
    ax2.legend(frameon=False, fontsize=8, markerscale=2)
    ax2.grid(axis="both", linewidth=0.8)
    ax2.spines[["top", "right"]].set_visible(False)

    OUT_FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG, dpi=150, bbox_inches="tight")
    print(f"\n[fig] {OUT_FIG.relative_to(PROJECT_ROOT)}")
    print(f"[csv] {OUT_CSV.relative_to(PROJECT_ROOT)}、{OUT_CSV_F1.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
