"""ETL：data/raw 的 9 個 CSV → pandas 清理 → staging CSV → LOAD DATA LOCAL INFILE → MySQL。

用法：
    uv run python etl/load.py

流程：
    1. TRUNCATE 全部表（冪等：重跑結果一致；僅此步驟暫關 FK 檢查，清空狀態無完整性風險）
    2. 依 FK 順序載入 9 張表（載入期間 FK 檢查全程開啟，引用完整性由 DB 當場把關）
    3. 由去重後的 geolocation 生成衍生彙總表 geolocation_zip
    4. 寫出 data/expected_counts.json（驗證用 manifest：預期值來自實跑，不寫死）

清理規則（全表通用）：
    * 一律 dtype=str 讀取：保留 zip 前導零、金額字串原樣過境到 DECIMAL（不經 float）、
      products 數值欄（原始即整數字串）不受浮點污染
    * 所有欄位 str.strip()
    * 空字串 = NULL 語意（zip 欄除外，見下）：staging CSV 落地空字串，
      LOAD DATA 以 NULLIF(col,'') 轉 NULL
      （不用 \\N 標記——它與反斜線跳脫互相衝突，故 LOAD DATA 採 ESCAPED BY ''；
        實測評論文字含反斜線 0 筆，但 quoting 相容性仍以 ESCAPED BY '' 為準）
    * 時間欄以 to_datetime(errors='raise') 驗證格式（異常直接炸出，不靜默 coerce），
      原字串已符合 MySQL DATETIME 格式，staging 原樣落地
    * zip 欄 zfill(5) 補前導零（實測本已全為 5 位，此為防禦性保險）；
      strip 後為空字串者直接 raise——zip 為 CHAR(5) NOT NULL，空值無合法表示，
      補零會得到 '00000' 這個無從與真實值區分的合成 zip，故不套用上述空字串=NULL

逐表特例：
    * geolocation：五欄完全重複列去重（實測 1,000,163 → 738,332）；chunked 讀取
    * order_reviews：載入前 assert (review_id, order_id) 組合唯一（實測成立）；
      review_id 單獨重複是資料事實，保留全部列
    * products：CSV 欄名拼字錯誤 *_lenght → 建表欄名 *_length，於此做對映
    * geolocation_zip（衍生）：先過濾巴西 bbox（lat -34..6, lng -74..-28，
      東界含 Fernando de Noronha 離島）外的異常座標，每 zip 取座標中位數、
      city/state 眾數；「僅有境外座標」的 zip 不會出現在本表（無可信座標可給）
"""

import json
import os
import time
from pathlib import Path
from tempfile import gettempdir

import pandas as pd
import pymysql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
MANIFEST_PATH = PROJECT_ROOT / "data" / "expected_counts.json"
# staging 落 ASCII-safe 暫存目錄：專案路徑含中文與「×」，不進 LOAD DATA 語句
STAGING_DIR = Path(os.environ.get("OLIST_STAGING_DIR", Path(gettempdir()) / "olist_staging"))

DT_FORMAT = "%Y-%m-%d %H:%M:%S"

# 載入順序尊重 FK：父表在前。(表名, CSV 檔名, 清理函式, 欄位對映)
# 欄位對映：list[(db欄, csv欄)]，順序須與 staging CSV 欄序一致。


