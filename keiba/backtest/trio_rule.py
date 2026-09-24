"""三連複を「カチカチに固いレースだけ」買うための条件を、学習外の予想と実際の払戻で決める。

三連複は確率の高い順に買うので、過去のオッズが無くても実際の払戻でそのまま答え合わせできる。
レースを「モデルの上位3頭の固さ」で分け、どの帯なら三連複 (上位 k 点) が回収率で勝つかを
2025年で選び、2026年で確かめる。結果は models/trio.json に書き、アプリはその帯のときだけ三連複を出す。
"""
import json, os, sqlite3, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE))
import features as F
import strategy

oos = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
con = sqlite3.connect(os.path.join(HERE, "data", "races.sqlite"), timeout=120)
info = pd.read_sql("SELECT race_id, surface, heads, cls, grade, name, payouts FROM races WHERE fetched=1", con); con.close()
info["cls_rank"] = [F.class_rank(" ".join(map(str, t))) for t in zip(info.cls, info.grade, info.name)]
imap = info.set_index("race_id").to_dict("index")

rows = []
for rid, g in oos.groupby("race_id"):
    inf = imap.get(rid)
    if not inf or inf["surface"] not in ("芝", "ダ"):
        continue
    pay = json.loads(inf["payouts"] or "{}").get("三連複") or []
    if not pay:
        continue
    win = {int(u): float(p) for u, p in zip(g.umaban_id, g.p_model)}
    trio = strategy.combo_probs(win)["trio"]
    ranked = sorted(trio.items(), key=lambda kv: -kv[1])
    hit_combo = {tuple(sorted(int(x) for x in c.split("-"))): a for c, a in pay}
    top = sorted(win.values(), reverse=True)
    rec = dict(race_id=rid, year=str(g.date.iloc[0])[:4], heads=len(win), cls_rank=inf["cls_rank"],
               top1=top[0], top3sum=sum(top[:3]), trio1=ranked[0][1])
    cum = 0.0
    for k in range(1, 11):
        combo, p = ranked[k - 1]
        cum += p
        rec[f"pay{k}"] = hit_combo.get(combo, 0)          # k 番目の組が当たったときの払戻 (100円あたり)
        rec[f"cover{k}"] = cum
    rows.append(rec)
df = pd.DataFrame(rows)
print(f"races {len(df)} years {df.year.value_counts().to_dict()}")

def roi(d, k):
    inv = 100 * k * len(d)
    ret = sum(d[f"pay{i}"].sum() for i in range(1, k + 1))
    hit = (sum((d[f"pay{i}"] > 0).astype(int) for i in range(1, k + 1)) > 0).mean() if len(d) else 0
    return (ret / inv if inv else 0), hit

# 帯: 上位3頭の勝率合計 (固いレースほど大きい)
df["band"] = pd.qcut(df.top3sum, 5, labels=["混戦", "やや混戦", "普通", "やや堅い", "堅い"])
out = []
for k in (1, 3, 5, 7, 10):
    for band, d in df.groupby("band", observed=True):
        rec = dict(k=k, band=str(band), top3sum=f"{d.top3sum.min():.2f}-{d.top3sum.max():.2f}")
        for y in ("2025", "2026"):
            x = d[d.year == y]; r, h = roi(x, k)
            rec[f"{y}_races"] = len(x); rec[f"{y}_hit"] = f"{h*100:.0f}%"; rec[f"{y}_roi"] = f"{r*100:.0f}%"
        out.append(rec)
tbl = pd.DataFrame(out)
print(tbl.to_string(index=False))
# 全体
for k in (1, 3, 5, 7, 10):
    print(k, "点 全体:", {y: f"{roi(df[df.year == y], k)[0]*100:.0f}% (的中{roi(df[df.year == y], k)[1]*100:.0f}%)" for y in ("2025", "2026")})

# 採用ルール: 「カチカチに固い」= 単勝の本命が圧倒的 (モデルの勝率 TOP1 以上) で、かつ上位3頭の勝率合計が TH 以上
# (2・3着候補まで絞れている)。全体の約4%。三連複を確率の高い順に K 点。
# 当てにいく補助なので、的中率が4割ある2点を採る (1点は回収率が高いが的中が4回に1回まで下がる)
TOP1, TH, K = 0.50, 0.85, 2
cond = (df.top1 >= TOP1) & (df.top3sum >= TH)
fit, test = df[(df.year == "2025") & cond], df[(df.year == "2026") & cond]
r_fit, h_fit = roi(fit, K)
r_test, h_test = roi(test, K)
share = float(cond.mean())
print(f"\n採用: 本命の勝率 {TOP1:.0%} 以上 かつ 上位3頭の勝率合計 {TH:.0%} 以上 (全レースの {share*100:.1f}%) で 三連複 上位{K}点 → "
      f"2025 回収率 {r_fit*100:.0f}% 的中{h_fit*100:.0f}% ({len(fit)}レース) / 2026 {r_test*100:.0f}% 的中{h_test*100:.0f}% ({len(test)}レース)")
res = dict(top1_min=TOP1, top3sum_min=TH, k=K, share=share, fit=dict(roi=float(r_fit), hit=float(h_fit), races=int(len(fit))),
           test=dict(roi=float(r_test), hit=float(h_test), races=int(len(test))), adopt=bool(r_fit >= 0.9 and r_test >= 0.9))
json.dump(res, open(os.path.join(os.path.dirname(HERE), "models", "trio.json"), "w"), ensure_ascii=False, indent=1)
print("saved", res)
