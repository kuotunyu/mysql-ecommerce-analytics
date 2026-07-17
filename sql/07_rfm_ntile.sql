-- 題目 07：RFM 三維計算（NTILE window）
-- 商業問題：每位自然人客戶的 R/F/M 分數為何？分數組合的人口分布長怎樣？
-- SQL 技巧：CTE 鏈（anchor→客戶聚合→打分→組合統計）、NTILE(5) window、
--           DATEDIFF、標量子查詢帶入可重現的基準日（不用 NOW()）
-- 口徑與方法論注意：R 基準日=資料內最大 delivered 下單日；M=GMV（v_order_gmv）。
--   F 不用 NTILE——實測 97.0% 客戶 F=1，NTILE 會把同值硬切五箱（箱界由排序隨機性
--   決定，是統計謊言）→ F 用固定分箱 1/3/5 分（一次/二次/3+ 次）。
--   輸出為 R×F×M 分數組合的人數與平均原始值（明細層供 mining/rfm_clustering.py 使用）。
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
    SELECT recency_days, frequency, monetary,
           NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
           CASE WHEN frequency = 1 THEN 1 WHEN frequency = 2 THEN 3 ELSE 5 END AS f_score,
           NTILE(5) OVER (ORDER BY monetary)          AS m_score
    FROM cust
)
SELECT r_score, f_score, m_score,
       COUNT(*)                    AS n_customers,
       ROUND(AVG(recency_days))    AS avg_recency_days,
       ROUND(AVG(frequency), 2)    AS avg_frequency,
       ROUND(AVG(monetary), 2)     AS avg_monetary
FROM scored
GROUP BY r_score, f_score, m_score
ORDER BY r_score DESC, f_score DESC, m_score DESC;
