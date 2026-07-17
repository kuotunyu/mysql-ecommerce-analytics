-- 題目 03：實際 vs 預估配送天數分佈
-- 商業問題：Olist 的預估交期準嗎？偏差往哪邊偏、偏多少天？
-- SQL 技巧：DATEDIFF 日期差、CASE 多段分箱、window SUM(COUNT(*)) OVER () 算佔比、
--           ORDER BY MIN() 讓分箱依語意排序
-- 口徑：delay_days = DATEDIFF(DATE(delivered), DATE(estimated))，負值＝提早；
--       estimated 只有日期（00:00:00），比 timestamp 會把當天下午送達誤判成晚到；
--       母體排除 8 筆無送達時間與時間順序異常列
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
