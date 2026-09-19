"""採用した買い方 (単勝・期待値1.5以上・新馬未勝利以外) の、ダートの年ごとの差を分解する。"""
import json, os, sqlite3, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import features as F
oos = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
con = sqlite3.connect(os.path.join(HERE, "data", "races.sqlite"))
races = pd.read_sql("SELECT race_id, date, venue, surface, distance, condition, heads, cls, grade, name FROM races WHERE fetched=1", con)
run = pd.read_sql("SELECT race_id, umaban, sex_age, jockey, style, interval, past FROM runners", con); con.close()
races["cls_rank"] = [F.class_rank(" ".join(map(str, t))) for t in zip(races.cls, races.grade, races.name)]
races["cls_band"] = pd.cut(races.cls_rank, [-1, 0.5, 1.2, 2.5, 9], labels=["新馬・未勝利", "1勝", "2勝・3勝", "OP・重賞"]).astype(str)
df = oos.merge(races, on="race_id", suffixes=("", "_r"))
df["ev"] = df.p_model * df.odds
df["year"] = df.date.astype(str).str[:4]
df["pay"] = np.where(df.is_win == 1, df.odds * 100, 0)
bets = df[(df.ev >= 1.5) & (df.p_model >= 0.05) & (df.cls_band != "新馬・未勝利")].copy()
def stat(d):
    n = len(d); hits = int(d.is_win.sum())
    return dict(bets=n, hits=hits, hit=f"{hits/n*100:.1f}%" if n else "-", roi=f"{d.pay.sum()/(100*n)*100:.0f}%" if n else "-",
                avg_odds=f"{d.odds.mean():.1f}" if n else "-", avg_p=f"{d.p_model.mean()*100:.1f}%" if n else "-",
                exp_hit=f"{d.p_model.mean()*100:.1f}%" if n else "-")
print("== 全体 (年×芝ダ)")
print(bets.groupby(["year", "surface"]).apply(lambda d: pd.Series(stat(d))).to_string())
d = bets[bets.surface == "ダ"]
for col in ["venue", "condition", "cls_band", "fold"]:
    print(f"\n== ダート: 年×{col}")
    print(d.groupby(["year", col]).apply(lambda x: pd.Series(stat(x))).to_string())
d["dist_band"] = pd.cut(d.distance, [0, 1300, 1700, 1900, 5000], labels=["〜1300", "1400-1700", "1800-1900", "2000〜"]).astype(str)
print("\n== ダート: 年×距離"); print(d.groupby(["year", "dist_band"]).apply(lambda x: pd.Series(stat(x))).to_string())
d["odds_band"] = pd.cut(d.odds, [0, 10, 20, 40, 1000], labels=["〜10倍", "10-20", "20-40", "40倍〜"]).astype(str)
print("\n== ダート: 年×オッズ帯"); print(d.groupby(["year", "odds_band"]).apply(lambda x: pd.Series(stat(x))).to_string())
d["month"] = d.date.astype(str).str[:6]
print("\n== ダート: 月別"); print(d.groupby("month").apply(lambda x: pd.Series(stat(x))).to_string())
# 的中の中身: 大きい払戻に依存していないか
print("\n== ダート 2025 的中の払戻 上位:", sorted(d[(d.year == "2025") & (d.is_win == 1)].odds.round(1).tolist(), reverse=True)[:12])
print("== ダート 2026 的中の払戻 上位:", sorted(d[(d.year == "2026") & (d.is_win == 1)].odds.round(1).tolist(), reverse=True)[:12])
# 期待的中率 (モデル) と実際
for y in ("2025", "2026"):
    x = d[d.year == y]; print(f"ダート {y}: モデルの期待的中率 {x.p_model.mean()*100:.1f}% / 実際 {x.is_win.mean()*100:.1f}% / 市場の期待 {x.p_market.mean()*100:.1f}% ({len(x)}点)")
# ブートストラップで回収率のぶれ幅
rng = np.random.default_rng(0)
for y in ("2025", "2026"):
    x = d[d.year == y].pay.to_numpy(); r = [rng.choice(x, len(x)).mean() / 100 for _ in range(4000)]
    print(f"ダート {y} 回収率の90%区間: {np.percentile(r, 5)*100:.0f}% 〜 {np.percentile(r, 95)*100:.0f}%")
# 芝も
for y in ("2025", "2026"):
    x = bets[(bets.surface == "芝") & (bets.year == y)].pay.to_numpy(); r = [rng.choice(x, len(x)).mean() / 100 for _ in range(4000)]
    print(f"芝 {y} 回収率の90%区間: {np.percentile(r, 5)*100:.0f}% 〜 {np.percentile(r, 95)*100:.0f}% ({len(x)}点)")
# ダート全体 (買い目に限らず) のモデル精度: 年×芝ダの対数損失
def ll(x): return float(-np.log(x.loc[x.is_win == 1, "p_model"].clip(1e-6)).mean())
def llm(x): return float(-np.log(x.loc[x.is_win == 1, "p_market"].clip(1e-6)).mean())
print("\n== 全馬: 年×芝ダ の対数損失 (モデル / 市場)")
for (y, s), x in df.groupby(["year", "surface"]):
    print(y, s, f"モデル {ll(x):.4f} 市場 {llm(x):.4f} 差 {ll(x)-llm(x):+.4f} ({x.race_id.nunique()}レース)")
