"""調教タイムの補正係数を決めて models/workout_blend.json に書く。

    python backtest/fit_workout_blend.py

学習外の予想 (oos.csv) に、JRA-VAN の調教 (jravan/data/SLOP_HC_*.txt・WOOD_WC_*.txt) の特徴を足す。
2025年で係数を決めて 2026年で確かめ、最後に全期間で決め直して保存する (保存するのは係数だけ)。
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import workouts as W  # noqa: E402


def t(s: str) -> float:
    try:
        v = int(s)
    except ValueError:
        return float("nan")
    return v / 10 if v > 0 else float("nan")


def read_jv_workouts(since: str) -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(os.path.join(ROOT, "jravan", "data", "SLOP_HC_*.txt"))):
        for l in open(f, encoding="utf-8"):
            if len(l.rstrip()) >= 58 and l[12:20] >= since:
                rows.append((l[24:34], l[12:20], int(l[11]), "H", t(l[34:38]), t(l[55:58])))
    for f in sorted(glob.glob(os.path.join(ROOT, "jravan", "data", "WOOD_WC_*.txt"))):
        for l in open(f, encoding="utf-8"):
            l = l.rstrip()
            if len(l) >= 60 and l[12:20] >= since:
                tail = l[-24:]
                rows.append((l[24:34], l[12:20], int(l[11]), "W", t(tail[0:4]), t(tail[21:24])))
    w = pd.DataFrame(rows, columns=["horse_id", "wdate", "center", "kind", "f4", "l1"]).dropna(subset=["f4"])
    return W.zscores(w)


def main() -> None:
    d = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
    d = d[d.finish > 0]
    con = sqlite3.connect(os.path.join(HERE, "data", "races.sqlite"))
    ru = pd.read_sql("SELECT race_id, umaban AS umaban_id, horse_id FROM runners", con)
    d = d.merge(ru, on=["race_id", "umaban_id"], how="left")
    first = str(d.date.min())
    since = (pd.Timestamp(first) - pd.Timedelta(days=W.WINDOW_DAYS + 1)).strftime("%Y%m%d")
    w = read_jv_workouts(since)
    print(f"調教 {len(w):,}本 ({since}〜)")
    d = W.race_features(d, w)
    print(f"調教がある馬の割合 {1 - d.w_missing.mean():.3f}")
    d["p_model"] = d.p_model.clip(1e-6) / d.groupby("race_id").p_model.transform("sum")
    d["lmo"] = np.log(d.p_model)
    cols = ["lmo"] + W.FEATURES

    def prob(v, df):
        s = sum(vi * df[c] for vi, c in zip(v, cols))
        e = np.exp(s - s.groupby(df.race_id).transform("max"))
        return e / e.groupby(df.race_id).transform("sum")

    nll = lambda p, df: float(-np.log(p[df.is_win == 1]).mean())
    fitf = lambda df: minimize(lambda v: nll(prob(v, df), df), [1.0] + [0.0] * len(W.FEATURES), method="Powell").x
    fit, test = d[d.date < 20260101], d[d.date >= 20260101]
    v = fitf(fit)
    pt = prob(v, test)
    res = dict(test_period=[str(test.date.min()), str(test.date.max())], test_races=int(test.race_id.nunique()),
               logloss_model=nll(test.p_model, test), logloss_blend=nll(pt, test))
    t2 = test.assign(p=pt)
    for th in (1.0, 1.1):
        for col in ("p_model", "p"):
            x = t2[(t2[col] * t2.odds >= th) & (t2[col] >= 0.05)]
            res[f"tansho_ev{th}_{col}"] = dict(bets=int(len(x)), roi=float((x.is_win * x.odds).sum() / max(len(x), 1)))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    v = fitf(d)
    out = dict(a=float(v[0]), w={f: float(x) for f, x in zip(W.FEATURES, v[1:])}, window_days=W.WINDOW_DAYS,
               fitted_on=[str(d.date.min()), str(d.date.max())], check=res)
    with open(W.BLEND, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("saved", W.BLEND, out["a"], out["w"])


if __name__ == "__main__":
    main()
