-- =====================================================================
-- 05_logistics.sql — 主題 5：物流時效
-- 口徑（全檔一致）：
--   * 母體 delivered_base = delivered 且送達時間非 NULL（排除 8 筆）
--     且 delivery_days >= 0（排除時間順序異常列，5-0 盤點實數）
--   * 準時判定：DATE(delivered) <= DATE(estimated)——estimated 只有日期(00:00:00)，
--     拿 timestamp 直接比會把「當天下午送達」誤判成晚到
--   * delay_days = DATEDIFF(DATE(delivered), DATE(estimated))，負值=提早
--   * on_time_rate = AVG(delay_days <= 0)——注意方向：AVG(delay_days>0) 是「晚到率」，
--     正負號寫反整章結論就反了（設計審查點名的陷阱）
--   * 月度趨勢裁剪 2017-01 ~ 2018-08（完整月；完整實況見 01_revenue）
-- =====================================================================

-- @name: 5-0 資料品質盤點與整體摘要（單列）
WITH raw AS (
    SELECT o.*,
           DATEDIFF(o.order_delivered_customer_date, o.order_purchase_timestamp) AS delivery_days,
           DATEDIFF(DATE(o.order_delivered_customer_date),
                    DATE(o.order_estimated_delivery_date))                       AS delay_days
    FROM orders o
    WHERE o.order_status = 'delivered'
), base AS (
    SELECT * FROM raw
    WHERE order_delivered_customer_date IS NOT NULL AND delivery_days >= 0
), ordered AS (
    SELECT delivery_days,
           ROW_NUMBER() OVER (ORDER BY delivery_days) AS rn,
           COUNT(*)   OVER ()                          AS n
    FROM base
)
SELECT (SELECT COUNT(*) FROM raw)                                          AS delivered_total,
       (SELECT COUNT(*) FROM raw WHERE order_delivered_customer_date IS NULL) AS excluded_null_delivery,
       (SELECT COUNT(*) FROM raw WHERE delivery_days < 0)                  AS excluded_negative_days,
       (SELECT COUNT(*) FROM base WHERE order_delivered_carrier_date IS NULL) AS carrier_date_null,
       (SELECT COUNT(*) FROM base)                                         AS analysis_base,
       (SELECT ROUND(AVG(delivery_days), 1) FROM base)                     AS avg_delivery_days,
       (SELECT delivery_days FROM ordered WHERE rn = CEILING(n / 2))       AS median_delivery_days,
       (SELECT MIN(delivery_days) FROM ordered WHERE rn >= CEILING(n * 0.9)) AS p90_delivery_days,
       (SELECT ROUND(AVG(delay_days <= 0) * 100, 1) FROM base)             AS on_time_rate_pct;

-- @name: 5-1 配送天數分布（下單→送達）
WITH base AS (
    SELECT DATEDIFF(order_delivered_customer_date, order_purchase_timestamp) AS delivery_days
    FROM orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
      AND DATEDIFF(order_delivered_customer_date, order_purchase_timestamp) >= 0
)
SELECT CASE WHEN delivery_days <= 3  THEN '0-3 天'
            WHEN delivery_days <= 7  THEN '4-7 天'
            WHEN delivery_days <= 14 THEN '8-14 天'
            WHEN delivery_days <= 21 THEN '15-21 天'
            WHEN delivery_days <= 30 THEN '22-30 天'
            ELSE '31+ 天' END AS days_bin,
       COUNT(*)                                         AS n_orders,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 1) AS pct
FROM base
GROUP BY days_bin
ORDER BY MIN(delivery_days);

-- @name: 5-2 vs 預估交期：早/晚到天數分布
WITH base AS (
    SELECT DATEDIFF(DATE(order_delivered_customer_date),
                    DATE(order_estimated_delivery_date)) AS delay_days
    FROM orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
      AND DATEDIFF(order_delivered_customer_date, order_purchase_timestamp) >= 0
)
SELECT CASE WHEN delay_days <= -8 THEN '提早 8+ 天'
            WHEN delay_days <= -4 THEN '提早 4-7 天'
            WHEN delay_days <= -1 THEN '提早 1-3 天'
            WHEN delay_days = 0   THEN '準時當日'
            WHEN delay_days <= 3  THEN '晚 1-3 天'
            WHEN delay_days <= 7  THEN '晚 4-7 天'
            ELSE '晚 8+ 天' END AS delay_bin,
       COUNT(*)                                         AS n_orders,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 1) AS pct
FROM base
GROUP BY delay_bin
ORDER BY MIN(delay_days);

