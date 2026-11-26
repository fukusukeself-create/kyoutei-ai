"""舟券フォーメーションの作成と展開。

フォーメーションとは「1着候補 - 2着候補 - 3着候補」をまとめて指定する買い方。
例: 1 - 2,3 - 2,3,4
    → 1着は1号艇、2着は2か3、3着は2か3か4（同じ艇は重複できない）
    → 1-2-3, 1-2-4, 1-3-2, 1-3-4 の4点
"""
from __future__ import annotations

import itertools

import numpy as np

BOATS = (1, 2, 3, 4, 5, 6)

# 買い方のスタイル: (本線の最大点数, 抑えの最大点数)
STYLES = {
    "的中重視": (12, 8),
    "バランス": (8, 4),
    "少点数": (4, 2),
}
DEFAULT_STYLE = "的中重視"

_SUBSETS = [s for n in range(1, 7) for s in itertools.combinations(range(6), n)]
_MASKS = np.zeros((len(_SUBSETS), 6))
for _i, _s in enumerate(_SUBSETS):
    _MASKS[_i, list(_s)] = 1.0
_VALID = np.zeros((6, 6, 6))
for _i, _j, _k in itertools.permutations(range(6), 3):
    _VALID[_i, _j, _k] = 1.0


def _clean(seq) -> list[int]:
    out = []
    for x in seq or []:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if v in BOATS and v not in out:
            out.append(v)
    return sorted(out)


def normalize(f) -> dict | None:
    """AIが返した買い目などを {first, second, third} の正しい形に整える。成立しなければ None。"""
    if not isinstance(f, dict):
        return None
    out = {"first": _clean(f.get("first")), "second": _clean(f.get("second")), "third": _clean(f.get("third"))}
    if not expand(out):
        return None
    return out


def expand(f) -> list[tuple[int, int, int]]:
    """フォーメーションを1点ずつの買い目（3連単）に展開する。"""
    tickets = []
    for a in f.get("first", []):
        for b in f.get("second", []):
            for c in f.get("third", []):
                if len({a, b, c}) == 3:
                    tickets.append((a, b, c))
    return tickets


def to_text(f) -> str:
    def part(xs):
        return "全" if len(xs) == 6 else ",".join(str(x) for x in xs)
    return f"{part(f['first'])} - {part(f['second'])} - {part(f['third'])}"


def coverage(P, tickets) -> float:
    return float(sum(P[a - 1, b - 1, c - 1] for a, b, c in set(tickets)))


def _sum_over_formations(MA, T):
    """全フォーメーション(1着集合 x 2着集合 x 3着集合)について、含まれる買い目の値 T[i,j,k] の合計を出す。"""
    x = np.tensordot(MA, T, axes=(1, 0))            # (A, 6, 6)
    x = np.einsum("ajk,bj->abk", x, _MASKS)          # (A, B, 6)
    return x @ _MASKS.T                              # (A, B, C)


def best_formation(P, max_points: int, exclude=(), max_first: int = 2, min_gain: float = 0.004):
    """確率 P(6x6x6) のもとで、max_points 点以内で的中確率が最大になるフォーメーションを探す。

    exclude: すでに買っている買い目（抑えは、これと重ならないものだけから選ぶ）
    min_gain: 1点増やすなら最低これだけ確率が増えること（無駄な点数を増やさない）
    """
    Pm = np.array(P, dtype=float) * _VALID
    banned = set(exclude)
    dup = np.zeros((6, 6, 6))
    for a, b, c in banned:
        dup[a - 1, b - 1, c - 1] = 1.0
    first_ok = _MASKS.sum(axis=1) <= max_first
    MA = _MASKS[first_ok]
    cov = _sum_over_formations(MA, Pm)
    pts = _sum_over_formations(MA, _VALID)
    score = np.where((pts >= 1) & (pts <= max_points), cov - min_gain * pts, -1.0)
    if banned:
        # すでに買っている買い目と1点でも重なるフォーメーションは使わない（二重買い防止）
        score = np.where(_sum_over_formations(MA, dup) > 0, -1.0, score)
    a, b, c = np.unravel_index(int(np.argmax(score)), score.shape)
    if score[a, b, c] <= 0:
        return None
    subsets_a = [s for s, ok in zip(_SUBSETS, first_ok) if ok]
    f = {
        "first": [x + 1 for x in subsets_a[a]],
        "second": [x + 1 for x in _SUBSETS[b]],
        "third": [x + 1 for x in _SUBSETS[c]],
    }
    return f


def describe(f, P=None, odds=None, label="", exclude=()) -> dict:
    """画面表示用に、点数・買い目一覧・確率などをまとめる。exclude にある買い目は二重買いになるので除く。"""
    skip = set(exclude)
    all_tickets = expand(f)
    tickets = [t for t in all_tickets if t not in skip]
    rows = []
    for t in tickets:
        key = f"{t[0]}-{t[1]}-{t[2]}"
        rows.append({
            "ticket": key,
            "prob": float(P[t[0] - 1, t[1] - 1, t[2] - 1]) if P is not None else None,
            "odds": (odds or {}).get(key),
        })
    if P is not None:
        rows.sort(key=lambda r: -r["prob"])
    return {
        "label": label,
        "formation": f,
        "text": to_text(f),
        "points": len(tickets),
        "dups": len(all_tickets) - len(tickets),
        "tickets": rows,
        "prob": coverage(P, tickets) if P is not None else None,
        "reason": f.get("reason", ""),
    }


