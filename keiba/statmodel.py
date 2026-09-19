"""学習済み統計モデル (models/) で、出走表から勝率・3着内率を出す。

models/ が無ければ None を返し、呼び出し側は簡易レーティング (model.py) に落ちる。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict
from typing import Optional

import features as F
from scraper import Race

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

_cache: dict = {}


def load() -> Optional[dict]:
    if "m" in _cache:
        return _cache["m"]
    try:
        import lightgbm as lgb
        win = lgb.Booster(model_file=os.path.join(MODEL_DIR, "win.txt"))
        top3 = lgb.Booster(model_file=os.path.join(MODEL_DIR, "top3.txt"))
        with open(os.path.join(MODEL_DIR, "stats.json"), encoding="utf-8") as fp:
            stats = json.load(fp)
        with open(os.path.join(MODEL_DIR, "meta.json"), encoding="utf-8") as fp:
            meta = json.load(fp)
        policy = None
        try:
            with open(os.path.join(MODEL_DIR, "bet2.json"), encoding="utf-8") as fp:
                policy = json.load(fp)
        except Exception:
            policy = None
        value_policy = None
        try:
            with open(os.path.join(MODEL_DIR, "profit.json"), encoding="utf-8") as fp:
                value_policy = json.load(fp)
        except Exception:
            value_policy = None
        _cache["m"] = dict(win=win, top3=top3, stats=stats, meta=meta, policy=policy, value_policy=value_policy)
    except Exception:
        _cache["m"] = None
    return _cache["m"]


def available() -> bool:
    return load() is not None


def market_probs(win_odds: dict[str, float], umabans: list[int]) -> dict[int, float]:
    raw = {u: 1.0 / win_odds[f"{u:02d}"] for u in umabans if win_odds.get(f"{u:02d}", 0) > 0}
    z = sum(raw.values())
    return {u: v / z for u, v in raw.items()} if z else {}


def predict(race: Race, win_odds: Optional[dict[str, float]] = None) -> Optional[dict]:
    """戻り: {probs, model_probs, market_probs, top3_probs, features, blend_w}。モデルが無ければ None。"""
    m = load()
    if m is None:
        return None
    import pandas as pd
    runners = [asdict(h) for h in race.horses if "取消" not in (h.rest_note or "") and "除外" not in (h.rest_note or "")]
    if not runners:
        return None
    race_d = dict(surface=race.surface, distance=race.distance, venue=race.venue, condition=race.condition,
                  heads=race.heads, cls=race.cls, grade=race.grade, name=race.name, turn=race.turn)
    ctx = F.race_context(race_d, runners)
    feats = [F.runner_features(r, ctx, m["stats"]) for r in runners]
    X = pd.DataFrame(feats)[m["meta"]["features"]]
    raw = m["win"].predict(X)
    raw3 = m["top3"].predict(X)
    umabans = [r["umaban"] for r in runners]
    if m["meta"].get("objective") == "rank":
        temp = float(m["meta"].get("temperature") or 1.0)
        sc = [float(v) / temp for v in raw]
        mx = max(sc)
        ex = [math.exp(v - mx) for v in sc]
        z = sum(ex)
        model_p = {u: e / z for u, e in zip(umabans, ex)}
    else:
        z = float(sum(raw)) or 1.0
        model_p = {u: float(p) / z for u, p in zip(umabans, raw)}
    z3 = float(sum(raw3)) / 3.0 or 1.0
    top3_p = {u: min(1.0, float(p) / z3) for u, p in zip(umabans, raw3)}
    market = market_probs(win_odds or {}, umabans)
    # 勝率は統計モデルのみ (オッズに左右されない)。オッズは期待値の計算にだけ使う。
    probs = dict(model_p)
    w = 0.0
    return dict(probs=probs, model_probs=model_p, market_probs=market, top3_probs=top3_p,
                features={u: f for u, f in zip(umabans, feats)}, blend_w=w)


def explain(f: dict, stats: dict) -> list[str]:
    """特徴量から人が読める根拠を数個。"""
    notes = []
    bw, bt = stats.get("base_win", 0.08), stats.get("base_top3", 0.24)
    if f.get("is_debut"):
        notes.append("初出走")
    else:
        if f.get("wins", 0) >= 1 and f.get("last_fin") == 0:
            notes.append("前走勝ち")
        elif f.get("top3", 0) >= 2:
            notes.append(f"近5走で{int(f['top3'])}回3着内")
        cu = f.get("class_up")
        if cu is not None and cu == cu:
            if cu > 0:
                notes.append("昇級戦")
            elif cu < 0:
                notes.append("格下げ")
        sp = f.get("same_band_perf")
        if sp == sp and f.get("same_band_n", 0) >= 2:
            notes.append("同距離帯" + ("◎" if sp >= 0.75 else "○" if sp >= 0.55 else "△"))
        ss = f.get("same_surf_n", 0)
        if ss == 0:
            notes.append("芝ダ替わり")
        iw = f.get("interval_w")
        if iw == iw and iw >= 10:
            notes.append("休み明け")
    if f.get("sire_sb_n", 0) >= 20:
        r = f["sire_sb_win"] / bw
        if r >= 1.25:
            notes.append("父がこの条件得意")
        elif r <= 0.75:
            notes.append("父がこの条件不得手")
    if f.get("jockey_n", 0) >= 50 and f["jockey_win"] >= bw * 1.5:
        notes.append("上位騎手")
    if f.get("trainer_n", 0) >= 50 and f["trainer_win"] >= bw * 1.4:
        notes.append("好調厩舎")
    bwd = f.get("bw_delta")
    if bwd == bwd and abs(bwd) >= 12:
        notes.append(f"馬体重{int(bwd):+d}kg")
    return notes[:4]
