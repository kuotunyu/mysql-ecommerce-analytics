-- 題目 05：回購率
-- 商業問題：客戶會回來買第二次嗎？平台是留客生意還是獲客生意？
-- SQL 技巧：以 customer_unique_id 折疊自然人（customer_id 是每單一個的訂單維度身分，
--           直接數會把回購率算成 0）、雙層聚合、window 佔比
-- 口徑：僅 delivered；回購 = 同一自然人有 >= 2 筆 delivered 訂單
WITH freq AS (
    SELECT c.customer_unique_id, COUNT(DISTINCT o.order_id) AS f
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
)
SELECT CASE WHEN f = 1 THEN 'F=1 一次客'
            WHEN f = 2 THEN 'F=2 二次客'
            ELSE 'F>=3 多次客' END AS f_bin,
       COUNT(*)                                         AS n_customers,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 2) AS pct
FROM freq
GROUP BY f_bin
ORDER BY MIN(f);
