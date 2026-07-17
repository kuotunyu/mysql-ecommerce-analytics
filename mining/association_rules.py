"""探勘二：品類關聯規則（同訂單品類構籃、apriori）。

用法：
    uv run python mining/association_rules.py

流程：
    1. SQL 取每筆 delivered 訂單的「相異品類集合」構籃（用品類層級而非商品層級，
       避免 32K 商品的過度稀疏；品類命名沿用全案 COALESCE 口徑）
    2. mlxtend TransactionEncoder → apriori（min_support 動態設為「至少 20 筆訂單」）
    3. association_rules 以 lift 排序，輸出 Top 規則表 →
       results/mining/association_rules.csv ＋ 3 條白話解讀（由實跑數字生成）→
       results/mining/ASSOCIATION_REPORT.md

誠實聲明：訂單平均僅 ~1.1 個品項，跨品類籃是少數——先報告籃子結構
（幾 % 訂單含 >=2 品類）再談規則；規則描述的是「小眾但顯著」的共購模式。
"""

import os
from pathlib import Path

import pandas as pd
import pymysql
from dotenv import load_dotenv
from mlxtend.frequent_patterns import apriori, association_rules
from mlxtend.preprocessing import TransactionEncoder
from tabulate import tabulate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_CSV = PROJECT_ROOT / "results" / "mining" / "association_rules.csv"
OUT_MD = PROJECT_ROOT / "results" / "mining" / "ASSOCIATION_REPORT.md"
MIN_COOCCUR_ORDERS = 10   # min_support = 10 筆訂單 / 籃子總數
                          # （跨品類籃僅 ~0.8%，20 筆門檻只剩 1 對規則；降到 10 換取
                          #   可解讀的規則數，樣本數列於表中由讀者自行評估穩健性）

BASKET_SQL = """
WITH cat_dim AS (
    SELECT p.product_id,
           COALESCE(t.product_category_name_english,
                    p.product_category_name, '(uncategorized)') AS category
    FROM products p
    LEFT JOIN product_category_name_translation t
      ON t.product_category_name = p.product_category_name
)
SELECT DISTINCT oi.order_id, cd.category
FROM order_items oi
JOIN orders  o  ON o.order_id    = oi.order_id
JOIN cat_dim cd ON cd.product_id = oi.product_id
WHERE o.order_status = 'delivered'
"""


def fetch_baskets() -> list[list[str]]:
    load_dotenv(PROJECT_ROOT / ".env")
    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"], port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"], charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(BASKET_SQL)
            df = pd.DataFrame(cur.fetchall(), columns=["order_id", "category"])
    finally:
        conn.close()
    return df.groupby("order_id")["category"].apply(list).tolist()


def fmt_items(s: frozenset) -> str:
    return " + ".join(sorted(s))


