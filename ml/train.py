"""晚到預測：LightGBM 訓練與評估（時間切分、三 baseline、SHAP）。

用法：
    uv run python ml/train.py

紅線落實：開頭強制執行 ml/test_features.py（時點正確性驗證），未通過即拒絕訓練。

設計要點：
    * 時間切分：按購買月份月界動態切——測試期＝使占比最接近 20% 的尾端月份；
      訓練期尾端再切 ~10% 當 validation（早停＋選閾值），train/val/test 時間嚴格遞增。
      賣家歷史特徵跨越切分點不是洩漏：每筆訂單只用「其購買時點之前已實現」的資訊，
      生產環境預測當下同樣可得；時間切分防的是「測試期資訊影響模型參數」，兩者正交。
    * LightGBM 固定超參不搜參（作品集場景，避免「搜參是否用了測試集」的質疑）；
      類別特徵用原生 categorical（levels 以訓練集為準）；不加 scale_pos_weight
      （保機率排序與校準，操作點交給閾值）；SEED=42、force_row_wise 求可重現。
    * 評估：AUC、PR-AUC（average_precision）、混淆矩陣（val max-F1 閾值＋0.5 對照）、
      precision@top-10%；PR 基準線＝「測試集實際盛行率」（測試期物流最佳＋尾端
      censoring，盛行率低於全域 6.8%，用全域值會誤導）。
    * 三 baseline：A 全猜不遲到（常數分數）；B 跨州即遲到（二值分數，AUC=balanced
      accuracy）；C 單特徵 seller_hist_late_rate（NULL 補訓練期晚到率僅供排序）——
      模型必須贏過最強單一特徵才算數。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import lightgbm as lgb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymysql
import shap
from dotenv import load_dotenv
from sklearn.metrics import (average_precision_score, confusion_matrix,
                             precision_recall_curve, roc_auc_score, roc_curve)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = PROJECT_ROOT / "results" / "figs"
METRICS_PATH = PROJECT_ROOT / "results" / "ml_metrics.json"
SEED = 42

SURFACE, INK1, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
C_BLUE, C_AQUA, C_YELLOW, C_RED = "#2a78d6", "#1baf7a", "#eda100", "#e34948"
plt.rcParams.update({
    "font.sans-serif": ["Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif"],
    "font.family": "sans-serif", "axes.unicode_minus": False,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK1, "axes.labelcolor": INK2, "axes.titlecolor": INK1,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.edgecolor": "#c3c2b7",
    "grid.color": GRID, "grid.linewidth": 0.8,
})

CAT_COLS = ["customer_state", "seller_state", "product_category"]
DROP_COLS = ["order_id", "purchase_ts", "is_late", "ym"]

PARAMS = dict(
    objective="binary", n_estimators=2000, learning_rate=0.05, num_leaves=63,
    min_child_samples=50, colsample_bytree=0.9, subsample=0.9, subsample_freq=1,
    reg_lambda=1.0, random_state=SEED, force_row_wise=True, verbose=-1,
)


def connect():
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4",
    )


def month_boundary_cut(months_sorted, sizes, target_tail_share):
    """從尾端往回找「尾段占比最接近 target」的月界，回傳尾段起始月。"""
    total = sizes.sum()
    best, best_diff = None, float("inf")
    tail = 0
    for m in reversed(months_sorted):
        tail += sizes[m]
        diff = abs(tail / total - target_tail_share)
        if diff < best_diff:
            best, best_diff = m, diff
    return best


def prevalence_metrics(y, score, name):
    return {"name": name, "auc": round(float(roc_auc_score(y, score)), 4),
            "pr_auc": round(float(average_precision_score(y, score)), 4)}


def cls_metrics(y, pred):
    tn, fp, fn, tp = confusion_matrix(y, pred).ravel()
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def main():
    # ---- 紅線：時點驗證不過不准訓練 ----
    print("[gate] 執行 ml/test_features.py …")
    r = subprocess.run([sys.executable, str(PROJECT_ROOT / "ml" / "test_features.py")])
    if r.returncode != 0:
        print("[gate] 時點特徵驗證失敗——拒絕訓練", file=sys.stderr)
        sys.exit(1)
    print("[gate] 驗證通過\n")

    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ml_features ORDER BY purchase_ts")
            df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
            # 尾端 censoring 量化：各購買月的「非 delivered / 無送達時間」占比
            cur.execute("""
                SELECT DATE_FORMAT(order_purchase_timestamp, '%Y-%m') AS ym,
                       COUNT(*) AS n_orders,
                       ROUND(AVG(order_status <> 'delivered'
                                 OR order_delivered_customer_date IS NULL) * 100, 2) AS excluded_pct
                FROM orders GROUP BY ym ORDER BY ym""")
            censor = pd.DataFrame(cur.fetchall(), columns=["ym", "n_orders", "excluded_pct"])
            censor["n_orders"] = censor["n_orders"].astype(int)
            censor["excluded_pct"] = censor["excluded_pct"].astype(float)
    finally:
        conn.close()

    for c in df.columns:
        if c not in CAT_COLS + ["order_id", "purchase_ts"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["ym"] = df["purchase_ts"].dt.strftime("%Y-%m")

    # ---- 月界時間切分 ----
    sizes = df.groupby("ym").size()
    months = sorted(sizes.index)
    test_start = month_boundary_cut(months, sizes, 0.20)
    trainval = [m for m in months if m < test_start]
    tv_sizes = sizes[sizes.index < test_start]
    val_start = month_boundary_cut(trainval, tv_sizes, 0.10)

    tr = df[df["ym"] < val_start].copy()
    va = df[(df["ym"] >= val_start) & (df["ym"] < test_start)].copy()
    te = df[df["ym"] >= test_start].copy()
    split_info = []
    for name, part in [("train", tr), ("val", va), ("test", te)]:
        split_info.append({"split": name, "months": f"{part['ym'].min()}~{part['ym'].max()}",
                           "rows": len(part), "share": round(len(part) / len(df), 4),
                           "late_rate": round(float(part['is_late'].mean()), 4)})
        print(f"[split] {name:<5} {part['ym'].min()}~{part['ym'].max()}  "
              f"{len(part):>6,} 列（{len(part)/len(df)*100:.1f}%）晚到率 {part['is_late'].mean():.4f}")

    # ---- 特徵準備（categorical levels 以訓練集為準）----
    feats = [c for c in df.columns if c not in DROP_COLS]
    for c in CAT_COLS:
        levels = sorted(tr[c].dropna().unique())
        dtype = pd.CategoricalDtype(categories=levels)
        for part in (tr, va, te):
            # 訓練集未見過的類別先明確設為 NaN（走 LightGBM missing 分支）
            part[c] = part[c].where(part[c].isin(levels)).astype(dtype)
    Xtr, ytr = tr[feats], tr["is_late"].astype(int)
    Xva, yva = va[feats], va["is_late"].astype(int)
    Xte, yte = te[feats], te["is_late"].astype(int)
    test_prev = float(yte.mean())

    # ---- 訓練 ----
    model = lgb.LGBMClassifier(**PARAMS)
    model.fit(Xtr, ytr, eval_set=[(Xva, yva)], eval_metric="auc",
              callbacks=[lgb.early_stopping(100, verbose=False)])
    print(f"\n[model] best_iteration = {model.best_iteration_}")

    p_va = model.predict_proba(Xva)[:, 1]
    p_te = model.predict_proba(Xte)[:, 1]

    # ---- 閾值：val max-F1 ----
    prec, rec, thr = precision_recall_curve(yva, p_va)
    f1s = 2 * prec[:-1] * rec[:-1] / np.clip(prec[:-1] + rec[:-1], 1e-12, None)
    best_thr = float(thr[int(np.argmax(f1s))])
    print(f"[threshold] val max-F1 閾值 = {best_thr:.4f}（F1={f1s.max():.4f}）")

    # ---- 主結果與 baseline ----
    results = []
    m = prevalence_metrics(yte, p_te, "LightGBM")
    m.update(cls_metrics(yte, (p_te >= best_thr).astype(int)))
    results.append(m)

    bA = prevalence_metrics(yte, np.zeros(len(yte)), "Baseline A 全猜不遲到")
    bA.update(cls_metrics(yte, np.zeros(len(yte), dtype=int)))
    bA["note"] = "常數分數：AUC 恆 0.5、PR-AUC=盛行率"
    results.append(bA)

    cross = Xte["is_cross_state"].to_numpy()
    bB = prevalence_metrics(yte, cross, "Baseline B 跨州即遲到")
    bB.update(cls_metrics(yte, cross.astype(int)))
    bB["note"] = "二值分數的 AUC = balanced accuracy"
    results.append(bB)

    hist_rate = Xte["seller_hist_late_rate"].fillna(float(ytr.mean())).to_numpy()
    bC = prevalence_metrics(yte, hist_rate, "Baseline C 賣家歷史遲到率單特徵")
    bC["note"] = "冷啟動 NULL 補訓練期晚到率（僅供排序）"
    results.append(bC)

    print(f"\n[results] 測試集 {len(yte):,} 列、盛行率 {test_prev:.4f}")
    for r_ in results:
        print(f"  {r_['name']:<28} AUC={r_['auc']:.4f}  PR-AUC={r_['pr_auc']:.4f}"
              + (f"  P={r_['precision']:.3f} R={r_['recall']:.3f} F1={r_['f1']:.3f}"
                 if "f1" in r_ else ""))

    cm_thr = cls_metrics(yte, (p_te >= best_thr).astype(int))
    cm_05 = cls_metrics(yte, (p_te >= 0.5).astype(int))
    k = max(1, int(len(yte) * 0.10))
    top_idx = np.argsort(-p_te)[:k]
    p_at_10 = float(yte.to_numpy()[top_idx].mean())
    print(f"[operating] precision@top-10%（{k:,} 列）= {p_at_10:.4f}（盛行率 {test_prev:.4f} 的 "
          f"{p_at_10 / test_prev:.1f} 倍）")

    monthly_auc = [{"ym": m_, "auc": round(float(roc_auc_score(g["is_late"],
                    p_te[te["ym"] == m_])), 4), "n": len(g)}
                   for m_, g in te.groupby("ym") if g["is_late"].nunique() > 1]

    # ---- 圖 ----
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    fpr, tpr, _ = roc_curve(yte, p_te)
    ax.plot(fpr, tpr, color=C_BLUE, linewidth=2, label=f"LightGBM（AUC={m['auc']:.3f}）")
    fprC, tprC, _ = roc_curve(yte, hist_rate)
    ax.plot(fprC, tprC, color=C_AQUA, linewidth=2,
            label=f"賣家歷史遲到率（AUC={bC['auc']:.3f}）")
    ax.plot([0, 1], [0, 1], color=MUTED, linewidth=1.2, linestyle="--",
            label="全猜不遲到（AUC=0.5）")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("ROC（測試期）")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(FIG_DIR / "roc_curve.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    prec_te, rec_te, _ = precision_recall_curve(yte, p_te)
    ax.plot(rec_te, prec_te, color=C_BLUE, linewidth=2,
            label=f"LightGBM（PR-AUC={m['pr_auc']:.3f}）")
    precC, recC, _ = precision_recall_curve(yte, hist_rate)
    ax.plot(recC, precC, color=C_AQUA, linewidth=2,
            label=f"賣家歷史遲到率（PR-AUC={bC['pr_auc']:.3f}）")
    ax.axhline(test_prev, color=MUTED, linewidth=1.2, linestyle="--",
               label=f"測試集盛行率 {test_prev:.3f}")
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Precision-Recall（測試期）")
    ax.legend(frameon=False, fontsize=9)
    ax.grid(linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(FIG_DIR / "pr_curve.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    explainer = shap.TreeExplainer(model.booster_)
    sv = explainer(Xte)
    if sv.values.ndim == 3:  # 二元分類部分版本回傳兩類
        sv = sv[..., 1]
    plt.figure()
    shap.plots.beeswarm(sv, max_display=20, show=False)
    plt.title("SHAP 全局重要度（測試期全量；單位 log-odds）", fontsize=11)
    plt.savefig(FIG_DIR / "shap_summary.png", dpi=150, bbox_inches="tight")
    plt.close("all")
    print(f"[figs] roc_curve / pr_curve / shap_summary → {FIG_DIR.relative_to(PROJECT_ROOT)}")

    METRICS_PATH.write_text(json.dumps({
        "split": split_info, "test_prevalence": round(test_prev, 4),
        "best_iteration": int(model.best_iteration_), "threshold_val_maxf1": round(best_thr, 4),
        "results": results, "confusion_at_threshold": cm_thr, "confusion_at_0.5": cm_05,
        "precision_at_top10pct": round(p_at_10, 4), "monthly_auc": monthly_auc,
        "censoring_by_month": censor.tail(8).to_dict("records"),
        "params": {k: str(v) for k, v in PARAMS.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[metrics] {METRICS_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