def _strip_all(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


def _check_datetime(df: pd.DataFrame, cols: list[str]) -> None:
    """驗證時間欄格式（空字串視為 NULL 跳過）；格式異常直接 raise。"""
    for c in cols:
        pd.to_datetime(df[c].replace("", pd.NA), format=DT_FORMAT, errors="raise")


def _zfill_zip(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """zip 欄補前導零；strip 後為空字串者直接 raise，不補零。
    zip 三表皆 CHAR(5) NOT NULL（schema.sql），空值無合法表示，
    且 ''.zfill(5) == '00000' 會產出看似正常的合成 zip（並與真實 '00000' 無從區分），
    故在補零前當場擋下，不延後依賴 LOAD DATA 的警告語意。"""
    blank = int(df[col].eq("").sum())
    if blank:
        raise ValueError(
            f"{col}：{blank} 列 strip 後為空字串——zip 為 NOT NULL 契約，"
            f"不可補零成 '00000' 合成值，中止載入"
        )
    df[col] = df[col].str.zfill(5)
    return df


def clean_translation(df):
    return _strip_all(df), None


def clean_customers(df):
    df = _strip_all(df)
    df = _zfill_zip(df, "customer_zip_code_prefix")
    return df, None


def clean_sellers(df):
    df = _strip_all(df)
    df = _zfill_zip(df, "seller_zip_code_prefix")
    return df, None


def clean_geolocation(df):
    df = _strip_all(df)
    df = _zfill_zip(df, "geolocation_zip_code_prefix")
    df = df.drop_duplicates()  # 去重差量的註記由 main 以「CSV 原始列數 - 最終列數」計算
    return df, None


def clean_products(df):
    return _strip_all(df), None


def clean_orders(df):
    df = _strip_all(df)
    _check_datetime(df, ["order_purchase_timestamp", "order_approved_at",
                         "order_delivered_carrier_date", "order_delivered_customer_date",
                         "order_estimated_delivery_date"])
    return df, None


def clean_order_items(df):
    df = _strip_all(df)
    _check_datetime(df, ["shipping_limit_date"])
    return df, None


def clean_order_payments(df):
    return _strip_all(df), None


def clean_order_reviews(df):
    df = _strip_all(df)
    _check_datetime(df, ["review_creation_date", "review_answer_timestamp"])
    dup = df.duplicated(["review_id", "order_id"]).sum()
    assert dup == 0, f"(review_id, order_id) 應唯一，實測重複 {dup} 列——中止載入"
    return df, "保留重複 review_id（複合 PK）"


TABLES = [
    ("product_category_name_translation", "product_category_name_translation.csv", clean_translation,
     [("product_category_name", "product_category_name"),
      ("product_category_name_english", "product_category_name_english")]),
    ("customers", "olist_customers_dataset.csv", clean_customers,
     [("customer_id", "customer_id"), ("customer_unique_id", "customer_unique_id"),
      ("customer_zip_code_prefix", "customer_zip_code_prefix"),
      ("customer_city", "customer_city"), ("customer_state", "customer_state")]),
    ("sellers", "olist_sellers_dataset.csv", clean_sellers,
     [("seller_id", "seller_id"), ("seller_zip_code_prefix", "seller_zip_code_prefix"),
      ("seller_city", "seller_city"), ("seller_state", "seller_state")]),
    ("geolocation", "olist_geolocation_dataset.csv", clean_geolocation,
     [("geolocation_zip_code_prefix", "geolocation_zip_code_prefix"),
      ("geolocation_lat", "geolocation_lat"), ("geolocation_lng", "geolocation_lng"),
      ("geolocation_city", "geolocation_city"), ("geolocation_state", "geolocation_state")]),
    ("products", "olist_products_dataset.csv", clean_products,
     [("product_id", "product_id"), ("product_category_name", "product_category_name"),
      ("product_name_length", "product_name_lenght"),          # CSV 原始欄名拼字錯誤
      ("product_description_length", "product_description_lenght"),  # 同上
      ("product_photos_qty", "product_photos_qty"), ("product_weight_g", "product_weight_g"),
      ("product_length_cm", "product_length_cm"), ("product_height_cm", "product_height_cm"),
      ("product_width_cm", "product_width_cm")]),
    ("orders", "olist_orders_dataset.csv", clean_orders,
     [("order_id", "order_id"), ("customer_id", "customer_id"), ("order_status", "order_status"),
      ("order_purchase_timestamp", "order_purchase_timestamp"),
      ("order_approved_at", "order_approved_at"),
      ("order_delivered_carrier_date", "order_delivered_carrier_date"),
      ("order_delivered_customer_date", "order_delivered_customer_date"),
      ("order_estimated_delivery_date", "order_estimated_delivery_date")]),
    ("order_items", "olist_order_items_dataset.csv", clean_order_items,
     [("order_id", "order_id"), ("order_item_id", "order_item_id"),
      ("product_id", "product_id"), ("seller_id", "seller_id"),
      ("shipping_limit_date", "shipping_limit_date"),
      ("price", "price"), ("freight_value", "freight_value")]),
    ("order_payments", "olist_order_payments_dataset.csv", clean_order_payments,
     [("order_id", "order_id"), ("payment_sequential", "payment_sequential"),
      ("payment_type", "payment_type"), ("payment_installments", "payment_installments"),
      ("payment_value", "payment_value")]),
    ("order_reviews", "olist_order_reviews_dataset.csv", clean_order_reviews,
     [("review_id", "review_id"), ("order_id", "order_id"), ("review_score", "review_score"),
      ("review_comment_title", "review_comment_title"),
      ("review_comment_message", "review_comment_message"),
      ("review_creation_date", "review_creation_date"),
      ("review_answer_timestamp", "review_answer_timestamp")]),
]

ALL_TABLES = [t[0] for t in TABLES] + ["geolocation_zip"]

# 東界 -28（非 -34）：含 Fernando de Noronha（lng≈-32.4，實測 zip 53990 有 6 個真座標點）
# 與 Trindade 等巴西離島；加納利/葡萄牙/菲律賓等真異常座標仍在界外。
BRAZIL_BBOX = {"lat_min": -34.0, "lat_max": 6.0, "lng_min": -74.0, "lng_max": -28.0}


def connect() -> pymysql.Connection:
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
        local_infile=True,
        autocommit=True,
    )


def read_csv(path: Path) -> pd.DataFrame:
    """dtype=str + keep_default_na=False：空欄回空字串（NULL 語意），不產生 NaN。
    geolocation（最大表）分塊讀取以控記憶體，其餘整檔。"""
    if path.name == "olist_geolocation_dataset.csv":
        chunks = [c.drop_duplicates() for c in
                  pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=100_000)]
        return pd.concat(chunks, ignore_index=True)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def truncate_all(cur) -> None:
    """冪等前置：清空全部表後再載。TRUNCATE 需暫關 FK 檢查（被引用表擋 TRUNCATE），
    清空狀態下無完整性風險；載入本身 FK 全程開啟。"""
    cur.execute("SET SESSION foreign_key_checks = 0")
    for t in ALL_TABLES:
        cur.execute(f"TRUNCATE TABLE {t}")
    cur.execute("SET SESSION foreign_key_checks = 1")


