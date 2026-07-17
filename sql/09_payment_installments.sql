-- 題目 09：付款方式與分期分佈
-- 商業問題：顧客怎麼付錢？分期行為集中在哪種付款方式、切多少期？
-- SQL 技巧：雙維 GROUP BY + CASE 分箱、window 佔比（全表與付款方式內兩層）、
--           條件聚合
-- 口徑：付款「列」粒度（order_payments 一單可多列：多重付款/voucher 組合），
--       金額佔比以 payment_value（金流口徑）計——注意與 GMV 口徑不可互驗；
--       installments=0 有 2 列、payment_type='not_defined' 有 3 列，原始資料保留
SELECT payment_type,
       CASE WHEN payment_installments <= 1  THEN '一次付清(0-1期)'
            WHEN payment_installments <= 3  THEN '2-3 期'
            WHEN payment_installments <= 6  THEN '4-6 期'
            WHEN payment_installments <= 12 THEN '7-12 期'
            ELSE '13+ 期' END AS installment_bin,
       COUNT(*)                                    AS n_payments,
       COUNT(DISTINCT order_id)                    AS n_orders,
       ROUND(SUM(payment_value), 2)                AS total_value,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 2)              AS pct_rows,
       ROUND(SUM(payment_value) / SUM(SUM(payment_value)) OVER () * 100, 2) AS pct_value
FROM order_payments
GROUP BY payment_type, installment_bin
ORDER BY payment_type, MIN(payment_installments);
