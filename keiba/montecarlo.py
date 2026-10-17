"""モンテカルロ法: 勝率から着順を何万回も模擬して、券種ごとの的中確率と、1日の収支のばらつきを出す。

着順の模擬 (プラケット・ルース型): 1着は勝率どおり、2着・3着は残りの馬から 勝率^g2・勝率^g3 に比例して選ぶ。
g2・g3 は backtest/mc_calib.py で、学習外のレースの3着内の結果に合わせて決める (strategy.combo_probs と同じ形)。
"""
from __future__ import annotations

import itertools
from collections import Counter

import numpy as np

G2, G3 = 0.85, 0.70     # mc_calib.py で決め直す


def simulate(win: dict[int, float], n: int = 20000, g2: float = G2, g3: float = G3, rng=None) -> tuple[list[int], np.ndarray]:
    """戻り: (馬番の並び, n×3 の着順 [1着,2着,3着] の添字)。ガンベル最大値の方法でまとめて引く。"""
    rng = rng or np.random.default_rng()
    us = sorted(win)
    p = np.array([max(win[u], 1e-9) for u in us])
    p = p / p.sum()
    lp = np.log(p)
    h = len(us)
    out = np.empty((n, min(3, h)), dtype=np.int64)
    taken = np.zeros((n, h), dtype=bool)
    for k, g in enumerate((1.0, g2, g3)[: out.shape[1]]):
        score = g * lp + rng.gumbel(size=(n, h))
        score[taken] = -np.inf
        idx = score.argmax(axis=1)
        out[:, k] = idx
        taken[np.arange(n), idx] = True
    return us, out


def probs(win: dict[int, float], n: int = 20000, **kw) -> dict:
    """券種ごとの確率 (模擬の割合)。キーは馬番のタプル (馬連・ワイド・三連複は昇順)。"""
    us, o = simulate(win, n, **kw)
    m = len(o)
    win_p = Counter(us[i] for i in o[:, 0])
    top3 = Counter(us[i] for row in o for i in row)
    umaren = Counter(tuple(sorted((us[a], us[b]))) for a, b in o[:, :2])
    trio = Counter(tuple(sorted(us[i] for i in row)) for row in o) if o.shape[1] == 3 else Counter()
    wide = Counter()
    for k, v in trio.items():
        for c in itertools.combinations(k, 2):
            wide[c] += v
    f = lambda c: {k: v / m for k, v in c.items()}
    return dict(win=f(win_p), place=f(top3), umaren=f(umaren), wide=f(wide), trio=f(trio))


def day_pnl(bets: list[dict], n: int = 20000, rng=None, **kw) -> dict:
    """1日の買い目の収支を模擬する。bets: [{win: 勝率dict, kind: '単勝'|'複勝'|'馬単'|'ワイド', umaban, second (2頭目), odds, stake}]。
    同じレースの買い目は同じ着順の模擬を使う (レースどうしは独立)。"""
    rng = rng or np.random.default_rng(0)
    total = np.zeros(n)
    stake = 0
    by_race: dict[str, list[dict]] = {}
    for b in bets:
        by_race.setdefault(b["race_id"], []).append(b)
    for rid, bs in by_race.items():
        us, o = simulate(bs[0]["win"], n, rng=rng, **kw)
        pos = {u: i for i, u in enumerate(us)}
        for b in bs:
            i = pos[b["umaban"]]
            if b["kind"] == "単勝":
                hit = o[:, 0] == i
            elif b["kind"] == "馬単":
                hit = (o[:, 0] == i) & (o[:, 1] == pos[b["second"]])
            elif b["kind"] == "ワイド":
                hit = (o == i).any(axis=1) & (o == pos[b["second"]]).any(axis=1)
            else:   # 複勝
                hit = (o == i).any(axis=1)
            total += np.where(hit, b["odds"] * b["stake"], 0.0)
            stake += b["stake"]
    pnl = total - stake
    return dict(stake=stake, mean=float(pnl.mean()), p_profit=float((pnl > 0).mean()), p_zero_hit=float((total == 0).mean()),
                q05=float(np.quantile(pnl, 0.05)), q50=float(np.quantile(pnl, 0.5)), q95=float(np.quantile(pnl, 0.95)))
