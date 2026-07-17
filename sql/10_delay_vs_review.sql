-- 題目 10：配送遲到與評分的交叉分析
-- 商業問題：晚到多少天會讓評分崩掉？物流是不是差評首因？
-- SQL 技巧：CTE、跨主題口徑重用（v_order_review 集中處理一單多評=取最新）、
--           CASE 分箱、布林條件聚合（低分率 AVG(score<=2)）
-- 口徑：母體＝delivered 且有送達時間的訂單之最新評論；
--       delay 定義同題目 03（DATE 級比較）
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
       COUNT(*)                               AS n_orders,
       ROUND(AVG(review_score), 2)            AS avg_score,
       ROUND(AVG(review_score <= 2) * 100, 1) AS low_score_rate_pct
FROM base
GROUP BY delay_bin
ORDER BY MIN(delay_days);
