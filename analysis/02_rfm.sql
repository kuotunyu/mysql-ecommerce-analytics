-- =====================================================================
-- 02_rfm.sql — 主題 2：RFM 客戶分群
-- 依賴：00_views.sql 的 v_order_gmv
-- 口徑：
--   * 以 customer_unique_id 折疊自然人（customer_id 是每單一個的訂單維度身分）
--   * 僅 delivered；M = 該客所有 delivered 訂單 GMV 合計
--   * R 基準日 = 資料內最大 delivered 下單時間（CTE anchor，不用 NOW()，可重現；
--     實測落在 2018-08，非殘月——2018-09/10 沒有任何 delivered）
--   * F 誠實化：實測 96.9% 客戶 F=1，NTILE(5) 會把同值硬切五箱（切法由排序隨機性
--     決定，是統計謊言）→ 固定分箱 F=1→1 分、F=2→3 分、F≥3→5 分
--   * R、M 為連續值無退化問題，用 NTILE(5)（R 分數 5=最近；R 的 NTILE 在同日大量
--     同值時箱界仍有隨機性，屬已知限制，影響遠小於 F）
-- 敘事定位：Olist 是收單平台、客戶關係屬賣家，結構性低回購是資料特徵不是錯誤；
--   本主題的 RFM 是「在一次性客戶海中找出值得喚回的高 M 客」，不是教科書忠誠度模板。
-- =====================================================================

-- @name: 2-1 客戶層 RFM 明細與打分
-- @nocsv
WITH anchor AS (
    SELECT MAX(order_purchase_timestamp) AS anchor_ts
    FROM orders WHERE order_status = 'delivered'
), cust AS (
    SELECT c.customer_unique_id,
           DATEDIFF((SELECT anchor_ts FROM anchor), MAX(o.order_purchase_timestamp)) AS recency_days,
           COUNT(DISTINCT o.order_id) AS frequency,
           SUM(g.gmv)                 AS monetary
    FROM orders o
    JOIN customers   c ON c.customer_id = o.customer_id
    JOIN v_order_gmv g ON g.order_id    = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
)
SELECT customer_unique_id,
       recency_days,
       frequency,
       ROUND(monetary, 2) AS monetary,
       NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,  -- 5 = 最近活躍
       CASE WHEN frequency = 1 THEN 1
            WHEN frequency = 2 THEN 3
            ELSE 5 END                             AS f_score,  -- 固定分箱（見檔頭）
       NTILE(5) OVER (ORDER BY monetary)           AS m_score
FROM cust;

-- @name: 2-2 F 分布真相表（先把殘酷事實釘在桌上）
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
       COUNT(*)                                              AS n_customers,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 2)      AS pct
FROM freq
GROUP BY f_bin
ORDER BY MIN(f);

-- @name: 2-3 七分群：人數、GMV 貢獻與流失天數
-- F 退化下以 R×M 為主軸、F>=2 獨立成群；「流失高價值」是本資料集最值得行動的一群。
WITH anchor AS (
    SELECT MAX(order_purchase_timestamp) AS anchor_ts
    FROM orders WHERE order_status = 'delivered'
), cust AS (
    SELECT c.customer_unique_id,
           DATEDIFF((SELECT anchor_ts FROM anchor), MAX(o.order_purchase_timestamp)) AS recency_days,
           COUNT(DISTINCT o.order_id) AS frequency,
           SUM(g.gmv)                 AS monetary
    FROM orders o
    JOIN customers   c ON c.customer_id = o.customer_id
    JOIN v_order_gmv g ON g.order_id    = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
), scored AS (
    SELECT customer_unique_id, recency_days, monetary,
           NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
           CASE WHEN frequency = 1 THEN 1 WHEN frequency = 2 THEN 3 ELSE 5 END AS f_score,
           NTILE(5) OVER (ORDER BY monetary)          AS m_score
    FROM cust
), seg AS (
    SELECT *,
           CASE WHEN f_score >= 3 AND r_score >= 4 THEN '回頭熟客'
                WHEN f_score >= 3                  THEN '沉睡回頭客'
                WHEN r_score >= 4 AND m_score >= 4 THEN '高價值新客'
                WHEN r_score >= 4                  THEN '一般新客'
                WHEN r_score = 3                   THEN '觀望中'
                WHEN m_score >= 4                  THEN '流失高價值'
                ELSE '流失低價值' END AS segment
    FROM scored
)
SELECT segment,
       COUNT(*)                                         AS n_customers,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 1) AS pct_customers,
       ROUND(AVG(monetary), 2)                          AS avg_monetary,
       ROUND(SUM(monetary) / SUM(SUM(monetary)) OVER () * 100, 1) AS pct_gmv,
       ROUND(AVG(recency_days))                         AS avg_recency_days
FROM seg
GROUP BY segment
ORDER BY FIELD(segment, '回頭熟客','沉睡回頭客','高價值新客','一般新客','觀望中','流失高價值','流失低價值');

-- @name: 2-4 R×M 5×5 方格（僅 F=1 一次性客戶）
-- 取代教科書的 R×F 矩陣——F 退化後 R×F 只剩一條線，R×M 才是有資訊量的地形圖。
WITH anchor AS (
    SELECT MAX(order_purchase_timestamp) AS anchor_ts
    FROM orders WHERE order_status = 'delivered'
), cust AS (
    SELECT c.customer_unique_id,
           DATEDIFF((SELECT anchor_ts FROM anchor), MAX(o.order_purchase_timestamp)) AS recency_days,
           COUNT(DISTINCT o.order_id) AS frequency,
           SUM(g.gmv)                 AS monetary
    FROM orders o
    JOIN customers   c ON c.customer_id = o.customer_id
    JOIN v_order_gmv g ON g.order_id    = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_unique_id
), scored AS (
    SELECT NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
           NTILE(5) OVER (ORDER BY monetary)          AS m_score,
           frequency, monetary
    FROM cust
)
SELECT r_score, m_score,
       COUNT(*)               AS n_customers,
       ROUND(AVG(monetary), 2) AS avg_monetary
FROM scored
WHERE frequency = 1
GROUP BY r_score, m_score
ORDER BY r_score, m_score;

-- @name: 2-5 州別客戶分布與 GMV（Pareto）
-- 極少數自然人的多張訂單分屬不同州，會在各州各計一次（影響可忽略，註明即可）。
WITH per_cust_state AS (
    SELECT c.customer_state, c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS f,
           SUM(g.gmv)                 AS gmv
    FROM orders o
    JOIN customers   c ON c.customer_id = o.customer_id
    JOIN v_order_gmv g ON g.order_id    = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY c.customer_state, c.customer_unique_id
), by_state AS (
    SELECT customer_state,
           COUNT(*)          AS n_customers,
           SUM(gmv)          AS gmv,
           AVG(gmv)          AS gmv_per_customer,
           AVG(f >= 2)       AS repeat_rate
    FROM per_cust_state
    GROUP BY customer_state
)
SELECT customer_state,
       n_customers,
       ROUND(gmv, 2) AS gmv,
       ROUND(gmv / SUM(gmv) OVER () * 100, 1) AS pct_gmv,
       ROUND(SUM(gmv) OVER (ORDER BY gmv DESC ROWS UNBOUNDED PRECEDING)
             / SUM(gmv) OVER () * 100, 1)     AS cum_pct_gmv,  -- 明寫 ROWS：RANGE 遇同值會共享累積值
       ROUND(gmv_per_customer, 2)             AS gmv_per_customer,
       ROUND(repeat_rate * 100, 2)            AS repeat_rate_pct
FROM by_state
ORDER BY gmv DESC;
