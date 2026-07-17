-- 題目 06：月度 cohort 留存（CTE + window）
-- 商業問題：新客首購後的第 1~6 個月還會回來嗎？留存有隨平台成熟改善嗎？
-- SQL 技巧：多層 CTE、PERIOD_DIFF/PERIOD_ADD 月差運算、衍生月齡表 CROSS 展開、
--           LEFT JOIN + COALESCE 補 0——關鍵：右截斷格（cohort 月+月齡超出資料期）
--           不輸出而非補 0，「觀察不到」和「留存 0%」是兩回事
-- 口徑：cohort = 首次 delivered 訂單所在月，僅取完整月 2017-01~2018-08；
--       觀察月同樣只到 2018-08；月齡 0-6（回購率 3% 下更遠月齡全是雜訊）
WITH orders_c AS (
    SELECT c.customer_unique_id,
           EXTRACT(YEAR_MONTH FROM o.order_purchase_timestamp) AS yyyymm
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
), firsts AS (
    SELECT customer_unique_id, MIN(yyyymm) AS cohort_m
    FROM orders_c
    GROUP BY customer_unique_id
), cohort_size AS (
    SELECT cohort_m, COUNT(*) AS n_cohort
    FROM firsts
    WHERE cohort_m BETWEEN 201701 AND 201808
    GROUP BY cohort_m
), ages AS (
    SELECT 0 AS month_age UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL
    SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
), grid AS (
    SELECT cs.cohort_m, cs.n_cohort, a.month_age
    FROM cohort_size cs
    JOIN ages a ON PERIOD_ADD(cs.cohort_m, a.month_age) <= 201808
), activity AS (
    SELECT f.cohort_m,
           PERIOD_DIFF(oc.yyyymm, f.cohort_m) AS month_age,
           COUNT(DISTINCT oc.customer_unique_id) AS n_active
    FROM firsts f
    JOIN orders_c oc USING (customer_unique_id)
    WHERE f.cohort_m BETWEEN 201701 AND 201808
      AND oc.yyyymm <= 201808
    GROUP BY f.cohort_m, month_age
)
SELECT g.cohort_m, g.month_age, g.n_cohort,
       COALESCE(a.n_active, 0)                              AS n_active,
       ROUND(COALESCE(a.n_active, 0) / g.n_cohort * 100, 2) AS retention_pct
FROM grid g
LEFT JOIN activity a ON a.cohort_m = g.cohort_m AND a.month_age = g.month_age
ORDER BY g.cohort_m, g.month_age;
