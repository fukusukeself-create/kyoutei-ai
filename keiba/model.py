"""近5走・騎手・脚質・斤量・馬場から各馬の勝率を推定する簡易レーティング。

学習済みモデルではなく、競馬予想で一般に使われる着眼点を点数化したもの。
オッズが出ている場合は市場の確率と混ぜる (市場は集合知として強いので重み 0.6)。
AI (Gemini) の評価があれば、その評価で確率を上下させる。
"""

from __future__ import annotations

import math
import re
from typing import Optional

from scraper import GRADE_RANK, Race, Horse

# 近走の重み (前走ほど重い)
RECENCY = [1.0, 0.8, 0.6, 0.45, 0.3]

# リーディング上位の騎手 (加点は小さい。過信しない)
TOP_JOCKEYS = {
    "ルメール", "川田", "戸崎圭", "戸崎", "横山武", "坂井", "松山", "岩田望", "武豊",
    "北村友", "西村淳", "鮫島駿", "団野", "菅原明", "三浦", "モレイラ", "Ｍデム", "レーン",
    "佐々木", "藤岡佑", "横山和", "津村", "幸", "岩田康", "浜中", "池添", "田辺", "丹内",
}

AI_RATING_MULT = {5: 1.40, 4: 1.15, 3: 1.0, 2: 0.85, 1: 0.60}


def _class_rank(text: str) -> float:
    """今日のレースのクラスを 0(未勝利)〜6(GI) に。"""
    t = text or ""
    if re.search(r"G1|GI\b|GＩ", t):
        return 6
    if re.search(r"G2|GII\b|GⅡ", t):
        return 5
    if re.search(r"G3|GIII\b|GⅢ", t):
        return 4
    if "オープン" in t or "OP" in t or "L" == t.strip():
        return 3
    if "3勝" in t or "1600万" in t:
        return 2
    if "2勝" in t or "1000万" in t:
        return 1.5
    if "1勝" in t or "500万" in t:
        return 1
    return 0


def _past_grade_rank(p: dict) -> float:
    g = p.get("grade") or ""
    if g in GRADE_RANK:
        return GRADE_RANK[g]
    return _class_rank(p.get("race_name", ""))


def _interval_weeks(text: str) -> Optional[int]:
    m = re.search(r"中(\d+)週", text or "")
    if m:
        return int(m.group(1))
    if "連闘" in (text or ""):
        return 0
    return None


def _body_weight_delta(text: str) -> Optional[int]:
    m = re.search(r"\(([+\-]?\d+)\)", text or "")
    return int(m.group(1)) if m else None


def horse_score(h: Horse, race: Race, today_rank: float, mean_weight: float) -> tuple[float, list[str]]:
    """点数と、その根拠の短い箇条書きを返す。"""
    notes: list[str] = []
    if h.past:
        total_w = 0.0
        acc = 0.0
        outrun = 0.0
        n_out = 0
        for i, p in enumerate(h.past[:5]):
            w = RECENCY[i] if i < len(RECENCY) else 0.2
            fin, heads = p.get("finish"), p.get("heads")
            if not fin or not heads or heads < 2:
                continue
            q = 1.0 - (fin - 1) / (heads - 1)                    # 着順 (1着=1, 最下位=0)
            margin = p.get("margin")
            if margin is None:
                m_part = q
            elif margin <= 0:
                m_part = min(1.2, 1.0 + (-margin) * 0.2)        # 勝ち馬。着差が大きいほど加点
            else:
                m_part = max(0.0, 1.0 - margin / 1.5)           # 1.5秒差以上は 0
            perf = 0.55 * q + 0.45 * m_part
            # クラス差: 格上で走った内容は価値が高く、格下は割り引く
            diff = _past_grade_rank(p) - today_rank
            perf += 0.08 * max(-2.0, min(2.0, diff))
            # 条件が違う走りは参考度を下げる
            if p.get("surface") and race.surface and p["surface"] != race.surface:
                w *= 0.5
            if p.get("distance") and race.distance and abs(p["distance"] - race.distance) > 400:
                w *= 0.7
            acc += w * perf
            total_w += w
            if p.get("ninki") and heads:
                # 人気より着順が良ければプラス (妙味の芽)
                outrun += ((p["ninki"] - 1) / (heads - 1)) - ((fin - 1) / (heads - 1))
                n_out += 1
        base = acc / total_w if total_w else 0.45
        if n_out:
            base += 0.05 * max(-1.0, min(1.0, outrun / n_out))
        best = min((p.get("finish") or 99) for p in h.past[:3])
        if best <= 3:
            notes.append(f"近3走内に{best}着")
        if h.past[0].get("finish") == 1:
            notes.append("前走勝ち")
        if any((_past_grade_rank(p) - today_rank) >= 1 and (p.get("finish") or 99) <= 5 for p in h.past[:3]):
            notes.append("格上で掲示板")
    else:
        base = 0.45
        notes.append("キャリア無し(未知数)")

    score = base

    # 休養明け / 間隔
    wk = _interval_weeks(h.interval)
    if wk is not None and wk >= 10:
        score -= 0.04
        notes.append("休み明け")
    elif "ヵ月" in (h.rest_note or "") and re.search(r"([3-9]|\d{2,})ヵ月", h.rest_note):
        score -= 0.04
        notes.append("休み明け")

    # 騎手
    jk = h.jockey.replace("▲", "").replace("△", "").replace("☆", "").replace("★", "").replace("◇", "")
    if jk in TOP_JOCKEYS:
        score += 0.03
        notes.append("上位騎手")

    # 斤量 (平均より重いほど不利)
    if h.weight and mean_weight:
        score -= 0.01 * (h.weight - mean_weight)

    # 馬体重の急変
    d = _body_weight_delta(h.body_weight)
    if d is not None and abs(d) >= 12:
        score -= 0.03
        notes.append(f"馬体重{d:+d}kg")

    # 外枠の多頭数短距離 (芝) はやや不利
    if race.surface == "芝" and race.distance <= 1600 and race.heads >= 16 and h.umaban >= 14:
        score -= 0.02

    if "取消" in (h.rest_note or "") or "除外" in (h.rest_note or ""):
        score = -9
        notes = ["出走取消"]
    return score, notes


