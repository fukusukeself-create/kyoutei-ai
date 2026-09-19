"""過去レースから勝率モデル (LightGBM) を学習し、時系列で検証して models/ に書き出す。

    python backtest/train.py

検証は「その時点までのデータだけで学習し、次の期間を予想する」を繰り返す
(2025年前半・後半・2026年)。血統・騎手・厩舎の成績表も学習側だけから作る。
繰り返しで得た学習外の予想 (out-of-sample) を backtest/data/oos.csv に残し、
bet2.py が賭け方の比較に使う。最後に全期間で学習し直したものを本番用に保存する。
"""

from __future__ import annotations

import argparse
import json
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

DB = os.path.join(HERE, "data", "races.sqlite")
OUT = os.path.join(ROOT, "models")
OOS = os.path.join(HERE, "data", "oos.csv")
FOLDS = [("20250101", "20250701"), ("20250701", "20260101"), ("20260101", "99999999")]
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_data_in_leaf=80,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, verbose=-1, seed=7)
ROUNDS = 1500


def load(db_path: str):
    con = sqlite3.connect(db_path)
    races = pd.read_sql("SELECT * FROM races WHERE fetched=1 AND surface IN ('芝','ダ')", con)
    runners = pd.read_sql("SELECT * FROM runners WHERE finish IS NOT NULL", con)
    con.close()
    runners = runners[runners.race_id.isin(races.race_id)]
    runners = runners.merge(races[["race_id", "date", "surface", "distance", "venue", "condition"]], on="race_id")
    return races, runners


