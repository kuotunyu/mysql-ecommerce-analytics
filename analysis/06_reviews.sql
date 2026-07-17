-- =====================================================================
-- 06_reviews.sql — 主題 6：評論與滿意度
-- 依賴：00_views.sql 的 v_order_review（一單多評=取最新一筆，全案唯一口徑）
-- 母體：除 6-0 外，一律「delivered 訂單的最新評論」——與物流章同基底，
--       延遲×評分（6-2）才能無縫互讀。非 delivered 訂單的評論數在報告文字帶過。
-- 文字內容（關鍵詞/情緒）不碰，留給 Stage C；本檔僅結構化統計。
-- =====================================================================

-- @name: 6-0 一單多評敏感度對照（單列：平均法 vs 最新法幾乎無差）
SELECT (SELECT COUNT(*) FROM (
            SELECT order_id FROM order_reviews GROUP BY order_id HAVING COUNT(*) > 1
        ) t)                                                       AS multi_review_orders,
       (SELECT COUNT(*) FROM order_reviews)                        AS review_rows,
       (SELECT COUNT(*) FROM v_order_review)                       AS distinct_orders,
       (SELECT ROUND(AVG(s), 4) FROM (
            SELECT AVG(review_score) AS s FROM order_reviews GROUP BY order_id
        ) a)                                                       AS avg_method_mean,
       (SELECT ROUND(AVG(review_score), 4) FROM v_order_review)    AS latest_method_mean;

-- @name: 6-1 評分分布（delivered 訂單、每單最新評論）
SELECT vr.review_score,
       COUNT(*)                                         AS n_orders,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 1) AS pct
FROM v_order_review vr
JOIN orders o ON o.order_id = vr.order_id
WHERE o.order_status = 'delivered'
GROUP BY vr.review_score
ORDER BY vr.review_score;

-- @name: 6-2 配送延遲 × 評分（招牌：晚到幾天，評分崩到哪）
WITH base AS (
    SELECT vr.review_score,
           DATEDIFF(DATE(o.order_delivered_customer_date),
                    DATE(o.order_estimated_delivery_date)) AS delay_days
    FROM v_order_review vr
    JOIN orders o ON o.order_id = vr.order_id
    WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
      AND DATEDIFF(o.order_delivered_customer_date, o.order_purchase_timestamp) >= 0
)
SELECT CASE WHEN delay_days <= 0 THEN '準時/提早'
            WHEN delay_days <= 3 THEN '晚 1-3 天'
            WHEN delay_days <= 7 THEN '晚 4-7 天'
            ELSE '晚 8+ 天' END AS delay_bin,
       COUNT(*)                                     AS n_orders,
       ROUND(AVG(review_score), 2)                  AS avg_score,
       ROUND(AVG(review_score <= 2) * 100, 1)       AS low_score_rate_pct  -- 1-2 分佔比
FROM base
GROUP BY delay_bin
ORDER BY MIN(delay_days);

-- @name: 6-3 留言率 × 評分（誰會動筆寫字？）
SELECT vr.review_score,
       COUNT(*) AS n_orders,
       ROUND(AVG(vr.review_comment_message IS NOT NULL
                 AND vr.review_comment_message <> '') * 100, 1)   AS comment_rate_pct,
       ROUND(AVG(CASE WHEN vr.review_comment_message IS NOT NULL
                       AND vr.review_comment_message <> ''
                      THEN CHAR_LENGTH(vr.review_comment_message) END), 0) AS avg_comment_len
FROM v_order_review vr
JOIN orders o ON o.order_id = vr.order_id
WHERE o.order_status = 'delivered'
GROUP BY vr.review_score
ORDER BY vr.review_score;

-- @name: 6-4 品類低分率 Top 15（order×category 去重防 fan-out；n_orders >= 200）
-- 多品項訂單同品類只計一次；一張訂單跨多品類則各品類各計一次（歸因口徑，註明）。
WITH cat_dim AS (
    SELECT p.product_id,
           COALESCE(t.product_category_name_english,
                    p.product_category_name, '(uncategorized)') AS category
    FROM products p
    LEFT JOIN product_category_name_translation t
      ON t.product_category_name = p.product_category_name
), order_cat AS (
    SELECT DISTINCT oi.order_id, cd.category
    FROM order_items oi
    JOIN cat_dim cd ON cd.product_id = oi.product_id
), base AS (
    SELECT oc.category, vr.review_score
    FROM order_cat oc
    JOIN v_order_review vr ON vr.order_id = oc.order_id
    JOIN orders o          ON o.order_id  = oc.order_id
    WHERE o.order_status = 'delivered'
)
SELECT category,
       COUNT(*)                               AS n_orders,
       ROUND(AVG(review_score), 2)            AS avg_score,
       ROUND(AVG(review_score <= 2) * 100, 1) AS low_score_rate_pct
FROM base
GROUP BY category
HAVING COUNT(*) >= 200
ORDER BY low_score_rate_pct DESC
LIMIT 15;
