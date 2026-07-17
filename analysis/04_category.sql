-- =====================================================================
-- 04_category.sql — 主題 4：品類與商品
-- 依賴：00_views.sql 的 v_order_gmv（僅 4-3 用）；品類營收在品項粒度直接聚合
-- 口徑：
--   * 品類名統一：COALESCE(英譯, 葡文原名, '(uncategorized)')——
--     一次處理 610 個 NULL 類別與 2 個缺英譯類別（與驗證/報告命名一致）
--   * 僅 delivered；品類 GMV 在 order_items 粒度加總（品項本來就屬於單一品類，
--     無 fan-out 問題）；訂單數一律 COUNT(DISTINCT order_id)
--   * 佔比先在全集合算完再取 Top N（LIMIT 放子查詢會把 share 算成榜內佔比）
-- =====================================================================

-- @name: 4-1 Top 15 品類：營收、件數、訂單數與全站佔比
WITH cat_dim AS (
    SELECT p.product_id,
           COALESCE(t.product_category_name_english,
                    p.product_category_name, '(uncategorized)') AS category
    FROM products p
    LEFT JOIN product_category_name_translation t
      ON t.product_category_name = p.product_category_name
), by_cat AS (
    SELECT cd.category,
           SUM(oi.price + oi.freight_value) AS gmv,
           COUNT(*)                         AS n_items,
           COUNT(DISTINCT oi.order_id)      AS n_orders
    FROM order_items oi
    JOIN orders o   ON o.order_id   = oi.order_id
    JOIN cat_dim cd ON cd.product_id = oi.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY cd.category
), shared AS (
    SELECT category, gmv, n_items, n_orders,
           gmv / SUM(gmv) OVER () * 100 AS pct_gmv   -- share 先於 LIMIT 在全集合上算
    FROM by_cat
)
SELECT category, ROUND(gmv, 2) AS gmv, n_items, n_orders, ROUND(pct_gmv, 1) AS pct_gmv
FROM shared
ORDER BY gmv DESC
LIMIT 15;

-- @name: 4-2 品類營收集中度 Pareto（80% 門檻）
WITH cat_dim AS (
    SELECT p.product_id,
           COALESCE(t.product_category_name_english,
                    p.product_category_name, '(uncategorized)') AS category
    FROM products p
    LEFT JOIN product_category_name_translation t
      ON t.product_category_name = p.product_category_name
), by_cat AS (
    SELECT cd.category, SUM(oi.price + oi.freight_value) AS gmv
    FROM order_items oi
    JOIN orders o   ON o.order_id   = oi.order_id
    JOIN cat_dim cd ON cd.product_id = oi.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY cd.category
)
SELECT ROW_NUMBER() OVER (ORDER BY gmv DESC)  AS rk,
       category,
       ROUND(gmv, 2)                          AS gmv,
       ROUND(gmv / SUM(gmv) OVER () * 100, 2) AS pct_gmv,
       ROUND(SUM(gmv) OVER (ORDER BY gmv DESC ROWS UNBOUNDED PRECEDING)
             / SUM(gmv) OVER () * 100, 2)     AS cum_pct_gmv  -- 明寫 ROWS（RANGE 同值陷阱）
FROM by_cat
ORDER BY gmv DESC;

-- @name: 4-3 商品長尾摘要：Top 1%/5%/10% 商品的營收佔比（單列，供報告文字）
WITH prod AS (
    SELECT oi.product_id, SUM(oi.price + oi.freight_value) AS gmv
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY oi.product_id
), ranked AS (
    SELECT gmv, PERCENT_RANK() OVER (ORDER BY gmv DESC) AS pr
    FROM prod
)
SELECT COUNT(*)                                                    AS n_products_sold,
       ROUND(SUM(CASE WHEN pr <= 0.01 THEN gmv END) / SUM(gmv) * 100, 1) AS top1pct_gmv_share,
       ROUND(SUM(CASE WHEN pr <= 0.05 THEN gmv END) / SUM(gmv) * 100, 1) AS top5pct_gmv_share,
       ROUND(SUM(CASE WHEN pr <= 0.10 THEN gmv END) / SUM(gmv) * 100, 1) AS top10pct_gmv_share
FROM ranked;

-- @name: 4-4 品類運費結構：運費佔比 vs 平均單價（GMV Top 15 品類）
-- 比率聚合正確算法：先加總再相除（平均「每單比率」會被小額訂單放大，是常見錯誤）。
WITH cat_dim AS (
    SELECT p.product_id,
           COALESCE(t.product_category_name_english,
                    p.product_category_name, '(uncategorized)') AS category
    FROM products p
    LEFT JOIN product_category_name_translation t
      ON t.product_category_name = p.product_category_name
), by_cat AS (
    SELECT cd.category,
           SUM(oi.price + oi.freight_value) AS gmv,
           SUM(oi.freight_value)            AS freight,
           AVG(oi.price)                    AS avg_item_price
    FROM order_items oi
    JOIN orders o   ON o.order_id   = oi.order_id
    JOIN cat_dim cd ON cd.product_id = oi.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY cd.category
), shared AS (
    SELECT category, gmv, freight, avg_item_price,
           freight / gmv * 100                          AS freight_share_pct,
           SUM(freight) OVER () / SUM(gmv) OVER () * 100 AS overall_freight_share_pct  -- 全站基準線
    FROM by_cat
)
SELECT category,
       ROUND(gmv, 2)                    AS gmv,
       ROUND(freight_share_pct, 1)      AS freight_share_pct,
       ROUND(avg_item_price, 2)         AS avg_item_price,
       ROUND(overall_freight_share_pct, 1) AS overall_freight_share_pct
FROM shared
ORDER BY gmv DESC
LIMIT 15;
