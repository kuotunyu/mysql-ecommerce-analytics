"""下載 Olist 巴西電商資料集（olistbr/brazilian-ecommerce）到 data/raw/ 並檢視每個 CSV。

用法：
    uv run python scripts/fetch_data.py

前置：
    ~/.kaggle/kaggle.json（kaggle.com → 頭像 → Settings → API → Create New Token）

行為：
    - data/raw/ 已有 9 個 CSV → 跳過下載，只做檢視
    - data/raw/ 已有 zip     → 跳過下載，解壓後檢視
    - 都沒有                 → 以 kaggle CLI 下載並解壓
    - 檢視：每個 CSV 印出欄位、筆數（pandas 解析的記錄數，非實體行數）與前 2 列
      ※ 全部以 dtype=str 讀取——保留 zip code 前導零等原貌，供 schema 決策核對
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DATASET = "olistbr/brazilian-ecommerce"
EXPECTED_CSV_COUNT = 9


def ensure_data() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    csvs = sorted(RAW_DIR.glob("*.csv"))
    if len(csvs) >= EXPECTED_CSV_COUNT:
        print(f"[skip] data/raw/ 已有 {len(csvs)} 個 CSV，跳過下載")
        return

    zips = sorted(RAW_DIR.glob("*.zip"))
    if not zips:
        kaggle_dir = Path.home() / ".kaggle"
        has_cred = (kaggle_dir / "access_token").exists() or (kaggle_dir / "kaggle.json").exists()
        if not has_cred:
            sys.exit(
                f"[錯誤] {kaggle_dir} 下找不到 access_token 或 kaggle.json\n"
                "請到 kaggle.com → 頭像 → Settings → API Tokens → Generate New Token，"
                "並把 token 存到 ~/.kaggle/access_token。"
            )
        print(f"[download] kaggle datasets download -d {DATASET}")
        subprocess.run(
            ["kaggle", "datasets", "download", "-d", DATASET, "-p", str(RAW_DIR)],
            check=True,
        )
        zips = sorted(RAW_DIR.glob("*.zip"))

    for z in zips:
        print(f"[unzip] {z.name}")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(RAW_DIR)


def inspect() -> None:
    csvs = sorted(RAW_DIR.glob("*.csv"))
    print(f"\n===== data/raw/ 共 {len(csvs)} 個 CSV =====")
    summary: list[tuple[str, int, int]] = []
    for path in csvs:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        summary.append((path.name, len(df), len(df.columns)))
        print(f"\n--- {path.name} ---")
        print(f"筆數（pandas 解析記錄數）: {len(df):,}")
        print(f"欄位（{len(df.columns)}）: {list(df.columns)}")
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print("前 2 列：")
            print(df.head(2).to_string())

    print("\n===== 筆數總表 =====")
    width = max(len(name) for name, _, _ in summary)
    for name, rows, cols in summary:
        print(f"{name:<{width}}  rows={rows:>9,}  cols={cols}")


if __name__ == "__main__":
    ensure_data()
    inspect()
