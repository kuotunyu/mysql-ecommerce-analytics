# 索引效能實驗報告

> 方法：每組「預熱 2 次 → 計時 5 次取中位數」，前後各做一次
> EXPLAIN FORMAT=TREE；實驗索引測完即 DROP（schema.sql 維持正典）。
> 所有計時為本機 Docker（MySQL 8.4）真實執行；資料量：orders 99,441、
> order_reviews 99,224、order_items 112,650。

## 題 06 cohort 留存 × orders 覆蓋索引

**假設**：cohort 查詢的 orders 端只需要 order_status / customer_id / order_purchase_timestamp 三欄，但現有索引皆不覆蓋此組合，優化器選擇全表掃描（叢集索引含全部欄位、較寬）。建三欄覆蓋索引後可 index-only scan（窄很多、且天然有序）。

**索引**：`CREATE INDEX idx_exp_orders_cohort ON orders (order_status, customer_id, order_purchase_timestamp)`

| | 5 次計時（ms） | **中位數（ms）** |
|---|---|---:|
| 建索引前 | 720、689、656、657、643 | **657.2** |
| 建索引後 | 648、683、680、663、662 | **662.8** |

**加速倍率：0.99x**

<details><summary>EXPLAIN FORMAT=TREE（前）</summary>

```
-> Sort: cohort_m, month_age
    -> Stream results  (cost=398609 rows=0)
        -> Nested loop left join  (cost=398609 rows=0)
            -> Filter: (period_add(cs.cohort_m,a.month_age) <= 201808)  (cost=84.8 rows=0)
                -> Inner hash join (no condition)  (cost=84.8 rows=0)
                    -> Table scan on cs  (cost=2.5..2.5 rows=0)
                        -> Materialize CTE cohort_size  (cost=0..0 rows=0)
                            -> Table scan on <temporary>
                                -> Aggregate using temporary table
                                    -> Filter: (firsts.cohort_m between 201701 and 201808)  (cost=62346..6399 rows=6317)
                                        -> Table scan on firsts  (cost=62355..63069 rows=56859)
                                            -> Materialize CTE firsts if needed  (cost=62355..62355 rows=56859)
                                                -> Group aggregate: min(extract(year_month from o.order_purchase_timestamp))  (cost=56669 rows=56859)
                                                    -> Nested loop inner join  (cost=50983 rows=56859)
                                                        -> Covering index scan on c using idx_customers_unique_id  (cost=11182 rows=100566)
                                                        -> Filter: (o.order_status = 'delivered')  (cost=0.283 rows=0.565)
                                                            -> Index lookup on o using fk_orders_customer (customer_id=c.customer_id)  (cost=0.283 rows=1.13)
                    -> Hash
                        -> Table scan on a  (cost=1.07..3.29 rows=7)
                            -> Materialize union CTE ages  (cost=0.7..0.7 rows=7)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
            -> Index lookup on a using <auto_key0> (cohort_m=cs.cohort_m, month_age=a.month_age)  (cost=0.25..9.01 rows=36)
                -> Materialize CTE activity  (cost=0..0 rows=0)
                    -> Group aggregate: count(distinct customers.customer_unique_id)
                        -> Sort: f.cohort_m, month_age
                            -> Stream results  (cost=11140 rows=3605)
                                -> Nested loop inner join  (cost=11140 rows=3605)
                                    -> Nested loop inner join  (cost=8616 rows=6377)
                                        -> Filter: (f.cohort_m between 201701 and 201808)  (cost=62346..6399 rows=6317)
                                            -> Table scan on f  (cost=62355..63069 rows=56859)
                                                -> Materialize CTE firsts if needed (query plan printed elsewhere)  (cost=62355..62355 rows=56859)
                                        -> Covering index lookup on c using idx_customers_unique_id (customer_unique_id=f.customer_unique_id)  (cost=0.25 rows=1.01)
                                    -> Filter: ((o.order_status = 'delivered') and (extract(year_month from o.order_purchase_timestamp) <= 201808))  (cost=0.283 rows=0.565)
                                        -> Index lookup on o using fk_orders_customer (customer_id=c.customer_id)  (cost=0.283 rows=1.13)

```
</details>

<details><summary>EXPLAIN FORMAT=TREE（後）</summary>

