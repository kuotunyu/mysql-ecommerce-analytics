# 專案進度總帳（回來先看這份）

> **最後更新：2026-07-18**｜現況一句話：**三階段全部完成、本地 git 三個 commit、
> 未發佈 GitHub（等使用者自行決定時機）。**
>
> 維護約定：每次工作階段結束更新「最後更新＋現況一句話＋異動記錄」；
> 隔了一段時間回來，讀完本檔＋[OPERATIONS.md](OPERATIONS.md) 就能接手。

## 一分鐘回憶（這專案是什麼）

Kaggle Olist 巴西電商資料（9 CSV、155 萬列）→ Docker MySQL 8.4 正規化倉庫 →
SQL 分析/探勘 → SQL 時點特徵 → LightGBM 晚到預測。作品集專案，全數字真實可重現。
三個招牌數字：**索引 9.2× 加速／模型 AUC 0.750 vs baseline 0.50／96.9% 客戶只買一次**。

## 已完成

| Stage | 內容 | 關鍵產出 | 狀態 |
|---|---|---|---|
| A 倉庫 | Docker(mysql:8.4+Adminer)、9 表 schema、ETL 21.5 秒載入 155 萬列、核對表全 PASS | [schema.sql](../schema.sql)、[PLAN.md](PLAN.md) | ✅ 已驗收 |
| B 分析＋探勘 | sql/ 十題一題一檔＋自動 SQL_REPORT；analysis/ 六主題 21 查詢 18 圖；索引實驗 9.2×；RFM KMeans；關聯規則 | [SQL_REPORT](../results/SQL_REPORT.md)、[REPORT](../reports/REPORT.md)、[INDEX_REPORT](../results/INDEX_REPORT.md)、[PLAN_B.md](PLAN_B.md) | ✅ 交付（含後補規格對齊） |
| C ML＋收尾 | ml_features 防洩漏時點特徵（200 筆性質測試＋4 手工案例）；LightGBM AUC 0.750 vs 三 baseline；Makefile；README 定稿；git 三 commit | [ML_REPORT](../results/ML_REPORT.md)、[PLAN_C.md](PLAN_C.md) | ✅ 交付、已巡覽 |

git 歷史：`3f6e9c0` Stage A → `f5fb9eb` Stage B → `3b18ea0` Stage C（工作樹乾淨）。

## 未完成／待辦

1. **發佈 GitHub**（唯一真正的待辦，使用者自行決定時機）：兩行指令在
   [OPERATIONS.md](OPERATIONS.md)「發佈到 GitHub」節。發佈前使用者說要先整理多個專案。
2. 可選的未來延伸（無承諾，來自 ML_REPORT 限制節）：機率校準（isotonic）、
   低分評論 NLP（差評文本密度最高：1 星留言率 77.5%）、冷啟動賣家貝氏平滑、
   purchase_month 時代特徵改造。

## 回來接手的步驟

1. 讀本檔 →「異動記錄」看最近動了什麼。
2. 開環境：照 [OPERATIONS.md](OPERATIONS.md)（`docker compose up -d`；**Docker Desktop
   起不來是這台機器已知毛病**，隔離兩個殭屍目錄即復活，OPERATIONS 有寫，直接叫我處理也行）。
3. 健康檢查：`uv run python scripts/run_verify.py` 全 PASS = 倉庫完好。
4. 重跑任何東西：`make all` 或 README 的指令對照表。

## 異動記錄（新的在上）

- **2026-07-18**：建立本檔；Docker 重啟巡覽成果（驗證重跑全 PASS）；Stage C 完成
  ＋git 三 commit（防洩漏特徵、LightGBM AUC 0.750、Makefile、README 定稿）。
- **2026-07-18**：Stage B 規格後補對齊（sql/ 十題、索引實驗 9.2×、RFM 分群、關聯規則）。
- **2026-07-17**：Stage B 六主題深度報告（21 查詢/18 圖）。
- **2026-07-17**：Stage A 完成並驗收（倉庫＋ETL＋驗證）；排除 Docker AF_UNIX 殭屍問題。
