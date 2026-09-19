"""ダート改善の実験: 新特徴量 + 芝ダ別モデル + 市場からのずれの縮小 (shrink) を、時系列3フォールドで比べる。"""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import features as F
import train as T

races, runners = T.load(T.DB)
print(f"races {len(races)} runners {len(runners)}", flush=True)
MF = F.FEATURES + F.MARKET_FEATURES
oos = []
for start, end in T.FOLDS:
    tr_r = runners[runners.date < start]
    va_races = races[(races.date >= start) & (races.date < end)]
    stats = F.build_stats(T._with_past(tr_r))
    df_tr = T.build_rows(races[races.date < start], tr_r, stats)
    df_va = T.build_rows(va_races, runners[runners.race_id.isin(va_races.race_id)], stats)
    uni = T.fit(df_tr, "is_win", feats=MF, market_base=True)
    df_va["p_uni_raw"] = T.predict_market_base(uni, df_va, MF)
    df_va["p_sep_raw"] = np.nan
    for surf in (0, 1):
        m = T.fit(df_tr[df_tr.surface == surf], "is_win", feats=MF, market_base=True)
        mask = df_va.surface == surf
        df_va.loc[mask, "p_sep_raw"] = T.predict_market_base(m, df_va[mask], MF)
        if surf == 1:
            imp = sorted(zip(MF, m.feature_importance("gain")), key=lambda x: -x[1])[:15]
            print(f"  ダート専用モデル 重要度上位: {[(k, int(v)) for k, v in imp]}", flush=True)
    df_va["p_uni"] = T.norm_in_race(df_va, "p_uni_raw")
    df_va["p_sep"] = T.norm_in_race(df_va, "p_sep_raw")
    df_va["p_market"] = T.market_prob(df_va)
    for surf, name in ((0, "芝"), (1, "ダ")):
        x = df_va[df_va.surface == surf]
        print(f"fold {start}: {name} logloss uni {T.logloss(x,'p_uni'):.4f} sep {T.logloss(x,'p_sep'):.4f} market {T.logloss(x,'p_market'):.4f} ({x.race_id.nunique()}レース)", flush=True)
    oos.append(df_va[["race_id", "date", "surface", "umaban_id", "finish", "odds", "is_win", "cls_rank", "p_uni", "p_sep", "p_market"]])
v = pd.concat(oos, ignore_index=True)
v["year"] = v.date.astype(str).str[:4]
v.to_csv(os.path.join(HERE, "data", "oos_lab.csv"), index=False)

def shrink(p, m, k, race_id):
    e = np.exp(k * np.log(p.clip(1e-6)) + (1 - k) * np.log(m.clip(1e-6)))
    return e / e.groupby(race_id).transform("sum")

print("\n== 単勝 期待値買い (新馬未勝利以外, p>=0.05): 芝/ダ × 年 の回収率 [点数]")
rows = []
for col in ("p_uni", "p_sep"):
    for k in (1.0, 0.7, 0.5, 0.3):
        v["p_adj"] = shrink(v[col], v.p_market, k, v.race_id)
        for th in (1.2, 1.3, 1.5):
            sel = v[(v.p_adj * v.odds >= th) & (v.p_adj >= 0.05) & (v.cls_rank > 0.5)]
            rec = dict(model=col, shrink=k, th=th)
            for (surf, y), x in sel.groupby(["surface", "year"]):
                rec[f"{'芝' if surf == 0 else 'ダ'}{y}"] = f"{(x.is_win * x.odds).mean()*100:.0f}% [{len(x)}]"
            allx = sel
            rec["全体"] = f"{(allx.is_win * allx.odds).mean()*100:.0f}% [{len(allx)}]"
            rows.append(rec)
print(pd.DataFrame(rows).to_string(index=False))
# 縮小後のキャリブレーション (ダ 2-3倍帯)
print("\n== 縮小 k ごとの: 予測が市場の1.5倍以上の馬の 予測勝率 / 実勝率 (ダート)")
for col in ("p_uni", "p_sep"):
    for k in (1.0, 0.7, 0.5, 0.3):
        v["p_adj"] = shrink(v[col], v.p_market, k, v.race_id)
        x = v[(v.surface == 1) & (v.p_adj >= 1.5 * v.p_market)]
        print(f"{col} k={k}: n={len(x)} 予測 {x.p_adj.mean()*100:.1f}% 市場 {x.p_market.mean()*100:.1f}% 実際 {x.is_win.mean()*100:.1f}%")
