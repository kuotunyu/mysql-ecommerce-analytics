# Stage C 實作計畫：晚到預測 ML 與專案收尾

> 規格：使用者提供之 Fable 原文（預測訂單是否晚於預估日送達）。
> 經 2 視角設計＋對抗性審查裁決定稿；審查抓到 1 個定義級洩漏（已修入設計）。
> GitHub 決議（使用者已答）：**本地 git commit 就好，不建遠端**；發佈指令寫進 OPERATIONS.md。

## 實測事實（設計依據）

- 母體：delivered 且有送達時間且無時間倒置 = **96,470**；晚到率 6.8%。
- 多賣家訂單僅 **1.3%**（1,278 筆；2 賣家 1,219、3+ 賣家 59）→ 主賣家法成立。
- 缺重量訂單僅 16 筆 → NULL 讓 LightGBM 原生處理。
- 環境：gh CLI 已登入（tun0000）、`make` 不存在（Makefile 交付給 GitHub 讀者，
  本機以等效指令逐條驗證）、lightgbm 4.6 + shap 0.52 已入依賴。

## ml/features.sql（多段 CTE → 實體表 ml_features）

- **Grain**＝訂單；`DROP TABLE IF EXISTS` + `CREATE TABLE ... AS SELECT`，PK(order_id)；
  train.py 只 `SELECT * ORDER BY purchase_ts`（測試與訓練吃同一快照）。
- **Label**：`is_late = DATE(delivered) > DATE(estimated)`（沿用全案 DATE 級口徑）。
- **時點特徵（可信度核心，審查修正後定案）**：主賣家（品項金額最高、平手 MIN(seller_id)）在 t=購買時點的——
  - `seller_hist_late_rate`：分母＝**delivered < t**（t 時結果已揭曉）的歷史單；冷啟動 NULL
    （不補先驗——全域先驗含測試期資訊是隱性洩漏；LightGBM 原生吃 missing）。
  - `seller_hist_order_cnt`：**從全狀態 orders 算**（purchase < t，不限 delivered）——
    ★審查抓到的洩漏：用 delivered-only 母體算「接單量」等於偷看歷史單最終結局。
  - `seller_hist_open_cnt`（在途/未結單）＝ order_cnt − (delivered<t 筆數)；
    註記：canceled 單無取消時戳、會留在 open 中，屬口徑近似，註解與報告載明。
  - `seller_hist_overdue_open_cnt`：在途單中 `DATE(estimated) < DATE(t)` 者——
    「t 時已知必遲到但還沒到」的爆倉訊號（審查建議的合法新特徵）。
  - 實作＝聚合式自我 JOIN（window 無法表達 delivered<purchase 跨欄非等值），
    `INDEX(seller_id, purchase_ts)` 輔助；配對量實測後才跑（估 <20M）。
- **其他特徵**：freight_total、price_total（SUM(price) 直算）、n_items、n_sellers、
  total_weight_g、total_volume_cm3（Σ L×H×W）、customer_state、seller_state、
  is_cross_state、product_category（最高價品項、平手 MIN(product_id)、COALESCE 口徑）、
  max_installments（per-order MAX）、purchase_month_num、purchase_dow、
  **est_days**（=DATEDIFF(estimated, purchase)——下單當下已知的承諾，合法且預期最強，
  報告註明其兼具「時代 proxy」性質）。
- **禁用清單（檔頭明列）**：delivered_*、review_*、order_approved_at（t 後才發生）、
  shipping_limit_date（由 approved 推導，保守排除）、order_status、任何 label 衍生量。
- 審查砍掉：is_multi_seller（n_sellers 可推）、ml_features 的 purchase_ts 索引、事件流備案只留註解。

## ml/test_features.py（不過不准訓練——train.py 開頭強制執行）

1. **性質測試（主力，審查新增）**：固定種子抽 **200 筆訂單**，用 pandas 從**原始表**
   獨立重算時點三聚合＋主賣家選擇，精確比對 ml_features——期望值不得參照 features.sql
   的任何 CTE（防循環論證）；嚴格 `<` 語意由此測涵蓋。
2. 手工案例 4 筆（order_id＋人工核對後硬編碼期望值）：
   Case1 冷啟動首單（cnt=0、late_rate IS NULL）；Case2 一般案例手算對照；
   Case3 在途單排除（h.purchase<t 且 h.delivered>t：計入 order_cnt、不入 late 分母）；
   Case4 canceled/unavailable 歷史單（計入 order_cnt、不入 late 分母）——審查新增。