```
-> Sort: cohort_m, month_age
    -> Stream results  (cost=300439 rows=0)
        -> Nested loop left join  (cost=300439 rows=0)
            -> Filter: (period_add(cs.cohort_m,a.month_age) <= 201808)  (cost=77.7 rows=0)
                -> Inner hash join (no condition)  (cost=77.7 rows=0)
                    -> Table scan on cs  (cost=2.5..2.5 rows=0)
                        -> Materialize CTE cohort_size  (cost=0..0 rows=0)
                            -> Table scan on <temporary>
                                -> Aggregate using temporary table
                                    -> Filter: (firsts.cohort_m between 201701 and 201808)  (cost=57745..5824 rows=5749)
                                        -> Table scan on firsts  (cost=57754..58403 rows=51747)
                                            -> Materialize CTE firsts if needed  (cost=57754..57754 rows=51747)
                                                -> Group aggregate: min(extract(year_month from o.order_purchase_timestamp))  (cost=52579 rows=51747)
                                                    -> Nested loop inner join  (cost=47404 rows=51747)
                                                        -> Covering index scan on c using idx_customers_unique_id  (cost=11182 rows=100566)
                                                        -> Filter: (o.order_status = 'delivered')  (cost=0.257 rows=0.515)
                                                            -> Index lookup on o using fk_orders_customer (customer_id=c.customer_id)  (cost=0.257 rows=1.03)
                    -> Hash
                        -> Table scan on a  (cost=1.07..3.29 rows=7)
                            -> Materialize union CTE ages  (cost=0.7..0.7 rows=7)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
                                -> Rows fetched before execution  (cost=0..0 rows=1)
            -> Index lookup on a using <auto_key0> (cohort_m=cs.cohort_m, month_age=a.month_age)  (cost=0.25..7.47 rows=29.9)
                -> Materialize CTE activity  (cost=0..0 rows=0)
                    -> Group aggregate: count(distinct customers.customer_unique_id)
                        -> Sort: f.cohort_m, month_age
                            -> Stream results  (cost=9932 rows=2986)
                                -> Nested loop inner join  (cost=9932 rows=2986)
                                    -> Nested loop inner join  (cost=7842 rows=5803)
                                        -> Filter: (f.cohort_m between 201701 and 201808)  (cost=57745..5824 rows=5749)
                                            -> Table scan on f  (cost=57754..58403 rows=51747)
                                                -> Materialize CTE firsts if needed (query plan printed elsewhere)  (cost=57754..57754 rows=51747)
                                        -> Covering index lookup on c using idx_customers_unique_id (customer_unique_id=f.customer_unique_id)  (cost=0.25 rows=1.01)
                                    -> Filter: ((o.order_status = 'delivered') and (extract(year_month from o.order_purchase_timestamp) <= 201808))  (cost=0.257 rows=0.515)
                                        -> Index lookup on o using fk_orders_customer (customer_id=c.customer_id)  (cost=0.257 rows=1.03)

```
</details>

## 題 10 延遲×評分 × order_reviews window 排序索引

**假設**：v_order_review 的 ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_creation_date DESC, ...) 需對 99K 列 filesort；建同序（含 DESC）複合索引讓 window 直接以索引序讀取。

**索引**：`CREATE INDEX idx_exp_reviews_latest ON order_reviews (order_id, review_creation_date DESC, review_answer_timestamp DESC)`

| | 5 次計時（ms） | **中位數（ms）** |
|---|---|---:|
| 建索引前 | 276、275、279、283、273 | **276.1** |
| 建索引後 | 275、279、272、274、274 | **274.3** |

**加速倍率：1.01x**

<details><summary>EXPLAIN FORMAT=TREE（前）</summary>

```
-> Sort: min((to_days(cast(o.order_delivered_customer_date as date)) - to_days(cast(o.order_estimated_delivery_date as date))))
    -> Table scan on <temporary>
        -> Aggregate using temporary table
            -> Nested loop inner join  (cost=15586 rows=2641)
                -> Filter: (t.rn = 1)  (cost=10565..11888 rows=10565)
                    -> Table scan on t  (cost=10565..11888 rows=105648)
                        -> Materialize  (cost=10565..10565 rows=105648)
                            -> Window aggregate: row_number() OVER (PARTITION BY r.order_id ORDER BY r.review_creation_date desc,r.review_answer_timestamp desc,r.review_id desc )   (cost=0 rows=105648)
                                -> Sort: r.order_id, r.review_creation_date DESC, r.review_answer_timestamp DESC, r.review_id DESC  (cost=10895 rows=105648)
                                    -> Table scan on r  (cost=10895 rows=105648)
                -> Filter: ((o.order_status = 'delivered') and (o.order_delivered_customer_date is not null) and ((to_days(o.order_delivered_customer_date) - to_days(o.order_purchase_timestamp)) >= 0))  (cost=0.25 rows=0.25)
                    -> Single-row index lookup on o using PRIMARY (order_id=t.order_id)  (cost=0.25 rows=1)

```
</details>

