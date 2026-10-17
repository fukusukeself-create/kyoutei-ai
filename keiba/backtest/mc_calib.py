"""モンテカルロの着順模擬の係数 (2着 g2・3着 g3) を、学習外の予想 (oos.csv) の3着内の結果に合わせて決める。

    python backtest/mc_calib.py

2025年で係数を選び、2026年で確かめる。比べるもの: 3着内の対数損失 (低いほど良い) と、三連複の本命1点の的中率。
"""
from __future__ import annotations

import itertools
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OOS = os.path.join(HERE, "data", "oos.csv")


def place_trio(p: np.ndarray, g2: float, g3: float):
    """3着内の確率 (各馬) と、三連複の確率が最も高い組 (添字)。模擬と同じ形を厳密に計算する。"""
    h = len(p)
    a2, a3 = p ** g2, p ** g3
    place = p.copy()
    trio: dict = {}
    for i in range(h):
        m2 = a2.copy(); m2[i] = 0; m2 /= m2.sum()
        for j in np.nonzero(m2)[0]:
            pij = p[i] * m2[j]
            m3 = a3.copy(); m3[[i, j]] = 0; m3 /= m3.sum()
            place[j] += pij
            place += pij * m3
            if h <= 18:
                for k in np.argsort(-m3)[:3]:
                    key = tuple(sorted((i, j, k)))
                    trio[key] = trio.get(key, 0.0) + pij * m3[k]
    return np.clip(place, 1e-6, 1 - 1e-6), max(trio, key=trio.get)


def evaluate(df: pd.DataFrame, g2: float, g3: float) -> tuple[float, float]:
    ll, hit, n = 0.0, 0, 0
    for _, r in df.groupby("race_id"):
        p = r.p_model.to_numpy(); p = p / p.sum()
        y = (r.finish.to_numpy() <= 3).astype(float)
        pl, best = place_trio(p, g2, g3)
        ll += -(y * np.log(pl) + (1 - y) * np.log(1 - pl)).sum()
        n += len(r)
        hit += int(all(y[list(best)] == 1))
    return ll / n, hit / df.race_id.nunique()


def main() -> None:
    d = pd.read_csv(OOS, dtype={"race_id": str})
    d = d[d.finish > 0]
    fit, test = d[d.date < 20260101], d[d.date >= 20260101]
    print(f"fit {fit.race_id.nunique()}レース / test {test.race_id.nunique()}レース")
    # 参考: 3着内を直接学習したモデル (p_top3) の対数損失
    for name, part in (("fit", fit), ("test", test)):
        y = (part.finish <= 3).astype(float); q = part.p_top3.clip(1e-6, 1 - 1e-6)
        print(f"  参考 p_top3 ({name}): {(-(y*np.log(q)+(1-y)*np.log(1-q))).mean():.4f}")
    grid = []
    for g2, g3 in itertools.product((0.6, 0.7, 0.8, 0.85, 0.9, 1.0), (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)):
        if g3 > g2:
            continue
        ll, hit = evaluate(fit, g2, g3)
        grid.append((ll, g2, g3, hit))
        print(f"g2={g2:.2f} g3={g3:.2f}: fit 3着内 対数損失 {ll:.4f} 三連複本命 {hit*100:.1f}%", flush=True)
    best = min(grid)
    _, g2, g3, _ = best
    cur = evaluate(test, 0.85, 0.70)
    new = evaluate(test, g2, g3)
    print(f"\n選んだ係数 g2={g2} g3={g3}")
    print(f"test 今の係数 (0.85, 0.70): 対数損失 {cur[0]:.4f} 三連複本命 {cur[1]*100:.1f}%")
    print(f"test 選んだ係数 ({g2}, {g3}): 対数損失 {new[0]:.4f} 三連複本命 {new[1]*100:.1f}%")


if __name__ == "__main__":
    main()
