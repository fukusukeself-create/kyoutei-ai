"""JRA-VAN の調教タイム (坂路・ウッド) で、統計モデルの勝率を補正する。

学習外の予想に、レース前14日の調教を足すと 2026年で対数損失 1.9215 → 1.9185 (backtest/jv_workout_eval.py)。
補正は「勝率の対数 + 調教の特徴 × 係数」をレース内で正規化する形 (係数は models/workout_blend.json)。

調教データは JRA-VAN の規約上 公開リポジトリに置けないので、data/jv_private/ (git に入れない) に置く:
    python jravan/load_weekly.py jv_weekly_YYYYMMDD.zip     # Windows の export_jvdata.py --weekly の出力を取り込む
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "data", "jv_private")
FEATS = os.path.join(PRIVATE, "features.csv.gz")   # 馬×レース日 の調教の特徴 (jravan/load_weekly.py が作る)
BLEND = os.path.join(HERE, "models", "workout_blend.json")

FEATURES = ["w_best4", "w_best1", "w_last4", "w_last1", "w_n", "w_missing"]
WINDOW_DAYS = 14

_cache: dict = {}


def zscores(w: pd.DataFrame) -> pd.DataFrame:
    """同じ日・同じトレセン・同じコース (坂路/ウッド) の中で、4F と ラスト1F を標準化する (速いほど +)。"""
    g = w.groupby(["kind", "center", "wdate"])
    for c in [c for c in ("f4", "l1", "f3") if c in w.columns]:
        sd = g[c].transform("std").replace(0, np.nan)
        w[c + "_z"] = -(w[c] - g[c].transform("mean")) / sd
    return w


def race_features(runners: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    """runners: [key..., horse_id, date(YYYYMMDD)] → 調教の特徴を付けて返す。w は zscores 済み。"""
    r = runners.copy()
    r["_d"] = pd.to_datetime(r["date"].astype(str))
    w = w.copy()
    w["_w"] = pd.to_datetime(w["wdate"].astype(str))
    m = r[["horse_id", "_d"]].drop_duplicates().merge(w, on="horse_id")
    m = m[(m._w < m._d) & (m._w >= m._d - pd.Timedelta(days=WINDOW_DAYS))]
    agg = m.groupby(["horse_id", "_d"]).agg(w_n=("f4", "size"), w_best4=("f4_z", "max"), w_best1=("l1_z", "max"))
    last = m.sort_values("_w").groupby(["horse_id", "_d"]).tail(1).set_index(["horse_id", "_d"])[["f4_z", "l1_z"]]
    last.columns = ["w_last4", "w_last1"]
    r = r.merge(agg, left_on=["horse_id", "_d"], right_index=True, how="left")
    r = r.merge(last, left_on=["horse_id", "_d"], right_index=True, how="left")
    r["w_missing"] = r["w_best4"].isna().astype(float)
    for c in ("w_best4", "w_best1", "w_last4", "w_last1"):
        r[c] = r[c].fillna(0.0).clip(-4, 4)
    r["w_n"] = r["w_n"].fillna(0).clip(0, 8)
    return r.drop(columns=["_d"])


EXT_FEATURES = ["w_best4", "w_best1", "w_last4", "w_last1", "w_n", "w_missing", "w_best3", "w_last3", "w_days", "w_lastW", "w_n30"]


def race_features_ext(runners: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    """学習用の拡張版: race_features に 3F・最後の調教からの日数・最後がウッドか・30日の本数 を足す。"""
    r = race_features(runners, w)
    r["_d"] = pd.to_datetime(r["date"].astype(str))
    w = w.copy()
    w["_w"] = pd.to_datetime(w["wdate"].astype(str))
    m = r[["horse_id", "_d"]].drop_duplicates().merge(w, on="horse_id")
    m30 = m[(m._w < m._d) & (m._w >= m._d - pd.Timedelta(days=30))]
    m14 = m30[m30._w >= m30._d - pd.Timedelta(days=WINDOW_DAYS)]
    a30 = m30.groupby(["horse_id", "_d"]).agg(w_n30=("f4", "size"), _lastw=("_w", "max"))
    a14 = m14.groupby(["horse_id", "_d"]).agg(w_best3=("f3_z", "max"))
    last = m14.sort_values("_w").groupby(["horse_id", "_d"]).tail(1).set_index(["horse_id", "_d"])
    last = pd.DataFrame({"w_last3": last["f3_z"], "w_lastW": (last["kind"] == "W").astype(float)})
    for t in (a30, a14, last):
        r = r.merge(t, left_on=["horse_id", "_d"], right_index=True, how="left")
    r["w_days"] = ((r["_d"] - r["_lastw"]).dt.days).fillna(31).clip(0, 31)
    r["w_n30"] = r["w_n30"].fillna(0).clip(0, 15)
    for c in ("w_best3", "w_last3"):
        r[c] = r[c].fillna(0.0).clip(-4, 4)
    r["w_lastW"] = r["w_lastW"].fillna(0.0)
    return r.drop(columns=["_d", "_lastw"])


def blend_probs(p: np.ndarray, feats: np.ndarray, weights: dict) -> np.ndarray:
    """p: 1レースの勝率 (合計1)、feats: 馬×FEATURES。補正後の勝率を返す。"""
    s = weights["a"] * np.log(np.clip(p, 1e-6, None)) + feats @ np.array([weights["w"][f] for f in FEATURES])
    e = np.exp(s - s.max())
    return e / e.sum()


def load() -> Optional[dict]:
    """取り込み済みの調教の特徴 (data/jv_private) と係数。無ければ None。"""
    if "v" in _cache:
        return _cache["v"]
    v = None
    if os.path.exists(FEATS) and os.path.exists(BLEND):
        f = pd.read_csv(FEATS, dtype={"horse_id": str, "race_date": str})
        with open(BLEND, encoding="utf-8") as fp:
            v = dict(feats=f.set_index(["horse_id", "race_date"]), weights=json.load(fp), latest=f.race_date.max())
    _cache["v"] = v
    return v


def adjust(win: dict[int, float], horse_ids: dict[int, str], race_date: str) -> Optional[dict]:
    """勝率 {馬番: p} を調教で補正する。そのレースの調教の特徴が無い馬が半分を超えれば None (補正しない)。"""
    v = load()
    if not v or not win:
        return None
    us = sorted(win)
    rows, found = [], 0
    for u in us:
        key = (horse_ids.get(u, ""), race_date)
        if key in v["feats"].index:
            rows.append([float(v["feats"].loc[key, c]) for c in FEATURES])
            found += 1
        else:
            rows.append([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    if found < len(us) / 2:
        return None
    feats = np.array(rows)
    p = np.array([win[u] for u in us])
    q = blend_probs(p / p.sum(), feats, v["weights"])
    have = float((feats[:, FEATURES.index("w_missing")] == 0).mean())
    return dict(probs={u: float(x) for u, x in zip(us, q)}, coverage=have,
                feats={u: dict(zip(FEATURES, map(float, r))) for u, r in zip(us, rows)})
