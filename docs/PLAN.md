# mysql-ecommerce-analytics — Stage 1 實作計畫（Olist → MySQL 8 正規化倉庫）

> 本檔即專案的實作藍圖。實作第一步會把它複製為專案內的 `docs/PLAN.md`，之後照著做；
> 過程中的調整同步回該文件。經過 3 個設計視角（schema / ETL / infra）＋1 輪對抗性審查定稿。

## Context（背景與目標）

在 `C:\Users\3Hml\Desktop\CC_github部隊\SQL\1_電商分析倉庫_Olist巴西電商_MySQL×探勘×ML\`
（**此資料夾即專案根**）建立專案 **mysql-ecommerce-analytics** 的 Stage 1：把 Kaggle 的
[olistbr/brazilian-ecommerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)
（9 個 CSV、約 155 萬列）建成正規化 MySQL 8 倉庫。後續 Stage B（SQL 分析）、Stage C（ML）
會直接查這座庫，schema 與索引為分析預作準備。目前**不建 git repo**（Stage C 收尾再建），
`.gitignore` / `.gitattributes` 現在寫好。

## 環境偵察結果（2026-07-17 實測）

| 項目 | 狀態 | 影響 |
|---|---|---|
| Docker Desktop | 已安裝（compose v5.3.0）但 daemon 未啟動 | Step 1 前先啟動並以 `docker info` 確認 ready |
| Port 3306 / 8080 | 空閒 | 用預設埠，但埠號做成 .env 變數以防未來衝突 |
| 既有 `.env` | 已存在，內含其他專案的 API keys | **絕不覆蓋**，只在檔尾附加 MySQL 區塊 |
| Kaggle 憑證 | `~/.kaggle/kaggle.json` 不存在 | 使用者自行產生；Step 2 前檢查，缺則停下提示 |
| uv | 0.11.18 | `uv init --bare --python 3.12` + `uv add` |
| 主機 mysql client | 無 | 連線實測走「容器內 client（docker exec）＋主機 PyMySQL」雙路 |

## 使用者已決定事項

- 專案檔案直接放目前資料夾（即專案根）；`mysql-ecommerce-analytics` 為 README/GitHub 專案名。
- Kaggle：使用者自行產生 kaggle.json 放 `C:\Users\3Hml\.kaggle\`。
- `.env`：MySQL 變數**附加**到現有檔案，原內容一字不動。
- README：正體中文為主，專有名詞用原文。
- DB 密碼：實作時隨機產生寫入 `.env`（**只用英數字元**，避免 `$` 等符號干擾 compose 插值與
  healthcheck shell）；`.env.example` 只放佔位符。

## 紅線（不可違反）

1. 憑證只走 `.env`；程式碼、README、驗證輸出、`docker compose config` 截圖都不得出現真實密碼。
2. 所有核對數字必須真實執行取得。
3. Stage 1 做完即停等驗收，不先寫分析。
4. 既有 `.env` 的其他 keys 原樣保留。

## 關鍵設計決策（已裁決）

### Schema（9 張表 + 1 張衍生彙總表）

表名用乾淨短名（DB 名 `olist` 已提供命名空間）：`customers`, `geolocation`, `orders`,
`order_items`, `order_payments`, `order_reviews`, `products`, `sellers`,
`product_category_name_translation`。每張表的 DDL 註解記載對應的原始 CSV 檔名。

| 表 | PK | 關鍵決策 |
|---|---|---|
| customers | `customer_id` CHAR(32) | `customer_unique_id` 建非唯一索引（RFM/cohort 折疊自然人用） |
| geolocation | 代理鍵 `geolocation_id` INT UNSIGNED AUTO_INCREMENT | 無自然鍵＋大量完全重複列 → **五欄完全重複列去重後載入**（~100 萬 → 實跑為準）；`zip_code_prefix` 建一般索引 |
| geolocation_zip（衍生） | `zip_code_prefix` CHAR(5) | 載入後由 pandas 聚合生成（中位數需求，SQL 不便）：每 zip 一列，先以巴西 bounding box（lat -34..6, lng **-74..-28**；東界含 Fernando de Noronha 離島，實作時據實測修正）過濾異常座標再取**中位數** lat/lng＋眾數 city/state；分析一律 JOIN 這張，不 JOIN 基表 |
| orders | `order_id` CHAR(32) | 5 個 timestamp 欄 nullable（未付款/未出貨/未送達是合法 NULL，不填補） |
| order_items | (`order_id`,`order_item_id`) | `price`/`freight_value` DECIMAL(10,2) |
| order_payments | (`order_id`,`payment_sequential`) | FK 借用 PK 左前綴，不另建索引 |
| order_reviews | **複合 (`review_id`,`order_id`)** | `review_id` 跨訂單重複、`order_id` 一單多評，單獨都不唯一；組合唯一（載入前 pandas assert 驗證）。保留全部列不去重 |
| products | `product_id` CHAR(32) | 原始欄名 typo **改正**為 `product_name_length`/`product_description_length`；欄位 `COMMENT` 記原始欄名＋README 對照表。數值欄 nullable |
| sellers | `seller_id` CHAR(32) | — |
| product_category_name_translation | `product_category_name` VARCHAR(64) | **維持 71 列原樣，不補缺譯列**（`pc_gamer` 等 2 類缺譯是資料事實）；分析用 LEFT JOIN + COALESCE |

**FK 政策——只加保證乾淨的 6 條**（載入時即驗證引用完整性，FK 檢查全程 ON）：
`orders→customers`、`order_items→orders/products/sellers`、`order_payments→orders`、
`order_reviews→orders`。

**明確不加的 FK**（DDL 註解說明理由與替代方案）：
- `*_zip_code_prefix → geolocation`：geolocation 的 zip 不唯一（FK 物理上建不起來）且有缺值
  → 兩邊建一般索引，JOIN `geolocation_zip`；缺值數列為驗證的 INFO 項（允許 >0，記錄基線）。
- `products.category → translation`：610 個 NULL category＋2 個缺譯類別 → LEFT JOIN + COALESCE。

**型別**：
- 32-hex ID：`CHAR(32) CHARACTER SET ascii COLLATE ascii_bin`（JOIN 熱點，索引最小化）。
- zip prefix：`CHAR(5) CHARACTER SET ascii`，**保留前導零**。
- 時間全 `DATETIME`（巴西當地時間、無時區語意）；金額全 `DECIMAL(10,2)`；
  lat `DECIMAL(10,8)` / lng `DECIMAL(11,8)`；`review_score` TINYINT、
  `payment_installments` TINYINT UNSIGNED；`order_status` VARCHAR(20)（不用 ENUM）；
  `review_comment_message` TEXT；城市名等文字 utf8mb4。
- 全庫預設 `utf8mb4` / `utf8mb4_0900_ai_ci`。

**二級索引（精簡版——原則：載入即建立高確定性的，其餘 Stage B 遇慢查詢再加）**：
- `customers(customer_unique_id)`、`customers(customer_state)`
- `orders(order_status, order_purchase_timestamp)`、`orders(order_purchase_timestamp)`、
  `orders(order_delivered_customer_date)`（物流時效）
- `order_items(product_id, price)` 覆蓋索引（同時充當 product FK 的索引）
- `order_reviews(order_id)`（PK 左前綴吃不到，FK 也需要）
- `products(product_category_name)`、`geolocation(zip_code_prefix)`
- 不建：FULLTEXT（Stage B 需要再建）、低基數欄（payment_type、review_score、seller_state）

### ETL（`etl/load.py`）

**路徑：pandas 清理 → 落地 staging CSV → `LOAD DATA LOCAL INFILE`**（9 表統一）。
geolocation ~100 萬列用 executemany 要 3–8 分鐘，LOAD DATA 數十秒；金額欄以字串
`dtype=str` 原樣過境 → DECIMAL，不經 float。

- **NULL 機制（單一套）**：`FIELDS ESCAPED BY ''`＋pandas 預設空字串落地＋
  `SET col = NULLIF(col,'')`。不用 `\N`（與反斜線跳脫互相衝突：評論文字含 `\` 會吃字錯位）。
  本資料集「空字串＝NULL」語意成立，README 文件化。
- staging CSV：`to_csv(index=False, lineterminator='\n', encoding='utf-8')`（明示 `\n`
  避免 Windows `\r\n` 尾欄殘留）；落在 **ASCII-safe 暫存目錄**（`tempfile.gettempdir()/olist_staging`，
  可用 env `OLIST_STAGING_DIR` 覆寫）——專案路徑含中文與 `×`，不進 LOAD DATA 語句；
  SQL 內路徑一律 forward slash。
- LOAD DATA：`FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY ''
  LINES TERMINATED BY '\n' IGNORE 1 LINES`＋逐欄對映清單。
- 連線：PyMySQL `local_infile=True`（server 端 compose 已開 `--local-infile=1`；
  **兩端都要開**，只開一端會報 ERROR 3948）。不引入 SQLAlchemy（無必要依賴）。
- **清理規則（README＋docstring 文件化）**：
  - 共通：字串 strip；時間欄 `pd.to_datetime(errors='raise')`——異常要炸出來，不靜默 coerce。
  - **zip 前導零（全案最大實務風險）**：customers/sellers/geolocation 三表 zip 欄一律
    `dtype=str` 讀入＋`.str.zfill(5)`，三表一致，否則 JOIN 靜默錯配。
  - 含 NaN 的整數欄（products 各數值欄）：pandas nullable `Int64`，避免 `58.0` 進
    SMALLINT 在 strict mode 炸掉。
  - geolocation：chunked 讀取（100k/chunk，逐 chunk 去重後 concat 再全域
    `drop_duplicates()`）；其餘 8 表量小整檔讀。
  - order_reviews：pandas C engine 正確解析多行 quoted 欄位；**記錄數以 pandas 解析結果為準**，
    嚴禁 wc -l；載入前 `assert` (review_id, order_id) 組合唯一。
- **載入順序（FK 檢查全程 ON）**：
  translation, customers, sellers, geolocation → products, orders →
  order_items, order_payments, order_reviews → 生成 geolocation_zip。
- **冪等**：run 開始 `SET FOREIGN_KEY_CHECKS=0` → TRUNCATE 全表 → `=1` → 依序重載
  （唯一用到 checks=0 的地方；清空狀態無完整性風險）。中途失敗直接整跑重來（全程 <5 分鐘）。
- 結束輸出：各表載入筆數＋**總耗時**，並寫出 `data/expected_counts.json` manifest：
  每表 `{csv_rows(pandas 解析), cleaned_rows, delta_note}`——驗證的預期值來自實跑，不寫死。
- 不做：`--method executemany` 備援旗標（README 一句話提及手動 fallback 即可）、
  `--only <table>` 單表重載。

### 驗證（`scripts/verify.sql` + `scripts/run_verify.py`）

- verify.sql 以 `-- @section:` 標記三區，runner 切割執行；單檔可手動貼進 client 跑。
- **Section A**：9 表＋geolocation_zip 的 `COUNT(*)`。
- **Section B**：6 條 FK 孤兒 LEFT JOIN 檢查（**必須全 0**）＋INFO 級檢查（允許 >0、只記錄）：
  customers/sellers zip 不在 geolocation 的數量、products 缺譯/NULL 類別數、
  `delivered` 但無送達時間的訂單數（已知 ~8 筆）。
- **Section C**：3 個抽樣 JOIN：
  1. 月營收趨勢：orders ⋈ order_items（delivered、按月加總 price+freight_value）
  2. 品類英譯銷量 Top 10：order_items ⋈ products ⟕ translation（COALESCE 缺譯）
  3. 付款方式 × 平均評分：orders ⋈ payments 預聚合 ⋈ reviews 預聚合（展示 fan-out 正確解法）
- runner 讀 manifest ＋ DB 實際值，tabulate 輸出核對表（欄位：
  `表 | CSV原始 | 清理後預期 | DB實際 | 差異原因 | 狀態`）。
  **PASS ＝ DB實際 == 清理後預期**；CSV 原始數永遠並列、落差在「差異原因」明說
  （geolocation 去重是設計內行為，不隱藏）。任何 FAIL → exit code 1。核對表全文貼給使用者。
- README 註記：`SUM(payment_value)` ≠ `SUM(price+freight_value)`（voucher/分期/口徑差異），
  兩口徑**不可互驗**。

### Infra（docker-compose.yml）

- `mysql:8.4`（LTS，鎖版本求可重現；「mysql:8」浮動 tag 不用）：
  - `command: --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci --local-infile=1`
    （**不用** config file bind mount——Windows 掛載常變 world-writable，MySQL 會默默忽略）。
  - `environment:` 用 `${VAR}` 插值（compose 只取用引用到的變數，`.env` 裡其他專案的 keys
    不會進容器；**禁用 `env_file:`**——那會整份灌入）。
  - named volume `mysql_data:/var/lib/mysql`（存於 WSL2 VM 內，不在專案資料夾，與 git 無關）。
  - healthcheck：`mysqladmin ping -h 127.0.0.1 --silent`（**無密碼版**：官方行為是 server
    活著就 exit 0，Access denied 也算活——正好符合語意，且完全避免密碼進 config/ps 與
    `$$` 跳脫的引號地雷）；`interval: 5s`、`retries: 10`、`start_period: 30s`。
  - ports：`"${MYSQL_PORT:-3306}:3306"`。
- `adminer`：`"${ADMINER_PORT:-8080}:8080"`，`depends_on: mysql: condition: service_healthy`。
- schema.sql **不掛** `/docker-entrypoint-initdb.d/`（只在 volume 全空首次生效，隱性且難重跑）；
  由顯式命令執行，可重複。
- `.env` 附加區塊 keys：`MYSQL_ROOT_PASSWORD`、`MYSQL_DATABASE=olist`、`MYSQL_USER=olist_app`、
  `MYSQL_PASSWORD`、`MYSQL_HOST=127.0.0.1`、`MYSQL_PORT=3306`、`ADMINER_PORT=8080`。

### Python 環境（uv）

`uv init --bare --python 3.12`（不用系統 3.10，uv 自抓 managed Python）→ `uv python pin 3.12`
→ `uv add pandas pymysql cryptography python-dotenv kaggle tabulate`。
`cryptography` 為 MySQL 8 `caching_sha2_password` 非 SSL 連線必需（缺了直接 RuntimeError）。
執行一律 `uv run python …`（免 activate、避開 PowerShell ExecutionPolicy）。
`uv.lock`、`.python-version` 之後要進 git。

## 專案結構

```
（目前資料夾＝專案根）
├── docker-compose.yml
├── .env                        # 既有檔案，附加 MySQL 區塊（不進 git）
├── .env.example                # 佔位符（進 git）
├── .gitignore                  # data/raw/、.env、.venv/、__pycache__/、*.zip、*.log
├── .gitattributes              # *.sql/*.sh 強制 LF（防禦性）
├── README.md                   # 專案說明＋mermaid erDiagram＋欄名對照＋清理策略
├── docs/PLAN.md                # 本計畫
├── pyproject.toml / uv.lock / .python-version
├── schema.sql                  # 9 表 DDL＋geolocation_zip＋FK＋索引＋決策註解
├── scripts/
│   ├── fetch_data.py           # kaggle API 下載＋解壓＋印出欄位/筆數/前2列
│   ├── verify.sql              # @section: counts / fk_orphans / sample_joins
│   └── run_verify.py           # 執行 verify.sql＋manifest 對照，輸出核對表
├── etl/load.py                 # 清理→staging→LOAD DATA；印總耗時；寫 manifest
└── data/
    ├── raw/                    # CSV（gitignore，留 .gitkeep）
    └── expected_counts.json    # load.py 產出（gitignore 之外，數字可進 git）