def load_infile(cur, table: str, staging: Path, db_cols: list[str]) -> tuple[int, int]:
    """staging CSV → LOAD DATA LOCAL INFILE。
    全欄先讀入 @vars，再以 NULLIF(@v,'') 統一「空字串=NULL」語意。
    ESCAPED BY ''：反斜線不作跳脫字元（與 pandas doublequote 引號規則相容）。"""
    vars_ = ", ".join(f"@v{i}" for i in range(len(db_cols)))
    sets = ", ".join(f"{c} = NULLIF(@v{i}, '')" for i, c in enumerate(db_cols))
    sql = f"""
        LOAD DATA LOCAL INFILE '{staging.as_posix()}'
        INTO TABLE {table}
        CHARACTER SET utf8mb4
        FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY ''
        LINES TERMINATED BY '\\n'
        IGNORE 1 LINES
        ({vars_})
        SET {sets}
    """
    loaded = cur.execute(sql)
    cur.execute("SELECT @@warning_count")
    warnings = cur.fetchone()[0]
    return loaded, warnings


def build_geolocation_zip(geo: pd.DataFrame) -> pd.DataFrame:
    """衍生彙總：bbox 過濾離群點 → 每 zip 中位數座標 + 眾數 city/state。"""
    g = geo.copy()
    g["lat"] = g["geolocation_lat"].astype(float)
    g["lng"] = g["geolocation_lng"].astype(float)
    bb = BRAZIL_BBOX
    inside = g[(g["lat"].between(bb["lat_min"], bb["lat_max"]))
               & (g["lng"].between(bb["lng_min"], bb["lng_max"]))]
    agg = inside.groupby("geolocation_zip_code_prefix").agg(
        lat=("lat", "median"),
        lng=("lng", "median"),
        city=("geolocation_city", lambda s: s.mode().iloc[0]),
        state=("geolocation_state", lambda s: s.mode().iloc[0]),
        n_points=("lat", "size"),
    ).reset_index().rename(columns={"geolocation_zip_code_prefix": "zip_code_prefix"})
    agg["lat"] = agg["lat"].map(lambda v: f"{v:.8f}")
    agg["lng"] = agg["lng"].map(lambda v: f"{v:.8f}")
    return agg[["zip_code_prefix", "lat", "lng", "city", "state", "n_points"]].astype(str)


