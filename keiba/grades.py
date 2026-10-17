"""全馬の S〜G ランク (総合と、分野ごと)。

総合: 最終の勝率 (統計モデル: 近走・騎手・血統・調教・枠・馬場など110項目 + オッズ、JRA-VAN 調教タイム補正) で決める。
  学習外の 2026年 (2,636レース) で、実際の勝率は S 52%・A 30%・B 19%・C 12%・D 8%・E 5%・F 2%・G 0.5%
  (backtest/oos.csv。2025年もほぼ同じ)。
分野: 騎手 (通算・直近60日・この競馬場)・厩舎・血統・近走・タイム・調教・相性 (馬とコース・距離・馬場)・枠/展開 の素の成績を、
  全馬の中の位置で S〜G にする (上位5%が S、次の10%が A、15% B、20% C、20% D、15% E、10% F、残り G)。
  分野のランクは「どこが強み・弱みか」を見るためのもので、総合はそれらをすべて合わせた統計モデルの答え。
馬と騎手のコンビの成績は表示だけ (backtest/combo_eval.py で勝率の予想を良くしなかったため)。
"""
from __future__ import annotations

import math
from typing import Optional

import features as F

LETTERS = "SABCDEFG"
# 総合: 勝率の下限 (学習外で実際の勝率とほぼ一致することを確認済み)
OVERALL = [(0.40, "S"), (0.25, "A"), (0.15, "B"), (0.10, "C"), (0.06, "D"), (0.03, "E"), (0.015, "F"), (0.0, "G")]
OVERALL_ACTUAL = {"S": "52%", "A": "30%", "B": "19%", "C": "12%", "D": "8%", "E": "5%", "F": "2%", "G": "0.5%"}
SHARE = [0.05, 0.10, 0.15, 0.20, 0.20, 0.15, 0.10, 0.05]   # S〜G の割合
FACTORS = ["騎手", "厩舎", "血統", "近走", "タイム", "調教", "相性", "枠・展開"]
# 分野の境目 (上位から S/A/B/C/D/E/F の下限)。実際の開催日の全出走馬の分布から決める (backtest/fit_grade_cuts.py)
try:
    import json as _json
    import os as _os
    with open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "models", "grade_cuts.json"), encoding="utf-8") as _fp:
        CUTS: dict[str, list[float]] = _json.load(_fp)["cuts"]
except (OSError, ValueError, KeyError):
    CUTS = {}


def overall(p: float) -> str:
    return next(g for th, g in OVERALL if p >= th)


def _ok(v) -> bool:
    return v is not None and v == v


def factor_scores(f: dict, horse, wk: Optional[dict], bw: float) -> dict[str, Optional[float]]:
    """分野ごとの点数 (大きいほど良い)。材料が無ければ None。"""
    def mean(xs):
        xs = [x for x in xs if _ok(x)]
        return sum(xs) / len(xs) if xs else None
    s: dict[str, Optional[float]] = {}
    # 騎手: 通算 4割・直近60日 (前日までの調子) 4割・この競馬場 2割
    s["騎手"] = 0.4 * f.get("jockey_win", bw) + 0.4 * f.get("jockey_form_win", bw) + 0.2 * f.get("jockey_venue_win", bw)
    s["厩舎"] = 0.6 * f.get("trainer_win", bw) + 0.4 * f.get("trainer_form_win", bw)
    s["血統"] = mean([f.get("sire_sb_win"), f.get("damsire_s_win"), f.get("sire_line_sb_win"), f.get("nick_win")])
    s["近走"] = f.get("perf_cls") if _ok(f.get("perf_cls")) else None
    s["タイム"] = mean([f.get("best_tidx"), f.get("same_surf_tidx"), f.get("mean_tidx")])
    if wk:
        s["調教"] = -wk["best"]   # 同日同コースでの上位% (小さいほど速い)
    else:
        # JRA-VAN の調教タイムが無いとき (公開アプリ) は netkeiba の追い切り評価 A〜D で代用 (A=A, B=C, C=E, D=G 相当)
        s["調教"] = {"A": -1.0, "B": -5.0, "C": -15.0, "D": -50.0}.get(getattr(horse, "oik_rank", "") or "", None)
    s["相性"] = mean([f.get("same_venue_perf"), f.get("same_band_perf"), f.get("same_surf_perf"), f.get("same_cond_perf")])
    s["枠・展開"] = mean([f.get("course_waku_win"), f.get("course_style_win"), f.get("cond_style_win")])
    return s


def fit_cuts(scores: list[dict]) -> dict[str, list[float]]:
    """全馬の点数から、分野ごとの境目を決める。"""
    out = {}
    for k in FACTORS:
        v = sorted((x[k] for x in scores if x.get(k) is not None), reverse=True)
        if not v:
            continue
        acc, cuts = 0.0, []
        for sh in SHARE[:-1]:
            acc += sh
            cuts.append(v[min(len(v) - 1, int(acc * len(v)))])
        out[k] = cuts
    return out


def letter(k: str, v: Optional[float], cuts: Optional[dict] = None) -> str:
    if v is None:
        return "－"
    cs = (cuts or CUTS).get(k)
    if not cs:
        return "－"
    for i, c in enumerate(cs):
        if v >= c:
            return LETTERS[i]
    return "G"


def combo_text(horse) -> str:
    """馬と今回の騎手のコンビの近走成績 (表示だけ)。"""
    rs = [p for p in (horse.past or [])[:5] if p.get("finish") and F.jockey_same(horse.jockey or "", p.get("jockey") or "")]
    if not rs:
        return "初コンビ" if horse.past else "－"
    w = sum(1 for p in rs if p["finish"] == 1)
    t3 = sum(1 for p in rs if p["finish"] <= 3)
    return f"{len(rs)}戦{w}勝・3着内{t3}"


def race_grades(race, sm: dict, probs: dict[int, float], stats: dict, race_wk: Optional[dict] = None,
                cuts: Optional[dict] = None) -> list[dict]:
    """1レースの全馬: [{umaban, name, jockey, p, 総合, 騎手…枠・展開, コンビ}] (勝率の高い順)。"""
    bw = stats.get("base_win", 0.072)
    out = []
    for h in race.horses:
        f = (sm.get("features") or {}).get(h.umaban)
        if f is None:
            continue
        sc = factor_scores(f, h, (race_wk or {}).get(h.umaban), bw)
        p = probs.get(h.umaban, 0.0)
        row = dict(umaban=h.umaban, name=h.name, jockey=h.jockey, p=p, 総合=overall(p))
        row.update({k: letter(k, sc[k], cuts) for k in FACTORS})
        row["コンビ"] = combo_text(h)
        jn = int(f.get("jockey_form_n") or 0)   # 直近60日の素の勝率 (平滑化前に戻す)
        jw = round(f.get("jockey_form_win", bw) * (jn + 10) - 10 * bw)
        row["騎手60日"] = f"{jw / jn * 100:.0f}%" if jn else "－"
        row["_scores"] = sc
        out.append(row)
    out.sort(key=lambda r: -r["p"])
    return out
