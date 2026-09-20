# 操作手冊：開啟／關閉這個專案

每次回來，照這份做，不用重新問。所有指令都在 **PowerShell**、且先切到專案資料夾再打：

```powershell
cd <本專案資料夾>
```

## 現在到底是開的還是關的？

```powershell
docker compose ps
```

| 結果 | 代表 | 該做什麼 |
|---|---|---|
| 列出 `olist-mysql`（healthy）與 `olist-adminer`（Up） | **已開著** | 不用再開，直接用 |
| 只有表頭、沒有任何列 | **已關閉** | 照下面「開啟」做 |
| 整個指令報錯（連不上 Docker） | **Docker Desktop 沒開** | 先手動點開 Docker Desktop，等工作列鯨魚圖示穩定，再重下這行確認 |

## 開啟

```powershell
docker compose up -d
docker compose ps          # 等 olist-mysql 變 (healthy)，通常 <30 秒
```

- **Adminer 網頁**：<http://localhost:8080>
  伺服器填 **`mysql`**（不是 localhost，Adminer 在容器內走 Docker 網路）／帳號 `olist_app`／
  密碼看 `.env` 檔尾 MySQL 區塊／資料庫 `olist`
- 資料不會不見：關容器不影響資料庫內容（存在 named volume），重開直接沿用，不用重跑 ETL。
- 要跑 Python（ETL、驗證、之後 Stage B 的分析腳本）：`uv run python <script>`。

## 關閉

```powershell
docker compose down
```

- 只停止並移除**容器**，資料庫內容不受影響，安全，隨時可關。
- **絕對不要加 `-v`**（`docker compose down -v`）——那會連資料一起砍掉，要重跑 ETL 才能救回來。

## 快速健康檢查（開著的狀態下才能跑）

```powershell
uv run python scripts/run_verify.py    # row count 對帳 / FK 孤兒 / 抽樣 JOIN，全 PASS 才正常
```

## 忘記密碼在哪

`.env` 檔尾「# --- MySQL (mysql-ecommerce-analytics, Stage 1) ---」區塊（不進 git，只在這台電腦）。

## Docker Desktop 跳錯誤視窗起不來

跳「An unexpected error occurred」時 **按 Quit，絕對不要按 Reset to factory defaults**
（會清光所有專案的 Docker 資料，不只這個）。這台機器的已知毛病、成因與修復步驟已記在
我（Claude）的長期記憶裡——直接跟我說「Docker 又起不來了」即可，不用重新診斷。

## 發佈到 GitHub（你自己決定時機，兩行搞定）

本地 git 歷史已就緒（三個 commit：Stage A/B/C）。要發佈時在專案根目錄執行：

```powershell
gh repo create mysql-ecommerce-analytics --public --source=. --remote=origin --push --description "Olist e-commerce: MySQL 8.4 warehouse + SQL analytics/mining + LightGBM late-delivery prediction"
gh repo edit --add-topic mysql --add-topic sql --add-topic docker --add-topic data-mining --add-topic machine-learning --add-topic lightgbm --add-topic e-commerce
```

（想先私人再轉公開：把 `--public` 換成 `--private`，之後 `gh repo edit --visibility public`。
topics 無法在 create 時給，必須用第二行的 `gh repo edit`。）

## 專案現況

Stage A（倉庫）、B（SQL 分析＋探勘）、C（ML＋收尾）皆已完成。
藍圖見 [PLAN_B.md](PLAN_B.md) / [PLAN_C.md](PLAN_C.md)，
成果總覽見 [../README.md](../README.md)。
