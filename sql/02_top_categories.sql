-- 題目 02：營收 Top 15 品類（含類別英文翻譯表 JOIN）
-- 商業問題：哪些品類撐起平台營收？集中度多高？
-- SQL 技巧：LEFT JOIN 翻譯表 + COALESCE 三層回退（英譯→葡文→'(uncategorized)'）、
--           全表 window SUM() OVER () 算佔比——share 在全集合算完才 LIMIT，
--           否則會變成「榜內佔比」（常見錯誤）
-- 口徑：僅 delivered；品項粒度聚合（品項屬單一品類，無 fan-out）；
--       訂單數用 COUNT(DISTINCT order_id)
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
    JOIN orders o   ON o.order_id    = oi.order_id
    JOIN cat_dim cd ON cd.product_id = oi.product_id
    WHERE o.order_status = 'delivered'
    GROUP BY cd.category
), shared AS (
    SELECT category, gmv, n_items, n_orders,
           gmv / SUM(gmv) OVER () * 100 AS pct_gmv
    FROM by_cat
)
SELECT category, ROUND(gmv, 2) AS gmv, n_items, n_orders, ROUND(pct_gmv, 1) AS pct_gmv
FROM shared
ORDER BY gmv DESC
LIMIT 15;
