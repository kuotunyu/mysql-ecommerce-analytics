"""驗證 runner：執行 scripts/verify.sql 並對照 data/expected_counts.json（load.py 實跑產出）。

用法：
    uv run python scripts/run_verify.py

輸出三段：
    1. Row Count 核對表：表 | CSV原始 | 清理後預期 | DB實際 | 差異原因 | 狀態
       PASS = DB實際 == 清理後預期；CSV 原始數永遠並列，落差原因明說（不隱藏去重）
    2. FK 孤兒檢查：6 條 FK 必須=0（任一 >0 即 FAIL）；INFO 級僅記錄基線不判定
    3. 抽樣 JOIN：各印全部回傳列（查詢本身已 LIMIT）＋執行秒數

任何 FAIL → exit code 1。
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from tabulate import tabulate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERIFY_SQL = PROJECT_ROOT / "scripts" / "verify.sql"
MANIFEST = PROJECT_ROOT / "data" / "expected_counts.json"


def connect() -> pymysql.Connection:
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",
    )


def parse_sections(sql_text: str) -> dict[str, list[tuple[str, str]]]:
    """依 '-- @section: name' 切割；區內再依 '-- @name: ...' 標記各語句名稱。
    回傳 {section: [(語句名, SQL), ...]}。"""
    sections: dict[str, list[tuple[str, str]]] = {}
    current = None
    name = ""
    buf: list[str] = []

    def flush():
        nonlocal buf, name
        stmt = "\n".join(buf).strip().rstrip(";").strip()
        if current and stmt:
            sections.setdefault(current, []).append((name, stmt))
        buf, name = [], ""

    for line in sql_text.splitlines():
        m = re.match(r"--\s*@section:\s*(\w+)", line)
        if m:
            flush()
            current = m.group(1)
            continue
        m = re.match(r"--\s*@name:\s*(.+)", line)
        if m:
            flush()
            name = m.group(1).strip()
            continue
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.strip().endswith(";"):
            flush()
    flush()
    return sections


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sections = parse_sections(VERIFY_SQL.read_text(encoding="utf-8"))
    failed = False

    conn = connect()
    try:
        with conn.cursor() as cur:
            # ---- 1) Row Count 核對 ----
            _, counts_sql = sections["counts"][0]
            cur.execute(counts_sql)
            db_counts = {tbl: n for tbl, n in cur.fetchall()}

            rows = []
            for tbl, m in manifest.items():
                db_n = db_counts.get(tbl)
                expected = m["cleaned_rows"]
                status = "PASS" if db_n == expected else "FAIL"
                if status == "FAIL":
                    failed = True
                csv_n = f"{m['csv_rows']:,}" if m["csv_rows"] is not None else "—（衍生表）"
                rows.append([tbl, csv_n, f"{expected:,}",
                             f"{db_n:,}" if db_n is not None else "MISSING",
                             m["delta_note"] or "1:1 載入", status])
            print("\n=== 1) Row Count 核對（PASS = DB實際 == 清理後預期）===")
            print(tabulate(rows, headers=["表", "CSV原始", "清理後預期", "DB實際", "差異原因", "狀態"],
                           tablefmt="github", colalign=("left", "right", "right", "right")))

            # ---- 2) FK 孤兒 + INFO ----
            _, orphan_sql = sections["fk_orphans"][0]
            cur.execute(orphan_sql)
            rows = []
            for chk, orphans, level in cur.fetchall():
                if level == "FK":
                    status = "PASS" if orphans == 0 else "FAIL"
                    if orphans != 0:
                        failed = True
                    rows.append([chk, f"{orphans:,}", "0", status])
                else:
                    rows.append([chk, f"{orphans:,}", "n/a", "INFO"])
            print("\n=== 2) FK 孤兒檢查（FK 必須=0；INFO 為已知資料品質基線）===")
            print(tabulate(rows, headers=["檢查", "孤兒數", "預期", "狀態"],
                           tablefmt="github", colalign=("left", "right", "right")))

            # ---- 3) 抽樣 JOIN ----
            print("\n=== 3) 抽樣 JOIN ===")
            for name, sql in sections["sample_joins"]:
                t0 = time.perf_counter()
                cur.execute(sql)
                dt = time.perf_counter() - t0
                cols = [d[0] for d in cur.description]
                data = cur.fetchall()
                print(f"\n--- {name}（{dt:.2f}s，{len(data)} 列）---")
                # disable_numparse：DECIMAL 以原樣字串輸出，避免科學記號（1.25e+06）
                print(tabulate(data, headers=cols, tablefmt="github", disable_numparse=True))
    finally:
        conn.close()

    print(f"\n=== 總結：{'FAIL（見上）' if failed else '全部 PASS'} ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