def main() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    manifest: dict[str, dict] = {}

    conn = connect()
    try:
        with conn.cursor() as cur:
            print("[truncate] 清空全部表（冪等重載）")
            truncate_all(cur)

            geo_cleaned: pd.DataFrame | None = None
            for table, csv_name, clean_fn, mapping in TABLES:
                t1 = time.perf_counter()
                df = read_csv(RAW_DIR / csv_name)
                csv_rows = len(df) if table != "geolocation" else None
                if table == "geolocation":
                    # chunked 讀取時逐塊去重過，csv_rows 需另計原始列數
                    with open(RAW_DIR / csv_name, encoding="utf-8") as f:
                        csv_rows = sum(1 for _ in f) - 1  # geolocation 無多行欄位，實體行數=記錄數
                df, note = clean_fn(df)
                if table == "geolocation":
                    geo_cleaned = df
                    note = f"五欄完全重複列去重 ({len(df) - csv_rows:+,})"

                staging = STAGING_DIR / f"{table}.csv"
                df_out = df[[csv_col for _, csv_col in mapping]]
                df_out.columns = [db_col for db_col, _ in mapping]
                df_out.to_csv(staging, index=False, lineterminator="\n", encoding="utf-8")

                loaded, warns = load_infile(cur, table, staging, [m[0] for m in mapping])
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                db_count = cur.fetchone()[0]
                dt = time.perf_counter() - t1
                manifest[table] = {"csv_rows": csv_rows, "cleaned_rows": len(df),
                                   "delta_note": note or "", "warnings": warns}
                print(f"[load] {table:<36} csv={csv_rows:>9,} cleaned={len(df):>9,} "
                      f"db={db_count:>9,} warn={warns} {dt:.1f}s")

            # 衍生彙總表
            t1 = time.perf_counter()
            assert geo_cleaned is not None
            gz = build_geolocation_zip(geo_cleaned)
            staging = STAGING_DIR / "geolocation_zip.csv"
            gz.to_csv(staging, index=False, lineterminator="\n", encoding="utf-8")
            loaded, warns = load_infile(cur, "geolocation_zip", staging,
                                        ["zip_code_prefix", "lat", "lng", "city", "state", "n_points"])
            cur.execute("SELECT COUNT(*) FROM geolocation_zip")
            db_count = cur.fetchone()[0]
            dt = time.perf_counter() - t1
            manifest["geolocation_zip"] = {"csv_rows": None, "cleaned_rows": len(gz),
                                           "delta_note": "衍生：bbox 過濾+每 zip 聚合", "warnings": warns}
            print(f"[load] {'geolocation_zip（衍生）':<34} 聚合={len(gz):>9,} db={db_count:>9,} "
                  f"warn={warns} {dt:.1f}s")
    finally:
        conn.close()

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = time.perf_counter() - t0
    print(f"\n[manifest] {MANIFEST_PATH}")
    print(f"[done] 總耗時 {total:.1f} 秒")


if __name__ == "__main__":
    main()
