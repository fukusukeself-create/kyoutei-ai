"""特徴量グループの足し引き実験。基準 (旧77特徴量) に対して各グループを1つずつ足し、
時系列3フォールドの対数損失と、単勝の期待値買い (縮小0.7・新馬未勝利除く) の fit/test 回収率を比べる。"""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import features as F
import train as T

GROUPS = {
    "jt": ["jockey_form_win", "jockey_form_n", "trainer_form_win", "trainer_form_n",
           "jockey_venue_win", "trainer_sb_win", "jt_combo_win", "jt_combo_n"],
    "oik": ["oik_rank", "oik_rank_rel", "oik_critic_win"],
    "lines": ["sire_line_sb_win", "sire_line_sb_n", "damsire_line_s_win", "nick_win", "nick_n"],
}
ONLY = [g for g in (os.environ.get("ABL_GROUPS") or "").split(",") if g]
if ONLY:
    GROUPS = {k: v for k, v in GROUPS.items() if k in ONLY}
ALL_NEW = [f for g in GROUPS.values() for f in g]
NEW_ANY = ["jockey_form_win", "jockey_form_n", "trainer_form_win", "trainer_form_n", "jockey_venue_win", "trainer_sb_win",
           "jt_combo_win", "jt_combo_n", "oik_rank", "oik_rank_rel", "oik_critic_win", "sire_line_sb_win", "sire_line_sb_n",
           "damsire_line_s_win", "nick_win", "nick_n"]
BASE = [f for f in F.FEATURES if f not in NEW_ANY]
VARIANTS = {"base": BASE, **{f"+{k}": BASE + v for k, v in GROUPS.items()}}
if len(GROUPS) > 1:
    VARIANTS["+all"] = BASE + ALL_NEW
SEEDS = (7, 11, 23)

races, runners = T.load(T.DB)
# 行は1回だけ作り、フォールドごとに使い回す (特徴量の列は全部入っている)
folds = []
for start, end in T.FOLDS:
    tr_r = runners[runners.date < start]
    va_races = races[(races.date >= start) & (races.date < end)]
    stats = F.build_stats(T._with_past(tr_r))
    career, form = {}, {"j": {}, "t": {}}
    df_tr = T.build_rows(races[races.date < start], tr_r, stats, career, form=form)
    df_va = T.build_rows(va_races, runners[runners.race_id.isin(va_races.race_id)], stats, career, form=form)
    df_va["p_market"] = T.market_prob(df_va)
    folds.append((start, df_tr, df_va))
    print("rows ready", start, len(df_tr), len(df_va), flush=True)

con = __import__("sqlite3").connect(T.DB)
info = pd.read_sql("SELECT race_id, cls, grade, name FROM races WHERE fetched=1", con); con.close()
info["cls_rank"] = [F.class_rank(" ".join(map(str, t))) for t in zip(info.cls, info.grade, info.name)]
cls_map = dict(zip(info.race_id, info.cls_rank))

def shrink(p, m, k, rid):
    e = np.exp(k * np.log(p.clip(1e-6)) + (1 - k) * np.log(m.clip(1e-6)))
    return e / e.groupby(rid).transform("sum")

results = []
for name, feats in VARIANTS.items():
    MF = feats + F.MARKET_FEATURES
    parts = []
    for start, df_tr, df_va in folds:
        m = T.fit_ensemble(df_tr, "is_win", MF, True, seeds=SEEDS)
        v = df_va.copy()
        v["p_raw"] = T.predict_market_base(m, v, MF)
        v["p_model"] = T.norm_in_race(v, "p_raw")
        parts.append(v)
    v = pd.concat(parts, ignore_index=True)
    v["year"] = v.date.astype(str).str[:4]
    v["p_adj"] = shrink(v.p_model, v.p_market, 0.7, v.race_id)
    v["cls_rank"] = v.race_id.map(cls_map)
    rec = dict(variant=name, n_feat=len(feats), logloss=round(T.logloss(v, "p_model"), 4))
    for th in (1.2, 1.3):
        sel = v[(v.p_adj * v.odds >= th) & (v.p_adj >= 0.05) & (v.cls_rank > 0.5)]
        for y in ("2025", "2026"):
            x = sel[sel.year == y]
            rec[f"th{th}_{y}"] = f"{(x.is_win * x.odds).mean()*100:.0f}% [{len(x)}]"
    results.append(rec)
    print(rec, flush=True)
print("\n" + pd.DataFrame(results).to_string(index=False))
