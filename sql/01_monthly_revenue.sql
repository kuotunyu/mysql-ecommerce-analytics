-- 題目 01：月營收趨勢
-- 商業問題：平台 GMV 的月度規模與成長動能如何？單均價值有沒有被擴張稀釋？
-- SQL 技巧：CTE、LAG() window 算 MoM、CASE 守門（僅相鄰兩月皆完整月才計成長率）
-- 口徑：GMV = SUM(price+freight_value)、僅 delivered（依賴 analysis/00_views.sql 的
--       v_order_gmv 防 fan-out）；month_flag 標注 2016 稀疏月與 2018-09/10 殘月
WITH monthly AS (
    SELECT DATE_FORMAT(o.order_purchase_timestamp, '%Y-%m') AS ym,
           COUNT(DISTINCT o.order_id)                       AS n_orders,
           SUM(g.gmv)                                       AS gmv
    FROM orders o
    JOIN v_order_gmv g ON g.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY ym
), flagged AS (
    SELECT ym, n_orders, gmv,
           CASE WHEN ym BETWEEN '2017-01' AND '2018-08' THEN 'full' ELSE 'partial' END AS month_flag
    FROM monthly
)
SELECT ym, month_flag, n_orders,
       ROUND(gmv, 2)            AS gmv,
       ROUND(gmv / n_orders, 2) AS aov,
       CASE WHEN month_flag = 'full' AND LAG(month_flag) OVER (ORDER BY ym) = 'full'
            THEN ROUND((gmv - LAG(gmv) OVER (ORDER BY ym))
                       / LAG(gmv) OVER (ORDER BY ym) * 100, 1)
       END AS mom_pct
FROM flagged
ORDER BY ym;
