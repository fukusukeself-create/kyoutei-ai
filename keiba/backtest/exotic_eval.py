"""単勝以外 (複勝・ワイド・馬連・馬単・三連複) の期待値買いを、JRA-VAN の実際の確定オッズで検証する。

    python backtest/exotic_eval.py [--oos 学習外の予想.csv]

学習外の勝率 (調教タイム補正後) から、着順の模擬と同じ式 (strategy.combo_probs) で組ごとの確率を出し、
期待値 = 確率 × 確定オッズ が下限以上の組を買ったとして、netkeiba の払戻で答え合わせする。
複勝・ワイドのオッズは幅の下限 (最低オッズ) で期待値を計算する (控えめ)。
下限は 2025年で選び、2026年で確かめる。結果は backtest/data/exotic_eval.json。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import features as F  # noqa: E402
import strategy  # noqa: E402

JV = os.path.join(ROOT, "jravan", "data")
THRESHOLDS = [1.0, 1.1, 1.2, 1.3, 1.5, 2.0]
PMIN = {"複勝": 0.15, "ワイド": 0.05, "馬連": 0.02, "馬単": 0.01, "三連複": 0.01}


def odd(s: str):
    return int(s) / 10 if s.isdigit() and int(s) > 0 else None


def parse_odds(races: set) -> dict:
    """race_id → {券種: {組 (馬番のタプル): オッズ}}。"""
    out: dict = {}

    def rid(l):
        return l[11:15] + l[19:21] + l[21:23] + l[23:25] + l[25:27]

    def files(k):
        return sorted(glob.glob(os.path.join(JV, f"RACE_{k}_202[5-6].txt")))

    for f in files("O1"):
        for l in open(f, encoding="utf-8"):
            r = rid(l)
            if r not in races:
                continue
            d = out.setdefault(r, {})
            base = 43 + 28 * 8
            for k in range(28):
                e = l[base + k * 12: base + (k + 1) * 12]
                if len(e) == 12 and e[:2].isdigit() and int(e[:2]) > 0 and odd(e[2:6]):
                    d.setdefault("複勝", {})[(int(e[:2]),)] = odd(e[2:6])
    for k, kind, n, w, size in (("O2", "馬連", 2, 6, 153), ("O3", "ワイド", 2, 5, 153), ("O4", "馬単", 2, 6, 306),
                                ("O5", "三連複", 3, 6, 816)):
        step = 2 * n + w + (5 if kind == "ワイド" else 0) + 3
        for f in files(k):
            for l in open(f, encoding="utf-8"):
                r = rid(l)
                if r not in races:
                    continue
                t = out.setdefault(r, {}).setdefault(kind, {})
                for j in range(size):
                    e = l[40 + j * step: 40 + (j + 1) * step]
                    if len(e) < step:
                        break
                    if not e[:2 * n].isdigit():
                        continue
                    combo = tuple(int(e[2 * i:2 * i + 2]) for i in range(n))
                    o = odd(e[2 * n:2 * n + w])
                    if o and all(combo):
                        t[combo] = o
    return out


def payout_map(pay: dict, kind: str) -> dict:
    m = {}
    for combo, yen in pay.get(kind, []):
        nums = tuple(int(x) for x in str(combo).split("-"))
        key = nums if kind == "馬単" else tuple(sorted(nums))
        m[key] = m.get(key, 0) + yen
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oos", default=os.path.join(HERE, "data", "oos.csv"))
    a = ap.parse_args()
    d = pd.read_csv(a.oos, dtype={"race_id": str})
    d = d[d.finish > 0]
    con = sqlite3.connect(os.path.join(HERE, "data", "races.sqlite"))
    info = {r: (c, g, n, json.loads(p or "{}")) for r, c, g, n, p in con.execute("SELECT race_id, cls, grade, name, payouts FROM races")}
    odds = parse_odds(set(d.race_id))
    print(f"レース {d.race_id.nunique():,} / オッズあり {len(odds):,}", flush=True)
    rows = []
    for rid, g in d.groupby("race_id"):
        if rid not in odds or rid not in info:
            continue
        cls, grade, name, pay = info[rid]
        newbie = F.class_rank(" ".join([str(cls), str(grade), str(name)])) <= 0.5
        p = dict(zip(g.umaban_id.astype(int), g.p_model / g.p_model.sum()))
        cp = strategy.combo_probs(p)
        probs = {"複勝": {(u,): v for u, v in cp["place"].items()}, "ワイド": cp["wide"], "馬連": cp["umaren"],
                 "馬単": cp["exacta"], "三連複": cp["trio"]}
        year = str(g.date.iloc[0])[:4]
        for kind, tb in probs.items():
            ot = odds[rid].get(kind, {})
            pm = payout_map(pay, kind)
            for combo, pr in tb.items():
                if pr < PMIN[kind]:
                    continue
                o = ot.get(combo)
                if not o or pr * o < THRESHOLDS[0]:
                    continue
                rows.append((rid, year, newbie, kind, pr * o, pm.get(combo, 0)))
    t = pd.DataFrame(rows, columns=["race_id", "year", "newbie", "kind", "ev", "payout"])
    res = {}
    print("\n券種 期待値の下限 | 2025年 (選ぶ期間) | 2026年 (確かめる期間)   ※新馬・未勝利を除く、1点100円")
    for kind in PMIN:
        for th in THRESHOLDS:
            line = f"{kind:4s} {th:.1f}以上"
            for y in ("2025", "2026"):
                x = t[(t.kind == kind) & (t.ev >= th) & (~t.newbie) & (t.year == y)]
                roi = x.payout.sum() / (100 * len(x)) if len(x) else 0
                res.setdefault(kind, {}).setdefault(str(th), {})[y] = dict(bets=int(len(x)), hits=int((x.payout > 0).sum()), roi=float(roi))
                line += f" | {len(x):5d}点 的中{(x.payout > 0).mean() * 100 if len(x) else 0:5.1f}% 回収{roi * 100:6.1f}%"
            print(line, flush=True)
    with open(os.path.join(HERE, "data", "exotic_eval.json"), "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
