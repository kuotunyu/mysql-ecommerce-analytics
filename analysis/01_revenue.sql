-- =====================================================================
-- 01_revenue.sql — 主題 1：月營收與成長
-- 依賴：00_views.sql 的 v_order_gmv
-- 口徑：GMV = SUM(price+freight_value)、僅 delivered、時間軸 = 下單時間。
--       母體為「有品項的 delivered 訂單」（INNER JOIN v_order_gmv 天然排除無品項者）。
-- 月份誠實性（本檔唯一一次呈現完整實況，其餘主題一律裁剪完整月）：
--   全量輸出 + month_flag（full = 2017-01~2018-08 的 20 個完整月；
--   partial = 2016 冷啟動稀疏期與 2018-09/10 資料截尾殘月）。
--   MoM 僅在「本月與前一月皆 full」時計算，否則 NULL——
--   避免 2017-01 對上只有 1 筆的 2016-12 算出數萬 % 的假成長，
--   也避開 2016-11 整月缺列造成 LAG 錯位。圖上 partial 月灰化。
-- =====================================================================

-- @name: Q1 月營收總覽（GMV、訂單數、AOV、MoM）
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
SELECT ym,
       month_flag,
       n_orders,
       ROUND(gmv, 2)            AS gmv,
       ROUND(gmv / n_orders, 2) AS aov,
       CASE WHEN month_flag = 'full' AND LAG(month_flag) OVER (ORDER BY ym) = 'full'
            THEN ROUND((gmv - LAG(gmv) OVER (ORDER BY ym))
                       / LAG(gmv) OVER (ORDER BY ym) * 100, 1)
       END AS mom_pct
FROM flagged
ORDER BY ym;