def suggest(P, style: str = DEFAULT_STYLE) -> dict:
    """データ予想だけで作る買い目（本線＋抑え）。"""
    main_pts, sub_pts = STYLES.get(style, STYLES[DEFAULT_STYLE])
    main = best_formation(P, main_pts)
    out = {"main": main, "sub": None}
    if main:
        out["sub"] = best_formation(P, sub_pts, exclude=expand(main), max_first=2)
    return out


HOLE_POINTS = 8          # 穴の買い目は最大8点（1点100円なら800円）に固定して、賭け金がふくらまないようにする
HOLE_MIN_ODDS = 100.0    # 万舟券 = 100円が1万円以上 = 100倍以上
HOLE_MIN_PROB = 0.001    # これより可能性の低い買い目は、数合わせで足さない


def suggest_hole(P, odds=None, bought=(), max_points: int = HOLE_POINTS, min_odds: float = HOLE_MIN_ODDS):
    """穴（万舟狙い）の買い目。本線・抑えとは別に、少ない点数で高配当だけを拾う。

    穴は1点ごとの確率が小さく、フォーメーションの形に揃えると取りこぼしが大きいので、
    1点ずつ（バラ）で確率の高い順に選ぶ。
      オッズがある時: オッズ min_odds 倍以上の買い目だけが対象。
      オッズが無い時: モデルの確率順で21番目以降（過去データでは万舟決着の97%がここ）が対象。
    戻り値: (買い目のリスト, 選び方 "odds" / "rank")
    """
    P = np.asarray(P, dtype=float)
    bought = set(bought)
    ranked = sorted(((i + 1, j + 1, k + 1) for i, j, k in itertools.permutations(range(6), 3)),
                    key=lambda t: -P[t[0] - 1, t[1] - 1, t[2] - 1])
    if odds and len(odds) >= 100:
        pool = [t for t in ranked if (odds.get(ticket_key(t)) or 0) >= min_odds]
        basis = "odds"
    else:
        pool = ranked[20:]
        basis = "rank"
    picks = [t for t in pool if t not in bought and P[t[0] - 1, t[1] - 1, t[2] - 1] >= HOLE_MIN_PROB]
    return picks[:max_points], basis


def ticket_key(t) -> str:
    return f"{t[0]}-{t[1]}-{t[2]}"


def parse_ticket(text):
    """'3-4-1' のような文字列を (3, 4, 1) にする。成立しない買い目は None。"""
    parts = str(text).replace(" ", "").replace("－", "-").split("-")
    try:
        t = tuple(int(x) for x in parts)
    except ValueError:
        return None
    if len(t) != 3 or len(set(t)) != 3 or not all(x in BOATS for x in t):
        return None
    return t


def describe_tickets(tickets, P=None, odds=None, label="", reason="") -> dict:
    """1点ずつの買い目（バラ買い）を画面表示用にまとめる。describe() と同じ形で返す。"""
    rows = []
    seen = set()
    for t in tickets:
        if t in seen:
            continue
        seen.add(t)
        rows.append({
            "ticket": ticket_key(t),
            "prob": float(P[t[0] - 1, t[1] - 1, t[2] - 1]) if P is not None else None,
            "odds": (odds or {}).get(ticket_key(t)),
        })
    if P is not None:
        rows.sort(key=lambda r: -r["prob"])
    return {
        "label": label,
        "formation": None,
        "text": " / ".join(r["ticket"] for r in rows),
        "points": len(rows),
        "dups": 0,
        "tickets": rows,
        "prob": coverage(P, seen) if P is not None else None,
        "reason": reason,
    }


def other_bets(P) -> dict:
    """3連単以外の、当たりやすい券種の上位候補。"""
    P = np.asarray(P, dtype=float)
    exacta = {}   # 2連単
    quinella = {}  # 2連複
    trio = {}     # 3連複
    for i, j, k in itertools.permutations(range(6), 3):
        p = P[i, j, k]
        exacta[(i + 1, j + 1)] = exacta.get((i + 1, j + 1), 0.0) + p
        q = tuple(sorted((i + 1, j + 1)))
        quinella[q] = quinella.get(q, 0.0) + p
        t = tuple(sorted((i + 1, j + 1, k + 1)))
        trio[t] = trio.get(t, 0.0) + p
    win = {i + 1: float(P[i].sum()) for i in range(6)}

    def top(d, n, sep):
        items = sorted(d.items(), key=lambda kv: -kv[1])[:n]
        return [{"ticket": sep.join(str(x) for x in (k if isinstance(k, tuple) else (k,))), "prob": float(v)} for k, v in items]

    return {
        "単勝": top(win, 2, ""),
        "2連単": top(exacta, 4, "-"),
        "2連複": top(quinella, 3, "="),
        "3連複": top(trio, 4, "="),
    }


def marks(win, top3) -> dict[int, str]:
    """予想印。◎本命 ○対抗 ▲単穴 △連下"""
    order = sorted(BOATS, key=lambda b: -(win[b - 1] * 2 + top3[b - 1]))
    symbols = ["◎", "○", "▲", "△"]
    return {b: (symbols[i] if i < len(symbols) else "") for i, b in enumerate(order)}
