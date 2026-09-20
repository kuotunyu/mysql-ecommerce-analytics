-- =====================================================================
-- schema.sql — mysql-ecommerce-analytics Stage 1（Olist 巴西電商倉庫）
-- 目標庫：olist（見 docker-compose / .env 的 MYSQL_DATABASE）
-- 執行：docker exec -i olist-mysql sh -c
--        'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4
--         -u"$MYSQL_USER" "$MYSQL_DATABASE"' < schema.sql
--
-- 所有型別與約束皆依 data/raw 實測剖析定稿（2026-07-17）：
--   * 所有業務 ID 實測為 32 字元小寫 hex → CHAR(32) ascii_bin（JOIN 熱點，
--     ascii 讓索引鍵 1 byte/char，比 utf8mb4 小 4 倍；binary collation 最快）
--   * zip 前綴實測三表全部恰為 5 位數字（含前導零，如 01037）→ CHAR(5) ascii
--   * 時間一律 DATETIME（巴西當地時間、無時區語意；不用 TIMESTAMP）
--   * 金額一律 DECIMAL(10,2)（實測最大 payment_value=13,664.08，充分）
--   * FK 政策：只建孤兒數實測=0 的 6 條；zip→geolocation 與
--     products→translation 明確不建（理由見各表註解）
-- =====================================================================

SET NAMES utf8mb4;

