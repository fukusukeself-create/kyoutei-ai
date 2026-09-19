"""勝率から連系の的中確率を作り、買い目 (単勝・馬連・ワイド・三連複) を組む。

確率は Harville の式 (2着・3着は残り馬の勝率比で決まる) を、人気馬の連対を
過大評価しないよう Stern 流に指数で緩めたもの。オッズがあれば期待値も出す。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

STYLES = {
    "堅実":   dict(umaren_target=0.40, umaren_max=4, trio_target=0.30, trio_max=6, wide_n=2,
                 min_odds_umaren=0, min_odds_trio=0),
    "バランス": dict(umaren_target=0.50, umaren_max=6, trio_target=0.42, trio_max=10, wide_n=3,
                 min_odds_umaren=0, min_odds_trio=0),
    "穴狙い":  dict(umaren_target=0.35, umaren_max=6, trio_target=0.30, trio_max=10, wide_n=3,
                 min_odds_umaren=15, min_odds_trio=50),
}

# 穴狙いで拾う組の下限確率と上限オッズ。確率の裾はモデルが過大評価しがちなので、
# 「安いが当たらない」組を期待値だけで拾わないようにする。
ANA_LIMITS = {"馬連": dict(min_prob=0.010, max_odds=200.0), "三連複": dict(min_prob=0.006, max_odds=800.0)}

MARKS = ["◎", "○", "▲", "△", "△", "☆"]


@dataclass
class Ticket:
    kind: str            # 単勝 / 馬連 / ワイド / 三連複
    combo: tuple[int, ...]
    prob: float
    odds: Optional[float] = None

    @property
    def label(self) -> str:
        return "-".join(str(c) for c in self.combo)

    @property
    def ev(self) -> Optional[float]:
        return self.prob * self.odds if self.odds else None


@dataclass
class Plan:
    style: str
    marks: dict[int, str]
    win_probs: dict[int, float]
    place_probs: dict[int, float]
    tickets: dict[str, list[Ticket]] = field(default_factory=dict)

    def all_tickets(self) -> list[Ticket]:
        return [t for ts in self.tickets.values() for t in ts]


def _cond(p: dict[int, float], exclude: tuple[int, ...], power: float) -> dict[int, float]:
    rest = {u: v ** power for u, v in p.items() if u not in exclude}
    z = sum(rest.values())
    return {u: v / z for u, v in rest.items()} if z else {}


def combo_probs(win: dict[int, float]) -> dict:
    """馬連・ワイド・三連複・複勝(3着内) の確率。"""
    umabans = sorted(win)
    exacta: dict[tuple[int, int], float] = {}
    for i in umabans:
        c2 = _cond(win, (i,), 0.85)
        for j, pj in c2.items():
            exacta[(i, j)] = win[i] * pj
    trifecta: dict[tuple[int, int, int], float] = {}
    for (i, j), pij in exacta.items():
        c3 = _cond(win, (i, j), 0.70)
        for k, pk in c3.items():
            trifecta[(i, j, k)] = pij * pk
    umaren: dict[tuple[int, int], float] = {}
    for (i, j), v in exacta.items():
        key = tuple(sorted((i, j)))
        umaren[key] = umaren.get(key, 0.0) + v
    trio: dict[tuple[int, int, int], float] = {}
    place: dict[int, float] = {u: 0.0 for u in umabans}
    for (i, j, k), v in trifecta.items():
        key = tuple(sorted((i, j, k)))
        trio[key] = trio.get(key, 0.0) + v
        place[i] += v
        place[j] += v
        place[k] += v
    wide: dict[tuple[int, int], float] = {}
    for key, v in trio.items():
        for a, b in itertools.combinations(key, 2):
            wide[(a, b)] = wide.get((a, b), 0.0) + v
    return dict(umaren=umaren, wide=wide, trio=trio, place=place)


def _odds_of(table: dict[str, float], combo: tuple[int, ...]) -> Optional[float]:
    return table.get("".join(f"{c:02d}" for c in combo))


def _pick(probs: dict[tuple, float], odds: dict[str, float], target: float, max_n: int,
          min_odds: float, kind: str) -> list[Ticket]:
    cands = []
    for combo, p in probs.items():
        o = _odds_of(odds, combo)
        if min_odds and (o is None or o < min_odds):
            continue
        cands.append(Ticket(kind, combo, p, o))
    if min_odds:
        # 穴: 期待値が高い順。オッズが無い組は除外済み
        lim = ANA_LIMITS.get(kind, dict(min_prob=0.0, max_odds=1e9))
        cands = [c for c in cands
                 if (c.ev or 0) >= 0.8 and c.prob >= lim["min_prob"] and (c.odds or 0) <= lim["max_odds"]]
        cands.sort(key=lambda t: -(t.ev or 0))
        return cands[:max_n]
    cands.sort(key=lambda t: -t.prob)
    out, cum = [], 0.0
    for c in cands:
        if len(out) >= max_n:
            break
        out.append(c)
        cum += c.prob
        if cum >= target and len(out) >= 2:
            break
    return out


def build_plan(win: dict[int, float], odds: dict[str, dict[str, float]], style: str = "バランス") -> Plan:
    cfg = STYLES.get(style, STYLES["バランス"])
    cp = combo_probs(win)
    ranked = sorted(win, key=lambda u: -win[u])
    marks = {}
    for i, u in enumerate(ranked[:5]):
        marks[u] = MARKS[i]
    # ☆: 上位3頭以外で単勝期待値が最も高い馬
    win_odds = odds.get("win", {})
    best_ev, best_u = 0.0, None
    for u in ranked[3:]:
        o = win_odds.get(f"{u:02d}")
        if o and win[u] * o > best_ev:
            best_ev, best_u = win[u] * o, u
    if best_u is not None and best_ev >= 1.0:
        marks[best_u] = "☆"

    plan = Plan(style=style, marks=marks, win_probs=win, place_probs=cp["place"])

    # 単勝: 期待値 1.1 以上かつ勝率 6% 以上 (人気薄の紛れを拾わない)
    tan = []
    for u in ranked:
        o = win_odds.get(f"{u:02d}")
        if o and win[u] >= 0.06 and win[u] * o >= 1.10:
            tan.append(Ticket("単勝", (u,), win[u], o))
    tan.sort(key=lambda t: -(t.ev or 0))
    plan.tickets["単勝"] = tan[:2]

    plan.tickets["馬連"] = _pick(cp["umaren"], odds.get("umaren", {}), cfg["umaren_target"],
                                cfg["umaren_max"], cfg["min_odds_umaren"], "馬連")
    wide_all = sorted((Ticket("ワイド", c, p, _odds_of(odds.get("wide", {}), c)) for c, p in cp["wide"].items()),
                      key=lambda t: -t.prob)
    plan.tickets["ワイド"] = wide_all[: cfg["wide_n"]]
    plan.tickets["三連複"] = _pick(cp["trio"], odds.get("sanrenpuku", {}), cfg["trio_target"],
                                 cfg["trio_max"], cfg["min_odds_trio"], "三連複")
    return plan


def summarize(tickets: list[Ticket]) -> dict:
    """点数・的中率 (いずれか当たる確率の近似) ・期待回収率 (同額買い)。"""
    if not tickets:
        return dict(points=0, hit=0.0, ev=None)
    hit = min(1.0, sum(t.prob for t in tickets))
    evs = [t.ev for t in tickets if t.ev is not None]
    ev = sum(evs) / len(tickets) if len(evs) == len(tickets) else None
    return dict(points=len(tickets), hit=hit, ev=ev)
