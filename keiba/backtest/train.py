"""過去レースから勝率モデル (LightGBM) を学習し、検証して models/ に書き出す。

    python backtest/train.py --valid-from 20260301

学習期間 = 収集した最古〜valid-from 前日、検証期間 = valid-from〜最新。
血統・騎手・厩舎の成績表は学習期間のみから作る (検証期間の結果を覗かない)。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys

import lightgbm as lgb
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import features as F  # noqa: E402
import strategy  # noqa: E402

DB = os.path.join(HERE, "data", "races.sqlite")
OUT = os.path.join(ROOT, "models")


def load(db_path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = sqlite3.connect(db_path)
    races = pd.read_sql("SELECT * FROM races WHERE fetched=1 AND surface != '障'", con)
    runners = pd.read_sql("SELECT * FROM runners WHERE finish IS NOT NULL", con)
    con.close()
    runners = runners[runners.race_id.isin(races.race_id)]
    return races, runners


def build_rows(races: pd.DataFrame, runners: pd.DataFrame, stats: dict) -> pd.DataFrame:
    rows = []
    race_map = races.set_index("race_id").to_dict("index")
    for rid, grp in runners.groupby("race_id", sort=False):
        race = race_map[rid]
        rs = []
        for r in grp.itertuples(index=False):
            d = r._asdict()
            try:
                d["past"] = json.loads(d.get("past") or "[]")
            except json.JSONDecodeError:
                d["past"] = []
            rs.append(d)
        ctx = F.race_context(race, rs)
        for d in rs:
            f = F.runner_features(d, ctx, stats)
            f["race_id"] = rid
            f["date"] = race["date"]
            f["umaban_id"] = d["umaban"]
            f["finish"] = d["finish"]
            f["odds"] = d["odds"]
            f["ninki"] = d["ninki"]
            f["is_win"] = 1 if d["finish"] == 1 else 0
            f["is_top3"] = 1 if d["finish"] <= 3 else 0
            rows.append(f)
    return pd.DataFrame(rows)


def norm_in_race(df: pd.DataFrame, col: str) -> pd.Series:
    s = df.groupby("race_id")[col].transform("sum")
    return df[col] / s.where(s > 0, 1.0)


def logloss(df: pd.DataFrame, col: str) -> float:
    p = df.loc[df.is_win == 1, col].clip(1e-6, 1)
    return float(-np.log(p).mean())


def market_prob(df: pd.DataFrame) -> pd.Series:
    inv = 1.0 / df["odds"].where(df["odds"] > 0)
    inv = inv.fillna(inv.groupby(df["race_id"]).transform("min").fillna(0.01))
    df = df.assign(_inv=inv)
    return norm_in_race(df, "_inv")


def blend(pm: pd.Series, pk: pd.Series, race_id: pd.Series, w: float) -> pd.Series:
    lg = (1 - w) * np.log(pm.clip(1e-6)) + w * np.log(pk.clip(1e-6))
    e = np.exp(lg)
    s = e.groupby(race_id).transform("sum")
    return e / s


def train_model(train: pd.DataFrame, target: str, params: dict) -> lgb.Booster:
    X = train[F.FEATURES]
    ds = lgb.Dataset(X, label=train[target], categorical_feature=F.CATEGORICAL, free_raw_data=False)
    return lgb.train(params, ds, num_boost_round=params.pop("rounds", 600))


def evaluate(valid: pd.DataFrame, races: pd.DataFrame) -> dict:
    """検証期間で、モデル/市場/混合の対数損失、1番手的中率、単勝戦略の回収率を出す。"""
    out = {}
    out["races"] = int(valid.race_id.nunique())
    out["logloss_model"] = logloss(valid, "p_model")
    out["logloss_market"] = logloss(valid, "p_market")
    best_w, best_ll = 0.0, 9.9
    for w in np.arange(0.0, 1.01, 0.05):
        valid["_b"] = blend(valid.p_model, valid.p_market, valid.race_id, w)
        ll = logloss(valid, "_b")
        if ll < best_ll:
            best_w, best_ll = float(w), ll
    out["blend_w_market"] = round(best_w, 2)
    out["logloss_blend"] = best_ll
    valid["p_blend"] = blend(valid.p_model, valid.p_market, valid.race_id, best_w)
    top = valid.loc[valid.groupby("race_id").p_model.idxmax()]
    out["top1_hit_model"] = float(top.is_win.mean())
    topm = valid.loc[valid.groupby("race_id").p_market.idxmax()]
    out["top1_hit_market"] = float(topm.is_win.mean())
    # 単勝戦略: 混合確率×オッズ >= 1.1 かつ 確率 >= 6%
    v = valid
    for name, col in (("model", "p_model"), ("blend", "p_blend")):
        pick = v[(v[col] * v.odds >= 1.1) & (v[col] >= 0.06)]
        ret = (pick.is_win * pick.odds).sum()
        out[f"tansho_{name}_bets"] = int(len(pick))
        out[f"tansho_{name}_roi"] = float(ret / len(pick)) if len(pick) else None
        out[f"tansho_{name}_hit"] = float(pick.is_win.mean()) if len(pick) else None
    # 単純に1番人気を買った場合 (基準)
    fav = v[v.ninki == 1]
    out["tansho_fav_roi"] = float((fav.is_win * fav.odds).sum() / len(fav)) if len(fav) else None
    # 三連複・馬連 (確率順に目標的中率まで) の回収率を払戻で答え合わせ
    race_map = races.set_index("race_id").to_dict("index")
    for style in ("堅実", "バランス"):
        inv = ret = hits = n = 0
        kinds = {"馬連": [0, 0, 0], "三連複": [0, 0, 0], "ワイド": [0, 0, 0]}
        for rid, grp in v.groupby("race_id"):
            pay = json.loads(race_map[rid].get("payouts") or "{}")
            if not pay:
                continue
            probs = dict(zip(grp.umaban_id.astype(int), grp.p_blend))
            plan = strategy.build_plan(probs, {}, style)
            n += 1
            race_hit = False
            for kind in kinds:
                for t in plan.tickets.get(kind, []):
                    kinds[kind][0] += 1
                    inv += 100
                    for combo, amt in pay.get(kind, []):
                        nums = sorted(int(x) for x in combo.split("-") if x.isdigit())
                        if nums == sorted(t.combo):
                            ret += amt
                            kinds[kind][1] += 1
                            kinds[kind][2] += amt
                            race_hit = True
            hits += 1 if race_hit else 0
        out[f"formation_{style}"] = dict(races=n, roi=ret / inv if inv else None, race_hit=hits / n if n else None,
                                        by_kind={k: dict(points=c[0], hits=c[1], roi=c[2] / (c[0] * 100) if c[0] else None)
                                                 for k, c in kinds.items()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--valid-from", default="20260301")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    races, runners = load(args.db)
    print(f"races {len(races)} runners {len(runners)} {races.date.min()}..{races.date.max()}")
    train_r = races[races.date < args.valid_from]
    valid_r = races[races.date >= args.valid_from]
    tr_run = runners[runners.race_id.isin(train_r.race_id)].merge(train_r[["race_id", "surface", "distance"]], on="race_id")
    stats = F.build_stats(tr_run.to_dict("records"))
    print("stats sizes", {k: len(v) for k, v in stats.items() if isinstance(v, dict)})

    df = build_rows(races, runners, stats)
    train = df[df.date < args.valid_from].copy()
    valid = df[df.date >= args.valid_from].copy()
    print(f"train rows {len(train)} valid rows {len(valid)}")

    params = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_data_in_leaf=80,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
                  verbose=-1, seed=7, rounds=700)
    win_model = train_model(train, "is_win", dict(params))
    top3_model = train_model(train, "is_top3", dict(params))

    valid["p_raw"] = win_model.predict(valid[F.FEATURES])
    valid["p_model"] = norm_in_race(valid, "p_raw")
    valid["p_top3_raw"] = top3_model.predict(valid[F.FEATURES])
    valid["p_market"] = market_prob(valid)
    metrics = evaluate(valid, races)
    metrics["train_races"] = int(train_r.race_id.nunique())
    metrics["train_period"] = [str(train_r.date.min()), str(train_r.date.max())]
    metrics["valid_period"] = [str(valid_r.date.min()), str(valid_r.date.max())]
    print(json.dumps(metrics, ensure_ascii=False, indent=1))

    imp = sorted(zip(F.FEATURES, win_model.feature_importance("gain")), key=lambda x: -x[1])
    metrics["importance"] = [(k, round(float(v), 1)) for k, v in imp[:25]]

    # 本番用: 全期間で成績表を作り直し、全期間で学習し直す (検証で決めた設定のまま)
    all_run = runners.merge(races[["race_id", "surface", "distance"]], on="race_id")
    stats_all = F.build_stats(all_run.to_dict("records"))
    df_all = build_rows(races, runners, stats_all)
    win_all = train_model(df_all, "is_win", dict(params))
    top3_all = train_model(df_all, "is_top3", dict(params))
    os.makedirs(args.out, exist_ok=True)
    win_all.save_model(os.path.join(args.out, "win.txt"))
    top3_all.save_model(os.path.join(args.out, "top3.txt"))
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fp:
        json.dump(stats_all, fp, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(dict(features=F.FEATURES, categorical=F.CATEGORICAL, metrics=metrics,
                       data_period=[str(races.date.min()), str(races.date.max())], races=int(len(races))),
                  fp, ensure_ascii=False, indent=1)
    print("saved to", args.out)


if __name__ == "__main__":
    main()
