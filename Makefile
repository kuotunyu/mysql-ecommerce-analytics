# mysql-ecommerce-analytics — 一鍵重現全流程
# Windows 無 make 者：每個 target 都是單行指令，直接照抄執行即可（README 有對照表）。

.PHONY: db-up fetch etl verify sql mine features test-ml train all

db-up:            ## 啟動 MySQL 8.4 + Adminer（等 healthcheck）
	docker compose up -d --wait

fetch:            ## 下載 Olist 資料集到 data/raw/（需 ~/.kaggle 憑證；已有 CSV 則跳過）
	uv run python scripts/fetch_data.py

etl:              ## 建表 + 清理 + LOAD DATA 載入（冪等，約 22 秒）
	docker compose exec -T mysql sh -c 'MYSQL_PWD="$$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u"$$MYSQL_USER" "$$MYSQL_DATABASE"' < schema.sql
	uv run python etl/load.py

verify:           ## 倉庫驗證：row count 核對 / FK 孤兒 / 抽樣 JOIN
	uv run python scripts/run_verify.py

sql:              ## 十題 SQL（自動生成 SQL_REPORT）＋六主題深度分析（18 張圖）
	uv run python scripts/run_sql.py
	uv run python scripts/run_analysis.py

mine:             ## 探勘：RFM KMeans 分群 + 品類關聯規則
	uv run python mining/rfm_clustering.py
	uv run python mining/association_rules.py

features:         ## SQL 特徵工程 → ml_features 實體表（約 1 分鐘）
	docker compose exec -T mysql sh -c 'MYSQL_PWD="$$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -u"$$MYSQL_USER" "$$MYSQL_DATABASE"' < ml/features.sql

test-ml:          ## 時點特徵防洩漏驗證（200 筆性質測試＋4 手工案例）
	uv run python ml/test_features.py

train: features   ## 訓練晚到預測模型（開頭自動先跑 test-ml 等效驗證，不過不訓練）
	uv run python ml/train.py

all: db-up fetch etl verify sql mine train   ## 全流程一鍵重現
