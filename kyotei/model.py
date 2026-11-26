"""データ予想モデル（統計AI）。

過去レース（出走表・直前情報・結果）から学習した重みを使い、
6艇の 1着 / 2着 / 3着 を順番に確率計算して 3連単120通りの確率を出す。

  1着: 6艇から選ぶ多項ロジット
  2着: 1着艇を除く5艇から選ぶ（1着のコースとの組合せ補正つき）
  3着: 1・2着艇を除く4艇から選ぶ（1着・2着のコースとの組合せ補正つき）

重みは scripts/train_model.py が kyotei/model_weights.json に書き出す。
学習と本番で同じ build_features() を使うので、特徴量のズレは起きない。
"""
from __future__ import annotations

import itertools
import json
import math
import os

import numpy as np

WEIGHTS_PATH = os.path.join(os.path.dirname(__file__), "model_weights.json")

CLASSES = ["A1", "A2", "B1", "B2"]

FEATURE_NAMES = (
    [f"course{c}" for c in range(1, 7)]
    + ["nat_win", "loc_win", "nat_2", "nat_3"]
    + [f"class_{c}" for c in CLASSES]
    + ["avg_st", "f_count", "motor_2", "motor_3", "boat_2", "weight"]
    + [f"course{c}_x_nat_win" for c in range(1, 7)]
    + ["series_place", "moved_in", "moved_out"]
    + ["crs_win", "crs_top2", "crs_top3", "crs_st", "course1_x_crs_win"]
    # ---- ここから直前情報（展示）がある時だけ値が入る ----
    + [f"course{c}_x_ex_time" for c in range(1, 7)]
    + ["ex_st", "ex_st_flying", "tilt", "weight_adj"]
    + [f"course{c}_x_tailwind" for c in range(1, 7)]
    + [f"course{c}_x_headwind" for c in range(1, 7)]
    + [f"course{c}_x_crosswind" for c in range(1, 7)]
    + [f"course{c}_x_wave" for c in range(1, 7)]
    + ["st_vs_inner", "ex_time_rank1"]
)
N_FEATURES = len(FEATURE_NAMES)
_IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
N_PRE_FEATURES = _IDX["course1_x_ex_time"]  # これより前は出走表だけで決まる


# コース別の全国平均（1〜6コースの 1着率 / 2連対率 / 3連対率 / 平均ST）。選手のコース別成績はこれとの差で見る。
BASE_WIN = [0.555, 0.133, 0.129, 0.103, 0.061, 0.019]
BASE_TOP2 = [0.727, 0.386, 0.347, 0.270, 0.190, 0.081]
BASE_TOP3 = [0.815, 0.574, 0.547, 0.472, 0.376, 0.219]
BASE_ST = [0.151, 0.162, 0.157, 0.161, 0.165, 0.181]
SHRINK_N = 15.0  # 出走数が少ない選手の成績は平均に寄せる
RACER_STATS_PATH = os.path.join(os.path.dirname(__file__), "racer_stats.json")


def course_stat_values(stats, course):
    """選手のコース別成績 [出走, 1着, 2連対, 3連対, ST合計, ST回数] を、全国平均との差(出走数で補正済み)にする。"""
    row = (stats or {}).get(str(course)) or (stats or {}).get(course)
    if not row:
        return 0.0, 0.0, 0.0, 0.0
    n, w, t2, t3, st_sum, st_n = (list(row) + [0] * 6)[:6]
    c = course - 1
    d_win = (w + SHRINK_N * BASE_WIN[c]) / (n + SHRINK_N) - BASE_WIN[c]
    d_top2 = (t2 + SHRINK_N * BASE_TOP2[c]) / (n + SHRINK_N) - BASE_TOP2[c]
    d_top3 = (t3 + SHRINK_N * BASE_TOP3[c]) / (n + SHRINK_N) - BASE_TOP3[c]
    d_st = (st_sum + SHRINK_N * BASE_ST[c]) / (st_n + SHRINK_N) - BASE_ST[c]
    return d_win, d_top2, d_top3, d_st


_racer_stats_cache = {}


def load_racer_stats(path=RACER_STATS_PATH):
    if path not in _racer_stats_cache:
        try:
            with open(path, encoding="utf-8") as f:
                _racer_stats_cache[path] = json.load(f)
        except (OSError, ValueError):
            _racer_stats_cache[path] = {"racers": {}, "meta": {}}
    return _racer_stats_cache[path]


