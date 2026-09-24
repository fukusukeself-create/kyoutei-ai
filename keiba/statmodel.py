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


class _Ensemble:
    def __init__(self, members):
        self.members = members

    def predict(self, X, raw_score=False):
        import numpy as np
        return np.mean([m.predict(X, raw_score=raw_score) for m in self.members], axis=0)


def load() -> Optional[dict]:
    if "m" in _cache:
        return _cache["m"]
    try:
        import lightgbm as lgb
        win = lgb.Booster(model_file=os.path.join(MODEL_DIR, "win.txt"))
        top3 = lgb.Booster(model_file=os.path.join(MODEL_DIR, "top3.txt"))
        try:
            members = [lgb.Booster(model_file=os.path.join(MODEL_DIR, "win_mkt.txt"))]
            i = 1
            while os.path.exists(os.path.join(MODEL_DIR, f"win_mkt_{i}.txt")):
                members.append(lgb.Booster(model_file=os.path.join(MODEL_DIR, f"win_mkt_{i}.txt")))
                i += 1
            win_mkt = _Ensemble(members)
        except Exception:
            win_mkt = None
        with open(os.path.join(MODEL_DIR, "stats.json"), encoding="utf-8") as fp:
            stats = json.load(fp)
        try:
            with open(os.path.join(MODEL_DIR, "horses.json"), encoding="utf-8") as fp:
                horses = json.load(fp)
        except Exception:
            horses = {}
        try:
            with open(os.path.join(MODEL_DIR, "lines.json"), encoding="utf-8") as fp:
                lines = json.load(fp)
        except Exception:
            lines = {"sire": {}, "damsire": {}}
        try:
            with open(os.path.join(MODEL_DIR, "form.json"), encoding="utf-8") as fp:
                form = json.load(fp)
        except Exception:
            form = {"j": {}, "t": {}}
        try:
            with open(os.path.join(MODEL_DIR, "trio.json"), encoding="utf-8") as fp:
                trio = json.load(fp)
        except Exception:
            trio = None
        try:   # 画面表示用のコース別集計 (予想には使わない)
            with open(os.path.join(MODEL_DIR, "display_stats.json"), encoding="utf-8") as fp:
                display_stats = json.load(fp)
        except Exception:
            display_stats = None
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
        _cache["m"] = dict(win=win, win_mkt=win_mkt, top3=top3, stats=stats, meta=meta, policy=policy,
                           value_policy=value_policy, horses=horses, lines=lines, form=form, trio=trio,
                           display_stats=display_stats)
    except Exception:
        _cache["m"] = None
    return _cache["m"]


def available() -> bool:
    return load() is not None


def market_probs(win_odds: dict[str, float], umabans: list[int]) -> dict[int, float]:
    raw = {u: 1.0 / win_odds[f"{u:02d}"] for u in umabans if win_odds.get(f"{u:02d}", 0) > 0}
    z = sum(raw.values())
    return {u: v / z for u, v in raw.items()} if z else {}