```

## 實作步驟

- **Step 0 骨架**：建目錄、.gitignore、.gitattributes、docs/PLAN.md（本檔複製）、
  uv init + add、README 骨架。
- **Step 1 Docker**：啟動 Docker Desktop → `docker info` preflight → 產生英數隨機密碼
  附加 `.env`＋寫 `.env.example` → docker-compose.yml → `up -d` 等 healthy →
  連線實測三路：(a) `docker exec` 容器內 mysql client（`MYSQL_PWD` 經容器 env，
  不經 host argv）`SELECT VERSION()`＋確認 `character_set_server`/`local_infile`；
  (b) 主機 `uv run` PyMySQL `SELECT 1`；(c) Browser 開 `http://localhost:8080` 確認
  Adminer 登入頁正常（**實際登入由使用者操作**，我不在網頁表單輸入密碼）。
- **Step 2 資料**：檢查 kaggle.json（缺則停下提示）→ `fetch_data.py` 下載解壓 →
  印每個 CSV 的欄位、pandas 解析筆數、前 2 列 → 以實際輸出核對 schema 假設
  （欄名 typo、9 檔齊全、zip 是否掉前導零）。
- **Step 3 schema.sql**：依 Step 2 實況定稿 DDL（上表決策）→ 執行建表 →
  `SHOW TABLES`/`SHOW CREATE TABLE` 抽查 → README 補 mermaid erDiagram。