-- 重建時先卸 FK 檢查再依任意序 DROP（僅此處使用；載入期間 FK 全程開啟）
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS order_reviews;
DROP TABLE IF EXISTS order_payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS sellers;
DROP TABLE IF EXISTS geolocation;
DROP TABLE IF EXISTS geolocation_zip;
DROP TABLE IF EXISTS product_category_name_translation;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------------
-- 1) product_category_name_translation ← product_category_name_translation.csv（71 列）
--    維持 71 列原樣：pc_gamer、portateis_cozinha_e_preparadores_de_alimentos
--    兩類缺英譯是「資料事實」，不補列；分析用 LEFT JOIN + COALESCE。
--    也因此 products.product_category_name 不建 FK 指向本表
--    （另有 610 個商品類別為 NULL）。
-- ---------------------------------------------------------------------
CREATE TABLE product_category_name_translation (
    product_category_name         VARCHAR(64) NOT NULL COMMENT '葡文類別名（實測最長 46 字元）',
    product_category_name_english VARCHAR(64) NOT NULL COMMENT '英文譯名（實測最長 39 字元）',
    PRIMARY KEY (product_category_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='商品類別葡→英對照（71 列原樣，缺譯不補）';

-- ---------------------------------------------------------------------
-- 2) customers ← olist_customers_dataset.csv（99,441 列）
--    customer_id 每張訂單一個（訂單維度代理身分）；customer_unique_id 才是
--    自然人識別（跨訂單重複）→ RFM / cohort 折疊用它，建非唯一索引。
--    customer_zip_code_prefix 不建 FK 指向 geolocation：
--    (a) geolocation.zip 不唯一，MySQL FK 需指向 PK/UNIQUE，物理上建不起來；
--    (b) 實測 157 個 zip（278 列）不存在於 geolocation → 強制 FK 會載入失敗。
--    替代：建一般索引，分析一律 LEFT JOIN 衍生表 geolocation_zip。
-- ---------------------------------------------------------------------
CREATE TABLE customers (
    customer_id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    customer_unique_id       CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '自然人識別，跨訂單重複',
    customer_zip_code_prefix CHAR(5)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'CEP 前 5 碼，保留前導零',
    customer_city            VARCHAR(64) NOT NULL COMMENT '實測最長 32 字元',
    customer_state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '巴西州代碼（實測 27 種）',
    PRIMARY KEY (customer_id),
    KEY idx_customers_unique_id (customer_unique_id) COMMENT 'RFM/cohort：折疊自然人',
    KEY idx_customers_state     (customer_state)     COMMENT '州級營收/物流分析',
    KEY idx_customers_zip       (customer_zip_code_prefix) COMMENT 'JOIN geolocation_zip'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='客戶（訂單維度）；一自然人多 customer_id';

-- ---------------------------------------------------------------------
-- 3) sellers ← olist_sellers_dataset.csv（3,095 列）
--    zip 同 customers：不建 FK（實測 7 個 zip 缺席 geolocation），建一般索引。
--    seller_state 不建索引：3,095 列全掃描成本可忽略（過度索引反增維護成本）。
-- ---------------------------------------------------------------------
CREATE TABLE sellers (
    seller_id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    seller_zip_code_prefix CHAR(5)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'CEP 前 5 碼，保留前導零',
    seller_city            VARCHAR(64) NOT NULL COMMENT '實測最長 40 字元（含髒值，ETL 僅 strip 不改寫）',
    seller_state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (seller_id),
    KEY idx_sellers_zip (seller_zip_code_prefix) COMMENT 'JOIN geolocation_zip'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='賣家';

-- ---------------------------------------------------------------------
-- 4) geolocation ← olist_geolocation_dataset.csv
--    無自然主鍵 + 大量五欄完全重複列（實測 1,000,163 列中 261,831 列重複）
--    → ETL 去重後載入 738,332 列，本表用代理鍵。zip 一對多（同 zip 多座標點）
--    → 分析不直接 JOIN 本表，改 JOIN 衍生彙總表 geolocation_zip（見下）。
--    座標實測有少量點落在巴西境外（如葡萄牙/菲律賓，原始資料異常）：
--    基表保留原樣，僅在生成 geolocation_zip 時以 bbox 過濾。
-- ---------------------------------------------------------------------
CREATE TABLE geolocation (
    geolocation_id              INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '代理鍵（CSV 無自然主鍵）',
    geolocation_zip_code_prefix CHAR(5) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '保留前導零',
    geolocation_lat             DECIMAL(10,8) NOT NULL COMMENT '8 位小數 ≈ mm 級精度',
    geolocation_lng             DECIMAL(11,8) NOT NULL,
    geolocation_city            VARCHAR(64) NOT NULL COMMENT '實測最長 38 字元',
    geolocation_state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (geolocation_id),
    KEY idx_geolocation_zip (geolocation_zip_code_prefix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='地理座標點（五欄完全重複列已去重：1,000,163→738,332）';

-- ---------------------------------------------------------------------
-- 5) geolocation_zip（衍生彙總表，非 9 張原始表之一）
--    由 etl/load.py 在載入 geolocation 後生成：每個 zip 一列，
--    先過濾巴西 bbox（lat -34..6, lng -74..-28，東界含 Fernando de
--    Noronha 離島）外的異常座標，座標取「中位數」（抗離群）、
--    city/state 取眾數。「僅有境外座標」的 zip 不在本表（無可信座標）。
--    分析 JOIN 地理資訊一律用本表（zip 在此唯一）。
-- ---------------------------------------------------------------------
CREATE TABLE geolocation_zip (
    zip_code_prefix CHAR(5) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lat             DECIMAL(10,8) NOT NULL COMMENT 'bbox 內座標點的中位數',
    lng             DECIMAL(11,8) NOT NULL COMMENT 'bbox 內座標點的中位數',
    city            VARCHAR(64) NOT NULL COMMENT '眾數',
    state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '眾數',
    n_points        INT UNSIGNED NOT NULL COMMENT '聚合使用的原始點數（bbox 過濾後）',
    PRIMARY KEY (zip_code_prefix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='衍生：每 zip 一列的地理彙總（分析用介面）';

-- ---------------------------------------------------------------------
-- 6) products ← olist_products_dataset.csv（32,951 列）
--    原始 CSV 欄名拼字錯誤 product_name_lenght / product_description_lenght
--    → 建表即改正（載入時做欄位對映），原始欄名記於 COMMENT 與 README 對照表。
--    數值欄實測含空值（name/desc/photos 各 610、weight/dims 各 2）→ 全部 nullable。
-- ---------------------------------------------------------------------
CREATE TABLE products (
    product_id                 CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    product_category_name      VARCHAR(64) NULL COMMENT '610 列 NULL；不建 FK → translation（2 類缺譯），LEFT JOIN + COALESCE',
    product_name_length        SMALLINT UNSIGNED NULL COMMENT 'CSV 原始欄名: product_name_lenght（拼字錯誤已改正）；實測 max 76',
    product_description_length SMALLINT UNSIGNED NULL COMMENT 'CSV 原始欄名: product_description_lenght；實測 max 3,992',
    product_photos_qty         TINYINT UNSIGNED NULL COMMENT '實測 max 20',
    product_weight_g           INT UNSIGNED NULL COMMENT '實測 max 40,425',
    product_length_cm          SMALLINT UNSIGNED NULL,
    product_height_cm          SMALLINT UNSIGNED NULL,
    product_width_cm           SMALLINT UNSIGNED NULL,
    PRIMARY KEY (product_id),
    KEY idx_products_category (product_category_name) COMMENT '類別分析 JOIN/GROUP BY 主路徑'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='商品';

-- ---------------------------------------------------------------------
-- 7) orders ← olist_orders_dataset.csv（99,441 列）
--    5 個時間欄中 3 個實測含空值（approved 160、carrier 1,783、customer 2,965）
--    ——未核准/未出貨/未送達的合法 NULL，不填補。
--    已知資料品質：8 筆 status='delivered' 卻無送達時間（驗證列 INFO 項）。
-- ---------------------------------------------------------------------
CREATE TABLE orders (
    order_id                      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    customer_id                   CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    order_status                  VARCHAR(20) NOT NULL COMMENT '實測 8 種值（最長 unavailable=11）；不用 ENUM 避免綁死值域',
    order_purchase_timestamp      DATETIME NOT NULL COMMENT '實測範圍 2016-09-04 ~ 2018-10-17',
    order_approved_at             DATETIME NULL,
    order_delivered_carrier_date  DATETIME NULL,
    order_delivered_customer_date DATETIME NULL,
    order_estimated_delivery_date DATETIME NOT NULL,
    PRIMARY KEY (order_id),
    KEY idx_orders_status_purchase (order_status, order_purchase_timestamp)
        COMMENT '最常見查詢模式：WHERE status=delivered AND 期間範圍',
    KEY idx_orders_purchase  (order_purchase_timestamp) COMMENT '不分 status 的時間軸掃描（趨勢/cohort）',
    KEY idx_orders_delivered (order_delivered_customer_date) COMMENT '物流時效：實際 vs 預estimated',
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='訂單（FK→customers 實測孤兒=0）';

-- ---------------------------------------------------------------------
-- 8) order_items ← olist_order_items_dataset.csv（112,650 列）
--    order_item_id 為訂單內行號（實測 1..21）→ 複合 PK (order_id, order_item_id)。
--    三條 FK 實測孤兒皆 0。fk_items_order 借用 PK 左前綴、
--    fk_items_product 借用 idx_items_product_price 左前綴，皆不另建索引。
-- ---------------------------------------------------------------------
CREATE TABLE order_items (
    order_id            CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    order_item_id       TINYINT UNSIGNED NOT NULL COMMENT '訂單內行號（實測 max 21）',
    product_id          CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    seller_id           CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    shipping_limit_date DATETIME NOT NULL,
    price               DECIMAL(10,2) NOT NULL COMMENT '實測 max 6,735.00',
    freight_value       DECIMAL(10,2) NOT NULL COMMENT '實測 max 409.68',
    PRIMARY KEY (order_id, order_item_id),
    KEY idx_items_product_price (product_id, price) COMMENT '覆蓋索引：商品/類別營收聚合免回表；同時供 FK 使用',
    CONSTRAINT fk_items_order   FOREIGN KEY (order_id)   REFERENCES orders (order_id),
    CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT fk_items_seller  FOREIGN KEY (seller_id)  REFERENCES sellers (seller_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='訂單明細（GMV 口徑：SUM(price+freight_value)）';

-- ---------------------------------------------------------------------
-- 9) order_payments ← olist_order_payments_dataset.csv（103,886 列）
--    複合 PK (order_id, payment_sequential)；FK 借用 PK 左前綴。
--    installments=0 有 2 列、payment_type='not_defined' 有 3 列：原始資料保留。
--    注意：SUM(payment_value) ≠ SUM(price+freight_value)（voucher/口徑差），不可互驗。
-- ---------------------------------------------------------------------
CREATE TABLE order_payments (
    order_id             CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payment_sequential   TINYINT UNSIGNED NOT NULL COMMENT '同單付款序號',
    payment_type         VARCHAR(20) NOT NULL COMMENT '實測 5 種（含 not_defined×3）',
    payment_installments TINYINT UNSIGNED NOT NULL COMMENT '實測 0..24（0 為原始資料）',
    payment_value        DECIMAL(10,2) NOT NULL COMMENT '實測 max 13,664.08',
    PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='付款（1 筆訂單實測無付款紀錄，屬已知資料品質）';

-- ---------------------------------------------------------------------
-- 10) order_reviews ← olist_order_reviews_dataset.csv（99,224 列）
--     review_id 跨訂單重複（實測唯一值 98,410 < 列數 99,224，同評掛多單）、
--     order_id 一單多評（唯一值 98,673）→ 兩者單獨皆不能當 PK；
--     複合 (review_id, order_id) 實測重複=0 → 複合 PK，保留全部列不去重。
--     order_id 在 PK 第二位吃不到左前綴 → 獨立索引（FK 亦需要）。
-- ---------------------------------------------------------------------
CREATE TABLE order_reviews (
    review_id               CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    order_id                CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    review_score            TINYINT NOT NULL COMMENT '實測 1..5',
    review_comment_title    VARCHAR(64) NULL COMMENT '實測最長 26 字元',
    review_comment_message  TEXT NULL COMMENT '實測最長 208 字元；TEXT 留彈性。多行/逗號/引號由 CSV quoting 正確處理',
    review_creation_date    DATETIME NOT NULL,
    review_answer_timestamp DATETIME NOT NULL,
    PRIMARY KEY (review_id, order_id),
    KEY idx_reviews_order (order_id) COMMENT 'PK 左前綴吃不到；FK 需要',
    CONSTRAINT fk_reviews_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='評論（review_id 可跨訂單重複，複合 PK）';