def predict(race: Race, win_odds: Optional[dict[str, float]] = None, day: Optional[dict] = None) -> Optional[dict]:
    """戻り: {probs, model_probs, market_probs, top3_probs, features, blend_w}。モデルが無ければ None。
    day: 当日その場で先に終わったレースの傾向 (features.day_bias の戻り)。"""
    m = load()
    if m is None:
        return None
    import datetime as _dt
    import pandas as pd
    runners = [asdict(h) for h in race.horses if "取消" not in (h.rest_note or "") and "除外" not in (h.rest_note or "")]
    if not runners:
        return None
    try:
        date_ord = _dt.date(int(race.date[:4]), int(race.date[4:6]), int(race.date[6:8])).toordinal()
    except (ValueError, TypeError):
        date_ord = _dt.date.today().toordinal()
    lines, form = m.get("lines") or {}, m.get("form") or {}
    for r in runners:
        r["career"] = F.career_asof(m.get("horses", {}).get(r.get("horse_id") or ""), date_ord)
        r["sire_line"] = (lines.get("sire") or {}).get(r.get("sire") or "", "")
        r["damsire_line"] = (lines.get("damsire") or {}).get(r.get("damsire") or "", "")
        r["jform"] = F.form_asof([tuple(x) for x in (form.get("j") or {}).get(F.jockey_key(r.get("jockey")), [])], date_ord)
        r["tform"] = F.form_asof([tuple(x) for x in (form.get("t") or {}).get(F.trainer_key(r.get("trainer")), [])], date_ord)
    race_d = dict(surface=race.surface, distance=race.distance, venue=race.venue, condition=race.condition,
                  heads=race.heads, cls=race.cls, grade=race.grade, name=race.name, turn=race.turn)
    ctx = F.race_context(race_d, runners, day)
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
    engine = "統計モデル"
    probs = dict(model_p)
    w = 0.0
    if market and m.get("win_mkt") is not None and m["meta"].get("market_features"):
        # 市場 (単勝オッズ) を土台に、統計特徴量で補正するモデル
        mk = F.market_features({u: (win_odds or {}).get(f"{u:02d}") for u in umabans})
        Xm = pd.DataFrame([{**f, **mk[u]} for u, f in zip(umabans, feats)])[m["meta"]["features"] + m["meta"]["market_features"]]
        if m["meta"].get("market_base"):
            pm = Xm["mkt_logp"].fillna(math.log(0.005)).map(lambda v: min(max(math.exp(v), 1e-4), 1 - 1e-4))
            logit = (pm / (1 - pm)).map(math.log).to_numpy()
            zs = logit + m["win_mkt"].predict(Xm, raw_score=True)
            rawm = 1.0 / (1.0 + __import__("numpy").exp(-zs))
        else:
            rawm = m["win_mkt"].predict(Xm)
        zm = float(sum(rawm)) or 1.0
        corrected = {u: float(p) / zm for u, p in zip(umabans, rawm)}
        # モデルは市場からのずれを過大に見積もる (検証で確認) ので、市場との幾何平均で縮める
        k = float(((m.get("value_policy") or {}).get("shrink")) or 0.7)
        e = {u: math.exp(k * math.log(max(corrected[u], 1e-6)) + (1 - k) * math.log(max(market.get(u, 1e-4), 1e-6))) for u in umabans}
        zz = sum(e.values())
        probs = {u: v / zz for u, v in e.items()}
        engine = "統計+市場補正"
        w = 1.0 - k
    return dict(probs=probs, model_probs=model_p, market_probs=market, top3_probs=top3_p,
                features={u: f for u, f in zip(umabans, feats)}, blend_w=w, engine=engine)


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
    if f.get("oik_rank") == 4:
        notes.insert(0, "調教A")
    elif f.get("oik_rank") == 1:
        notes.append("調教D")
    if f.get("jockey_form_n", 0) >= 20 and f.get("jockey_form_win", 0) >= bw * 1.6:
        notes.append("騎手好調")
    if f.get("nick_n", 0) >= 30 and f.get("nick_win", 0) >= bw * 1.3:
        notes.append("ニックス良")
    bwd = f.get("bw_delta")
    if bwd == bwd and abs(bwd) >= 12:
        notes.append(f"馬体重{int(bwd):+d}kg")
    return notes[:5]


def horse_info(race: Race) -> dict[int, dict]:
    """画面表示用: 馬番 -> {父系統, 母父系統, 騎手の直近60日, 調教師の直近60日}。"""
    import datetime as _dt
    m = load() or {}
    lines, form = m.get("lines") or {}, m.get("form") or {}
    try:
        date_ord = _dt.date(int(race.date[:4]), int(race.date[4:6]), int(race.date[6:8])).toordinal()
    except (ValueError, TypeError):
        date_ord = _dt.date.today().toordinal()
    last = max((d for hs in (form.get("j") or {}).values() for d, _ in hs), default=None)
    out = {}
    for h in race.horses:
        jn, jw = F.form_asof([tuple(x) for x in (form.get("j") or {}).get(F.jockey_key(h.jockey), [])], date_ord)
        tn, tw = F.form_asof([tuple(x) for x in (form.get("t") or {}).get(F.trainer_key(h.trainer), [])], date_ord)
        out[h.umaban] = dict(sire_line=(lines.get("sire") or {}).get(h.sire, ""), damsire_line=(lines.get("damsire") or {}).get(h.damsire, ""),
                             jockey_form=(jn, jw), trainer_form=(tn, tw))
    out["_form_asof"] = _dt.date.fromordinal(last).strftime("%Y/%m/%d") if last else ""
    return out
