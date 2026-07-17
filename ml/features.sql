-- =====================================================================
-- ml/features.sql — 晚到預測特徵工程（全部在 SQL 完成）→ 實體表 ml_features
-- 執行：docker exec -i olist-mysql sh -c
--        'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4
--         -u"$MYSQL_USER" "$MYSQL_DATABASE"' < ml/features.sql
--
-- Grain：一筆 = 一張訂單。母體 = delivered 且有送達時間、預估日非 NULL、
--        無時間倒置（實測 96,470 筆；ml/test_features.py 會 assert）。
-- Label：is_late = DATE(送達) > DATE(預估)（沿用全案 DATE 級口徑）。
--
-- 【時點特徵——可信度核心，嚴禁偷看未來】主賣家在購買時點 t 的歷史狀態：
--   * seller_hist_late_rate   分母＝「t 時結果已揭曉」的歷史單（delivered < t，嚴格小於）
--   * seller_hist_order_cnt   從「全狀態 orders」計（purchase < t，不限 delivered）——
--                             若只算 delivered 單，等於偷看歷史單的最終結局（定義級洩漏）
--   * seller_hist_open_cnt    = order_cnt − delivered_cnt（t 時在途/未結單；canceled 單
--                             無取消時戳、會留在 open 中，屬口徑近似，報告載明）
--   * seller_hist_overdue_open_cnt  在途單中預估日已過（DATE(est) < DATE(t)）者——
--                             「t 時已知必遲到但尚未送達」的爆倉訊號
--   冷啟動（無已揭曉歷史）late_rate = NULL：不補全域先驗（先驗含測試期資訊＝隱性洩漏），
--   LightGBM 原生處理 missing。
--
-- 主賣家 = 訂單中品項金額（SUM(price)）最高的賣家，平手取 MIN(seller_id)（可重現）。
-- 多賣家訂單實測僅 1.3%（1,278/98,666），主賣家法足夠；另供 n_sellers 特徵。
-- 無品項的訂單無賣家歸屬，天然不在歷史計數內（delivered 訂單全部有品項）。
--
-- est_days = DATEDIFF(預估日, 下單日)：下單當下平台展示的承諾天數，t 時完全已知，
-- 合法特徵（出現在 label 公式 ≠ 洩漏；判準是「t 時是否已知」）。
--
-- 【禁用清單】order_delivered_*（t 後才發生/構成 label）、order_approved_at（t 後）、
-- order_items.shipping_limit_date（由 approved 推導，保守排除）、order_reviews.*
-- （label 下游）、order_status（母體恆 delivered）、任何 delivery_days/late_days 衍生量。
--
-- 自我 JOIN 效能：等值鍵 seller_id 走 hash join；配對量 Σ nᵢ×mᵢ 約千萬級，一次性建表
-- 可接受。（備案：事件流法——purchase/delivery 事件 UNION 成單一時間軸後用累積 window，
-- O(n log n)，但可驗證性差，未採用。）
-- =====================================================================

DROP TABLE IF EXISTS ml_features;

