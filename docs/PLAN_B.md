# Stage B 實作計畫：六大主題 SQL 分析（Olist 倉庫）

> 經 4 視角設計＋對抗性審查後裁決定稿。實作照此執行，調整同步回本檔。
> 交付：`analysis/*.sql`（可獨立重跑）＋ `scripts/run_analysis.py`（產表產圖）＋
> `reports/REPORT.md`（正體中文，含圖表）。之後進 GitHub 作品集。

## 資料事實（全實測，設計依據）

- 96,096 位自然人（customer_unique_id）；**93,099（96.9%）只買一次**，回購率 3.12%
  （2,745 人×2 次、203×3、…max 17）→ RFM 的 F 用 NTILE 必退化，改固定分箱。
- **完整月份區間 2017-01 ~ 2018-08（20 個月）**；2016-09/10/12 稀疏（4/324/1 筆，
  2016-11 整月缺）、2018-09/10 殘月（16/4 筆、0 delivered）。
- delivered 96,478，其中 8 筆無送達時間（物流母體 96,470，報告一句話交代差異）。
- 一單多評與一評多單存在；GMV 口徑（items）≠ 金流口徑（payments），不可互驗。

## 全域口徑裁決（所有 SQL 一體適用）

1. **delivered 口徑**：營收/RFM/cohort 用 `order_status='delivered'`；
   物流另加 `delivered_customer_date IS NOT NULL`（排 8 筆）。
2. **一單多評＝取最新一筆**（ROW_NUMBER 按 creation/answer/review_id 降冪），
   由 view 集中實作，全案唯一口徑；06 檔附「平均法 vs 最新法」敏感度對照查詢佐證差異可忽略。
3. **月份處理**：01 的月總覽「全量輸出＋month_flag（full/partial）＋圖上灰化＋
   MoM 僅雙 full 月計算」——完整實況只呈現這一次；**其餘所有趨勢/cohort 查詢一律
   WHERE 裁剪到 2017-01~2018-08** 並註解說明。
4. **準時定義**：`DATE(delivered) <= DATE(estimated)`（estimated 只有日期，比 timestamp 會誤判）；
   `delay_days = DATEDIFF(DATE(delivered), DATE(estimated))`；命名一律 `on_time_rate`
   ＝ `AVG(delay_days <= 0)`（審查抓到的正負號陷阱，註解點名）。
5. **品類**：`COALESCE(t.english, p.category_name, '(uncategorized)')` 統一命名；
   品類×評論分析以 **order×category 去重**防 fan-out；訂單數一律 `COUNT(DISTINCT order_id)`。
6. **R 基準日**：資料內最大 delivered purchase_ts（落在 2018-08，非殘月）；CTE anchor，不用 NOW()。
7. **累積 window 一律明寫 `ROWS UNBOUNDED PRECEDING`**（RANGE 同值共享陷阱）。
8. **禁雙軸圖**：不同量級用上下雙 panel。
9. 佔比先算後 LIMIT（share 在子查詢算完再取 Top N）。
10. 3-3 回購者畫像修右截斷偏誤：對照組限首購 ≤ 2018-02（保證 ≥6 個月觀察窗）。

## 檔案結構

```
analysis/
├── 00_views.sql        # 僅 2 個 view（CREATE OR REPLACE，冪等）：
│                       #   v_order_gmv（order_id, n_items, gmv）——集中防 fan-out
│                       #   v_order_review（每單最新一筆評論）——集中一單多評口徑
├── 01_revenue.sql      # 月營收與成長
├── 02_rfm.sql          # RFM 客戶分群
├── 03_cohort.sql       # cohort 留存與回購
├── 04_category.sql     # 品類與商品
├── 05_logistics.sql    # 物流時效
└── 06_reviews.sql      # 評論與滿意度
scripts/run_analysis.py # 先跑 00 → 解析 -- @name: 標記逐查詢執行
reports/
├── REPORT.md           # 主報告（實作最後手寫，數字全部取自實跑輸出）
├── figures/*.png       # 進 git（作品集要看得到圖）
└── tables/*.csv        # 僅聚合表進 git；9.3 萬列 RFM 明細不落地
```

- 審查裁決採納：**砍幽靈主題 04_payments**（付款行為僅存在於 3-3 一欄與 KPI）；
  **砍 chart DSL**——.sql 只有 `-- @name:` 標記，出圖全部寫在 runner 的具名 Python 函式
  （灰化殘月、censoring 灰格、log 軸、Pareto 組合圖 DSL 都做不到）。

## 查詢清單（21 支）與圖（18 張，實作定案）

