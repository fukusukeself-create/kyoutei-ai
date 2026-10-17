"""馬と騎手の相性 (同じ騎手が乗った近走の成績) が、勝率の予想に役立つかを確かめる。

    python backtest/combo_eval.py

近5走のうち、今回と同じ騎手が乗ったレースの成績 (着順・着差から作る 0〜1.2 の指標) と、
別の騎手のときの成績の差。学習外の予想 (oos.csv) に「勝率の対数 × a + 相性 × w」を足して、
2025年で係数を決め、2026年で対数損失を比べる。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import features as F  # noqa: E402


def combo_feats(jockey: str, past: list) -> tuple[float, float, float]:
    """(同じ騎手の近走数/5, 同じ騎手のときの成績 − 別の騎手のときの成績, 同じ騎手で勝った数)。"""
    same, other, wins = [], [], 0
    for p in (past or [])[:5]:
        perf = F._perf(p)
        if perf is None:
            continue
        if F.jockey_same(jockey or "", p.get("jockey") or ""):
            same.append(perf)
            wins += 1 if p.get("finish") == 1 else 0
        else:
            other.append(perf)
    diff = (np.mean(same) - np.mean(other)) if same and other else 0.0
    return len(same) / 5, float(diff), float(wins)


def main() -> None:
    d = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
    d = d[d.finish > 0]
    con = sqlite3.connect(os.path.join(HERE, "data", "races_jv.sqlite"))
    ids = tuple(d.race_id.unique())
    rows = []
    for i in range(0, len(ids), 900):
        q = f"SELECT race_id, umaban, jockey, past FROM runners WHERE race_id IN ({','.join('?' * len(ids[i:i+900]))})"
        rows += con.execute(q, ids[i:i + 900]).fetchall()
    t = pd.DataFrame([(r, u, *combo_feats(j, json.loads(p or "[]"))) for r, u, j, p in rows],
                     columns=["race_id", "umaban_id", "hj_n", "hj_diff", "hj_win"])
    d = d.merge(t, on=["race_id", "umaban_id"], how="left").fillna({"hj_n": 0, "hj_diff": 0, "hj_win": 0})
    d["p_model"] = d.p_model.clip(1e-6) / d.groupby("race_id").p_model.transform("sum")
    d["lmo"] = np.log(d.p_model)
    print(f"同じ騎手の近走がある馬 {(d.hj_n > 0).mean():.3f} / 比べられる馬 {(d.hj_diff != 0).mean():.3f}")
    fit, test = d[d.date < 20260101], d[d.date >= 20260101]

    def prob(v, df, cols):
        s = sum(vi * df[c] for vi, c in zip(v, cols))
        e = np.exp(s - s.groupby(df.race_id).transform("max"))
        return e / e.groupby(df.race_id).transform("sum")
    nll = lambda p, df: float(-np.log(p[df.is_win == 1]).mean())
    print(f"2026年 {test.race_id.nunique():,}レース  そのまま {nll(test.p_model, test):.5f}")
    for cols in (["lmo"], ["lmo", "hj_n", "hj_diff", "hj_win"]):
        v = minimize(lambda v: nll(prob(v, fit, cols), fit), [1.0] + [0.0] * (len(cols) - 1), method="Powell").x
        print(cols, f"{nll(prob(v, test, cols), test):.5f}", np.round(v, 4))


if __name__ == "__main__":
    main()
