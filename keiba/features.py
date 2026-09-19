"""統計モデルの特徴量。学習 (backtest/train.py) と本番 (statmodel.py) で同じ関数を使う。

使う情報は出走時点で分かるものだけ:
  馬柱の近5走 (着順・着差・人気・クラス・距離・馬場・上り) / 血統 (父・母父) /
  騎手・厩舎 / 斤量・馬体重・間隔・脚質 / レース条件 (芝ダ・距離・場・馬場・頭数・クラス)
血統・騎手・厩舎は、学習期間の成績表 (勝率・3着内率を平滑化) を引いて数値にする。
"""

from __future__ import annotations

import re
from typing import Optional

RECENCY = [1.0, 0.8, 0.6, 0.45, 0.3]
GRADE_RANK = {"GI": 6, "GII": 5, "GIII": 4, "OP": 3, "L": 3, "3勝": 2, "2勝": 1.5, "1勝": 1, "未勝利": 0, "新馬": 0}
VENUE_CODE = {"札幌": 1, "函館": 2, "福島": 3, "新潟": 4, "東京": 5, "中山": 6, "中京": 7, "京都": 8, "阪神": 9, "小倉": 10}
COND_CODE = {"良": 0, "稍": 1, "稍重": 1, "重": 2, "不": 3, "不良": 3}
STYLE_CODE = {"逃": 1, "先": 2, "差": 3, "追": 4}
SEX_CODE = {"牡": 0, "牝": 1, "セ": 2}

# 血統・騎手・厩舎の成績を平滑化する事前重み (この頭数ぶん全体平均を混ぜる)
PRIOR_N = 30

FEATURES = [
    # レース条件
    "surface", "distance", "band", "venue", "cond", "heads", "cls_rank", "turn_right",
    # 馬
    "waku", "umaban", "umaban_ratio", "sex", "age", "weight", "weight_diff", "body_weight", "bw_delta",
    "interval_w", "is_debut", "style", "n_nige", "n_front",
    # 近走
    "n_past", "perf_w", "perf_cls", "last_fin", "last_margin", "last_ninki", "last_cls_diff", "last_agari",
    "best_fin", "mean_margin", "wins", "top3", "outrun", "class_up",
    "same_surf_n", "same_surf_perf", "same_band_n", "same_band_perf", "same_venue_n", "same_venue_perf",
    "same_cond_n", "same_cond_perf", "big_field_perf",
    "last_tidx", "best_tidx", "mean_tidx", "last_agari_idx",
    # 血統・人
    "sire_win", "sire_top3", "sire_n", "sire_sb_win", "sire_sb_top3", "sire_sb_n",
    "damsire_win", "damsire_top3", "damsire_s_win", "damsire_s_n",
    "jockey_win", "jockey_top3", "jockey_n", "trainer_win", "trainer_top3", "trainer_n",
]
CATEGORICAL = ["surface", "band", "venue", "cond", "sex", "style"]


def dist_band(d: int) -> int:
    if d <= 1400:
        return 0
    if d <= 1800:
        return 1
    if d <= 2200:
        return 2
    return 3


def class_rank(text: str) -> float:
    t = text or ""
    if re.search(r"G1|GI\b|GＩ|ＧⅠ", t):
        return 6
    if re.search(r"G2|GII\b|GⅡ", t):
        return 5
    if re.search(r"G3|GIII\b|GⅢ", t):
        return 4
    if "オープン" in t or re.search(r"\bOP\b|\(L\)|リステッド", t):
        return 3
    if "3勝" in t or "1600万" in t:
        return 2
    if "2勝" in t or "1000万" in t:
        return 1.5
    if "1勝" in t or "500万" in t:
        return 1
    return 0


def past_rank(p: dict) -> float:
    g = p.get("grade") or ""
    if g in GRADE_RANK:
        return GRADE_RANK[g]
    return class_rank(p.get("race_name", ""))