- **Step 4 ETL**：`etl/load.py` 如上設計 → 實跑 → 記錄各表筆數與總耗時 → 產出 manifest。
- **Step 5 驗證**：verify.sql + run_verify.py → 核對表全 PASS、FK 孤兒全 0、
  3 個 JOIN 出合理結果 → **核對表全文貼給使用者，停下等驗收**。

## 驗證方式（整體驗收清單）

1. `docker compose ps`：mysql healthy、adminer running。
2. 三路連線實測通過（容器內 client / 主機 PyMySQL / Adminer 頁面回應）。
3. `run_verify.py`：10 表（含衍生表）核對全 PASS；6 條 FK 孤兒全 0；INFO 項有數字與說明；
   3 個 JOIN 前 5 列＋耗時。
4. README 含 erDiagram、欄名對照、清理與去重策略、兩種營收口徑警語。
5. 憑證不出現在任何進 git 的檔案。

## 已知風險與對策（實作時逐項核對）

| 風險 | 對策 |
|---|---|
| zip 前導零錯配（三表不一致 → JOIN 靜默錯） | 三表統一 `dtype=str`＋`zfill(5)`；Step 2 檢視實際值確認 |
| 評論文字含 `\`、逗號、換行、引號 | `ESCAPED BY ''`＋pandas doublequote；不用 `\N` |
| 含 NaN 整數欄變 `58.0` 炸 strict mode | pandas `Int64` nullable dtype |
| 專案路徑含中文/`×` 進 LOAD DATA | staging 落 ASCII 暫存目錄、SQL 內 forward slash |
| (review_id,order_id) 組合若非唯一 → 撞 PK | 載入前 pandas assert，炸在 Python 端好定位 |
| Docker daemon 未啟動 / 埠被占 | preflight：`docker info`＋埠檢查；埠走 .env 變數 |
| compose 解析整份 .env | 附加區塊維持標準 `KEY=VALUE`；密碼純英數 |
| `docker compose config` 印明文密碼 | 不在任何貼給使用者的輸出中執行它 |

## 與原需求的偏差聲明（皆有理由，可再討論）

1. 「pandas 分塊清理」→ 只有 geolocation（最大表）分塊，其餘 8 表量小整檔讀，程式更簡單。
2. 「mysql:8」→ 鎖 `mysql:8.4`（LTS）求可重現，仍屬 MySQL 8。
3. 批次寫入具體實作＝ LOAD DATA LOCAL INFILE（比 executemany 快 10–50 倍，
   且正好用上你要求開啟的 local_infile）。
4. 9 張表之外多一張**衍生**彙總表 `geolocation_zip`（zip 不唯一無法直接 JOIN 分析，
   這張表是 Stage B 地理分析的實用介面；基表仍是 9 張）。
5. row count 核對呈現為三欄（CSV 原始/清理後預期/DB 實際）＋差異原因——geolocation 去重、
   reviews 多行解析後的記錄數都能誠實對帳。
