-- =====================================================================
-- 03_cohort.sql — 主題 3：cohort 留存與回購
-- 依賴：00_views.sql 的 v_order_gmv、v_order_review
-- 口徑：
--   * cohort 鍵 = customer_unique_id 的首次 delivered 訂單所在月
--   * cohort 月僅取 2017-01 ~ 2018-08（完整月）；觀察月同樣只到 2018-08——
--     2018-09/10 是不完整殘月，拿來當觀察月會系統性低估留存
--   * 右截斷：cohort 月 + 月齡 > 2018-08 的格子「不可觀察」→ 不輸出該列，
--     runner pivot 後以 NaN 呈現、圖上塗灰（絕不能畫成 0%）
--   * 月齡只看 0–6：總回購率 3.12%，更遠月齡的格子全是 0.0x% 雜訊
-- 誠實聲明：留存矩陣的格間差異多半在抽樣雜訊範圍內（cohort 數千人 × 留存 <1%），
--   本主題的招牌是「累積二購率曲線」（3-1b），heatmap 是輔助視覺。
-- =====================================================================

-- @name: 3-1 首購月 × 月齡留存矩陣（月齡 0–6，右截斷不輸出）
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
    -- 只展開「可觀察」的 cohort×月齡組合；observable 上限 = 最後完整月
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
       COALESCE(a.n_active, 0)                                AS n_active,
       ROUND(COALESCE(a.n_active, 0) / g.n_cohort * 100, 2)   AS retention_pct
FROM grid g
LEFT JOIN activity a ON a.cohort_m = g.cohort_m AND a.month_age = g.month_age
ORDER BY g.cohort_m, g.month_age;

-- @name: 3-1b 累積二購率曲線（招牌圖；限首購 <= 2018-02 保證 6 個月觀察窗）
-- 對每位客戶取第二張 delivered 訂單的月齡，輸出「首購後 m 個月內完成二購」的累積 %。
-- series = 首購年（2017 vs 2018-01/02），檢驗平台成熟是否改善二購。
WITH orders_c AS (
    SELECT c.customer_unique_id, o.order_id,
           o.order_purchase_timestamp,
           EXTRACT(YEAR_MONTH FROM o.order_purchase_timestamp) AS yyyymm,
           ROW_NUMBER() OVER (PARTITION BY c.customer_unique_id
                              ORDER BY o.order_purchase_timestamp) AS rn
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
), firsts AS (
    SELECT customer_unique_id, yyyymm AS cohort_m
    FROM orders_c WHERE rn = 1
), seconds AS (
    SELECT customer_unique_id, yyyymm AS second_m
    FROM orders_c WHERE rn = 2
), base AS (
    SELECT f.customer_unique_id,
           CASE WHEN f.cohort_m BETWEEN 201701 AND 201712 THEN '2017 首購'
                ELSE '2018-01/02 首購' END AS cohort_year,
           PERIOD_DIFF(s.second_m, f.cohort_m) AS second_age  -- NULL = 資料期內無二購
    FROM firsts f
    LEFT JOIN seconds s USING (customer_unique_id)
    WHERE f.cohort_m BETWEEN 201701 AND 201802   -- 右截斷修正：所有人都有 >=6 個月觀察窗
), ages AS (
    SELECT 0 AS m UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL
    SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
)
SELECT b.cohort_year, a.m AS month_age,
       COUNT(*)                                             AS n_cohort,
       SUM(b.second_age IS NOT NULL AND b.second_age <= a.m) AS n_second,
       ROUND(SUM(b.second_age IS NOT NULL AND b.second_age <= a.m)
             / COUNT(*) * 100, 2)                            AS cum_second_pct
FROM base b
CROSS JOIN ages a
GROUP BY b.cohort_year, a.m
ORDER BY b.cohort_year, a.m;

-- @name: 3-2 二購間隔分箱（會回購的人多快回購？喚回窗口在哪？）
WITH ranked AS (
    SELECT c.customer_unique_id, o.order_purchase_timestamp,
           ROW_NUMBER() OVER (PARTITION BY c.customer_unique_id
                              ORDER BY o.order_purchase_timestamp) AS rn
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
), gaps AS (
    SELECT r1.customer_unique_id,
           DATEDIFF(r2.order_purchase_timestamp, r1.order_purchase_timestamp) AS gap_days
    FROM ranked r1
    JOIN ranked r2 ON r2.customer_unique_id = r1.customer_unique_id AND r2.rn = 2
    WHERE r1.rn = 1
)
SELECT CASE WHEN gap_days <= 7   THEN '0-7 天'
            WHEN gap_days <= 30  THEN '8-30 天'
            WHEN gap_days <= 60  THEN '31-60 天'
            WHEN gap_days <= 90  THEN '61-90 天'
            WHEN gap_days <= 180 THEN '91-180 天'
            ELSE '180+ 天' END AS gap_bin,
       COUNT(*)                                         AS n_customers,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 1) AS pct
FROM gaps
GROUP BY gap_bin
ORDER BY MIN(gap_days);

-- @name: 3-3 回購者 vs 一次性客戶：首單畫像對比（右截斷修正）
-- 兩群都限「首購 <= 2018-02」：否則 2018 年中後才首購、根本沒時間回購的人
-- 會被誤歸為「一次性」，把「晚買」偽裝成「不回購的特徵」。
-- 分期數先 per-order MAX 聚合（order_payments 一單多列，直接 AVG 是列級平均會失真）。
WITH ranked AS (
    SELECT c.customer_unique_id, o.order_id, o.order_purchase_timestamp,
           ROW_NUMBER() OVER (PARTITION BY c.customer_unique_id
                              ORDER BY o.order_purchase_timestamp) AS rn,
           COUNT(*) OVER (PARTITION BY c.customer_unique_id)       AS n_orders
    FROM orders o
    JOIN customers c ON c.customer_id = o.customer_id
    WHERE o.order_status = 'delivered'
), order_inst AS (
    SELECT order_id, MAX(payment_installments) AS max_installments
    FROM order_payments
    GROUP BY order_id
), firsts AS (
    SELECT r.customer_unique_id,
           CASE WHEN r.n_orders >= 2 THEN '回購者' ELSE '一次性客戶' END AS grp,
           g.gmv,
           oi.freight_share,
           vr.review_score,
           inst.max_installments
    FROM ranked r
    JOIN v_order_gmv g ON g.order_id = r.order_id
    LEFT JOIN (
        SELECT order_id, SUM(freight_value) / SUM(price + freight_value) AS freight_share
        FROM order_items GROUP BY order_id
    ) oi ON oi.order_id = r.order_id
    LEFT JOIN v_order_review vr ON vr.order_id = r.order_id
    LEFT JOIN order_inst inst   ON inst.order_id = r.order_id
    WHERE r.rn = 1
      AND EXTRACT(YEAR_MONTH FROM r.order_purchase_timestamp) BETWEEN 201701 AND 201802
)
SELECT grp,
       COUNT(*)                          AS n_customers,
       ROUND(AVG(gmv), 2)                AS avg_first_gmv,
       ROUND(AVG(review_score), 3)       AS avg_first_review,
       ROUND(AVG(freight_share) * 100, 1) AS avg_freight_share_pct,
       ROUND(AVG(max_installments), 2)   AS avg_max_installments
FROM firsts
GROUP BY grp
ORDER BY grp;