def interval_weeks(interval: str, rest_note: str) -> Optional[int]:
    m = re.search(r"中(\d+)週", interval or "")
    if m:
        return int(m.group(1))
    if "連闘" in (interval or ""):
        return 0
    m = re.search(r"(\d+)ヵ月", rest_note or "")
    if m:
        return int(m.group(1)) * 4
    return None


def parse_body_weight(text: str) -> tuple[Optional[float], Optional[float]]:
    m = re.search(r"(\d{3})\s*\(\s*([+\-]?\d+)\s*\)", text or "")
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d{3})", text or "")
    return (float(m.group(1)), None) if m else (None, None)


def time_sec(text: str) -> Optional[float]:
    m = re.match(r"(\d+):(\d\d\.\d)", text or "")
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"(\d\d\.\d)$", text or "")
    return float(m.group(1)) if m else None


def time_key(venue: str, surface: str, distance, condition: str) -> str:
    return f"{venue}|{surface}|{distance}|{condition}"


def time_index(stats: dict, venue: str, surface: str, distance, condition: str, t: Optional[float]) -> Optional[float]:
    """走破時計の指数。そのコース・馬場の標準 (中央値) より何秒速いかを標準偏差で割ったもの。速いほど大きい。"""
    if t is None:
        return None
    tbl = stats.get("time_std") or {}
    v = tbl.get(time_key(venue, surface, distance, condition)) or tbl.get(time_key(venue, surface, distance, ""))
    if not v:
        return None
    med, sd = v
    return (med - t) / max(sd, 0.3)


def _rate(tbl: dict, key: str, kind: str, base: float) -> tuple[float, float]:
    """平滑化した率と件数。tbl[key] = [n, wins, top3]"""
    v = tbl.get(key)
    if not v:
        return base, 0.0
    n, wins, top3 = v
    num = wins if kind == "win" else top3
    return (num + PRIOR_N * base) / (n + PRIOR_N), float(n)


def _perf(p: dict) -> Optional[float]:
    fin, heads = p.get("finish"), p.get("heads")
    if not fin or not heads or heads < 2:
        return None
    q = 1.0 - (fin - 1) / (heads - 1)
    margin = p.get("margin")
    if margin is None:
        m_part = q
    elif margin <= 0:
        m_part = min(1.2, 1.0 + (-margin) * 0.2)
    else:
        m_part = max(0.0, 1.0 - margin / 1.5)
    return 0.55 * q + 0.45 * m_part


def race_context(race: dict, runners: list[dict]) -> dict:
    """レース単位で共有する値。race: surface, distance, venue, condition, heads, cls, grade, name, turn"""
    styles = [r.get("style") or "" for r in runners]
    weights = [r.get("weight") for r in runners if r.get("weight")]
    return dict(
        surface={"芝": 0, "ダ": 1, "障": 2}.get(race.get("surface", ""), 1),
        distance=int(race.get("distance") or 0),
        band=dist_band(int(race.get("distance") or 0)),
        venue=VENUE_CODE.get(race.get("venue", ""), 0),
        cond=COND_CODE.get(race.get("condition", ""), 0),
        heads=int(race.get("heads") or len(runners)),
        cls_rank=class_rank(" ".join([race.get("cls", ""), race.get("grade", ""), race.get("name", "")])),
        turn_right=1 if race.get("turn") == "右" else 0,
        n_nige=sum(1 for s in styles if s == "逃"),
        n_front=sum(1 for s in styles if s in ("逃", "先")),
        mean_weight=sum(weights) / len(weights) if weights else 0.0,
        surface_str=race.get("surface", ""),
        venue_str=race.get("venue", ""),
        cond_str=race.get("condition", ""),
    )


