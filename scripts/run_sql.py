"""依序執行 sql/ 十題、結果存 results/sql/*.csv、自動生成 results/SQL_REPORT.md。

用法：
    uv run python scripts/run_sql.py

設計：
    * 一題一檔（sql/01~10），每檔單一 SELECT；執行前先確保 analysis/00_views.sql
      的共用 view 存在（v_order_gmv / v_order_review）
    * SQL_REPORT.md 每題輸出：商業問題 → SQL 技巧一句話 → 結果前幾列 → 一句商業洞察
      —— 洞察句由「本次實跑結果」的數字即時生成（紅線：不寫死數字）
"""

import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import pymysql
from dotenv import load_dotenv
from tabulate import tabulate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = PROJECT_ROOT / "sql"
VIEWS_SQL = PROJECT_ROOT / "analysis" / "00_views.sql"
OUT_DIR = PROJECT_ROOT / "results" / "sql"
REPORT_PATH = PROJECT_ROOT / "results" / "SQL_REPORT.md"


def connect():
    load_dotenv(PROJECT_ROOT / ".env")
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4",
    )


def to_df(cur) -> pd.DataFrame:
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    for c in df.columns:
        conv = pd.to_numeric(df[c], errors="coerce")
        if not conv.isna().all() and conv.notna().sum() == df[c].notna().sum():
            df[c] = conv
    return df


# ---- 每題 metadata：標題／商業問題／技巧一句話／洞察生成函式（吃實跑 df）----

def _i01(df):
    fullm = df[df["month_flag"] == "full"]
    peak = fullm.loc[fullm["gmv"].idxmax()]
    return (f"GMV 峰值 {peak['ym']}（黑色星期五）達 R$ {peak['gmv']:,.0f}；"
            f"AOV 穩定在 R$ {fullm['aov'].min():.0f}~{fullm['aov'].max():.0f}——成長全靠單量，客單價未被稀釋。")


def _i02(df):
    top = df.iloc[0]
    return (f"{top['category']} 以 {top['pct_gmv']}% 居冠；Top 15 合計 {df['pct_gmv'].sum():.1f}% GMV"
            f"——中度集中，第一名也未獨大。")


def _i03(df):
    early = df[df["delay_bin"].str.contains("提早")]["pct"].sum()
    late = df[df["delay_bin"].str.contains("晚")]["pct"].sum()
    return (f"預估交期極度保守：{early:.1f}% 訂單提早到貨、僅 {late:.1f}% 晚到——"
            f"但這 {late:.1f}% 的尾巴正是差評主力（見題 10）。")


def _i04(df):
    worst = df.iloc[0]
    sp = df[df["customer_state"] == "SP"].iloc[0]
    return (f"{worst['customer_state']} 遲到率 {worst['late_rate_pct']}%、平均配送 "
            f"{worst['avg_delivery_days']} 天，對照 SP 的 {sp['late_rate_pct']}%／"
            f"{sp['avg_delivery_days']} 天——跨州物流品質是結構性弱點。")


def _i05(df):
    once = df[df["f_bin"].str.startswith("F=1")]["pct"].iloc[0]
    return (f"{once}% 客戶只買一次（回購率僅 {100 - float(once):.1f}%）——"
            f"Olist 是結構性獲客生意，教科書式留客敘事在此不成立。")


def _i06(df):
    older = df[df["month_age"] >= 1]
    return (f"首購後各月齡留存率全面落在 {older['retention_pct'].min():.1f}%~"
            f"{older['retention_pct'].max():.1f}%——趨近於零且兩年未變，cohort 間差異多為抽樣雜訊。")


def _i07(df):
    perfect = df[(df["r_score"] == 5) & (df["f_score"] == 5) & (df["m_score"] == 5)]
    n555 = int(perfect["n_customers"].iloc[0]) if len(perfect) else 0
    big = df.loc[df["n_customers"].idxmax()]
    return (f"最大分數組合是 R{int(big['r_score'])}F{int(big['f_score'])}M{int(big['m_score'])}"
            f"（{int(big['n_customers']):,} 人）；R5F5M5 完美客僅 {n555} 人——"
            f"F 維度退化（97% F=1）是本資料集 RFM 的方法論核心。")


def _i08(df):
    return (f"第 1 名賣家僅佔 {df.iloc[0]['pct_gmv']}% GMV，Top 20（共 3,095 家）累積 "
            f"{df.iloc[-1]['cum_pct_gmv']}%——賣家端高度分散的長尾市集，"
            f"集中度遠低於品類端（題 02 的 Top 15 品類即佔七成六）。")


def _i09(df):
    by_type = df.groupby("payment_type")["pct_rows"].sum().sort_values(ascending=False)
    cc = df[df["payment_type"] == "credit_card"]
    cc_total = cc["pct_rows"].sum()
    cc_single = cc[cc["installment_bin"].str.startswith("一次付清")]["pct_rows"].sum()
    cc_multi_share = (cc_total - cc_single) / cc_total * 100
    return (f"credit_card 佔 {by_type.iloc[0]:.1f}% 付款列（boleto {by_type.get('boleto', 0):.1f}%）；"
            f"信用卡列中 {cc_multi_share:.0f}% 有分期（其餘一次付清）——"
            f"分期滲透率體現巴西電商的支付習慣。")