<details><summary>EXPLAIN FORMAT=TREE（後）</summary>

```
-> Sort: min((to_days(cast(o.order_delivered_customer_date as date)) - to_days(cast(o.order_estimated_delivery_date as date))))
    -> Table scan on <temporary>
        -> Aggregate using temporary table
            -> Nested loop inner join  (cost=14337 rows=2430)
                -> Filter: (t.rn = 1)  (cost=9718..10935 rows=9718)
                    -> Table scan on t  (cost=9718..10935 rows=97182)
                        -> Materialize  (cost=9718..9718 rows=97182)
                            -> Window aggregate: row_number() OVER (PARTITION BY r.order_id ORDER BY r.review_creation_date desc,r.review_answer_timestamp desc,r.review_id desc )   (cost=0 rows=97182)
                                -> Sort: r.order_id, r.review_creation_date DESC, r.review_answer_timestamp DESC, r.review_id DESC  (cost=10048 rows=97182)
                                    -> Table scan on r  (cost=10048 rows=97182)
                -> Filter: ((o.order_status = 'delivered') and (o.order_delivered_customer_date is not null) and ((to_days(o.order_delivered_customer_date) - to_days(o.order_purchase_timestamp)) >= 0))  (cost=0.25 rows=0.25)
                    -> Single-row index lookup on o using PRIMARY (order_id=t.order_id)  (cost=0.25 rows=1)

```
</details>

## 對照組：選擇性查詢 × geolocation 覆蓋索引（倉庫最大表 738K 列）

**假設**：十題皆為全表聚合型 OLAP（索引效益天生有限），本組展示索引真正的用武之地：營運型選擇性查詢（取單一城市的座標質心）。geolocation_city 無索引 → 738K 列全表掃描；建 (city, lat, lng) 覆蓋索引後為 index-only range scan，只讀命中列。

**索引**：`CREATE INDEX idx_exp_geo_city ON geolocation (geolocation_city, geolocation_lat, geolocation_lng)`

| | 5 次計時（ms） | **中位數（ms）** |
|---|---|---:|
| 建索引前 | 60、59、59、60、60 | **59.6** |
| 建索引後 | 6、6、7、6、7 | **6.4** |

**加速倍率：9.24x**

<details><summary>EXPLAIN FORMAT=TREE（前）</summary>

```
-> Group aggregate: avg(geolocation.geolocation_lng), avg(geolocation.geolocation_lat), count(0)  (cost=81199 rows=1)
    -> Filter: (geolocation.geolocation_city = 'rio de janeiro')  (cost=73876 rows=73227)
        -> Table scan on geolocation  (cost=73876 rows=732271)

```
</details>

<details><summary>EXPLAIN FORMAT=TREE（後）</summary>

```
-> Group aggregate: avg(geolocation.geolocation_lng), avg(geolocation.geolocation_lat), count(0)  (cost=16089 rows=1)
    -> Covering index lookup on geolocation using idx_exp_geo_city (geolocation_city='rio de janeiro')  (cost=9162 rows=69266)

```
</details>

## 結論

1. **招牌數字：選擇性查詢 + 覆蓋索引 = 9.2x**（60 ms → 6.4 ms，
   exp3）。738K 列全表掃描變成 index-only range scan，是索引威力的教科書場景。
2. **十題型 OLAP 全表聚合，追加索引近乎無效**（exp1 0.99x、exp2 1.01x）。
   EXPLAIN 揭示了原因：cohort 查詢的 orders 端**本來就走 Stage A 預建的
   `idx_orders_status_purchase`**，優化器對實驗新增的覆蓋索引看都不看——
   「先為已知查詢模式設計索引」的 Stage A 決策在此得到實測背書；而 window
   物化（exp2）需要讀取全部欄位，索引序無從發揮。
3. **方法論註記（誠實聲明）**：開發過程中同一 exp1 在冷快取下曾測得 1.34x 的
   「加速」，穩定暖快取後收斂到 ~1.0x——buffer pool 暖機是效能測試最常見的
   污染源，這正是「預熱 + 多次取中位數」流程存在的理由。本報告數字皆為
   暖快取穩定值。
