-- =====================================================================
-- 00_views.sql — Stage B 共用 view（僅 2 個，CREATE OR REPLACE 冪等）
-- run_analysis.py 每次執行前必先跑本檔；各主題檔依賴這兩個 view。
-- 設計原則：把「最容易寫錯的口徑」集中一處——
--   (1) fan-out 重複計數（訂單×品項 JOIN 後 SUM 會虛增）
--   (2) 一單多評（99,224 評論列 vs 98,673 個訂單）
-- =====================================================================

-- v_order_gmv：訂單粒度 GMV。任何營收計算一律 JOIN 本 view，
-- 禁止先 JOIN order_items 再 SUM（多品項訂單會重複計入訂單級欄位）。
-- GMV 口徑 = SUM(price + freight_value)，與金流口徑（order_payments）不可互驗。
CREATE OR REPLACE VIEW v_order_gmv AS
SELECT order_id,
       COUNT(*)                  AS n_items,
       SUM(price + freight_value) AS gmv
FROM order_items
GROUP BY order_id;

-- v_order_review：每訂單「最新一筆」評論（全案唯一的一單多評口徑）。
-- 取最新而非平均的理由：(1) 多評語意上是問卷重發/顧客改評，最新代表最終態度；
-- (2) 保留 1..5 整數分布，分箱不出現人工小數；(3) score 與留言/時間欄位同一筆、口徑一致。
-- 06_reviews.sql 的 6-0 有「平均法 vs 最新法」敏感度對照，差異可忽略。
CREATE OR REPLACE VIEW v_order_review AS
SELECT order_id, review_id, review_score,
       review_comment_title, review_comment_message,
       review_creation_date, review_answer_timestamp
FROM (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY review_creation_date DESC, review_answer_timestamp DESC, review_id DESC
           ) AS rn
    FROM order_reviews r
) t
WHERE rn = 1;