def pace_adjust(race: Race, scores: dict[int, float]) -> dict[int, float]:
    """脚質構成から展開の恩恵を加減する。逃げ馬が1頭なら楽逃げ、先行馬多数なら差し有利。"""
    styles = {h.umaban: h.style for h in race.horses}
    n_nige = sum(1 for s in styles.values() if s == "逃")
    n_front = sum(1 for s in styles.values() if s in ("逃", "先"))
    out = dict(scores)
    for u, s in styles.items():
        if out.get(u, 0) < -1:
            continue
        if n_nige == 1 and s == "逃":
            out[u] += 0.04
        elif n_nige >= 3 and s == "逃":
            out[u] -= 0.03
        if n_front >= max(5, len(styles) * 0.45) and s in ("差", "追"):
            out[u] += 0.02
    return out


def softmax(scores: dict[int, float], temp: float = 0.13) -> dict[int, float]:
    live = {u: s for u, s in scores.items() if s > -1}
    if not live:
        return {}
    mx = max(live.values())
    ex = {u: math.exp((s - mx) / temp) for u, s in live.items()}
    z = sum(ex.values())
    return {u: v / z for u, v in ex.items()}


def market_probs(win_odds: dict[str, float], umabans: list[int]) -> dict[int, float]:
    """単勝オッズから控除率を取り除いた市場確率。"""
    raw = {}
    for u in umabans:
        o = win_odds.get(f"{u:02d}")
        if o and o > 0:
            raw[u] = 1.0 / o
    z = sum(raw.values())
    return {u: v / z for u, v in raw.items()} if z else {}


def blend(model: dict[int, float], market: dict[int, float], w_market: float = 0.6) -> dict[int, float]:
    if not market:
        return dict(model)
    out = {}
    for u, pm in model.items():
        pk = market.get(u)
        if pk is None:
            out[u] = pm * 0.3   # 市場に無い = 取消の可能性
            continue
        out[u] = math.exp((1 - w_market) * math.log(max(pm, 1e-6)) + w_market * math.log(max(pk, 1e-6)))
    z = sum(out.values())
    return {u: v / z for u, v in out.items()} if z else out


def apply_ai(probs: dict[int, float], ratings: dict[int, int]) -> dict[int, float]:
    if not ratings:
        return dict(probs)
    out = {u: p * AI_RATING_MULT.get(ratings.get(u, 3), 1.0) for u, p in probs.items()}
    z = sum(out.values())
    return {u: v / z for u, v in out.items()} if z else out


def estimate(race: Race, win_odds: Optional[dict[str, float]] = None,
             ai_ratings: Optional[dict[int, int]] = None) -> dict:
    """戻り: {probs: {馬番: 勝率}, model_probs, market_probs, scores, notes}"""
    today_rank = _class_rank(race.cls + " " + race.grade + " " + race.name)
    weights = [h.weight for h in race.horses if h.weight]
    mean_w = sum(weights) / len(weights) if weights else 0.0
    scores: dict[int, float] = {}
    notes: dict[int, list[str]] = {}
    for h in race.horses:
        s, n = horse_score(h, race, today_rank, mean_w)
        scores[h.umaban], notes[h.umaban] = s, n
    scores = pace_adjust(race, scores)
    model = softmax(scores)
    market = market_probs(win_odds or {}, [h.umaban for h in race.horses])
    probs = blend(model, market)
    probs = apply_ai(probs, ai_ratings or {})
    return dict(probs=probs, model_probs=model, market_probs=market, scores=scores, notes=notes,
                today_rank=today_rank)