def runner_features(r: dict, ctx: dict, stats: dict) -> dict:
    """r: waku, umaban, sex_age, weight, jockey, trainer, sire, damsire, style, interval, rest_note,
    body_weight, past(list)。stats: {sire, sire_sb, damsire, damsire_s, jockey, trainer, base_win, base_top3}"""
    f: dict = {k: ctx[k] for k in ("surface", "distance", "band", "venue", "cond", "heads", "cls_rank",
                                   "turn_right", "n_nige", "n_front")}
    heads = ctx["heads"] or 1
    f["waku"] = r.get("waku") or 0
    f["umaban"] = r.get("umaban") or 0
    f["umaban_ratio"] = f["umaban"] / heads
    sa = r.get("sex_age") or ""
    f["sex"] = SEX_CODE.get(sa[:1], 0)
    m = re.search(r"(\d+)", sa)
    f["age"] = int(m.group(1)) if m else 0
    f["weight"] = float(r.get("weight") or 0)
    f["weight_diff"] = f["weight"] - ctx["mean_weight"] if f["weight"] and ctx["mean_weight"] else 0.0
    bw, bwd = parse_body_weight(r.get("body_weight") or "")
    f["body_weight"] = bw if bw is not None else float("nan")
    f["bw_delta"] = bwd if bwd is not None else float("nan")
    iw = interval_weeks(r.get("interval") or "", r.get("rest_note") or "")
    f["interval_w"] = iw if iw is not None else float("nan")
    past = [p for p in (r.get("past") or [])[:5] if p.get("finish") and p.get("heads")]
    f["is_debut"] = 1 if not past else 0
    f["style"] = STYLE_CODE.get(r.get("style") or "", 0)

    f["n_past"] = len(past)
    nan = float("nan")
    for k in ("perf_w", "perf_cls", "last_fin", "last_margin", "last_ninki", "last_cls_diff", "last_agari",
              "best_fin", "mean_margin", "outrun", "class_up", "same_surf_perf", "same_band_perf",
              "same_venue_perf", "same_cond_perf", "big_field_perf", "last_tidx", "best_tidx", "mean_tidx", "last_agari_idx"):
        f[k] = nan
    f["wins"] = f["top3"] = 0
    f["same_surf_n"] = f["same_band_n"] = f["same_venue_n"] = f["same_cond_n"] = 0
    if past:
        tw = acc = acc_c = 0.0
        outrun, n_out = 0.0, 0
        margins, fins = [], []
        groups = {"surf": [], "band": [], "venue": [], "cond": [], "big": []}
        for i, p in enumerate(past):
            perf = _perf(p)
            if perf is None:
                continue
            w = RECENCY[i] if i < len(RECENCY) else 0.2
            diff = past_rank(p) - ctx["cls_rank"]
            acc += w * perf
            acc_c += w * (perf + 0.08 * max(-2.0, min(2.0, diff)))
            tw += w
            fins.append((p["finish"] - 1) / max(1, p["heads"] - 1))
            if p.get("margin") is not None:
                margins.append(p["margin"])
            if p.get("ninki"):
                outrun += (p["ninki"] - 1) / max(1, p["heads"] - 1) - (p["finish"] - 1) / max(1, p["heads"] - 1)
                n_out += 1
            if p["finish"] == 1:
                f["wins"] += 1
            if p["finish"] <= 3:
                f["top3"] += 1
            if p.get("surface") == ctx["surface_str"]:
                groups["surf"].append(perf)
            if p.get("distance") and dist_band(p["distance"]) == ctx["band"]:
                groups["band"].append(perf)
            if p.get("venue") == ctx["venue_str"]:
                groups["venue"].append(perf)
            if p.get("condition") and (COND_CODE.get(p["condition"], 0) >= 2) == (ctx["cond"] >= 2):
                groups["cond"].append(perf)
            if p["heads"] >= 14:
                groups["big"].append(perf)
        if tw:
            f["perf_w"] = acc / tw
            f["perf_cls"] = acc_c / tw
        p0 = past[0]
        f["last_fin"] = (p0["finish"] - 1) / max(1, p0["heads"] - 1)
        f["last_margin"] = p0.get("margin") if p0.get("margin") is not None else nan
        f["last_ninki"] = (p0["ninki"] - 1) / max(1, p0["heads"] - 1) if p0.get("ninki") else nan
        f["last_cls_diff"] = past_rank(p0) - ctx["cls_rank"]
        f["last_agari"] = p0.get("agari") if p0.get("agari") else nan
        f["best_fin"] = min(fins) if fins else nan
        f["mean_margin"] = sum(margins) / len(margins) if margins else nan
        f["outrun"] = outrun / n_out if n_out else nan
        f["class_up"] = ctx["cls_rank"] - max(past_rank(p) for p in past[:3])
        for g, key in (("surf", "same_surf"), ("band", "same_band"), ("venue", "same_venue"), ("cond", "same_cond")):
            f[key + "_n"] = len(groups[g])
            f[key + "_perf"] = sum(groups[g]) / len(groups[g]) if groups[g] else nan
        f["big_field_perf"] = sum(groups["big"]) / len(groups["big"]) if groups["big"] else nan
        tidx = []
        for i, p in enumerate(past):
            ti = time_index(stats, p.get("venue", ""), p.get("surface", ""), p.get("distance"), p.get("condition", ""),
                            time_sec(p.get("time", "")))
            if ti is not None and -6 < ti < 6:
                tidx.append((i, ti))
        if tidx:
            f["last_tidx"] = tidx[0][1] if tidx[0][0] == 0 else nan
            f["best_tidx"] = max(t for _, t in tidx)
            ws = [(RECENCY[i] if i < len(RECENCY) else 0.2) for i, _ in tidx]
            f["mean_tidx"] = sum(w * t for w, (_, t) in zip(ws, tidx)) / sum(ws)
        ag = (stats.get("agari_std") or {}).get(f"{ctx['surface_str']}|{ctx['band']}")
        if ag and p0.get("agari"):
            f["last_agari_idx"] = (ag[0] - p0["agari"]) / max(ag[1], 0.2)

    bw_, bt_ = stats.get("base_win", 0.08), stats.get("base_top3", 0.24)
    sire, damsire = r.get("sire") or "", r.get("damsire") or ""
    sb_key = f"{sire}|{ctx['surface_str']}|{ctx['band']}"
    f["sire_win"], f["sire_n"] = _rate(stats.get("sire", {}), sire, "win", bw_)
    f["sire_top3"], _ = _rate(stats.get("sire", {}), sire, "top3", bt_)
    f["sire_sb_win"], f["sire_sb_n"] = _rate(stats.get("sire_sb", {}), sb_key, "win", bw_)
    f["sire_sb_top3"], _ = _rate(stats.get("sire_sb", {}), sb_key, "top3", bt_)
    f["damsire_win"], _ = _rate(stats.get("damsire", {}), damsire, "win", bw_)
    f["damsire_top3"], _ = _rate(stats.get("damsire", {}), damsire, "top3", bt_)
    f["damsire_s_win"], f["damsire_s_n"] = _rate(stats.get("damsire_s", {}), f"{damsire}|{ctx['surface_str']}", "win", bw_)
    jk = re.sub(r"[▲△☆★◇]", "", r.get("jockey") or "")
    f["jockey_win"], f["jockey_n"] = _rate(stats.get("jockey", {}), jk, "win", bw_)
    f["jockey_top3"], _ = _rate(stats.get("jockey", {}), jk, "top3", bt_)
    tr = (r.get("trainer") or "").split()[-1] if r.get("trainer") else ""
    f["trainer_win"], f["trainer_n"] = _rate(stats.get("trainer", {}), tr, "win", bw_)
    f["trainer_top3"], _ = _rate(stats.get("trainer", {}), tr, "top3", bt_)
    return f


