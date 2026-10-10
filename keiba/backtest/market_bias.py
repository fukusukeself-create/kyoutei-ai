"""市場のクセ調べ: モデルを通さず、条件ごとに「その条件の馬を全部100円ずつ買ったら」の単勝回収率を数える。

    python backtest/market_bias.py

2021〜2024年 (fit) で回収率が高かった条件が、2025年以降 (test) でも高いかを見る。
条件をたくさん試すと偶然プラスの条件が混ざるので、試した条件の数と、偶然で通る数の目安も出す。
"""
from __future__ import annotations

import math
import os
import re
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "races.sqlite")
SPLIT = "20250101"


def load() -> pd.DataFrame:
    c = sqlite3.connect(DB)
    r = pd.read_sql("SELECT race_id,date,venue,surface,distance,condition,cls,heads FROM races WHERE fetched=1 AND surface IN ('芝','ダ')", c)
    u = pd.read_sql("SELECT race_id,umaban,waku,sex_age,weight,jockey,trainer,style,interval,body_weight,finish,ninki,odds FROM runners WHERE finish IS NOT NULL", c)
    d = u.merge(r, on="race_id")
    d["ret"] = np.where(d.finish == 1, d.odds, 0.0)   # 100円あたりの払戻 / 100
    d["odds_band"] = pd.cut(d.odds, [0, 2, 3, 5, 7, 10, 15, 20, 30, 50, 100, 1000],
                            labels=["〜2", "2-3", "3-5", "5-7", "7-10", "10-15", "15-20", "20-30", "30-50", "50-100", "100〜"]).astype(str)
    d["ninki_b"] = d.ninki.clip(upper=12).astype(str)
    d["dist_b"] = pd.cut(d.distance, [0, 1300, 1600, 2000, 2400, 5000], labels=["〜1300", "1400-1600", "1700-2000", "2100-2400", "2500〜"]).astype(str)
    d["heads_b"] = pd.cut(d.heads, [0, 9, 12, 15, 18], labels=["〜9", "10-12", "13-15", "16-18"]).astype(str)
    d["cls_b"] = d.cls.fillna("").str.replace("サラ系", "", regex=False)
    d["sex"] = d.sex_age.str[0]
    d["age"] = d.sex_age.str[1:].str.extract(r"(\d+)")[0].fillna("0").astype(int).clip(upper=7).astype(str)
    bw = d.body_weight.fillna("").str.extract(r"(\d+)\(([+-]?\d+)\)")
    d["bw_chg"] = pd.cut(pd.to_numeric(bw[1], errors="coerce"), [-100, -10, -4, 3, 9, 100], labels=["-10↓", "-9〜-4", "-3〜+3", "+4〜+9", "+10↑"]).astype(str)
    d["wk"] = d.waku.astype(str)
    d["trainer_area"] = d.trainer.fillna("").str.split().str[0]
    d["interval_b"] = d.interval.fillna("").str.extract(r"(\d+)")[0].astype(float).pipe(
        lambda s: pd.cut(s, [-1, 1, 3, 6, 12, 26, 1000], labels=["連闘", "2-3週", "4-6週", "7-12週", "13-26週", "半年〜"])).astype(str)
    return d


COND_COLS = ["odds_band", "ninki_b", "surface", "dist_b", "venue", "condition", "cls_b", "heads_b", "wk", "umaban", "sex", "age",
             "style", "bw_chg", "trainer_area", "interval_b", "jockey", "trainer"]


def stat(g: pd.DataFrame) -> tuple[int, float, float]:
    n = len(g)
    roi = g.ret.mean() if n else float("nan")
    se = g.ret.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    return n, roi, se


def main() -> None:
    d = load()
    fit, test = d[d.date < SPLIT], d[d.date >= SPLIT]
    print(f"全体: fit {len(fit):,}頭 回収率 {fit.ret.mean()*100:.1f}% / test {len(test):,}頭 回収率 {test.ret.mean()*100:.1f}%")
    rows = []
    combos = [(c,) for c in COND_COLS] + [("surface", "odds_band"), ("surface", "ninki_b"), ("cls_b", "odds_band"), ("dist_b", "odds_band"), ("venue", "odds_band")]
    for cols in combos:
        for key, g in fit.groupby(list(cols)):
            key = key if isinstance(key, tuple) else (key,)
            if len(g) < 800:
                continue
            gt = test
            for col, v in zip(cols, key):
                gt = gt[gt[col] == v]
            if len(gt) < 200:
                continue
            nf, rf, sf = stat(g)
            nt, rt, st = stat(gt)
            rows.append(dict(cond=" & ".join(f"{c}={v}" for c, v in zip(cols, key)), n_fit=nf, roi_fit=rf, z_fit=(rf - 1) / sf,
                             n_test=nt, roi_test=rt, z_test=(rt - 1) / st))
    t = pd.DataFrame(rows)
    print(f"試した条件の数: {len(t)}  (偶然で z>2 になる目安: 約{len(t)*0.023:.0f}件)")
    cand = t[(t.roi_fit > 1.0)].sort_values("z_test", ascending=False)
    print("\n== fit で回収率100%超の条件のうち、test の成績が良い順 (上位15) ==")
    for _, x in cand.head(15).iterrows():
        print(f"{x.cond:<40} fit {x.n_fit:>6} {x.roi_fit*100:5.1f}% (z {x.z_fit:+.1f}) | test {x.n_test:>5} {x.roi_test*100:5.1f}% (z {x.z_test:+.1f})")
    both = t[(t.roi_fit > 1.0) & (t.roi_test > 1.0)]
    print(f"\nfit・test の両方で回収率100%超: {len(both)}件 / fit で100%超 {int((t.roi_fit>1).sum())}件")
    print("fit で100%超の条件の、test 平均回収率 (点数加重):",
          f"{(cand.roi_test*cand.n_test).sum()/cand.n_test.sum()*100:.1f}%")
    print("\n== 人気・オッズ帯ごとの全体回収率 (test) ==")
    for b, g in test.groupby("odds_band"):
        n, r_, s_ = stat(g)
        print(f"オッズ{b:<7} {n:>6}頭 回収率 {r_*100:5.1f}%  (±{s_*100:.1f})")


if __name__ == "__main__":
    main()
