-- 題目 08：賣家營收排名（RANK window）
-- 商業問題：平台營收多依賴頭部賣家？Top 賣家的規模與集中度？
-- SQL 技巧：RANK() OVER 排名（同額並列跳號，與 DENSE_RANK 的差異在註解點名）、
--           累積佔比 window 明寫 ROWS UNBOUNDED PRECEDING（RANGE 遇同值共享累積值）
-- 口徑：僅 delivered；賣家 GMV 在品項粒度加總（品項屬單一賣家，無 fan-out）
WITH by_seller AS (
    SELECT oi.seller_id,
           s.seller_state,
           SUM(oi.price + oi.freight_value) AS gmv,
           COUNT(DISTINCT oi.order_id)      AS n_orders
    FROM order_items oi
    JOIN orders  o ON o.order_id  = oi.order_id
    JOIN sellers s ON s.seller_id = oi.seller_id
    WHERE o.order_status = 'delivered'
    GROUP BY oi.seller_id, s.seller_state
)
SELECT RANK() OVER (ORDER BY gmv DESC) AS rk,   -- RANK：同額並列、名次跳號
       seller_id,
       seller_state,
       ROUND(gmv, 2)                          AS gmv,
       n_orders,
       ROUND(gmv / SUM(gmv) OVER () * 100, 2) AS pct_gmv,
       ROUND(SUM(gmv) OVER (ORDER BY gmv DESC ROWS UNBOUNDED PRECEDING)
             / SUM(gmv) OVER () * 100, 1)     AS cum_pct_gmv
FROM by_seller
ORDER BY gmv DESC
LIMIT 20;
