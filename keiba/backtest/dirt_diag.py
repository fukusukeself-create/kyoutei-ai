"""ダートで、モデルがどんな馬を買い被っているか (予測勝率 − 実際の勝率) を属性別に見る。"""
import json, os, sqlite3, sys, re
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import features as F
oos = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
con = sqlite3.connect(os.path.join(HERE, "data", "races.sqlite"))
races = pd.read_sql("SELECT race_id, venue, surface, distance, condition, heads, cls, grade, name FROM races WHERE fetched=1", con)
run = pd.read_sql("SELECT race_id, umaban, waku, sex_age, weight, jockey, trainer, sire, style, interval, body_weight, rest_note, past FROM runners", con); con.close()
df = oos.merge(races, on="race_id").merge(run, left_on=["race_id", "umaban_id"], right_on=["race_id", "umaban"])
df["year"] = df.date.astype(str).str[:4]
def attrs(row):
    past = json.loads(row["past"] or "[]")
    p0 = past[0] if past else {}
    dirt_runs = [p for p in past if p.get("surface") == "ダ"]
    bw, bwd = F.parse_body_weight(row["body_weight"] or "")
    first_corner = None
    if p0.get("passing"):
        try: first_corner = int(p0["passing"].split("-")[0]) / max(1, p0.get("heads") or 1)
        except ValueError: pass
    return pd.Series(dict(
        n_past=len(past), first_dirt=int(len(past) > 0 and len(dirt_runs) == 0), turf_to_dirt=int(bool(p0) and p0.get("surface") == "芝"),
        dist_change=(row["distance"] - p0["distance"]) if p0.get("distance") else np.nan,
        bw=bw if bw else np.nan, bwd=bwd if bwd is not None else np.nan, first_corner=first_corner,
        last_fin=p0.get("finish"), last_heads=p0.get("heads"), last_margin=p0.get("margin"),
        last_cond=p0.get("condition"), last_venue_same=int(p0.get("venue") == row["venue"]) if p0 else 0,
        iw=F.interval_weeks(row["interval"] or "", row["rest_note"] or ""),
    ))
d = df[df.surface == "ダ"].copy()
d = pd.concat([d, d.apply(attrs, axis=1)], axis=1)
d["cls_rank"] = [F.class_rank(" ".join(map(str, t))) for t in zip(d.cls, d.grade, d.name)]
d["p_gap"] = d.p_model - d.p_market
d["over"] = d.p_model - d.is_win          # 予測 − 実際 (集計すると 予測勝率 − 実勝率)
def table(col, bins=None, labels=None, sub=None):
    x = d if sub is None else d[sub]
    key = pd.cut(x[col], bins, labels=labels).astype(str) if bins is not None else x[col].astype(str)
    g = x.groupby([key, "year"]).agg(n=("is_win", "size"), model=("p_model", "mean"), market=("p_market", "mean"), actual=("is_win", "mean")).reset_index()
    g["model−実際"] = (g.model - g.actual) * 100; g["市場−実際"] = (g.market - g.actual) * 100
    g[["model", "market", "actual"]] *= 100
    print(f"\n== ダート全馬: {col}"); print(g.round(1).to_string(index=False))
table("style")
table("first_dirt"); table("turf_to_dirt")
table("dist_change", [-9999, -150, -50, 50, 150, 9999], ["短縮200+", "短縮100", "同距離", "延長100", "延長200+"])
table("bw", [0, 439, 469, 499, 999], ["〜439kg", "440-469", "470-499", "500kg〜"])
table("first_corner", [0, 0.2, 0.4, 0.7, 1.01], ["前走先頭〜2割", "2-4割", "4-7割", "後方"])
table("iw", [-1, 3, 8, 15, 999], ["中3週以内", "中4-8週", "中9-15週", "休み明け"])
table("waku", [0, 2, 4, 6, 8], ["1-2枠", "3-4枠", "5-6枠", "7-8枠"])
table("condition")
table("cls_rank", [-1, 0.5, 1.2, 2.5, 9], ["新馬未勝利", "1勝", "2勝3勝", "OP"])
d["odds_band"] = pd.cut(d.odds, [0, 5, 10, 20, 40, 9999], labels=["〜5", "5-10", "10-20", "20-40", "40〜"]).astype(str)
table("odds_band")
# 買い目 (期待値1.5以上) に限った属性分布: 2025 と 2026 で何が違うか
b = d[(d.p_model * d.odds >= 1.5) & (d.p_model >= 0.05) & (d.cls_rank > 0.5)]
print("\n== 期待値1.5以上のダート買い目の属性 (平均)")
print(b.groupby("year")[["first_dirt", "turf_to_dirt", "first_corner", "bw", "iw", "waku", "p_model", "p_market", "odds"]].mean().round(2).to_string())
print(b.groupby(["year", "style"]).agg(n=("is_win", "size"), hit=("is_win", "mean"), roi=("odds", lambda s: 0)).to_string())
for col in ["style", "first_dirt", "turf_to_dirt"]:
    g = b.groupby(["year", col]).apply(lambda x: pd.Series(dict(n=len(x), hits=int(x.is_win.sum()), roi=(x.is_win * x.odds).sum() / len(x)))).reset_index()
    print(f"\n買い目 {col}"); print(g.to_string(index=False))