def build_stats(rows) -> dict:
    """rows: iterable of dict(sire, damsire, jockey, trainer, surface, distance, finish, venue, condition, time, past)。
    学習期間から成績表と標準時計表を作る。"""
    import statistics
    tables = {k: {} for k in ("sire", "sire_sb", "damsire", "damsire_s", "jockey", "trainer")}
    n_all = w_all = t_all = 0
    times: dict[str, list[float]] = {}
    agaris: dict[str, list[float]] = {}

    def add(tbl, key, fin):
        if not key:
            return
        v = tbl.setdefault(key, [0, 0, 0])
        v[0] += 1
        v[1] += 1 if fin == 1 else 0
        v[2] += 1 if fin <= 3 else 0

    for r in rows:
        fin = r.get("finish")
        if not fin:
            continue
        n_all += 1
        w_all += 1 if fin == 1 else 0
        t_all += 1 if fin <= 3 else 0
        band = dist_band(int(r.get("distance") or 0))
        add(tables["sire"], r.get("sire"), fin)
        add(tables["sire_sb"], f"{r.get('sire')}|{r.get('surface')}|{band}", fin)
        add(tables["damsire"], r.get("damsire"), fin)
        add(tables["damsire_s"], f"{r.get('damsire')}|{r.get('surface')}", fin)
        add(tables["jockey"], re.sub(r"[▲△☆★◇]", "", r.get("jockey") or ""), fin)
        tr = (r.get("trainer") or "").split()[-1] if r.get("trainer") else ""
        add(tables["trainer"], tr, fin)
        t = time_sec(r.get("time") or "")
        if t and r.get("venue"):
            times.setdefault(time_key(r["venue"], r.get("surface", ""), r.get("distance"), r.get("condition", "")), []).append(t)
            times.setdefault(time_key(r["venue"], r.get("surface", ""), r.get("distance"), ""), []).append(t)
        # 馬柱の近走 (2023年より前の走りも含む) からも標準時計を集める
        for p in (r.get("past") or []) if isinstance(r.get("past"), list) else []:
            tp = time_sec(p.get("time") or "")
            if tp and p.get("venue") and p.get("distance"):
                times.setdefault(time_key(p["venue"], p.get("surface", ""), p["distance"], p.get("condition", "")), []).append(tp)
                times.setdefault(time_key(p["venue"], p.get("surface", ""), p["distance"], ""), []).append(tp)
            if p.get("agari") and p.get("distance"):
                agaris.setdefault(f"{p.get('surface','')}|{dist_band(int(p['distance']))}", []).append(float(p["agari"]))
    tables["time_std"] = {k: [round(statistics.median(v), 2), round(statistics.pstdev(v), 3)]
                          for k, v in times.items() if len(v) >= 30}
    tables["agari_std"] = {k: [round(statistics.median(v), 2), round(statistics.pstdev(v), 3)]
                           for k, v in agaris.items() if len(v) >= 100}
    # 件数の少ない鍵は捨てて表を小さくする (平滑化で全体平均に近いので落としても影響が小さい)
    for k in ("sire", "sire_sb", "damsire", "damsire_s", "jockey", "trainer"):
        tables[k] = {key: v for key, v in tables[k].items() if v[0] >= 5}
    tables["base_win"] = w_all / n_all if n_all else 0.08
    tables["base_top3"] = t_all / n_all if n_all else 0.24
    return tables