3. 全域 sanity：COUNT=96,470、晚到率≈0.068±0.005、open_cnt≥0、est_days≥0、
   `late_rate IS NULL ⇔ delivered_cnt=0`。
4. 硬編碼期望值綁定資料快照；重抓資料後測試紅是預期行為（README 註明）。

## ml/train.py

- **時間切分**：月界動態找「測試占比最接近 20%」的 cutoff（預期 2018-06 附近，實測為準）；
  訓練期尾端再切 ~10% 當 validation（早停 100 輪＋選閾值，一魚兩吃、時間嚴格遞增）。
  賣家歷史跨切分點**不是洩漏**（生產環境同樣可得全部歷史；報告寫明論證）。
- **LightGBM**：固定超參不搜參（lr 0.05、num_leaves 63、min_data_in_leaf 50、
  feature/bagging_fraction 0.9、λ2=1、n_est 2000＋早停）；原生 categorical
  （category dtype、levels 以訓練集為準）；**不加 scale_pos_weight**（保機率校準，
  操作點由閾值處理）；SEED=42、force_row_wise。
- **評估**：AUC、PR-AUC（average_precision）、混淆矩陣（val max-F1 閾值＋0.5 對照兩張表）、
  precision@top-10%（對盛行率漂移穩健，審查新增）；
  **PR 基準線與 Baseline A 的 PR-AUC 用「測試集實際盛行率」**（審查修正——測試期物流最佳
  ＋尾端 censoring，盛行率低於全域 6.8%）；各月 AUC 一行。
- **三個 baseline**：A 全猜不遲到（AUC=0.5 常數分數註記、PR-AUC=test prevalence、
  accuracy 誠實對照）；B 跨州即遲到（hard classifier 的 P/R/F1＋「二值分數 AUC=balanced
  accuracy」註記）；C **單特徵 seller_hist_late_rate 當分數**（審查新增——證明模型贏過
  最強單一特徵，NULL 以訓練集晚到率補值僅供排序）。
- **SHAP**：TreeExplainer、測試集全量、beeswarm（max_display=20，log-odds 註記）。
- **限制節（必寫）**：尾端 censoring（量化各月未送達佔比）、P(晚到|最終有送達) 的條件化、
  2017-11 黑五單次事件、est_days 的時代 proxy 性質。

## 產出

- `results/ML_REPORT.md`（任務口徑/切分表/特徵與時點驗證/超參表/主結果表/SHAP/限制）
- `results/figs/roc_curve.png`、`pr_curve.png`（test-prevalence 虛線）、`shap_summary.png`

## 收尾

1. **Makefile**：db-up / fetch / etl / verify / sql / mine / test-ml / train / all，
   recipe 全部單行跨平台（docker compose / uv run）；本機無 make → PowerShell 逐條驗證，
   README Quick Start 附「make 目標 ↔ 底層指令」對照表（無 kaggle 憑證者從 etl 起跑註記）。
2. **README 定稿**：動機 → 三個招牌數字置頂（索引 9.2×；LightGBM AUC vs 三 baseline；
   97% 一次性客戶／二購率 3.2%，圖用 reports/figures/03_second_purchase_curve.png）→
   mermaid 流程圖（Kaggle→fetch→MySQL 8.4→ETL+驗證→SQL 十題+六主題→索引實驗→
   RFM/關聯→SQL 特徵→LightGBM）→ ER 圖（已有）→ Quick Start → 各報告連結 →
   credit-default-xai 連動句 → 資料授權（CC BY-NC-SA，原始檔不隨 repo 散布）。
3. **git（本地）**：`git init` → 衛生檢查（git ls-files 全掃＋grep 秘鑰樣式、
   .env/.kaggle/data/raw 不在清單、圖檔大小、9 萬列明細 CSV 不入庫）→
   分三個 commit（Stage A 倉庫／Stage B 分析與探勘／Stage C ML 與收尾）。
   **不建遠端**；`gh repo create ... --public --source=. --push`＋`gh repo edit --add-topic ...`
   完整指令寫進 docs/OPERATIONS.md 供使用者之後一鍵發佈（topics 不能在 create 給，審查查證）。

## 紅線

時點特徵與時間切分驗證通過才准訓練（train.py 程式強制）；所有數字真實；
做完停下等驗收；憑證與原始資料不進 git。