def _with_past(runners: pd.DataFrame) -> list[dict]:
    rows = runners.to_dict("records")
    for d in rows:
        try:
            d["past"] = json.loads(d.get("past") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["past"] = []
    return rows


def build_rows(races: pd.DataFrame, runners: pd.DataFrame, stats: dict) -> pd.DataFrame:
    rows = []
    race_map = races.set_index("race_id").to_dict("index")
    for rid, grp in runners.groupby("race_id", sort=False):
        race = race_map[rid]
        rs = []
        for d in grp.to_dict("records"):
            try:
                d["past"] = json.loads(d.get("past") or "[]")
            except json.JSONDecodeError:
                d["past"] = []
            rs.append(d)
        ctx = F.race_context(race, rs)
        for d in rs:
            f = F.runner_features(d, ctx, stats)
            f.update(race_id=rid, date=race["date"], umaban_id=d["umaban"], finish=d["finish"], odds=d["odds"],
                     ninki=d["ninki"], is_win=int(d["finish"] == 1), is_top3=int(d["finish"] <= 3))
            rows.append(f)
    return pd.DataFrame(rows)


def norm_in_race(df: pd.DataFrame, col: str) -> pd.Series:
    s = df.groupby("race_id")[col].transform("sum")
    return df[col] / s.where(s > 0, 1.0)


def market_prob(df: pd.DataFrame) -> pd.Series:
    inv = (1.0 / df["odds"].where(df["odds"] > 0)).fillna(0.005)
    return norm_in_race(df.assign(_inv=inv), "_inv")


def blend(pm, pk, race_id, w: float):
    e = np.exp((1 - w) * np.log(pm.clip(1e-6)) + w * np.log(pk.clip(1e-6)))
    return e / e.groupby(race_id).transform("sum")


def logloss(df: pd.DataFrame, col: str) -> float:
    return float(-np.log(df.loc[df.is_win == 1, col].clip(1e-6, 1)).mean())


def _softmax_in_race(df: pd.DataFrame, score: np.ndarray, temp: float) -> np.ndarray:
    d = df[["race_id"]].copy()
    d["s"] = score / temp
    d["s"] -= d.groupby("race_id")["s"].transform("max")
    d["e"] = np.exp(d["s"])
    return (d["e"] / d.groupby("race_id")["e"].transform("sum")).to_numpy()


def fit_rank(train: pd.DataFrame, rounds: int | None = None) -> tuple[lgb.Booster, float]:
    """lambdarank: レース内で 1着>2着>3着>その他 の順位を学習する。スコアはレース内 softmax で確率にし、
    その温度は学習期間末尾10%の対数損失が最小になる値にする。"""
    train = train.sort_values("race_id").reset_index(drop=True)
    label = train.finish.map({1: 3, 2: 2, 3: 1}).fillna(0).astype(int)
    params = dict(PARAMS, objective="lambdarank", metric="ndcg", eval_at=[1, 3], lambdarank_truncation_level=8)
    dates = sorted(train.date.unique())
    cut = dates[int(len(dates) * 0.9)]
    tr_m = train.date < cut
    tr, ho = train[tr_m], train[~tr_m]
    g_tr, g_ho = tr.groupby("race_id", sort=False).size().to_numpy(), ho.groupby("race_id", sort=False).size().to_numpy()
    if rounds is None:
        ds = lgb.Dataset(tr[F.FEATURES], label=label[tr_m], group=g_tr, categorical_feature=F.CATEGORICAL, free_raw_data=False)
        dv = lgb.Dataset(ho[F.FEATURES], label=label[~tr_m], group=g_ho, categorical_feature=F.CATEGORICAL, reference=ds)
        m = lgb.train(params, ds, num_boost_round=ROUNDS, valid_sets=[dv], callbacks=[lgb.early_stopping(50, verbose=False)])
        rounds = max(50, m.best_iteration or ROUNDS)
    else:
        ds = lgb.Dataset(tr[F.FEATURES], label=label[tr_m], group=g_tr, categorical_feature=F.CATEGORICAL, free_raw_data=False)
        m = lgb.train(params, ds, num_boost_round=rounds)
    # 温度: 末尾10%で対数損失が最小
    sc = m.predict(ho[F.FEATURES])
    best_t, best_ll = 1.0, 9.9
    for t in np.arange(0.3, 3.01, 0.1):
        pr = _softmax_in_race(ho, sc, t)
        ll = float(-np.log(np.clip(pr[ho.is_win.to_numpy() == 1], 1e-6, 1)).mean())
        if ll < best_ll:
            best_t, best_ll = float(t), ll
    ds_all = lgb.Dataset(train[F.FEATURES], label=label, group=train.groupby("race_id", sort=False).size().to_numpy(),
                         categorical_feature=F.CATEGORICAL, free_raw_data=False)
    booster = lgb.train(params, ds_all, num_boost_round=rounds)
    booster.rounds_used = rounds
    booster.temperature = best_t
    return booster, best_t


def fit(train: pd.DataFrame, target: str, rounds: int | None = None) -> lgb.Booster:
    """rounds を指定しなければ、学習期間の末尾10% (日付順) を早期終了用に使って木の本数を決める。"""
    if rounds is None:
        dates = sorted(train.date.unique())
        cut = dates[int(len(dates) * 0.9)]
        tr, ho = train[train.date < cut], train[train.date >= cut]
        ds = lgb.Dataset(tr[F.FEATURES], label=tr[target], categorical_feature=F.CATEGORICAL, free_raw_data=False)
        dv = lgb.Dataset(ho[F.FEATURES], label=ho[target], categorical_feature=F.CATEGORICAL, reference=ds)
        m = lgb.train(PARAMS, ds, num_boost_round=ROUNDS, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        rounds = max(50, m.best_iteration or ROUNDS)
    ds = lgb.Dataset(train[F.FEATURES], label=train[target], categorical_feature=F.CATEGORICAL, free_raw_data=False)
    booster = lgb.train(PARAMS, ds, num_boost_round=rounds)
    booster.rounds_used = rounds
    return booster


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--folds", type=int, default=len(FOLDS))
    ap.add_argument("--valid-from", help="動作確認用: この日以降を1つの検証期間にする")
    ap.add_argument("--objective", choices=["binary", "rank"], default="rank")
    args = ap.parse_args()
    folds = FOLDS[-args.folds:] if not args.valid_from else [(args.valid_from, "99999999")]

    races, runners = load(args.db)
    print(f"races {len(races)} runners {len(runners)} {races.date.min()}..{races.date.max()}", flush=True)

    oos = []
    for start, end in folds:
        tr_r = runners[runners.date < start]
        va_races = races[(races.date >= start) & (races.date < end)]
        if tr_r.empty or va_races.empty:
            continue
        stats = F.build_stats(_with_past(tr_r))
        df_tr = build_rows(races[races.date < start], tr_r, stats)
        df_va = build_rows(va_races, runners[runners.race_id.isin(va_races.race_id)], stats)
        top3_m = fit(df_tr, "is_top3")
        if args.objective == "rank":
            win_m, temp = fit_rank(df_tr)
            df_va = df_va.sort_values("race_id").reset_index(drop=True)
            df_va["p_model"] = _softmax_in_race(df_va, win_m.predict(df_va[F.FEATURES]), temp)
        else:
            win_m = fit(df_tr, "is_win")
            df_va["p_raw"] = win_m.predict(df_va[F.FEATURES])
            df_va["p_model"] = norm_in_race(df_va, "p_raw")
        df_va["p_top3"] = top3_m.predict(df_va[F.FEATURES])
        df_va["p_market"] = market_prob(df_va)
        df_va["fold"] = f"{start}-{end}"
        oos.append(df_va[["race_id", "date", "umaban_id", "finish", "odds", "ninki", "is_win", "is_top3",
                          "p_model", "p_top3", "p_market", "fold"]])
        print(f"fold {start}-{end}: train {df_tr.race_id.nunique()} races, valid {df_va.race_id.nunique()} races, "
              f"rounds {win_m.rounds_used}/{top3_m.rounds_used}, "
              f"logloss model {logloss(df_va, 'p_model'):.4f} market {logloss(df_va, 'p_market'):.4f}", flush=True)

    valid = pd.concat(oos, ignore_index=True)
    best_w, best_ll = 0.0, 9.9
    for w in np.arange(0.0, 1.001, 0.05):
        valid["_b"] = blend(valid.p_model, valid.p_market, valid.race_id, w)
        ll = logloss(valid, "_b")
        if ll < best_ll:
            best_w, best_ll = round(float(w), 2), ll
    valid["p_blend"] = blend(valid.p_model, valid.p_market, valid.race_id, best_w)
    valid.drop(columns=["_b"]).to_csv(OOS, index=False)

    top = valid.loc[valid.groupby("race_id").p_model.idxmax()]
    topm = valid.loc[valid.groupby("race_id").p_market.idxmax()]
    topb = valid.loc[valid.groupby("race_id").p_blend.idxmax()]
    metrics = dict(
        races=int(valid.race_id.nunique()), valid_period=[str(valid.date.min()), str(valid.date.max())],
        logloss_model=logloss(valid, "p_model"), logloss_market=logloss(valid, "p_market"),
        logloss_blend=best_ll, blend_w_market=best_w,
        top1_hit_model=float(top.is_win.mean()), top1_hit_market=float(topm.is_win.mean()),
        top1_hit_blend=float(topb.is_win.mean()),
        top1_roi_model=float((top.is_win * top.odds).mean()), top1_roi_market=float((topm.is_win * topm.odds).mean()),
    )
    for name, col in (("model", "p_model"), ("blend", "p_blend")):
        pick = valid[(valid[col] * valid.odds >= 1.1) & (valid[col] >= 0.06)]
        metrics[f"tansho_{name}_bets"] = int(len(pick))
        metrics[f"tansho_{name}_roi"] = float((pick.is_win * pick.odds).mean()) if len(pick) else None
        metrics[f"tansho_{name}_hit"] = float(pick.is_win.mean()) if len(pick) else None
    print(json.dumps(metrics, ensure_ascii=False, indent=1), flush=True)

    # 本番用: 全期間で学習
    stats_all = F.build_stats(_with_past(runners))
    df_all = build_rows(races, runners, stats_all)
    top3_all = fit(df_all, "is_top3")
    temperature = None
    if args.objective == "rank":
        win_all, temperature = fit_rank(df_all)
    else:
        win_all = fit(df_all, "is_win")
    imp = sorted(zip(F.FEATURES, win_all.feature_importance("gain")), key=lambda x: -x[1])
    metrics["importance"] = [(k, round(float(v), 1)) for k, v in imp[:25]]
    os.makedirs(args.out, exist_ok=True)
    win_all.save_model(os.path.join(args.out, "win.txt"))
    top3_all.save_model(os.path.join(args.out, "top3.txt"))
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fp:
        json.dump(stats_all, fp, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(dict(features=F.FEATURES, categorical=F.CATEGORICAL, metrics=metrics,
                       objective=args.objective, temperature=temperature,
                       data_period=[str(races.date.min()), str(races.date.max())], races=int(len(races))),
                  fp, ensure_ascii=False, indent=1)
    print("saved to", args.out, flush=True)


if __name__ == "__main__":
    main()
