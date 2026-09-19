"""勝率から連系の的中確率を作り、買い目 (単勝・馬連・ワイド・三連複) を組む。

確率は Harville の式 (2着・3着は残り馬の勝率比で決まる) を、人気馬の連対を
過大評価しないよう Stern 流に指数で緩めたもの。オッズがあれば期待値も出す。
"""

from __future__ import annotations

import itertools
import re
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
    return dict(umaren=umaren, wide=wide, trio=trio, place=place, exacta=exacta, trifecta=trifecta)


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


# ---------------------------------------------------------------- 2点勝負
# 券種ごとの候補 (確率の高い順)。オッズ表のキーは scraper.fetch_all_odds と同じ。
KIND_KEY = {"単勝": "win", "馬連": "umaren", "馬単": "umatan", "ワイド": "wide", "三連複": "sanrenpuku", "三連単": "sanrentan"}


def candidates(win: dict[int, float], odds: dict[str, dict[str, float]] | None = None) -> dict[str, list[Ticket]]:
    """券種 -> 確率順の候補 (上位10)。"""
    cp = combo_probs(win)
    odds = odds or {}
    out: dict[str, list[Ticket]] = {}
    src = {"単勝": {(u,): p for u, p in win.items()}, "馬連": cp["umaren"], "馬単": cp["exacta"],
           "ワイド": cp["wide"], "三連複": cp["trio"], "三連単": cp["trifecta"]}
    for kind, table in src.items():
        ts = [Ticket(kind, c, p, _odds_of(odds.get(KIND_KEY[kind], {}), c)) for c, p in table.items()]
        ts.sort(key=lambda t: -t.prob)
        out[kind] = ts[:10]
    return out


# 検証で決めた既定の方針: 本命の勝率の帯ごとに、どの券種を何点買うか。
# backtest/bet2.py が models/bet2.json に書き出したものがあればそちらを使う。
DEFAULT_POLICY = {
    "buckets": [0.0, 0.20, 0.30, 0.40, 1.01],
    "choice": ["ワイド2", "ワイド2", "馬連1+ワイド1", "単勝1+馬連1"],
}


def parse_choice(choice: str) -> list[tuple[str, int]]:
    """"単勝1+馬連1" -> [("単勝",1),("馬連",1)]"""
    out = []
    for part in choice.split("+"):
        kind = part.rstrip("0123456789")
        n = int(part[len(kind):] or 1)
        out.append((kind, n))
    return out


def best_two(win: dict[int, float], odds: dict[str, dict[str, float]] | None, policy: dict | None = None) -> tuple[list[Ticket], str]:
    """2点以内の買い目と、その帯の説明。"""
    policy = policy or DEFAULT_POLICY
    cands = candidates(win, odds)
    p_top = max(win.values()) if win else 0.0
    bks = policy["buckets"]
    idx = max(i for i in range(len(bks) - 1) if p_top >= bks[i])
    choice = policy["choice"][idx]
    tickets: list[Ticket] = []
    for kind, n in parse_choice(choice):
        tickets.extend(cands.get(kind, [])[:n])
    reason = f"本命勝率 {p_top*100:.0f}% (帯 {bks[idx]*100:.0f}〜{min(bks[idx+1],1.0)*100:.0f}%) → {choice}"
    return tickets[:2], reason


# ---------------------------------------------------------------- 三連複・三連単フォーメーション
# 競艇アプリと同じ考え方: 確率の合計が目標に届く点数の中で、1〜2本のフォーメーション
# ("1 - 2,3 - 2,3,4") で書ける組み合わせのうち当たる確率が最大のものを選ぶ。
FORMATION_CFG = {
    "三連複": {"堅実": (0.30, 6), "バランス": (0.42, 10), "穴狙い": (0.45, 12)},
    "三連単": {"堅実": (0.20, 8), "バランス": (0.30, 12), "穴狙い": (0.35, 18)},
}


def _subsets(items: list[int], kmax: int):
    for k in range(1, min(kmax, len(items)) + 1):
        yield from itertools.combinations(items, k)


def expand_block(kind: str, a: tuple, b: tuple, c: tuple) -> set[tuple]:
    out = set()
    for x in a:
        for y in b:
            for z in c:
                if len({x, y, z}) == 3:
                    out.add((x, y, z) if kind == "三連単" else tuple(sorted((x, y, z))))
    return out


def block_text(a, b, c) -> str:
    return " - ".join(",".join(str(x) for x in sorted(s)) for s in (a, b, c))


def expand_formation(kind: str, text: str) -> list[tuple]:
    """"1 - 2,3 - 2,3,4 / 2 - 1 - 3" を展開 (重複なし)。"""
    out: list[tuple] = []
    for part in str(text).split(" / "):
        cols = [seg.strip() for seg in part.split("-")]
        if len(cols) != 3:
            continue
        sets = [tuple(int(x) for x in col.split(",") if x.strip().isdigit()) for col in cols]
        if not all(sets):
            continue
        for t in sorted(expand_block(kind, *sets)):
            if t not in out:
                out.append(t)
    return out