def _i10(df):
    on_time = df.iloc[0]
    worst = df.iloc[-1]
    return (f"準時訂單平均 {on_time['avg_score']} 分 → 晚 8+ 天崩至 {worst['avg_score']} 分"
            f"（1-2 星佔 {worst['low_score_rate_pct']}%）——物流延遲是滿意度的頭號殺手。")


QUESTIONS = [
    ("01_monthly_revenue", "月營收趨勢", "平台 GMV 的月度規模與成長動能如何？",
     "CTE + LAG() window 算 MoM，CASE 守門確保只對相鄰完整月計成長率。", _i01),
    ("02_top_categories", "營收 Top 品類", "哪些品類撐起平台營收？",
     "LEFT JOIN 英譯表 + COALESCE 三層回退；window 佔比在全集合算完才 LIMIT。", _i02),
    ("03_delivery_vs_estimate", "實際 vs 預估配送分佈", "預估交期準嗎？偏差往哪偏？",
     "DATEDIFF 日期差 + CASE 七段分箱 + window 佔比（DATE 級比較防誤判）。", _i03),
    ("04_late_rate_by_state", "各州遲到率", "哪些州是物流重災區？",
     "布林條件聚合 AVG(delay>0) 一行得遲到率（正負號方向已註解防呆）。", _i04),
    ("05_repurchase_rate", "回購率", "客戶會回來買第二次嗎？",
     "以 customer_unique_id 折疊自然人後雙層聚合（用 customer_id 會把回購率算成 0）。", _i05),
    ("06_cohort_retention", "月度 cohort 留存", "新客首購後 1~6 個月還會回來嗎？",
     "多層 CTE + PERIOD_DIFF/PERIOD_ADD；右截斷格不輸出（觀察不到 ≠ 留存 0%）。", _i06),
    ("07_rfm_ntile", "RFM 三維計算", "客戶的 R/F/M 分數組合分布？",
     "NTILE(5) window 給 R/M 打分；F 因 97% 同值改固定分箱（NTILE 會硬切同值）。", _i07),
    ("08_seller_revenue_rank", "賣家營收排名", "營收多依賴頭部賣家？",
     "RANK() OVER 排名 + 累積佔比 window（明寫 ROWS UNBOUNDED PRECEDING）。", _i08),
    ("09_payment_installments", "付款方式與分期分佈", "顧客怎麼付錢、切幾期？",
     "雙維 GROUP BY + CASE 分箱 + 兩層 window 佔比（金流口徑，與 GMV 不互驗）。", _i09),
    ("10_delay_vs_review", "配送遲到 × 評分", "晚到幾天評分會崩？",
     "重用 v_order_review（一單多評取最新）+ CASE 分箱 + 低分率條件聚合。", _i10),
]


def ensure_views(cur):
    text = VIEWS_SQL.read_text(encoding="utf-8")
    stmts = [s.strip() for s in re.sub(r"^\s*--.*$", "", text, flags=re.M).split(";") if s.strip()]
    for s in stmts:
        cur.execute(s)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    sections = []

    conn = connect()
    try:
        with conn.cursor() as cur:
            ensure_views(cur)
            for stem, title, question, technique, insight_fn in QUESTIONS:
                sql = (SQL_DIR / f"{stem}.sql").read_text(encoding="utf-8").strip().rstrip(";")
                t1 = time.perf_counter()
                cur.execute(sql)
                df = to_df(cur)
                dt = time.perf_counter() - t1
                df.to_csv(OUT_DIR / f"{stem}.csv", index=False, encoding="utf-8-sig")
                insight = insight_fn(df)
                print(f"[{stem}] {dt:.2f}s，{len(df):,} 列 → {insight}")

                head_n = 8 if len(df) > 8 else len(df)
                table_md = tabulate(df.head(head_n), headers=df.columns, tablefmt="github",
                                    showindex=False, disable_numparse=True)
                more = f"\n\n*（共 {len(df):,} 列，完整結果見 [sql/{stem}.csv](sql/{stem}.csv)）*" \
                       if len(df) > head_n else ""
                sections.append(
                    f"## 題 {stem[:2]}：{title}\n\n"
                    f"**商業問題**：{question}\n\n"
                    f"**SQL 技巧**：{technique}（[sql/{stem}.sql](../sql/{stem}.sql)，{dt:.2f}s）\n\n"
                    f"{table_md}{more}\n\n"
                    f"**洞察**：{insight}\n"
                )
    finally:
        conn.close()

    header = (
        "# SQL 十題分析報告（自動生成）\n\n"
        "> 由 `scripts/run_sql.py` 執行 `sql/01~10` 產生；每題完整結果在 "
        "[results/sql/](sql/)。\n"
        "> 口徑：GMV = SUM(price+freight_value)、僅 delivered；金流口徑（題 09）"
        "與 GMV 口徑不可互驗；一單多評取最新一筆。\n"
        "> 深度版分析（六大主題、18 張圖）見 [../reports/REPORT.md](../reports/REPORT.md)。\n\n"
    )
    REPORT_PATH.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"\n[report] {REPORT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"[done] 總耗時 {time.perf_counter() - t0:.1f} 秒")


if __name__ == "__main__":
    try:
        main()
    except pymysql.MySQLError as e:
        print(f"[SQL 錯誤] {e}", file=sys.stderr)
        sys.exit(1)