def main():
    baskets = fetch_baskets()
    n = len(baskets)
    multi = sum(1 for b in baskets if len(b) >= 2)
    min_support = MIN_COOCCUR_ORDERS / n
    print(f"[baskets] {n:,} 筆 delivered 訂單籃；含 >=2 相異品類者 {multi:,}"
          f"（{multi / n * 100:.2f}%）——跨品類共購是小眾行為（如實呈現）")
    print(f"[apriori] min_support={min_support:.6f}（= {MIN_COOCCUR_ORDERS} 筆訂單）")

    def mine(basket_list, min_orders):
        te = TransactionEncoder()
        onehot = pd.DataFrame(te.fit_transform(basket_list), columns=te.columns_)
        ms = min_orders / len(basket_list)
        freq = apriori(onehot, min_support=ms, use_colnames=True)
        try:
            r = association_rules(freq, metric="lift", min_threshold=0.0)
        except TypeError:  # mlxtend >= 0.23.2 需要 num_itemsets
            r = association_rules(freq, num_itemsets=len(freq), metric="lift", min_threshold=0.0)
        r["n_orders"] = (r["support"] * len(basket_list)).round().astype(int)
        r["pair"] = r.apply(lambda x: frozenset(x["antecedents"] | x["consequents"]), axis=1)
        # 同一品類對只留信心較高的方向（去鏡像列）
        return (r.sort_values("confidence", ascending=False)
                 .drop_duplicates("pair").sort_values("lift", ascending=False))

    def to_show(r, top_n=15):
        t = r.head(top_n).copy()
        t["antecedents"] = t["antecedents"].apply(fmt_items)
        t["consequents"] = t["consequents"].apply(fmt_items)
        return t[["antecedents", "consequents", "n_orders", "support",
                  "confidence", "lift"]].round({"support": 5, "confidence": 3, "lift": 2})

    # ---- 視角一：全域宇宙（96K 訂單）——誠實的負面結果 ----
    rules_g = mine(baskets, MIN_COOCCUR_ORDERS)
    above1 = (rules_g["lift"] > 1.0).sum()
    show = to_show(rules_g[rules_g["lift"] > 1.0])
    print(f"\n[global] 共現 >={MIN_COOCCUR_ORDERS} 筆的品類對中，lift>1 者僅 {above1} 對——"
          f"單一品類訂單佔 99.2%，共購結構性低於隨機期望（誠實負面結果）")
    if len(show):
        print(tabulate(show, headers=show.columns, tablefmt="github", showindex=False))

    # ---- 視角二：條件宇宙（只看 780 筆跨品類訂單）——「會跨買的人怎麼搭」 ----
    multi_baskets = [b for b in baskets if len(b) >= 2]
    rules_c = mine(multi_baskets, MIN_COOCCUR_ORDERS)
    show_c = to_show(rules_c[rules_c["lift"] > 1.0])
    print(f"\n[conditional] 跨品類訂單宇宙（{len(multi_baskets):,} 籃）內的 Top 組合：")
    print(tabulate(show_c, headers=show_c.columns, tablefmt="github", showindex=False))

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    show_c.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # 3 條白話解讀（條件宇宙 lift 最高的三對；每對已取信心較高的方向）
    single = show_c[(show_c["antecedents"].str.contains(r"\+") == False)
                    & (show_c["consequents"].str.contains(r"\+") == False)]
    interp = []
    for _, r in single.head(3).iterrows():
        interp.append(
            f"- 在「會跨品類購買」的訂單中，買了 **{r['antecedents']}** 的有 "
            f"**{r['confidence'] * 100:.1f}%** 同單也買 **{r['consequents']}**——"
            f"是該宇宙隨機期望的 **{r['lift']:.1f} 倍**（共現 {r['n_orders']} 筆，樣本小，方向性參考）。")
    if not interp:
        interp.append("-（門檻下無單一品類對規則——跨品類共購過於稀疏，此為誠實負面結果）")
    print("\n[interpretation]")
    print("\n".join(interp))

    global_tbl = (tabulate(show, headers=show.columns, tablefmt="github", showindex=False)
                  if len(show) else "（無 lift > 1 的規則）")
    md = f"""# 品類關聯規則報告（apriori）

> 籃子 = 同一 delivered 訂單內的相異品類集合（品類層級，避免商品層級過度稀疏）。
> 由 `mining/association_rules.py` 真實執行產生；條件宇宙完整規則表見
> [association_rules.csv](association_rules.csv)。

## 籃子結構（先講誠實的前提）

- 訂單籃總數：**{n:,}**；含 >= 2 相異品類的籃子：**{multi:,}（{multi / n * 100:.2f}%）**。
- 共現門檻：>= {MIN_COOCCUR_ORDERS} 筆訂單（樣本數列於各表，由讀者評估穩健性）。

## 視角一：全域宇宙（{n:,} 筆訂單）——誠實的負面結果

單一品類訂單佔 99.2%，導致幾乎所有品類對的共購率**低於**隨機期望
（lift < 1 是結構性現象）。共現 >= {MIN_COOCCUR_ORDERS} 筆的品類對中，
lift > 1 者僅 **{above1} 對**：

{global_tbl}

**結論**：Olist 的訂單結構不支撐大規模品類交叉銷售敘事——這本身是重要發現。

## 視角二：條件宇宙（{len(multi_baskets):,} 筆跨品類訂單）——會跨買的人怎麼搭

在「至少買了兩個品類」的訂單裡，哪些組合出現得比該宇宙的隨機期望更頻繁：

{tabulate(show_c, headers=show_c.columns, tablefmt="github", showindex=False)}

## 三條白話解讀（條件宇宙）

{chr(10).join(interp)}

**商業應用與界線**：條件宇宙的高 lift 組合可作為「加購推薦」候選，但母體僅
{multi / n * 100:.2f}% 的訂單，期望效益要按此比例折現；別把小眾慣性當大眾行為。
"""
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"\n[csv] {OUT_CSV.relative_to(PROJECT_ROOT)}")
    print(f"[md]  {OUT_MD.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