def exact_formation(kind: str, combos: list[tuple]) -> str:
    """買い目の集合を、展開すると過不足なく同じになるフォーメーション表記にする (" / " 区切り)。"""
    want = set(combos)
    if not want:
        return ""
    tree: dict = {}
    for a, b, c in combos:
        tree.setdefault(a, {}).setdefault(b, set()).add(c)
    parts = []
    for a in sorted(tree):
        groups = [({b}, set(cs)) for b, cs in tree[a].items()]
        merged = True
        while merged:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    bs, cs = groups[i][0] | groups[j][0], groups[i][1] | groups[j][1]
                    have = {t for t in want if t[0] == a and t[1] in bs}
                    if expand_block(kind, (a,), tuple(bs), tuple(cs)) == have:
                        groups[i] = (bs, cs)
                        del groups[j]
                        merged = True
                        break
                if merged:
                    break
        for bs, cs in sorted(groups, key=lambda g: sorted(g[0])):
            parts.append(block_text((a,), bs, cs))
    text = " / ".join(parts)
    got = expand_formation(kind, text)
    if set(got) != want or len(got) != len(want):
        text = " / ".join("-".join(map(str, t)) for t in sorted(want, key=lambda t: t))
    return text


def formation(kind: str, win: dict[int, float], odds: dict[str, dict[str, float]] | None = None,
              style: str = "バランス") -> dict:
    """kind: 三連複 / 三連単。戻り: text, tickets, points, cover, ev(同額買いの期待回収率 or None)"""
    import numpy as np
    target, max_points = FORMATION_CFG[kind].get(style, FORMATION_CFG[kind]["バランス"])
    cp = combo_probs(win)
    table = cp["trifecta"] if kind == "三連単" else cp["trio"]
    ranked = sorted(win, key=lambda u: -win[u])
    # 目標に届く点数 (確率順の上位N点)
    ordered = sorted(table.items(), key=lambda kv: -kv[1])
    cum, need = 0.0, 0
    for _, p in ordered[:max_points]:
        need += 1
        cum += p
        if cum >= target and need >= 2:
            break
    need = max(2, min(max_points, need))
    top = ranked[:8]
    universe = [c for c in table if all(x in top for x in c)]
    index = {c: i for i, c in enumerate(universe)}
    p = np.array([table[c] for c in universe])
    if kind == "三連単":
        heads = [(x,) for x in ranked[:3]] + list(itertools.combinations(ranked[:3], 2))
        seconds, thirds = list(_subsets(ranked[:6], 4)), list(_subsets(ranked[:7], 5))
    else:
        heads = [(x,) for x in ranked[:2]] + [tuple(ranked[:2])]
        seconds, thirds = list(_subsets(ranked[:6], 4)), list(_subsets(ranked[:8], 6))
    rows, spec = [], []
    seen = {}
    for a in heads:
        for b in seconds:
            for c in thirds:
                bets = expand_block(kind, a, b, c)
                if not bets or len(bets) > need:
                    continue
                key = frozenset(bets)
                size = len(a) + len(b) + len(c)
                if key in seen and seen[key][1] <= size:
                    continue
                row = np.zeros(len(universe), dtype=bool)
                row[[index[x] for x in bets if x in index]] = True
                if key in seen:
                    rows[seen[key][0]] = row
                    spec[seen[key][0]] = (a, b, c)
                    seen[key] = (seen[key][0], size)
                else:
                    seen[key] = (len(rows), size)
                    rows.append(row)
                    spec.append((a, b, c))
    if not rows:
        combos = [c for c, _ in ordered[:need]]
        text = exact_formation(kind, combos)
    else:
        m = np.stack(rows)
        pts = m.sum(axis=1)
        cover = m @ p
        best = (float(cover.max()), [int(np.argmax(cover))])
        for i in np.argsort(-cover)[:30]:
            room = need - int(pts[i])
            if room < 1:
                continue
            ok2 = np.flatnonzero(pts <= room)
            union = m[ok2] | m[i]
            fit = union.sum(axis=1) <= need
            if not fit.any():
                continue
            uc = union[fit] @ p
            j = int(np.argmax(uc))
            if uc[j] > best[0] + 1e-12:
                best = (float(uc[j]), [int(i), int(ok2[np.flatnonzero(fit)[j]])])
        mask = np.zeros(len(universe), dtype=bool)
        for i in best[1]:
            mask |= m[i]
        combos = [universe[k] for k in np.flatnonzero(mask)]
        text = " / ".join(block_text(*spec[i]) for i in best[1])
        if set(expand_formation(kind, text)) != set(combos):
            text = exact_formation(kind, combos)
    key = KIND_KEY[kind]
    tickets = [Ticket(kind, c, table[c], _odds_of((odds or {}).get(key, {}), c)) for c in combos]
    tickets.sort(key=lambda t: -t.prob)
    sm = summarize(tickets)
    return dict(kind=kind, text=text, tickets=tickets, points=len(tickets), cover=sm["hit"], ev=sm["ev"],
                target=target, max_points=max_points)


