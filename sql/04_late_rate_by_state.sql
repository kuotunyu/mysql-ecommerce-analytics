-- 題目 04：各州遲到率
-- 商業問題：哪些州是物流重災區？跨州服務品質差距多大？
-- SQL 技巧：JOIN 維度表取州、布林條件聚合 AVG(condition) 直接得比率
--           （注意方向：AVG(delay>0)=遲到率、AVG(delay<=0)=準時率，寫反整題反向）
-- 口徑：同題目 03；遲到 = DATE(delivered) > DATE(estimated)
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
       ROUND(AVG(delay_days > 0) * 100, 1)  AS late_rate_pct,
       ROUND(AVG(delivery_days), 1)         AS avg_delivery_days,
       ROUND(AVG(CASE WHEN delay_days > 0 THEN delay_days END), 1) AS avg_late_days
FROM base
GROUP BY customer_state
ORDER BY late_rate_pct DESC;
