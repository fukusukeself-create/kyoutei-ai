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
    runners = attach_extra(runners, db_path)
    return races, runners


def load_lines(db_path: str) -> tuple[dict, dict]:
    """父→系統、母父→系統 の対応表 (collect_extra.py pedigree が作る)。無ければ空。"""
    con = sqlite3.connect(db_path)
    try:
        sire = {}
        for name, line, n in con.execute("SELECT sire, sire_line, COUNT(*) FROM ped_horse WHERE sire_line != '' GROUP BY sire, sire_line"):
            if name not in sire or n > sire[name][1]:
                sire[name] = (line, n)
        dam = {name: line for name, line in con.execute("SELECT name, line FROM damsire_line WHERE line != ''")}
    except sqlite3.OperationalError:
        return {}, {}
    finally:
        con.close()
    return {k: v[0] for k, v in sire.items()}, dam


def attach_extra(runners: pd.DataFrame, db_path: str) -> pd.DataFrame:
    """調教評価と、父・母父の系統を出走馬に付ける。"""
    con = sqlite3.connect(db_path)
    try:
        oik = pd.read_sql("SELECT race_id, umaban, critic AS oik_critic, rank AS oik_rank FROM oikiri", con)
    except Exception:
        oik = pd.DataFrame(columns=["race_id", "umaban", "oik_critic", "oik_rank"])
    con.close()
    runners = runners.merge(oik, on=["race_id", "umaban"], how="left")
    sire_line, dam_line = load_lines(db_path)
    runners["sire_line"] = runners["sire"].map(sire_line).fillna("")
    runners["damsire_line"] = runners["damsire"].map(dam_line).fillna("")
    runners["oik_critic"] = runners["oik_critic"].fillna("")
    runners["oik_rank"] = runners["oik_rank"].fillna("")
    return runners


def _with_past(runners: pd.DataFrame) -> list[dict]:
    rows = runners.to_dict("records")
    for d in rows:
        try:
            d["past"] = json.loads(d.get("past") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["past"] = []
    return rows


def _date_ord(d: str) -> int:
    import datetime as _dt
    return _dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).toordinal()


def build_rows(races: pd.DataFrame, runners: pd.DataFrame, stats: dict, career: dict | None = None,
               career_seed: dict | None = None, form: dict | None = None, loo: bool = False) -> pd.DataFrame:
    """特徴量の行を作る。日付順に処理し、馬の通算 (career) と当日の傾向は「そのレースより前」の情報だけで作る。
    career を渡すとその dict を更新しながら使う (学習→検証と続けて呼べる)。"""
    rows = []
    race_map = races.set_index("race_id").to_dict("index")
    career = career if career is not None else {}
    form = form if form is not None else {"j": {}, "t": {}}
    form.setdefault("j", {}); form.setdefault("t", {})
    if career_seed:
        for k, v in career_seed.items():
            career.setdefault(k, json.loads(json.dumps(v)))
    order = races.sort_values(["date", "venue", "rno"])[["race_id", "date", "venue", "rno"]]
    groups = {rid: g for rid, g in runners.groupby("race_id", sort=False)}
    day_state: dict[tuple, list] = {}
    for rid, date, venue, rno in order.itertuples(index=False):
        grp = groups.get(rid)
        if grp is None:
            continue
        race = race_map[rid]
        rs = []
        for d in grp.to_dict("records"):
            try:
                d["past"] = json.loads(d.get("past") or "[]")
            except json.JSONDecodeError:
                d["past"] = []
            d["career"] = F.career_asof(career.get(d.get("horse_id") or ""), _date_ord(date))
            d["jform"] = F.form_asof(form["j"].get(F.jockey_key(d.get("jockey"))), _date_ord(date))
            d["tform"] = F.form_asof(form["t"].get(F.trainer_key(d.get("trainer"))), _date_ord(date))
            rs.append(d)
        day = F.day_bias(day_state.get((date, venue), []))
        ctx = F.race_context(race, rs, day)
        ctx["loo"] = loo   # 学習用の行は、集計表から自分の結果を抜く
        mk = F.market_features({d["umaban"]: d.get("odds") for d in rs})
        for d in rs:
            f = F.runner_features(d, ctx, stats)
            f.update(mk[d["umaban"]])
            f.update(race_id=rid, date=race["date"], umaban_id=d["umaban"], finish=d["finish"], odds=d["odds"],
                     ninki=d["ninki"], is_win=int(d["finish"] == 1), is_top3=int(d["finish"] <= 3))
            rows.append(f)
        # 出走後: 騎手・調教師の調子、通算、当日傾向を更新
        for d in rs:
            if d.get("finish"):
                win = 1 if d["finish"] == 1 else 0
                F.form_update(form["j"].setdefault(F.jockey_key(d.get("jockey")), []), _date_ord(date), win)
                F.form_update(form["t"].setdefault(F.trainer_key(d.get("trainer")), []), _date_ord(date), win)
        for d in rs:
            hid = d.get("horse_id") or ""
            if not hid or not d.get("finish"):
                continue
            ti = F.time_index(stats, race["venue"], race["surface"], race["distance"], race["condition"], F.time_sec(d.get("time") or ""))
            F.career_update(career.setdefault(hid, {}), race["surface"], int(d["finish"]), ti, _date_ord(date))
        winner = next((d for d in rs if d.get("finish") == 1), None)
        if winner:
            day_state.setdefault((date, venue), []).append(dict(
                winner_style=winner.get("style"), winner_waku=winner.get("waku"), heads=len(rs),
                top3_wakus=[d.get("waku") for d in rs if d.get("finish") and d["finish"] <= 3 and d.get("waku")]))
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