def attach_racer_stats(race, table=None):
    """各艇に course_stats（選手のコース別成績）を付ける。"""
    racers = (table or load_racer_stats()).get("racers", {})
    for b in race["boats"]:
        if not b.get("course_stats"):
            b["course_stats"] = racers.get(str(b.get("toban")), {})
    return race


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        f = float(v)
        if math.isnan(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _rel(values):
    """レース内平均との差。欠損(None)は平均扱い(=0)。"""
    known = [v for v in values if v is not None]
    if not known:
        return [0.0] * len(values)
    mean = sum(known) / len(known)
    return [0.0 if v is None else v - mean for v in values]


def wind_components(speed, direction):
    """公式サイトの風向番号(1-16, 17=無風)を 追い風 / 向かい風 / 横風 の強さに分解する。

    公式の図はスタンドが下・1マークが右。番号1が上向きで時計回りに22.5度ずつ。
    5 = 右向き = スタートから1マークへ吹く追い風、13 = 向かい風。
    """
    speed = _num(speed, 0.0) or 0.0
    direction = int(_num(direction, 0) or 0)
    if speed <= 0 or not 1 <= direction <= 16:
        return 0.0, 0.0, 0.0
    ang = math.radians((direction - 1) * 22.5)
    along = speed * math.sin(ang)
    cross = speed * math.cos(ang)
    return max(along, 0.0), max(-along, 0.0), abs(cross)


def wind_label(speed, direction):
    speed = _num(speed, 0.0) or 0.0
    direction = int(_num(direction, 0) or 0)
    if speed <= 0 or not 1 <= direction <= 16:
        return "無風"
    tail, head, cross = wind_components(speed, direction)
    along = tail - head
    if abs(along) >= 2.4 * cross:
        return "追い風" if along > 0 else "向かい風"
    if cross >= 2.4 * abs(along):
        return "横風"
    return "斜め追い風" if along > 0 else "斜め向かい風"


def series_score(series):
    """今節成績(着順のリスト)を -1(好調)〜+1(不調) 程度の値にする。出走が少ないほど0に寄せる。"""
    places = []
    for s in series or []:
        p = s.get("place") if isinstance(s, dict) else s
        p = _num(p)
        if p is not None and 1 <= p <= 6:
            places.append(p)
    if not places:
        return 0.0
    n = len(places)
    return (sum(places) / n - 3.5) * n / (n + 2.0) / 2.5


def resolve_courses(race, use_preview=True):
    """進入コース。スタート展示の進入が 1〜6 の並べ替えになっていればそれ、なければ枠なり。"""
    boats = sorted(race["boats"], key=lambda b: b["boat"])
    lanes = [b["boat"] for b in boats]
    pv = race.get("preview") if use_preview else None
    if pv:
        cs = []
        for b in boats:
            c = _num((pv.get("boats") or {}).get(b["boat"], {}).get("course"))
            cs.append(int(c) if c is not None else None)
        if sorted(c for c in cs if c is not None) == list(range(1, len(boats) + 1)) and len(boats) == 6:
            return cs
    return lanes


def has_preview(race):
    pv = race.get("preview")
    if not pv:
        return False
    n = sum(1 for b in (pv.get("boats") or {}).values() if _num(b.get("ex_time")))
    return n >= 4


def build_features(race, use_preview=True):
    """6艇ぶんの特徴量行列 (6, N_FEATURES) と進入コース配列を返す。"""
    boats = sorted(race["boats"], key=lambda b: b["boat"])
    if len(boats) != 6:
        raise ValueError("6艇そろっていないレースは計算できません")
    use_preview = use_preview and has_preview(race)
    courses = resolve_courses(race, use_preview)
    X = np.zeros((6, N_FEATURES), dtype=np.float64)

    nat_win = _rel([_num(b.get("nat_win")) for b in boats])
    loc_raw = []
    for b in boats:
        lw = _num(b.get("loc_win"))
        loc_raw.append(lw if lw else _num(b.get("nat_win")))
    loc_win = _rel(loc_raw)
    nat_2 = _rel([_num(b.get("nat_2")) for b in boats])
    nat_3 = _rel([_num(b.get("nat_3")) for b in boats])
    avg_st = _rel([(_num(b.get("avg_st")) or None) for b in boats])
    motor_2 = _rel([(_num(b.get("motor_2")) or None) for b in boats])
    motor_3 = _rel([(_num(b.get("motor_3")) or None) for b in boats])
    boat_2 = _rel([(_num(b.get("boat_2")) or None) for b in boats])
    weight = _rel([(_num(b.get("weight")) or None) for b in boats])

    for i, b in enumerate(boats):
        c = courses[i]
        X[i, c - 1] = 1.0
        X[i, _IDX["nat_win"]] = nat_win[i]
        X[i, _IDX["loc_win"]] = loc_win[i]
        X[i, _IDX["nat_2"]] = nat_2[i] / 10.0
        X[i, _IDX["nat_3"]] = nat_3[i] / 10.0
        cls = str(b.get("cls") or "").upper()
        if cls in CLASSES:
            X[i, _IDX[f"class_{cls}"]] = 1.0
        X[i, _IDX["avg_st"]] = avg_st[i] * 10.0
        X[i, _IDX["f_count"]] = min(_num(b.get("f"), 0.0) or 0.0, 2.0)
        X[i, _IDX["motor_2"]] = motor_2[i] / 10.0
        X[i, _IDX["motor_3"]] = motor_3[i] / 10.0
        X[i, _IDX["boat_2"]] = boat_2[i] / 10.0
        X[i, _IDX["weight"]] = weight[i] / 5.0
        X[i, _IDX[f"course{c}_x_nat_win"]] = nat_win[i]
        X[i, _IDX["series_place"]] = series_score(b.get("series"))
        if c < b["boat"]:
            X[i, _IDX["moved_in"]] = 1.0
        elif c > b["boat"]:
            X[i, _IDX["moved_out"]] = 1.0
        d_win, d_top2, d_top3, d_st = course_stat_values(b.get("course_stats"), c)
        X[i, _IDX["crs_win"]] = d_win * 10.0
        X[i, _IDX["crs_top2"]] = d_top2 * 10.0
        X[i, _IDX["crs_top3"]] = d_top3 * 10.0
        X[i, _IDX["crs_st"]] = d_st * 10.0
        if c == 1:
            X[i, _IDX["course1_x_crs_win"]] = d_win * 10.0

    if not use_preview:
        return X, courses

    pv = race["preview"]
    pb = pv.get("boats") or {}
    ex_raw = [(_num(pb.get(b["boat"], {}).get("ex_time")) or None) for b in boats]
    ex_time = _rel(ex_raw)
    st_raw = [_num(pb.get(b["boat"], {}).get("st")) for b in boats]
    st_clip = [None if s is None else max(min(s, 0.40), -0.05) for s in st_raw]
    ex_st = _rel(st_clip)
    tail, head, cross = wind_components(pv.get("wind"), pv.get("wind_dir"))
    wave = min(_num(pv.get("wave"), 0.0) or 0.0, 20.0)
    st_by_course = {courses[i]: st_clip[i] for i in range(6)}
    known_ex = [v for v in ex_raw if v is not None]
    best_ex = min(known_ex) if known_ex else None

    for i, b in enumerate(boats):
        c = courses[i]
        p = pb.get(b["boat"], {})
        X[i, _IDX[f"course{c}_x_ex_time"]] = max(min(ex_time[i], 0.3), -0.3) * 10.0
        X[i, _IDX["ex_st"]] = ex_st[i] * 10.0
        if st_raw[i] is not None and st_raw[i] < 0:
            X[i, _IDX["ex_st_flying"]] = 1.0
        X[i, _IDX["tilt"]] = _num(p.get("tilt"), 0.0) or 0.0
        X[i, _IDX["weight_adj"]] = _num(p.get("weight_adj"), 0.0) or 0.0
        X[i, _IDX[f"course{c}_x_tailwind"]] = tail / 5.0
        X[i, _IDX[f"course{c}_x_headwind"]] = head / 5.0
        X[i, _IDX[f"course{c}_x_crosswind"]] = cross / 5.0
        X[i, _IDX[f"course{c}_x_wave"]] = wave / 5.0
        if c >= 2 and st_clip[i] is not None and st_by_course.get(c - 1) is not None:
            X[i, _IDX["st_vs_inner"]] = (st_by_course[c - 1] - st_clip[i]) * 10.0
        if best_ex is not None and ex_raw[i] is not None and ex_raw[i] <= best_ex + 1e-9:
            X[i, _IDX["ex_time_rank1"]] = 1.0
    return X, courses


# ---------------------------------------------------------------------------
# 推論
# ---------------------------------------------------------------------------
_weights_cache = {}


def load_weights(path=WEIGHTS_PATH):
    if path not in _weights_cache:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        models = {}
        for name in ("pre", "live"):
            m = raw["models"][name]
            models[name] = {k: np.asarray(v, dtype=np.float64) for k, v in m.items()}
        _weights_cache[path] = {"models": models, "meta": raw.get("meta", {})}
    return _weights_cache[path]


def _softmax(u, mask):
    u = np.where(mask, u, -np.inf)
    u = u - np.max(u)
    e = np.where(mask, np.exp(u), 0.0)
    return e / e.sum()


def trifecta_probs(X, courses, jcd, w):
    """3連単120通りの確率 P[i,j,k]（i,j,k は艇番-1）を返す。"""
    v = int(jcd) - 1
    ci = np.asarray(courses) - 1
    u1 = X @ w["w1"] + w["v1"][v, ci]
    u2 = X @ w["w2"] + w["v2"][v, ci]
    u3 = X @ w["w3"] + w["v3"][v, ci]
    allm = np.ones(6, dtype=bool)
    p1 = _softmax(u1, allm)
    P = np.zeros((6, 6, 6))
    for i in range(6):
        m2 = allm.copy()
        m2[i] = False
        p2 = _softmax(u2 + w["p12"][ci[i], ci], m2)
        for j in range(6):
            if j == i:
                continue
            m3 = m2.copy()
            m3[j] = False
            p3 = _softmax(u3 + w["p13"][ci[i], ci] + w["p23"][ci[j], ci], m3)
            P[i, j, :] = p1[i] * p2[j] * p3
    return P


def predict(race, use_preview=True, weights=None):
    """レース辞書から予想結果を作る。

    戻り値:
      mode      : "live"(展示込み) / "pre"(出走表のみ)
      courses   : 予想に使った進入コース（艇番順）
      P         : 3連単確率 6x6x6
      win       : 各艇の1着確率、top2 / top3 : 2連対・3連対確率
      trifecta  : [(確率, (1着,2着,3着)), ...] 確率の高い順
    """
    weights = weights or load_weights()
    live = use_preview and has_preview(race)
    X, courses = build_features(race, use_preview=live)
    w = weights["models"]["live" if live else "pre"]
    P = trifecta_probs(X, courses, race["jcd"], w)
    out = {"mode": "live" if live else "pre", "courses": courses, "market": False}
    out.update(summarize(P))
    return out


def summarize(P) -> dict:
    """3連単確率 P(6x6x6) から、各艇の着順確率と、確率順の買い目一覧を作る。"""
    P = np.asarray(P, dtype=np.float64)
    win = P.sum(axis=(1, 2))
    second = P.sum(axis=(0, 2))
    third = P.sum(axis=(0, 1))
    combos = [
        (float(P[i, j, k]), (i + 1, j + 1, k + 1))
        for i, j, k in itertools.permutations(range(6), 3)
    ]
    combos.sort(reverse=True)
    return {
        "P": P,
        "win": [float(x) for x in win],
        "second": [float(x) for x in second],
        "third": [float(x) for x in third],
        "top2": [float(x) for x in win + second],
        "top3": [float(x) for x in win + second + third],
        "trifecta": combos,
    }


# ---------------------------------------------------------------------------
# オッズ（世間の評価）を予想に取り込む
# ---------------------------------------------------------------------------
# 確定オッズつきの検証では、的中組合せの予測はオッズのほうがこのモデルより正確だった。
# そこで、展示後（オッズが固まってくる時間帯）は、モデルの確率とオッズから逆算した確率を掛け合わせる。
# 0 = モデルだけ、1 = オッズだけ。締切前のオッズは確定オッズほど正確ではないので、半々より少しモデル寄りにはしない。
MARKET_WEIGHT = 0.5


def market_probs(odds) -> np.ndarray | None:
    """3連単オッズ120通りから、世間が見ている確率（1/オッズ を合計1にしたもの）を作る。そろっていなければ None。"""
    Q = np.zeros((6, 6, 6))
    for i, j, k in itertools.permutations(range(6), 3):
        o = (odds or {}).get(f"{i + 1}-{j + 1}-{k + 1}")
        if not o or o <= 0:
            return None
        Q[i, j, k] = 1.0 / o
    return Q / Q.sum()


def blend_with_market(P, odds, weight: float = MARKET_WEIGHT) -> np.ndarray | None:
    """モデルの確率 P とオッズを混ぜた確率（幾何平均）。オッズがそろっていなければ None。"""
    Q = market_probs(odds)
    if Q is None or weight <= 0:
        return None
    B = np.where(Q > 0, np.power(np.maximum(np.asarray(P, dtype=np.float64), 1e-9), 1 - weight) * np.power(np.maximum(Q, 1e-12), weight), 0.0)
    return B / B.sum()