| 檔 | 查詢 | 圖 |
|---|---|---|
| 01 | Q1 月 GMV/訂單/AOV/MoM（flag 守門） | `01_gmv_mom.png`（GMV+MoM 雙 panel，殘月灰化）、`01_orders_aov.png`（雙 panel） |
| 02 | 2-1 客戶層 RFM 明細（供圖用，不落 CSV）；2-2 F 分布真相表；2-3 七分群（F≥2 獨立成群、R×M 主軸）；2-4 R×M 5×5（F=1）；2-5 州別客戶/GMV | `02_rm_distribution.png`（M log 軸+R 雙 panel）、`02_segments.png`（人數% vs GMV% 對照）、`02_rxm_heatmap.png`、`02_state_pareto.png` |
| 03 | 3-1 cohort 矩陣（月齡 0–6，右截斷=NULL）；3-1b 累積二購率曲線（招牌）；3-2 二購間隔分箱；3-3 回購者 vs 一次客畫像（修偏誤，表格） | `03_second_purchase_curve.png`、`03_cohort_heatmap.png`（色階上限 ~1.5%、censored 灰格） |
| 04 | 4-1 Top15 品類（share 全站算）；4-2 品類 Pareto（80% 線）；4-3 Top10% 商品營收佔比（1 列文字用）；4-4 品類運費佔比（全站基準線） | `04_top15.png`、`04_pareto.png`、`04_freight_by_category.png` |
| 05 | 5-0 時間異常盤點（1 列）；5-1 配送天數分箱+summary；5-2 早/晚到分箱；5-3 月度準時率與天數；5-4 州別；5-5 賣家逾期出貨 Top（表格）；5-6 三階段拆解 | `05_delivery_distribution.png`（5-1+5-2 雙 panel）、`05_monthly_ontime.png`（雙 panel）、`05_state_delivery.png`、`05_stage_breakdown.png` |
| 06 | 6-0 一單多評敏感度（1 列）；6-1 分數分布；6-2 延遲×評分（招牌）；6-3 留言率×分數；6-4 品類低分率（order×category 去重、n≥200） | `06_score_comment.png`（6-1+6-3 雙 panel）、`06_delay_vs_score.png`、`06_lowscore_category.png` |

審查砍掉：GMV vs payment 並列圖（口徑不可互驗→報告文字交代）、Q4-2 品類月趨勢、
商品十分位長尾圖（留 1 列摘要）、運費月趨勢、3-4 品類 lift（右截斷+小樣本）、
6-4 州別低分（與 5-4 重複）、6-5 回覆時效、州別圖從 3 張併成 2 張（2-5、5-4 各講不同故事）。

## run_analysis.py 規格

- 讀 `.env` 連線（沿用 run_verify 模式）→ 必先執行 00_views.sql → 依 `--themes` 跑選定檔。
- 每查詢：印 `@name（X.XXs，N 列）`＋tabulate 前 20 列；聚合結果寫 `reports/tables/`
  （UTF-8-sig CSV）；明細級查詢（2-1）僅在記憶體供圖。
- 圖：具名函式；`Microsoft JhengHei`、`axes.unicode_minus=False`、150 dpi、
  `bbox_inches='tight'`；寫圖前先載入 dataviz skill 的設計規範。
- CLI：無參數=全部；`--themes 01,05`；`--list`；`--no-figures`。SQL 錯誤即非零退出。

## REPORT.md 結構

1. 標題＋專案簡介 → 2. KPI 總覽表（數字全取自實跑，含來源查詢名）→
3. 「閱讀前必知」框（兩口徑警語＋期間聲明，集中一處不逐章重複）→
4. 六章（問題→方法→圖→發現，發現逐條帶實測數字）→
5. 附錄（口徑定義表、view 定義、重現步驟）。
敘事鏈：F 分布真相 → 流失高價值群 → cohort 趨近 0 的留存與二購窗口 →
品類集中度 → 物流長尾與歸因 → 延遲×評分收尾，為 Stage C 鋪路。

## 紅線（沿用 Stage A）

數字全部真實執行取得（設計文中所有預估比例禁止直接寫進報告）；
圖表不誤導（殘月灰化、censored 灰格、色階誠實、無雙軸）；做完停下等驗收。

## 驗證方式

1. `run_analysis.py` 全量跑通、exit 0、每查詢有列數與耗時。
2. 抽 3 支查詢人工核對（如月 GMV 對照 verify.sql J1、F 分布對照剖析 93,099/2,745/…）。
3. 16 張 PNG 產出且中文不豆腐；REPORT.md 圖片路徑可用。
4. 報告數字可溯源到 reports/tables/ 的 CSV。

## 規格對齊補做（2026-07-17 追加）

使用者補交了原始 Stage B 規格（Fable 規劃版）後 gap check：分析內容已涵蓋 8/10 題，
但檔案結構踩規格紅線（一題一檔）、且缺索引實驗與兩個探勘任務。裁決＝**雙層並存**：

- `sql/` 十題一題一檔（新寫：08 賣家營收排名 RANK、09 付款分期分佈）＋
  `scripts/run_sql.py` → `results/sql/*.csv`＋自動 `results/SQL_REPORT.md`
  （洞察句由實跑數字生成；題 08 曾因預設敘事與實數相反而重寫——賣家端是長尾分散）。
- 索引實驗 `scripts/index_experiment.py` → `results/INDEX_REPORT.md`：
  exp1/exp2（OLAP 聚合）≈1.0x、exp3（選擇性+覆蓋索引）9.2x；
  含快取暖機誠實聲明；實驗索引測後即 DROP。
- `mining/rfm_clustering.py`：階層式——全量 silhouette 選 k=2（重新發現 F 二元結構，
  與 SQL 側 F 固定分箱互證），F=1 主群再分 k=3（近期低消費/中期高消費/流失中消費）。
- `mining/association_rules.py`：跨品類籃僅 0.81%→全域 lift>1 僅 1 對（誠實負面結果），
  另以 780 筆跨品類訂單的條件宇宙給出可解讀 Top 規則（perfumery→health_beauty 4.5x 等）。