CREATE TABLE ml_features (PRIMARY KEY (order_id)) AS
WITH main_seller AS (
    -- 每訂單的主賣家（品項金額最高，平手 MIN(seller_id)）
    SELECT order_id, seller_id
    FROM (
        SELECT oi.order_id, oi.seller_id,
               ROW_NUMBER() OVER (PARTITION BY oi.order_id
                                  ORDER BY SUM(oi.price) DESC, oi.seller_id) AS rn
        FROM order_items oi
        GROUP BY oi.order_id, oi.seller_id
    ) t
    WHERE rn = 1
), seller_orders_all AS (
    -- 主賣家 × 全狀態訂單（含 canceled/unavailable…）：時點計數的歷史母體
    SELECT ms.seller_id,
           o.order_id,
           o.order_purchase_timestamp                       AS pts,
           o.order_delivered_customer_date                  AS dts,
           o.order_estimated_delivery_date                  AS ets,
           (o.order_status = 'delivered'
            AND o.order_delivered_customer_date IS NOT NULL
            AND DATE(o.order_delivered_customer_date)
                > DATE(o.order_estimated_delivery_date))    AS is_late_hist
    FROM orders o
    JOIN main_seller ms ON ms.order_id = o.order_id
), base AS (
    -- 特徵表母體（= label 可定義的訂單）
    SELECT o.order_id, o.customer_id,
           o.order_purchase_timestamp      AS pts,
           o.order_estimated_delivery_date AS ets,
           o.order_delivered_customer_date AS dts
    FROM orders o
    WHERE o.order_status = 'delivered'
      AND o.order_delivered_customer_date IS NOT NULL
      AND o.order_estimated_delivery_date IS NOT NULL
      AND o.order_delivered_customer_date >= o.order_purchase_timestamp
), hist AS (
    -- 時點聚合：對每筆目標單 t，掃主賣家的所有更早（purchase < t，嚴格）歷史單
    SELECT b.order_id,
           COUNT(h.order_id)                                    AS seller_hist_order_cnt,
           COALESCE(SUM(h.dts IS NOT NULL AND h.dts < b.pts), 0) AS seller_hist_delivered_cnt,
           COALESCE(SUM(h.dts IS NOT NULL AND h.dts < b.pts AND h.is_late_hist), 0) AS hist_late_cnt,
           COALESCE(SUM((h.dts IS NULL OR h.dts >= b.pts)
                        AND DATE(h.ets) < DATE(b.pts)), 0)       AS seller_hist_overdue_open_cnt
    FROM base b
    JOIN main_seller ms ON ms.order_id = b.order_id
    LEFT JOIN seller_orders_all h
      ON h.seller_id = ms.seller_id
     AND h.pts < b.pts
    GROUP BY b.order_id
), items_agg AS (
    -- 品項聚合；SUM 忽略 NULL 品項重量（全缺才回 NULL——僅 2 個 products 缺）
    SELECT oi.order_id,
           SUM(oi.freight_value)             AS freight_total,
           SUM(oi.price)                     AS price_total,
           COUNT(*)                          AS n_items,
           COUNT(DISTINCT oi.seller_id)      AS n_sellers,
           SUM(p.product_weight_g)           AS total_weight_g,
           SUM(p.product_length_cm * p.product_height_cm * p.product_width_cm)
                                             AS total_volume_cm3
    FROM order_items oi
    JOIN products p ON p.product_id = oi.product_id
    GROUP BY oi.order_id
), main_cat AS (
    -- 訂單品類 = 最高價品項的品類（平手 MIN(product_id)），COALESCE 口徑同全案
    SELECT order_id, category
    FROM (
        SELECT oi.order_id,
               COALESCE(t.product_category_name_english,
                        p.product_category_name, '(uncategorized)') AS category,
               ROW_NUMBER() OVER (PARTITION BY oi.order_id
                                  ORDER BY oi.price DESC, oi.product_id) AS rn
        FROM order_items oi
        JOIN products p ON p.product_id = oi.product_id
        LEFT JOIN product_category_name_translation t
          ON t.product_category_name = p.product_category_name
    ) x
    WHERE rn = 1
), pay AS (
    SELECT order_id, MAX(payment_installments) AS max_installments
    FROM order_payments
    GROUP BY order_id
)
SELECT b.order_id,
       b.pts                                            AS purchase_ts,
       (DATE(b.dts) > DATE(b.ets)) + 0                  AS is_late,           -- label
       h.seller_hist_order_cnt,
       h.seller_hist_delivered_cnt,
       CAST(h.hist_late_cnt AS DOUBLE) / NULLIF(h.seller_hist_delivered_cnt, 0)
                                                        AS seller_hist_late_rate,  -- DOUBLE：整數相除預設 4 位小數會截斷
       h.seller_hist_order_cnt - h.seller_hist_delivered_cnt
                                                        AS seller_hist_open_cnt,
       h.seller_hist_overdue_open_cnt,
       ia.freight_total,
       ia.price_total,
       ia.n_items,
       ia.n_sellers,
       ia.total_weight_g,
       ia.total_volume_cm3,
       c.customer_state,
       ss.seller_state,
       (c.customer_state <> ss.seller_state) + 0        AS is_cross_state,
       mc.category                                      AS product_category,
       pay.max_installments,
       MONTH(b.pts)                                     AS purchase_month_num,
       DAYOFWEEK(b.pts)                                 AS purchase_dow,      -- 1=週日
       DATEDIFF(b.ets, DATE(b.pts))                     AS est_days
FROM base b
JOIN hist        h   ON h.order_id  = b.order_id
JOIN items_agg   ia  ON ia.order_id = b.order_id
JOIN main_cat    mc  ON mc.order_id = b.order_id
JOIN main_seller ms  ON ms.order_id = b.order_id
JOIN sellers     ss  ON ss.seller_id = ms.seller_id
JOIN customers   c   ON c.customer_id = b.customer_id
LEFT JOIN pay        ON pay.order_id = b.order_id;
