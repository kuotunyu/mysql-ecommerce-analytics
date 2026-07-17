# 品類關聯規則報告（apriori）

> 籃子 = 同一 delivered 訂單內的相異品類集合（品類層級，避免商品層級過度稀疏）。
> 由 `mining/association_rules.py` 真實執行產生；條件宇宙完整規則表見
> [association_rules.csv](association_rules.csv)。

## 籃子結構（先講誠實的前提）

- 訂單籃總數：**96,478**；含 >= 2 相異品類的籃子：**780（0.81%）**。
- 共現門檻：>= 10 筆訂單（樣本數列於各表，由讀者評估穩健性）。

## 視角一：全域宇宙（96,478 筆訂單）——誠實的負面結果

單一品類訂單佔 99.2%，導致幾乎所有品類對的共購率**低於**隨機期望
（lift < 1 是結構性現象）。共現 >= 10 筆的品類對中，
lift > 1 者僅 **1 對**：

| antecedents   | consequents    |   n_orders |   support |   confidence |   lift |
|---------------|----------------|------------|-----------|--------------|--------|
| home_confort  | bed_bath_table |         43 |   0.00045 |         0.11 |   1.14 |

**結論**：Olist 的訂單結構不支撐大規模品類交叉銷售敘事——這本身是重要發現。

## 視角二：條件宇宙（780 筆跨品類訂單）——會跨買的人怎麼搭

在「至少買了兩個品類」的訂單裡，哪些組合出現得比該宇宙的隨機期望更頻繁：

| antecedents               | consequents     |   n_orders |   support |   confidence |   lift |
|---------------------------|-----------------|------------|-----------|--------------|--------|
| perfumery                 | health_beauty   |         11 |   0.0141  |        0.407 |   4.48 |
| home_confort              | bed_bath_table  |         43 |   0.05513 |        0.86  |   3.3  |
| toys                      | baby            |         19 |   0.02436 |        0.373 |   3.12 |
| cool_stuff                | baby            |         20 |   0.02564 |        0.299 |   2.5  |
| construction_tools_lights | furniture_decor |         11 |   0.0141  |        0.611 |   2.34 |
| health_beauty             | sports_leisure  |         14 |   0.01795 |        0.197 |   2.14 |
| home_construction         | furniture_decor |         13 |   0.01667 |        0.5   |   1.91 |
| (uncategorized)           | housewares      |         14 |   0.01795 |        0.233 |   1.57 |
| bed_bath_table            | furniture_decor |         70 |   0.08974 |        0.345 |   1.32 |
| garden_tools              | housewares      |         11 |   0.0141  |        0.155 |   1.04 |
| sports_leisure            | housewares      |         11 |   0.0141  |        0.153 |   1.03 |

## 三條白話解讀（條件宇宙）

- 在「會跨品類購買」的訂單中，買了 **perfumery** 的有 **40.7%** 同單也買 **health_beauty**——是該宇宙隨機期望的 **4.5 倍**（共現 11 筆，樣本小，方向性參考）。
- 在「會跨品類購買」的訂單中，買了 **home_confort** 的有 **86.0%** 同單也買 **bed_bath_table**——是該宇宙隨機期望的 **3.3 倍**（共現 43 筆，樣本小，方向性參考）。
- 在「會跨品類購買」的訂單中，買了 **toys** 的有 **37.3%** 同單也買 **baby**——是該宇宙隨機期望的 **3.1 倍**（共現 19 筆，樣本小，方向性參考）。

**商業應用與界線**：條件宇宙的高 lift 組合可作為「加購推薦」候選，但母體僅
0.81% 的訂單，期望效益要按此比例折現；別把小眾慣性當大眾行為。
