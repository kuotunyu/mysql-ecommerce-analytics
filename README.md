# mysql-ecommerce-analytics

把 [Olist 巴西電商公開資料集](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
（9 個 CSV、155 萬列真實交易）從 raw CSV 一路做到機器學習：**Docker MySQL 8.4 正規化倉庫
→ SQL 分析與探勘 → SQL 時點特徵 → LightGBM 晚到預測**。全案以 SQL 為核心，
所有數字皆真實執行可重現（固定種子、uv.lock 鎖版本）。

## 三個招牌數字

| | 數字 | 出處 |
|---|---|---|
| 索引效能實驗 | **9.2× 加速**（59.6→6.4ms，覆蓋索引；並實證 OLAP 聚合追加索引無效 ~1.0×） | [INDEX_REPORT](results/INDEX_REPORT.md) |
| 晚到預測 | **AUC 0.750**，最強單特徵 baseline 僅 0.500、跨州規則 0.401（時間切分＋防洩漏時點特徵） | [ML_REPORT](results/ML_REPORT.md) |
| 客戶結構 | **96.9% 客戶只買一次**、6 個月累積二購率 3.2%——RFM/cohort 全部圍繞這個殘酷事實誠實設計 | [REPORT](reports/REPORT.md) |

## 架構流程

```mermaid
flowchart LR
    K["Kaggle CSV ×9<br/>155 萬列"] -->|scripts/fetch_data.py| R["data/raw/"]
    R -->|"etl/load.py<br/>pandas 清理 + LOAD DATA<br/>（21.5 秒）"| M[("MySQL 8.4<br/>Docker + Adminer")]
    M --> V["驗證核對表<br/>row count / FK 孤兒 = 0"]
    M --> S["sql/ 十題<br/>SQL_REPORT（自動生成）"]
    M --> A["analysis/ 六主題<br/>REPORT.md（18 張圖）"]
    M --> I["索引效能實驗<br/>INDEX_REPORT"]
    M --> D["mining/<br/>RFM KMeans＋品類關聯規則"]
    M -->|"ml/features.sql<br/>時點特徵（防洩漏）"| F["ml_features<br/>96,470 × 20"]
    F -->|"ml/train.py<br/>時間切分 + LightGBM + SHAP"| L["ML_REPORT"]
```

## 三階段

| Stage | 內容 | 主要交付 |
|---|---|---|
| A 倉庫 | Docker MySQL 8.4、9 表 schema（6 條 FK 孤兒實測 0）、ETL 155 萬列 21.5 秒、核對表驗證 | [schema.sql](schema.sql) |
| B 分析與探勘 | 十題 SQL（一題一檔）＋六主題深度報告（21 查詢/18 圖）＋索引實驗＋RFM 分群＋關聯規則 | [SQL_REPORT](results/SQL_REPORT.md)、[REPORT](reports/REPORT.md)、[INDEX_REPORT](results/INDEX_REPORT.md)、[ASSOCIATION_REPORT](results/mining/ASSOCIATION_REPORT.md) |
| C 機器學習 | SQL 時點特徵（200 筆性質測試＋4 手工案例防洩漏，**驗證不過不准訓練**）、LightGBM vs 三 baseline、SHAP | [ML_REPORT](results/ML_REPORT.md)、[ml/](ml/) |

精選發現：晚到 8+ 天的訂單平均評分從 4.29 崩至 **1.70**（79.3% 給 1-2 星）；
州際配送差 **3.4 倍**（SP 8.7 天 vs RR 29.3 天）；74 品類中前 18 個吃掉 81.3% GMV；
跨品類共購僅 0.81%（關聯規則的誠實負面結果＋條件宇宙 Top 組合）。

## Quick Start（一鍵重現）

```bash
make all        # db-up → fetch → etl → verify → sql → mine → train
```

無 make 的環境（如 Windows）直接照抄對應指令：

| make 目標 | 等效指令 |
|---|---|
| `db-up` | `docker compose up -d --wait` |
| `fetch` | `uv run python scripts/fetch_data.py`（需 `~/.kaggle` 憑證；已有 CSV 自動跳過） |
| `etl` | `docker compose exec -T mysql sh -c '…mysql…' < schema.sql`＋`uv run python etl/load.py` |
| `verify` | `uv run python scripts/run_verify.py` |
| `sql` | `uv run python scripts/run_sql.py`＋`uv run python scripts/run_analysis.py` |
| `mine` | `uv run python mining/rfm_clustering.py`＋`uv run python mining/association_rules.py` |
| `train` | `make features` 後 `uv run python ml/train.py`（開頭強制防洩漏驗證） |

前置：Docker Desktop、[uv](https://docs.astral.sh/uv/)、`.env`（見 [.env.example](.env.example)）。
無 Kaggle 憑證者可手動放 CSV 進 `data/raw/` 後從 `etl` 起跑。
日常開關與細節見 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

## ER Diagram

實線 = 實體 FK（6 條，孤兒實測全 0）；虛線 = 刻意**不建 FK** 的分析用 LEFT JOIN 關係
（zip 不唯一且有缺值、品類缺英譯——理由見 [schema.sql](schema.sql) 註解）。

```mermaid
erDiagram
    customers ||--o{ orders : "fk_orders_customer"
    orders ||--|{ order_items : "fk_items_order"
    orders ||--o{ order_payments : "fk_payments_order"
    orders ||--o{ order_reviews : "fk_reviews_order"
    products ||--o{ order_items : "fk_items_product"
    sellers ||--o{ order_items : "fk_items_seller"
    products }o..o| product_category_name_translation : "LEFT JOIN + COALESCE"
    customers }o..o| geolocation_zip : "zip LEFT JOIN"
    sellers }o..o| geolocation_zip : "zip LEFT JOIN"
    geolocation ||..|| geolocation_zip : "ETL 聚合生成"

    customers {
        char32 customer_id PK
        char32 customer_unique_id "自然人識別（跨訂單重複）"
        char5  customer_zip_code_prefix "保留前導零"
        varchar64 customer_city
        char2  customer_state
    }
    orders {
        char32 order_id PK
        char32 customer_id FK
        varchar20 order_status
        datetime order_purchase_timestamp
        datetime order_approved_at "NULL=未核准"
        datetime order_delivered_carrier_date "NULL=未出貨"
        datetime order_delivered_customer_date "NULL=未送達"
        datetime order_estimated_delivery_date
    }
    order_items {
        char32 order_id PK "複合 PK"
        tinyint order_item_id PK "訂單內行號 1..21"
        char32 product_id FK
        char32 seller_id FK
        datetime shipping_limit_date
        decimal_10_2 price
        decimal_10_2 freight_value
    }
    order_payments {
        char32 order_id PK "複合 PK"
        tinyint payment_sequential PK
        varchar20 payment_type
        tinyint payment_installments "0..24"
        decimal_10_2 payment_value
    }
    order_reviews {
        char32 review_id PK "複合 PK（單獨會重複）"
        char32 order_id PK
        tinyint review_score "1..5"
        varchar64 review_comment_title
        text review_comment_message
        datetime review_creation_date
        datetime review_answer_timestamp
    }
    products {
        char32 product_id PK
        varchar64 product_category_name "610 列 NULL"
        smallint product_name_length "原欄名 lenght（typo 已改正）"
        smallint product_description_length
        tinyint product_photos_qty
        int product_weight_g
        smallint product_length_cm
        smallint product_height_cm
        smallint product_width_cm
    }
    sellers {
        char32 seller_id PK
        char5  seller_zip_code_prefix
        varchar64 seller_city
        char2  seller_state
    }
    geolocation {
        int geolocation_id PK "代理鍵"
        char5 geolocation_zip_code_prefix "一 zip 多點"
        decimal_10_8 geolocation_lat
        decimal_11_8 geolocation_lng
        varchar64 geolocation_city
        char2 geolocation_state
    }
    geolocation_zip {
        char5 zip_code_prefix PK "衍生：每 zip 一列"
        decimal_10_8 lat "bbox 內中位數"
        decimal_11_8 lng
        varchar64 city "眾數"
        char2 state
        int n_points
    }
    product_category_name_translation {
        varchar64 product_category_name PK
        varchar64 product_category_name_english
    }
```

## 工程與方法論重點

- **口徑治理**：GMV（items）與金流（payments）兩口徑不可互驗、一單多評取最新、
  完整月 2017-01~2018-08——全案單一定義，view 集中實作（`v_order_gmv` 防 fan-out）。
- **誠實紅線**：所有數字真實執行；殘月灰化、cohort 右截斷灰格、色階上限誠實、禁雙軸；
  關聯規則的負面結果（99.2% 單品類訂單）如實呈現。
- **防洩漏**：時點特徵「已揭曉/存在/在途」三口徑分離；性質測試曾實際抓到
  MySQL 整數相除截斷 bug；時間切分 train/val/test 嚴格遞增。
- 模型可解釋性（SHAP 個體歸因、部分依賴）在我的另一個專案 **credit-default-xai**
  有完整展開，本專案以全局重要度驗收特徵工程。

## 資料來源與授權

資料來自 Kaggle [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
（License: **CC BY-NC-SA 4.0**）。本 repo **不隨附原始資料**，僅提供下載腳本
（`scripts/fetch_data.py`，需自備 Kaggle API 憑證）；分析產出（圖表、聚合表、報告）
屬衍生著作，沿用相同授權精神、僅供學習展示。

## 文件索引

[docs/OPERATIONS.md](docs/OPERATIONS.md)（開關機手冊）