# ---------------------------------------------------------------- 収支プラス狙い (期待値買い)
# 点数は問わず、確率 × 実オッズ (期待値) が券種ごとの下限を超える組だけ買う。
# 下限は backtest/profit.py が学習外の予想で決めて models/profit.json に書く。無ければ既定値。
DEFAULT_VALUE_POLICY = {
    "policy": {"単勝": {"threshold": 1.2, "pmin": 0.05}, "馬連": {"threshold": 1.2, "pmin": 0.02},
               "ワイド": {"threshold": 1.2, "pmin": 0.05}, "三連複": {"threshold": 1.3, "pmin": 0.01}},
    "max_points": 12,
}


def value_bets(win: dict[int, float], odds: dict[str, dict[str, float]] | None, policy: dict | None = None) -> dict[str, list[Ticket]]:
    """券種 -> 期待値の高い順の買い目 (上限 max_points)。オッズが無い券種は空。"""
    pol = policy or DEFAULT_VALUE_POLICY
    odds = odds or {}
    cands = candidates(win, odds)
    # candidates は上位10までなので、期待値買いは全組から見直す
    cp = combo_probs(win)
    src = {"単勝": {(u,): p for u, p in win.items()}, "馬連": cp["umaren"], "馬単": cp["exacta"],
           "ワイド": cp["wide"], "三連複": cp["trio"], "三連単": cp["trifecta"]}
    out: dict[str, list[Ticket]] = {}
    for kind, rule in pol["policy"].items():
        table = odds.get(KIND_KEY[kind], {})
        if not table:
            out[kind] = []
            continue
        ts = []
        for c, p in src[kind].items():
            if p < rule["pmin"]:
                continue
            o = _odds_of(table, c)
            if o and p * o >= rule["threshold"]:
                ts.append(Ticket(kind, c, p, o))
        ts.sort(key=lambda t: -(t.ev or 0))
        out[kind] = ts[: pol.get("max_points", 12)]
    return out


PMIN_DEFAULT = {"単勝": 0.05, "馬連": 0.02, "ワイド": 0.05, "馬単": 0.01, "三連複": 0.01, "三連単": 0.003}


def ev_ranked(win: dict[int, float], odds: dict[str, dict[str, float]] | None, pmin: dict | None = None,
              max_points: int = 12) -> dict[str, list[Ticket]]:
    """券種ごとに、確率 >= pmin の組を期待値の高い順に (実オッズがある券種だけ)。"""
    pmin = pmin or PMIN_DEFAULT
    odds = odds or {}
    cp = combo_probs(win)
    src = {"単勝": {(u,): p for u, p in win.items()}, "馬連": cp["umaren"], "馬単": cp["exacta"],
           "ワイド": cp["wide"], "三連複": cp["trio"], "三連単": cp["trifecta"]}
    out: dict[str, list[Ticket]] = {}
    for kind, table in src.items():
        ot = odds.get(KIND_KEY[kind], {})
        if not ot:
            continue
        ts = [Ticket(kind, c, p, _odds_of(ot, c)) for c, p in table.items() if p >= pmin.get(kind, 0.0)]
        ts = [t for t in ts if t.odds]
        ts.sort(key=lambda t: -(t.ev or 0))
        out[kind] = ts[:max_points]
    return out


def noskip_bets(win: dict[int, float], odds: dict[str, dict[str, float]] | None, label: str,
                pmin: dict | None = None) -> list[Ticket]:
    """見送り無しの買い方。label は profit.py が選んだ "全券種 上位3点" / "ワイド 上位2点" / "単勝1点+ワイド1点"。"""
    ranked = ev_ranked(win, odds, pmin)
    m = re.match(r"全券種 上位(\d+)点", label)
    if m:
        allt = sorted((t for ts in ranked.values() for t in ts), key=lambda t: -(t.ev or 0))
        return allt[: int(m.group(1))]
    m = re.match(r"(\S+) 上位(\d+)点", label)
    if m and m.group(1) in ranked:
        return ranked[m.group(1)][: int(m.group(2))]
    out = []
    for part in label.split("+"):
        mm = re.match(r"(\D+)(\d+)点", part)
        if mm and mm.group(1) in ranked:
            out.extend(ranked[mm.group(1)][: int(mm.group(2))])
    return out
