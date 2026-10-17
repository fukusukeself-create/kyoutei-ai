"""最新の手法の比較: 同じ特徴量・同じ検証期間で、勝率モデルを作り比べる。

    python backtest/train.py --db backtest/data/races_jv.sqlite --dump backtest/data/folds_jv   # 特徴量を保存
    python backtest/research.py --folds backtest/data/folds_jv

どれも「市場 (単勝オッズ) の評価を土台に、データで補正する」形 (今のモデルと同じ考え方)。
  lgb   : LightGBM (今のモデルと同じ設定、1シード)
  cat   : CatBoost (順序付きブースティング。カテゴリの扱いが強い)
  xgb   : XGBoost
  nn    : レース単位のニューラルネット (Transformer で同じレースの全馬を同時に見て、レース内で勝率を配る。
          リストワイズ学習 = 勝ち馬の確率を最大化)
  ens   : 上の平均 (勝率の対数を平均してレース内で正規化)
評価: 学習外の対数損失 (勝ち馬の確率)、本命の的中率、期待値1.0以上の単勝の回収率。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import features as F  # noqa: E402

FEATS = F.FEATURES + F.MARKET_FEATURES


def market_logit(df: pd.DataFrame) -> np.ndarray:
    p = np.clip(np.exp(df["mkt_logp"].fillna(np.log(0.005)).to_numpy()), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def norm(df: pd.DataFrame, p: np.ndarray) -> np.ndarray:
    s = pd.Series(p, index=df.index)
    return (s / s.groupby(df.race_id).transform("sum")).to_numpy()


def evaluate(df: pd.DataFrame, p: np.ndarray) -> dict:
    d = df[["race_id", "is_win", "odds"]].assign(p=p)
    ll = float(-np.log(np.clip(d.loc[d.is_win == 1, "p"], 1e-6, 1)).mean())
    top = d.loc[d.groupby("race_id").p.idxmax()]
    pick = d[(d.p * d.odds >= 1.0) & (d.p >= 0.05)]
    return dict(logloss=round(ll, 4), top1=round(float(top.is_win.mean()), 3), ev_bets=int(len(pick)),
                ev_roi=round(float((pick.is_win * pick.odds).mean()), 3) if len(pick) else None)


def holdout(tr: pd.DataFrame):
    dates = sorted(tr.date.unique())
    cut = dates[int(len(dates) * 0.9)]
    return tr[tr.date < cut], tr[tr.date >= cut]


# ---------------------------------------------------------------- 木のモデル
def fit_lgb(tr, va):
    import lightgbm as lgb
    params = dict(objective="binary", learning_rate=0.02, num_leaves=15, min_data_in_leaf=200, lambda_l2=20.0,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=7)
    cats = [c for c in F.CATEGORICAL if c in FEATS]
    ds = lambda d, ref=None: lgb.Dataset(d[FEATS], d.is_win, init_score=market_logit(d), categorical_feature=cats,
                                         reference=ref, free_raw_data=False)
    a, b = holdout(tr)
    m = lgb.train(params, ds(a), 2000, valid_sets=[ds(b, ds(a))], callbacks=[lgb.early_stopping(50, verbose=False)])
    m = lgb.train(params, ds(tr), max(20, m.best_iteration))
    z = market_logit(va) + m.predict(va[FEATS], raw_score=True)
    return 1 / (1 + np.exp(-z))


def fit_cat(tr, va):
    from catboost import CatBoostClassifier, Pool
    cats = [c for c in F.CATEGORICAL if c in FEATS]
    def pool(d, base=True):
        X = d[FEATS].copy()
        for c in cats:
            X[c] = X[c].fillna(-1).astype(int).astype(str)
        return Pool(X, d.is_win, cat_features=cats, baseline=market_logit(d) if base else None)
    a, b = holdout(tr)
    kw = dict(loss_function="Logloss", learning_rate=0.05, depth=6, l2_leaf_reg=10, verbose=False, thread_count=4,
              random_seed=7)
    m = CatBoostClassifier(iterations=2000, od_type="Iter", od_wait=50, **kw).fit(pool(a), eval_set=pool(b))
    m = CatBoostClassifier(iterations=max(20, m.get_best_iteration()), **kw).fit(pool(tr))
    z = market_logit(va) + m.predict(pool(va, base=False), prediction_type="RawFormulaVal")   # 補正分に市場を足す
    return 1 / (1 + np.exp(-z))


def fit_xgb(tr, va):
    import xgboost as xgb
    params = dict(objective="binary:logistic", eta=0.03, max_depth=5, min_child_weight=50, subsample=0.8,
                  colsample_bytree=0.8, reg_lambda=20, tree_method="hist", nthread=4, seed=7)
    dm = lambda d: xgb.DMatrix(d[FEATS].astype(float), d.is_win, base_margin=market_logit(d))
    a, b = holdout(tr)
    m = xgb.train(params, dm(a), 2000, evals=[(dm(b), "v")], early_stopping_rounds=50, verbose_eval=False)
    m = xgb.train(params, dm(tr), max(20, m.best_iteration))
    z = m.predict(dm(va), output_margin=True)   # base_margin 込み
    return 1 / (1 + np.exp(-z))


# ---------------------------------------------------------------- レース単位のニューラルネット
def fit_nn(tr, va, epochs: int = 12, seed: int = 7):
    import torch
    import torch.nn as nn
    torch.manual_seed(seed)
    torch.set_num_threads(4)
    num = [c for c in FEATS if c not in F.CATEGORICAL]
    cats = [c for c in F.CATEGORICAL if c in FEATS]
    mu = tr[num].mean()
    sd = tr[num].std().replace(0, 1)
    cat_maps = {c: {v: i + 1 for i, v in enumerate(sorted(tr[c].dropna().unique()))} for c in cats}

    def tensors(d):
        d = d.sort_values(["race_id", "umaban_id"])
        X = ((d[num] - mu) / sd).fillna(0).clip(-6, 6).to_numpy(np.float32)
        M = d[num].isna().to_numpy(np.float32)
        C = np.stack([d[c].map(cat_maps[c]).fillna(0).astype(int).to_numpy() for c in cats], 1)
        base = np.log(np.clip(np.exp(d["mkt_logp"].fillna(np.log(0.005)).to_numpy()), 1e-6, 1)).astype(np.float32)
        y = d.is_win.to_numpy(np.float32)
        rid = d.race_id.to_numpy()
        starts = np.r_[0, np.flatnonzero(rid[1:] != rid[:-1]) + 1, len(rid)]
        H = 18
        R = len(starts) - 1
        Xb = np.zeros((R, H, X.shape[1] * 2), np.float32)
        Cb = np.zeros((R, H, len(cats)), np.int64)
        Bb = np.full((R, H), -30.0, np.float32)
        Yb = np.zeros((R, H), np.float32)
        Kb = np.zeros((R, H), bool)
        for r in range(R):
            a, b = starts[r], min(starts[r + 1], starts[r] + H)
            n = b - a
            Xb[r, :n] = np.concatenate([X[a:b], M[a:b]], 1)
            Cb[r, :n] = C[a:b]
            Bb[r, :n] = base[a:b]
            Yb[r, :n] = y[a:b]
            Kb[r, :n] = True
        return d, [torch.tensor(t) for t in (Xb, Cb, Bb, Yb, Kb)]

    class RaceNet(nn.Module):
        """各馬を埋め込み → Transformer で同じレースの馬どうしを見比べる → 市場の対数確率に補正を足して softmax。"""
        def __init__(self, nf, ncat_sizes, d=64):
            super().__init__()
            self.embs = nn.ModuleList([nn.Embedding(n + 2, 8) for n in ncat_sizes])
            self.inp = nn.Sequential(nn.Linear(nf + 8 * len(ncat_sizes), d), nn.GELU(), nn.Linear(d, d))
            layer = nn.TransformerEncoderLayer(d, 4, 2 * d, dropout=0.1, batch_first=True)
            self.enc = nn.TransformerEncoder(layer, 2)
            self.out = nn.Linear(d, 1)
            nn.init.zeros_(self.out.weight)
            nn.init.zeros_(self.out.bias)

        def forward(self, X, C, B, K):
            e = torch.cat([X] + [emb(C[..., i]) for i, emb in enumerate(self.embs)], -1)
            h = self.enc(self.inp(e), src_key_padding_mask=~K)
            s = B + self.out(h).squeeze(-1)
            return s.masked_fill(~K, -1e9)

    _, (Xt, Ct, Bt, Yt, Kt) = tensors(tr)
    dva, (Xv, Cv, Bv, Yv, Kv) = tensors(va)
    keep = Yt.sum(1) == 1          # 勝ち馬が1頭のレースだけで学習 (同着は除く)
    Xt, Ct, Bt, Yt, Kt = Xt[keep], Ct[keep], Bt[keep], Yt[keep], Kt[keep]
    model = RaceNet(Xt.shape[-1], [len(cat_maps[c]) for c in cats])
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    n = len(Xt)
    order = np.arange(n)
    # 末尾10% のレースで早期終了
    nv = n // 10
    tr_idx, ho_idx = order[:-nv], order[-nv:]
    best, best_state, bad = 9.9, None, 0
    for ep in range(epochs):
        model.train()
        perm = np.random.default_rng(seed + ep).permutation(tr_idx)
        for i in range(0, len(perm), 256):
            b = perm[i:i + 256]
            s = model(Xt[b], Ct[b], Bt[b], Kt[b])
            loss = -(torch.log_softmax(s, -1) * Yt[b]).sum(-1).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            s = model(Xt[ho_idx], Ct[ho_idx], Bt[ho_idx], Kt[ho_idx])
            ho = float(-(torch.log_softmax(s, -1) * Yt[ho_idx]).sum(-1).mean())
        print(f"    nn epoch {ep + 1}: 早期終了用 {ho:.4f}", flush=True)
        if ho < best - 1e-4:
            best, best_state, bad = ho, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= 2:
                break
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        P = torch.softmax(model(Xv, Cv, Bv, Kv), -1).numpy()
    # 並べ替えた順から元の順へ
    dva = dva.copy()
    vals = []
    for r, (rid, g) in enumerate(dva.groupby("race_id", sort=False)):
        vals.append(P[r, :len(g)])
    dva["p"] = np.concatenate(vals) if vals else []
    return dva["p"].reindex(va.index).to_numpy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folds", default=os.path.join(HERE, "data", "folds_jv"))
    ap.add_argument("--models", default="lgb,cat,xgb,nn")
    ap.add_argument("--out", default=os.path.join(HERE, "data", "research.json"))
    ap.add_argument("--only", help="この fold (開始日) だけ計算して research_oos_<開始日>.pkl に保存する")
    ap.add_argument("--summary", action="store_true", help="保存済みの research_oos_*.pkl をまとめて評価だけする")
    args = ap.parse_args()
    results = {}
    allp = []
    outdir = os.path.dirname(args.out)
    if args.summary:
        allp = pd.concat([pd.read_pickle(p) for p in sorted(glob.glob(os.path.join(outdir, "research_oos_*.pkl")))])
        print(f"=== 合計 {allp.race_id.nunique():,}レース ===")
        for col in [c for c in allp.columns if c.startswith("p_")]:
            print(f"  {col[2:]:7s} {evaluate(allp, allp[col].to_numpy())}")
            for y in ("2025", "2026"):
                s = allp[allp.date.astype(str).str[:4] == y]
                print(f"      {y}: {evaluate(s, s[col].to_numpy())}")
        return
    for trp in sorted(glob.glob(os.path.join(args.folds, "tr_*.pkl"))):
        start = os.path.basename(trp)[3:-4]
        if args.only and start != args.only:
            continue
        tr = pd.read_pickle(trp)
        va = pd.read_pickle(os.path.join(args.folds, f"va_{start}.pkl"))
        print(f"\n=== fold {start}: 学習 {tr.race_id.nunique():,}レース / 検証 {va.race_id.nunique():,}レース", flush=True)
        ps = {"market": norm(va, np.exp(va.mkt_logp.fillna(np.log(0.005))))}
        for name in args.models.split(","):
            t0 = time.time()
            p = {"lgb": fit_lgb, "cat": fit_cat, "xgb": fit_xgb, "nn": fit_nn}[name](tr, va)
            ps[name] = norm(va, p)
            print(f"  {name}: {evaluate(va, ps[name])} ({time.time() - t0:.0f}秒)", flush=True)
        models = [k for k in ps if k != "market"]
        lp = np.mean([np.log(np.clip(ps[k], 1e-9, 1)) for k in models], 0)
        ps["ens"] = norm(va, np.exp(lp))
        for k, p in ps.items():
            results.setdefault(k, {})[start] = evaluate(va, p)
        print(f"  market: {results['market'][start]}\n  ens: {results['ens'][start]}", flush=True)
        one = va[["race_id", "date", "umaban_id", "is_win", "odds", "finish"]].assign(**{f"p_{k}": v for k, v in ps.items()})
        one.to_pickle(os.path.join(outdir, f"research_oos_{start}.pkl"))
        allp.append(one)
    if args.only:
        return
    allp = pd.concat(allp)
    allp.to_pickle(os.path.join(os.path.dirname(args.out), "research_oos.pkl"))
    print("\n=== 全 fold 合計 ===")
    summary = {k: evaluate(allp, allp[f"p_{k}"].to_numpy()) for k in results}
    for k, v in summary.items():
        print(f"  {k:7s} {v}")
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(dict(by_fold=results, total=summary), fp, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
