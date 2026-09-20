-- =====================================================================
-- verify.sql — 載入後驗證（由 scripts/run_verify.py 依 @section 切割執行；
--               也可整檔手動貼進 mysql client 跑）
-- Section A) counts      ：每表 COUNT(*)，runner 對照 manifest（load.py 實跑產出）
-- Section B) fk_orphans  ：6 條 FK 孤兒檢查（必須=0）＋ INFO 級已知資料品質基線
-- Section C) sample_joins：3 個代表性 JOIN（跑通即過，結果供人工 sanity check）
-- =====================================================================

-- @section: counts
SELECT 'product_category_name_translation' AS tbl, COUNT(*) AS n FROM product_category_name_translation
UNION ALL SELECT 'customers',       COUNT(*) FROM customers
UNION ALL SELECT 'sellers',         COUNT(*) FROM sellers
UNION ALL SELECT 'geolocation',     COUNT(*) FROM geolocation
UNION ALL SELECT 'products',        COUNT(*) FROM products
UNION ALL SELECT 'orders',          COUNT(*) FROM orders
UNION ALL SELECT 'order_items',     COUNT(*) FROM order_items
UNION ALL SELECT 'order_payments',  COUNT(*) FROM order_payments
UNION ALL SELECT 'order_reviews',   COUNT(*) FROM order_reviews
UNION ALL SELECT 'geolocation_zip', COUNT(*) FROM geolocation_zip;

-- @section: fk_orphans
-- 6 條 FK（載入期間 FK 檢查全程開啟，此處為事後複核；預期全 0）
SELECT 'orders.customer_id -> customers' AS chk, COUNT(*) AS orphans, 'FK' AS level
FROM orders o LEFT JOIN customers c ON o.customer_id = c.customer_id
WHERE c.customer_id IS NULL
UNION ALL
SELECT 'order_items.order_id -> orders', COUNT(*), 'FK'
FROM order_items i LEFT JOIN orders o ON i.order_id = o.order_id
WHERE o.order_id IS NULL
UNION ALL
SELECT 'order_items.product_id -> products', COUNT(*), 'FK'
FROM order_items i LEFT JOIN products p ON i.product_id = p.product_id
WHERE p.product_id IS NULL
UNION ALL
SELECT 'order_items.seller_id -> sellers', COUNT(*), 'FK'
FROM order_items i LEFT JOIN sellers s ON i.seller_id = s.seller_id
WHERE s.seller_id IS NULL
UNION ALL
SELECT 'order_payments.order_id -> orders', COUNT(*), 'FK'
FROM order_payments p LEFT JOIN orders o ON p.order_id = o.order_id
WHERE o.order_id IS NULL
UNION ALL
SELECT 'order_reviews.order_id -> orders', COUNT(*), 'FK'
FROM order_reviews r LEFT JOIN orders o ON r.order_id = o.order_id
WHERE o.order_id IS NULL
UNION ALL
-- INFO 級：已知資料品質事實（允許 >0，僅記錄基線）
SELECT 'INFO: customers.zip 無 geolocation_zip 對應（預期279=剖析278+1個僅境外座標zip）', COUNT(*), 'INFO'
FROM customers c LEFT JOIN geolocation_zip g ON c.customer_zip_code_prefix = g.zip_code_prefix
WHERE g.zip_code_prefix IS NULL
UNION ALL
SELECT 'INFO: sellers.zip 無 geolocation_zip 對應（預期7）', COUNT(*), 'INFO'
FROM sellers s LEFT JOIN geolocation_zip g ON s.seller_zip_code_prefix = g.zip_code_prefix
WHERE g.zip_code_prefix IS NULL
UNION ALL
SELECT 'INFO: products 類別為 NULL（預期610）', COUNT(*), 'INFO'
FROM products WHERE product_category_name IS NULL
UNION ALL
SELECT 'INFO: products 類別缺英譯（pc_gamer 等 2 類）', COUNT(*), 'INFO'
FROM products p LEFT JOIN product_category_name_translation t
  ON p.product_category_name = t.product_category_name
WHERE p.product_category_name IS NOT NULL AND t.product_category_name IS NULL
UNION ALL
SELECT 'INFO: delivered 但無送達時間（預期8）', COUNT(*), 'INFO'
FROM orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NULL
UNION ALL
SELECT 'INFO: 訂單無任何付款紀錄（預期1）', COUNT(*), 'INFO'
FROM orders o LEFT JOIN order_payments p ON o.order_id = p.order_id
WHERE p.order_id IS NULL;

-- @section: sample_joins
-- @name: J1 月營收趨勢（delivered，GMV=price+freight；驗 DATETIME/DECIMAL/複合索引）
SELECT DATE_FORMAT(o.order_purchase_timestamp, '%Y-%m') AS ym,
       COUNT(DISTINCT o.order_id)                        AS n_orders,
       ROUND(SUM(oi.price + oi.freight_value), 2)        AS gmv
FROM orders o
JOIN order_items oi ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY ym
ORDER BY ym
LIMIT 5;

-- @name: J2 品類英譯銷售 Top 10（三表鏈 + LEFT JOIN 缺譯 COALESCE 回退）
SELECT COALESCE(t.product_category_name_english,
                p.product_category_name, '(uncategorized)') AS category,
       COUNT(*)                                             AS items_sold,
       ROUND(SUM(oi.price), 2)                              AS revenue
FROM order_items oi
JOIN products p ON p.product_id = oi.product_id
LEFT JOIN product_category_name_translation t
  ON t.product_category_name = p.product_category_name
GROUP BY category
ORDER BY revenue DESC
LIMIT 10;

-- @name: J3 付款方式 × 平均評分（先各自按訂單預聚合，避免 fan-out 重複計數）
WITH order_score AS (
    SELECT order_id, AVG(review_score) AS score
    FROM order_reviews GROUP BY order_id
), order_pay AS (
    SELECT DISTINCT order_id, payment_type
    FROM order_payments
)
SELECT op.payment_type,
       COUNT(*)                 AS n_orders,
       ROUND(AVG(os.score), 3)  AS avg_review_score
FROM order_pay op
JOIN order_score os ON os.order_id = op.order_id
GROUP BY op.payment_type
ORDER BY n_orders DESC;
