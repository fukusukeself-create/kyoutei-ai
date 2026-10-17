"""騎手の「当日の調子」と「前日の調子」が、勝率の予想に役立つかを確かめる。

    python backtest/jockey_day_eval.py

当日: 同じ日に、そのレースより前に発走したレース (全場) の騎乗。
前日: 直前の開催日 (3日以内) の騎乗。
調子 = 勝った数 − 単勝オッズから見た勝つはずの数 (市場の期待を上回った分)。人気馬で勝っただけでは調子が良いとは言わない。
学習外の予想 (oos.csv) の勝率に「勝率の対数 × a + 調子 × w」を足して、2025年で係数を決め、2026年で確かめる。
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import features as F  # noqa: E402

FEATS = ["jd_n", "jd_res", "jd_res3", "jp_n", "jp_res", "jp_res3"]


def jockey_day_table(con, since: str) -> pd.DataFrame:
    """出走ごとの、騎手の当日 (先に発走したレース) と前日の (騎乗数, 勝利−期待, 3着内−期待)。"""
    q = f"""SELECT r.race_id, r.date, r.start, u.umaban, u.jockey, u.finish, u.odds
            FROM runners u JOIN races r USING (race_id) WHERE r.date >= '{since}'"""
    d = pd.read_sql(q, con)
    d = d[d.finish.fillna(0) >= 0]
    inv = 1.0 / d.odds.where(d.odds > 0)
    d["pm"] = (inv / inv.groupby(d.race_id).transform("sum")).fillna(0.0)
    heads = d.groupby("race_id").umaban.transform("size")
    d["pm3"] = (d.pm * 3).clip(upper=0.95).where(heads > 3, 1.0)   # 3着内の期待 (おおまか)
    d["jk"] = d.jockey.map(F.jockey_key)
    d["win"] = (d.finish == 1).astype(float)
    d["t3"] = d.finish.between(1, 3).astype(float)
    d["done"] = d.finish.fillna(0) > 0
    d["res"] = np.where(d.done, d.win - d.pm, 0.0)
    d["res3"] = np.where(d.done, d.t3 - d.pm3, 0.0)
    d["start"] = d.start.fillna("99:99")
    d = d.sort_values(["jk", "date", "start"])
    g = d.groupby(["jk", "date"])
    # 当日: 同じ日の先のレースの累計 (同じ発走時刻は含めない)
    for c in ("done", "res", "res3"):
        d["c_" + c] = g[c].cumsum() - d[c]
    same_t = d.groupby(["jk", "date", "start"])
    for c in ("done", "res", "res3"):
        d["c_" + c] -= same_t[c].cumsum() - d[c]
    d["jd_n"], d["jd_res"], d["jd_res3"] = d.c_done.astype(float), d.c_res, d.c_res3
    # 前日: 騎手ごとの開催日合計を1日ずらす (3日以内のみ)
    day = d.groupby(["jk", "date"]).agg(n=("done", "sum"), r=("res", "sum"), r3=("res3", "sum")).reset_index()
    day["dt"] = pd.to_datetime(day.date)
    day = day.sort_values(["jk", "dt"])
    for c in ("n", "r", "r3", "dt"):
        day["p_" + c] = day.groupby("jk")[c].shift(1)
    ok = (day.dt - day.p_dt).dt.days <= 3
    day["jp_n"] = day.p_n.where(ok, 0).fillna(0).astype(float)
    day["jp_res"] = day.p_r.where(ok, 0).fillna(0)
    day["jp_res3"] = day.p_r3.where(ok, 0).fillna(0)
    d = d.merge(day[["jk", "date", "jp_n", "jp_res", "jp_res3"]], on=["jk", "date"], how="left")
    return d[["race_id", "umaban"] + FEATS].rename(columns={"umaban": "umaban_id"})


def main() -> None:
    d = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
    d = d[d.finish > 0]
    con = sqlite3.connect(os.path.join(HERE, "data", "races_jv.sqlite"))
    since = str(pd.Timestamp(str(d.date.min())) - pd.Timedelta(days=5))[:10].replace("-", "")
    t = jockey_day_table(con, since)
    d = d.merge(t, on=["race_id", "umaban_id"], how="left")
    for c in FEATS:
        d[c] = d[c].fillna(0.0)
    d["jd_n"] = d.jd_n.clip(0, 12) / 12
    d["jp_n"] = d.jp_n.clip(0, 12) / 12
    d["p_model"] = d.p_model.clip(1e-6) / d.groupby("race_id").p_model.transform("sum")
    d["lmo"] = np.log(d.p_model)
    print(f"当日に先の騎乗がある割合 {(d.jd_n > 0).mean():.3f} / 前日に騎乗がある割合 {(d.jp_n > 0).mean():.3f}")

    def run(cols):
        def prob(v, df):
            s = sum(vi * df[c] for vi, c in zip(v, cols))
            e = np.exp(s - s.groupby(df.race_id).transform("max"))
            return e / e.groupby(df.race_id).transform("sum")
        nll = lambda p, df: float(-np.log(p[df.is_win == 1]).mean())
        fit, test = d[d.date < 20260101], d[d.date >= 20260101]
        v = minimize(lambda v: nll(prob(v, fit), fit), [1.0] + [0.0] * (len(cols) - 1), method="Powell").x
        return nll(prob(v, test), test), dict(zip(cols, np.round(v, 4)))

    test = d[d.date >= 20260101]
    print(f"2026年 {test.race_id.nunique():,}レース  そのまま: {float(-np.log(test.p_model[test.is_win == 1]).mean()):.5f}")
    for name, cols in (("係数だけ", ["lmo"]), ("当日", ["lmo", "jd_n", "jd_res", "jd_res3"]),
                       ("前日", ["lmo", "jp_n", "jp_res", "jp_res3"]), ("当日+前日", ["lmo"] + FEATS)):
        ll, v = run(cols)
        print(f"{name:6s} {ll:.5f}  {v}", flush=True)


if __name__ == "__main__":
    main()