# ---------------------------------------------------------------- 市場 (単勝オッズ) を土台にした補正モデル用
MARKET_FEATURES = ["mkt_logp", "mkt_rank", "mkt_top_gap", "mkt_n"]


def market_features(odds_by_umaban: dict[int, Optional[float]]) -> dict[int, dict]:
    """単勝オッズ (馬番→オッズ) から、市場確率の対数・人気順位 (頭数比)・1番人気との差を作る。
    オッズが無い馬は場の最低確率の半分として扱う。"""
    import math
    valid = {u: 1.0 / o for u, o in odds_by_umaban.items() if o and o > 0}
    if not valid:
        return {u: dict(mkt_logp=float("nan"), mkt_rank=float("nan"), mkt_top_gap=float("nan"), mkt_n=0)
                for u in odds_by_umaban}
    floor = min(valid.values()) / 2
    inv = {u: valid.get(u, floor) for u in odds_by_umaban}
    z = sum(inv.values())
    p = {u: v / z for u, v in inv.items()}
    order = sorted(p, key=lambda u: -p[u])
    top = math.log(p[order[0]])
    n = len(order)
    return {u: dict(mkt_logp=math.log(p[u]), mkt_rank=(order.index(u) + 1) / n, mkt_top_gap=top - math.log(p[u]),
                    mkt_n=len(valid)) for u in odds_by_umaban}