def market_logit(df: pd.DataFrame) -> np.ndarray:
    p = np.clip(np.exp(df["mkt_logp"].fillna(np.log(0.005)).to_numpy()), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def fit(train: pd.DataFrame, target: str, rounds: int | None = None, feats: list[str] | None = None,
        market_base: bool = False) -> lgb.Booster:
    """rounds を指定しなければ、学習期間の末尾10% (日付順) を早期終了用に使って木の本数を決める。
    market_base=True なら市場確率のロジットを初期値 (init_score) にし、木は市場からの補正だけを学ぶ。
    その場合の予測は sigmoid(市場ロジット + predict(raw_score=True))。"""
    feats = feats or F.FEATURES
    params = dict(PARAMS)
    if market_base:
        params.update(learning_rate=0.02, num_leaves=15, min_data_in_leaf=200, lambda_l2=20.0)

    cats = [c for c in F.CATEGORICAL if c in feats]   # 使う特徴量に含まれるカテゴリだけ

    def dataset(d, ref=None):
        kw = dict(label=d[target], categorical_feature=cats)
        if market_base:
            kw["init_score"] = market_logit(d)
        return lgb.Dataset(d[feats], reference=ref, free_raw_data=False, **kw)

    if rounds is None:
        dates = sorted(train.date.unique())
        cut = dates[int(len(dates) * 0.9)]
        tr, ho = train[train.date < cut], train[train.date >= cut]
        ds = dataset(tr)
        dv = dataset(ho, ds)
        m = lgb.train(params, ds, num_boost_round=ROUNDS, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        rounds = max(20 if market_base else 50, m.best_iteration or ROUNDS)
    booster = lgb.train(params, dataset(train), num_boost_round=rounds)
    booster.rounds_used = rounds
    return booster


class Ensemble:
    """同じ設定・別シードの Booster の平均 (raw_score)。save_model は各メンバーを連番で保存する。"""
    def __init__(self, members):
        self.members = members
        self.rounds_used = members[0].rounds_used

    def predict(self, X, raw_score=False):
        return np.mean([m.predict(X, raw_score=raw_score) for m in self.members], axis=0)

    def feature_importance(self, kind):
        return np.mean([m.feature_importance(kind) for m in self.members], axis=0)

    def save_model(self, path):
        base, ext = os.path.splitext(path)
        for i, m in enumerate(self.members):
            m.save_model(path if i == 0 else f"{base}_{i}{ext}")


def fit_ensemble(train, target, feats, market_base, seeds=(7, 11, 23)):
    members = []
    rounds = None
    for sd in seeds:
        PARAMS["seed"] = sd
        m = fit(train, target, rounds=rounds, feats=feats, market_base=market_base)
        rounds = m.rounds_used   # 木の本数は最初のシードで決めたものを使い回す
        members.append(m)
    PARAMS["seed"] = 7
    return Ensemble(members)


def predict_market_base(booster, df: pd.DataFrame, feats: list[str]) -> np.ndarray:
    z = market_logit(df) + booster.predict(df[feats], raw_score=True)
    return 1.0 / (1.0 + np.exp(-z))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--folds", type=int, default=len(FOLDS))
    ap.add_argument("--valid-from", help="動作確認用: この日以降を1つの検証期間にする")
    ap.add_argument("--objective", choices=["binary", "rank"], default="binary")
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
        career: dict = {}
        form: dict = {"j": {}, "t": {}}
        df_tr = build_rows(races[races.date < start], tr_r, stats, career, form=form, loo=True)
        df_va = build_rows(va_races, runners[runners.race_id.isin(va_races.race_id)], stats, career, form=form)
        top3_m = fit(df_tr, "is_top3")
        if args.objective == "rank":
            win_m, temp = fit_rank(df_tr)
            df_va = df_va.sort_values("race_id").reset_index(drop=True)
            df_va["p_model"] = _softmax_in_race(df_va, win_m.predict(df_va[F.FEATURES]), temp)
        else:
            win_m = fit(df_tr, "is_win")
            df_va["p_raw"] = win_m.predict(df_va[F.FEATURES])
            df_va["p_pure"] = norm_in_race(df_va, "p_raw")
        MF = F.FEATURES + F.MARKET_FEATURES
        mkt_m = fit_ensemble(df_tr, "is_win", MF, True)
        df_va["p_raw_mkt"] = predict_market_base(mkt_m, df_va, MF)
        df_va["p_model"] = norm_in_race(df_va, "p_raw_mkt")     # 以降の検証・賭け方比較は市場補正モデルで
        df_va["p_top3"] = top3_m.predict(df_va[F.FEATURES])
        df_va["p_market"] = market_prob(df_va)
        df_va["fold"] = f"{start}-{end}"
        oos.append(df_va[["race_id", "date", "umaban_id", "finish", "odds", "ninki", "is_win", "is_top3",
                          "p_model", "p_pure", "p_top3", "p_market", "fold"]])
        print(f"fold {start}-{end}: train {df_tr.race_id.nunique()} races, valid {df_va.race_id.nunique()} races, "
              f"rounds {win_m.rounds_used}/{mkt_m.rounds_used}/{top3_m.rounds_used}, "
              f"logloss pure {logloss(df_va, 'p_pure'):.4f} market-corrected {logloss(df_va, 'p_model'):.4f} "
              f"market {logloss(df_va, 'p_market'):.4f}", flush=True)

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
    topp = valid.loc[valid.groupby("race_id").p_pure.idxmax()]
    topm = valid.loc[valid.groupby("race_id").p_market.idxmax()]
    topb = valid.loc[valid.groupby("race_id").p_blend.idxmax()]
    metrics = dict(
        races=int(valid.race_id.nunique()), valid_period=[str(valid.date.min()), str(valid.date.max())],
        logloss_model=logloss(valid, "p_model"), logloss_market=logloss(valid, "p_market"), logloss_pure=logloss(valid, "p_pure"),
        top1_hit_pure=float(topp.is_win.mean()), top1_roi_pure=float((topp.is_win * topp.odds).mean()),
        logloss_blend=best_ll, blend_w_market=best_w,
        top1_hit_model=float(top.is_win.mean()), top1_hit_market=float(topm.is_win.mean()),
        top1_hit_blend=float(topb.is_win.mean()),
        top1_roi_model=float((top.is_win * top.odds).mean()), top1_roi_market=float((topm.is_win * topm.odds).mean()),
    )
    for name, col in (("model", "p_model"), ("pure", "p_pure"), ("blend", "p_blend")):
        pick = valid[(valid[col] * valid.odds >= 1.1) & (valid[col] >= 0.06)]
        metrics[f"tansho_{name}_bets"] = int(len(pick))
        metrics[f"tansho_{name}_roi"] = float((pick.is_win * pick.odds).mean()) if len(pick) else None
        metrics[f"tansho_{name}_hit"] = float(pick.is_win.mean()) if len(pick) else None
    print(json.dumps(metrics, ensure_ascii=False, indent=1), flush=True)

    # 本番用: 全期間で学習
    stats_all = F.build_stats(_with_past(runners))
    career_all: dict = {}
    form_all: dict = {"j": {}, "t": {}}
    os.makedirs(args.out, exist_ok=True)
    df_all = build_rows(races, runners, stats_all, career_all, form=form_all, loo=True)
    # 本番用: 騎手・調教師の直近の成績 (日付序数, 勝ち) と、父・母父の系統表
    with open(os.path.join(args.out, "form.json"), "w", encoding="utf-8") as fp:
        json.dump(form_all, fp, ensure_ascii=False, separators=(",", ":"))
    sire_line, dam_line = load_lines(args.db)
    with open(os.path.join(args.out, "lines.json"), "w", encoding="utf-8") as fp:
        json.dump(dict(sire=sire_line, damsire=dam_line), fp, ensure_ascii=False, separators=(",", ":"))
    # 本番用: 馬ごとの通算 (最新時点)。dates は直近12走の日付序数
    with open(os.path.join(args.out, "horses.json"), "w", encoding="utf-8") as fp:
        json.dump(career_all, fp, ensure_ascii=False, separators=(",", ":"))
    top3_all = fit(df_all, "is_top3")
    mkt_all = fit_ensemble(df_all, "is_win", F.FEATURES + F.MARKET_FEATURES, True)
    temperature = None
    if args.objective == "rank":
        win_all, temperature = fit_rank(df_all)
    else:
        win_all = fit(df_all, "is_win")
    imp = sorted(zip(F.FEATURES, win_all.feature_importance("gain")), key=lambda x: -x[1])
    metrics["importance"] = [(k, round(float(v), 1)) for k, v in imp[:25]]
    os.makedirs(args.out, exist_ok=True)
    win_all.save_model(os.path.join(args.out, "win.txt"))
    mkt_all.save_model(os.path.join(args.out, "win_mkt.txt"))
    top3_all.save_model(os.path.join(args.out, "top3.txt"))
    with open(os.path.join(args.out, "stats.json"), "w", encoding="utf-8") as fp:
        json.dump(stats_all, fp, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(dict(features=F.FEATURES, market_features=F.MARKET_FEATURES, market_base=True, ensemble=3, categorical=F.CATEGORICAL, metrics=metrics,
                       objective=args.objective, temperature=temperature,
                       data_period=[str(races.date.min()), str(races.date.max())], races=int(len(races))),
                  fp, ensure_ascii=False, indent=1)
    print("saved to", args.out, flush=True)


if __name__ == "__main__":
    main()