-- @name: 5-3 月度配送天數與準時率（2017-01~2018-08）
WITH base AS (
    SELECT DATE_FORMAT(order_purchase_timestamp, '%Y-%m') AS ym,
           DATEDIFF(order_delivered_customer_date, order_purchase_timestamp) AS delivery_days,
           DATEDIFF(DATE(order_delivered_customer_date),
                    DATE(order_estimated_delivery_date)) AS delay_days
    FROM orders
    WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
      AND DATEDIFF(order_delivered_customer_date, order_purchase_timestamp) >= 0
      AND order_purchase_timestamp >= '2017-01-01'
      AND order_purchase_timestamp <  '2018-09-01'
)
SELECT ym,
       COUNT(*)                                    AS n_orders,
       ROUND(AVG(delivery_days), 1)                AS avg_delivery_days,
       ROUND(AVG(delay_days <= 0) * 100, 1)        AS on_time_rate_pct,
       ROUND(AVG(CASE WHEN delay_days > 0 THEN delay_days END), 1) AS avg_late_days  -- 全月零晚到時為 NULL
FROM base
GROUP BY ym
ORDER BY ym;

-- @name: 5-4 州別配送天數與準時率
WITH base AS (
    SELECT c.customer_state,
           DATEDIFF(o.order_delivered_customer_date, o.order_purchase_timestamp) AS delivery_days,
           DATEDIFF(DATE(o.order_delivered_customer_date),
                    DATE(o.order_estimated_delivery_date)) AS delay_days
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
      AND DATEDIFF(o.order_delivered_customer_date, o.order_purchase_timestamp) >= 0
)
SELECT customer_state,
       COUNT(*)                             AS n_orders,
       ROUND(AVG(delivery_days), 1)         AS avg_delivery_days,
       ROUND(AVG(delay_days <= 0) * 100, 1) AS on_time_rate_pct,
       ROUND(AVG(CASE WHEN delay_days > 0 THEN delay_days END), 1) AS avg_late_days
FROM base
GROUP BY customer_state
ORDER BY avg_delivery_days DESC;

-- @name: 5-5 賣家逾期出貨 Top 20（出貨 item 數 >= 50）
-- item 級判定：delivered_carrier_date > shipping_limit_date = 逾期交貨給物流。
WITH item_ship AS (
    SELECT oi.seller_id,
           (o.order_delivered_carrier_date > oi.shipping_limit_date)          AS is_late_ship,
           DATEDIFF(DATE(o.order_delivered_carrier_date),
                    DATE(oi.shipping_limit_date))                             AS ship_lead_days
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    WHERE o.order_status = 'delivered' AND o.order_delivered_carrier_date IS NOT NULL
), by_seller AS (
    SELECT seller_id,
           COUNT(*)            AS n_items,
           AVG(is_late_ship)   AS late_ship_rate,
           AVG(ship_lead_days) AS avg_lead_days
    FROM item_ship
    GROUP BY seller_id
    HAVING COUNT(*) >= 50
)
SELECT seller_id,
       n_items,
       ROUND(late_ship_rate * 100, 1) AS late_ship_rate_pct,
       ROUND(avg_lead_days, 1)        AS avg_lead_days,
       ROUND(PERCENT_RANK() OVER (ORDER BY late_ship_rate) * 100, 1) AS pctl_among_sellers,
       (SELECT ROUND(AVG(is_late_ship) * 100, 1) FROM item_ship)     AS overall_late_ship_rate_pct
FROM by_seller
ORDER BY late_ship_rate DESC
LIMIT 20;

-- @name: 5-6 配送三階段拆解：準時 vs 晚到的時間都花在哪段
-- approve（下單→核准）/ handling（核准→交物流）/ transit（物流→送達）；
-- 限三段皆非 NULL 且非負（分母與 5-0 的 analysis_base 不同，註明）。
WITH base AS (
    SELECT DATEDIFF(order_approved_at, order_purchase_timestamp)                    AS approve_days,
           DATEDIFF(order_delivered_carrier_date, order_approved_at)                AS handling_days,
           DATEDIFF(order_delivered_customer_date, order_delivered_carrier_date)    AS transit_days,
           DATEDIFF(DATE(order_delivered_customer_date),
                    DATE(order_estimated_delivery_date))                            AS delay_days
    FROM orders
    WHERE order_status = 'delivered'
      AND order_delivered_customer_date IS NOT NULL
      AND order_approved_at IS NOT NULL
      AND order_delivered_carrier_date IS NOT NULL
)
SELECT CASE WHEN delay_days <= 0 THEN '準時/提早' ELSE '晚到' END AS grp,
       COUNT(*)                     AS n_orders,
       ROUND(AVG(approve_days), 1)  AS avg_approve_days,
       ROUND(AVG(handling_days), 1) AS avg_handling_days,
       ROUND(AVG(transit_days), 1)  AS avg_transit_days
FROM base
WHERE approve_days >= 0 AND handling_days >= 0 AND transit_days >= 0
GROUP BY grp
ORDER BY grp;
