"""ml_features 時點正確性驗證——不過不准訓練（ml/train.py 開頭強制執行本檔）。

用法：
    uv run python ml/test_features.py

三層驗證：
    1. 性質測試（主力）：固定種子抽 200 筆訂單，用 pandas 從「原始表」獨立重算
       時點聚合／主賣家／label／各特徵，精確比對 ml_features。
       期望值完全不參照 ml/features.sql 的任何邏輯（防循環論證）；
       嚴格「<」語意（h.purchase < t、h.delivered < t）由此測涵蓋。
    2. 手工案例 4 筆：order_id 與期望值為人工逐列讀取賣家原始歷史核算後硬編碼
       （2026-07-18 核對；綁定資料快照，重抓資料後測試紅屬預期，見 README）。
       Case1 冷啟動首單／Case2 一般手算對照／Case3 在途單排除（h 購於 t 前、
       送達於 t 後→計入接單量、不入已揭曉分母）／Case4 非 delivered 狀態歷史單
       （shipped 永未送達→計入接單量、不入分母——接單量必須從全狀態 orders 算）。
    3. 全域 sanity：母體筆數、晚到率、open>=0、est_days>=0、
       late_rate IS NULL ⇔ delivered_cnt=0。
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pymysql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED = 42
N_SAMPLE = 200

# ---- 手工案例（人工核算後硬編碼；(order_cnt, delivered_cnt, late_rate, open_cnt, overdue_open)）----
HAND_CASES = {
    "00018f77f2f0320c557190d7a144bdd3": (0, 0, None, 0, 0),   # Case1 冷啟動首單
    "0029f17cf0e7640c5cb6825af681303f": (6, 4, 0.5, 2, 1),    # Case2 一般手算
    "00042b26cf59d7ce69dfabb4e55b4fd9": (3, 2, 0.0, 1, 0),    # Case3 在途單排除
    "00254baeb6c932b0a8aeead91fbd02b5": (4, 3, 0.0, 1, 0),    # Case4 shipped 未送達單
}


def connect():
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4",
    )


def read(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])


def main():
    conn = connect()
    try:
        mf = read(conn, "SELECT * FROM ml_features")
        orders = read(conn, """SELECT order_id, customer_id, order_status,
                                      order_purchase_timestamp AS pts,
                                      order_delivered_customer_date AS dts,
                                      order_estimated_delivery_date AS ets
                               FROM orders""")
        items = read(conn, "SELECT order_id, seller_id, product_id, price, freight_value FROM order_items")
        products = read(conn, """SELECT p.product_id, p.product_weight_g, p.product_length_cm,
                                        p.product_height_cm, p.product_width_cm,
                                        COALESCE(t.product_category_name_english,
                                                 p.product_category_name, '(uncategorized)') AS category
                                 FROM products p
                                 LEFT JOIN product_category_name_translation t
                                   ON t.product_category_name = p.product_category_name""")
        customers = read(conn, "SELECT customer_id, customer_state FROM customers")
        sellers = read(conn, "SELECT seller_id, seller_state FROM sellers")
        pay = read(conn, "SELECT order_id, MAX(payment_installments) AS max_inst FROM order_payments GROUP BY order_id")
    finally:
        conn.close()

    for c in ["seller_hist_late_rate", "freight_total", "price_total",
              "total_weight_g", "total_volume_cm3"]:
        mf[c] = mf[c].astype(float)
    mf = mf.set_index("order_id")
    fails = []

    def check(order_id, field, actual, expected, tol=None):
        ok = ((actual is None and expected is None)
              or (actual is not None and expected is not None
                  and (abs(float(actual) - float(expected)) <= (tol or 0))))
        if not ok:
            fails.append(f"{order_id}.{field}: 實際={actual} 期望={expected}")

    # ---- 獨立重算基礎（只用原始表；與 features.sql 邏輯零共用）----
    sp = (items.groupby(["order_id", "seller_id"])["price"].sum().reset_index()
               .sort_values(["order_id", "price", "seller_id"], ascending=[True, False, True]))
    main_seller = sp.drop_duplicates("order_id").set_index("order_id")["seller_id"]

    so = orders.merge(main_seller.rename("m_seller"), left_on="order_id", right_index=True)
    so["is_late_hist"] = ((so["order_status"] == "delivered") & so["dts"].notna()
                          & (so["dts"].dt.date > so["ets"].dt.date))
    by_seller = dict(tuple(so.groupby("m_seller", sort=False)))

    ip = items.merge(products, on="product_id")
    orders_ix = orders.set_index("order_id")
    cust_state = customers.set_index("customer_id")["customer_state"]
    seller_state = sellers.set_index("seller_id")["seller_state"]
    pay_ix = pay.set_index("order_id")["max_inst"]

    def recompute(oid):
        row = orders_ix.loc[oid]
        t = row["pts"]
        s = main_seller.loc[oid]
        h = by_seller[s]
        h = h[h["pts"] < t]                                   # 嚴格 <
        cnt = len(h)
        known = h["dts"].notna() & (h["dts"] < t)             # t 時結果已揭曉
        dcnt = int(known.sum())
        late = int((known & h["is_late_hist"]).sum())
        open_cnt = cnt - dcnt
        overdue = int(((~known) & (h["ets"].dt.date < t.date())).sum())
        oi = ip[ip["order_id"] == oid]
        cat = oi.sort_values(["price", "product_id"], ascending=[False, True]).iloc[0]["category"]
        w = oi["product_weight_g"].astype(float)
        vol = (oi["product_length_cm"].astype(float) * oi["product_height_cm"].astype(float)
               * oi["product_width_cm"].astype(float))
        return {
            "order_cnt": cnt, "delivered_cnt": dcnt,
            "late_rate": (late / dcnt) if dcnt else None,
            "open_cnt": open_cnt, "overdue_open": overdue,
            "is_late": int(row["dts"].date() > row["ets"].date()),
            "est_days": (row["ets"].date() - t.date()).days,
            "freight_total": float(oi["freight_value"].sum()),
            "price_total": float(oi["price"].sum()),
            "n_items": len(oi), "n_sellers": oi["seller_id"].nunique(),
            "total_weight_g": None if w.isna().all() else float(w.sum()),
            "total_volume_cm3": None if vol.isna().all() else float(vol.sum()),
            "is_cross_state": int(cust_state[row["customer_id"]] != seller_state[s]),
            "product_category": cat,
            "max_installments": pay_ix.get(oid),
        }

    def compare(oid, exp):
        a = mf.loc[oid]
        check(oid, "order_cnt", a["seller_hist_order_cnt"], exp["order_cnt"])
        check(oid, "delivered_cnt", a["seller_hist_delivered_cnt"], exp["delivered_cnt"])
        lr = a["seller_hist_late_rate"]
        check(oid, "late_rate", None if pd.isna(lr) else lr, exp["late_rate"], tol=1e-6)
        check(oid, "open_cnt", a["seller_hist_open_cnt"], exp["open_cnt"])
        check(oid, "overdue_open", a["seller_hist_overdue_open_cnt"], exp["overdue_open"])

    # ---- 1) 性質測試：200 筆抽樣、全特徵重算比對 ----
    rng = np.random.default_rng(SEED)
    sample = rng.choice(mf.index.to_numpy(), size=N_SAMPLE, replace=False)
    for oid in sample:
        exp = recompute(oid)
        a = mf.loc[oid]
        compare(oid, exp)
        check(oid, "is_late", a["is_late"], exp["is_late"])
        check(oid, "est_days", a["est_days"], exp["est_days"])
        check(oid, "freight_total", a["freight_total"], exp["freight_total"], tol=1e-4)
        check(oid, "price_total", a["price_total"], exp["price_total"], tol=1e-4)
        check(oid, "n_items", a["n_items"], exp["n_items"])
        check(oid, "n_sellers", a["n_sellers"], exp["n_sellers"])
        check(oid, "total_weight_g",
              None if pd.isna(a["total_weight_g"]) else a["total_weight_g"],
              exp["total_weight_g"], tol=1e-4)
        check(oid, "total_volume_cm3",
              None if pd.isna(a["total_volume_cm3"]) else a["total_volume_cm3"],
              exp["total_volume_cm3"], tol=1e-4)
        check(oid, "is_cross_state", a["is_cross_state"], exp["is_cross_state"])
        if a["product_category"] != exp["product_category"]:
            fails.append(f"{oid}.category: 實際={a['product_category']} 期望={exp['product_category']}")
        mi = a["max_installments"]
        check(oid, "max_installments",
              None if pd.isna(mi) else mi, exp["max_installments"])
    print(f"[1] 性質測試：{N_SAMPLE} 筆全特徵獨立重算比對 {'通過' if not fails else '失敗'}")

    # ---- 2) 手工案例 ----
    for oid, (cnt, dcnt, lr, open_c, overdue) in HAND_CASES.items():
        compare(oid, {"order_cnt": cnt, "delivered_cnt": dcnt, "late_rate": lr,
                      "open_cnt": open_c, "overdue_open": overdue})
    print(f"[2] 手工案例：{len(HAND_CASES)} 筆硬編碼期望值比對 {'通過' if not fails else '失敗'}")

    # ---- 3) 全域 sanity ----
    assert len(mf) == 96470, f"母體筆數 {len(mf)} != 96470"
    late_mean = mf["is_late"].mean()
    assert abs(late_mean - 0.068) < 0.005, f"晚到率 {late_mean:.4f} 偏離 0.068±0.005"
    assert (mf["seller_hist_open_cnt"] >= 0).all(), "open_cnt 出現負值"
    assert (mf["est_days"] >= 0).all(), "est_days 出現負值"
    null_lr = mf["seller_hist_late_rate"].isna()
    zero_d = mf["seller_hist_delivered_cnt"] == 0
    assert (null_lr == zero_d).all(), "late_rate NULL 與 delivered_cnt=0 不同構"
    print(f"[3] 全域 sanity：母體 96,470、晚到率 {late_mean:.4f}、冷啟動 {null_lr.sum():,} 筆 通過")

    if fails:
        print(f"\n[FAIL] {len(fails)} 項不符：")
        for f in fails[:20]:
            print("  " + f)
        sys.exit(1)
    print("\n[PASS] ml_features 時點正確性驗證全數通過——允許訓練")


if __name__ == "__main__":
    main()
