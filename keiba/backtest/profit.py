"""収支プラスを狙う買い方 (期待値買い) を、学習外の予想で探す。

    python backtest/profit.py

考え方: 点数は問わず「モデルの確率が市場の見立てより十分高い組だけ買う」。
  - 単勝は確定オッズがあるので 期待値 = 確率 × オッズ をそのまま使う
  - 馬連・ワイド・馬単・三連複・三連単は過去のオッズが無いので、単勝オッズから
    Harville 式で市場の組確率を作り、控除率を引いた「近似オッズ」で期待値を出す
    (本番アプリでは実オッズを使う。ここでの回収率は実際の払戻で計算している)
券種ごとに期待値の下限 (1.0〜1.5) を 2025年の予想で選び、2026年で確かめる。
結果は models/profit.json に書き、アプリの「収支プラス狙い」がそれに従う。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import strategy  # noqa: E402

DB = os.path.join(HERE, "data", "races.sqlite")
OOS = os.path.join(HERE, "data", "oos.csv")
OUT = os.path.join(ROOT, "models", "profit.json")

TAKEOUT = {"単勝": 0.20, "馬連": 0.225, "ワイド": 0.225, "馬単": 0.25, "三連複": 0.25, "三連単": 0.275}
# 確率がこれ未満の組は、期待値が高く見えても買わない (裾の確率はモデルが過大評価しがち)
PMIN = {"単勝": 0.05, "馬連": 0.02, "ワイド": 0.05, "馬単": 0.01, "三連複": 0.01, "三連単": 0.003}
THRESHOLDS = [1.0, 1.1, 1.2, 1.3, 1.5]
MAX_POINTS = 12       # 1券種1レースあたりの上限点数 (期待値の高い順)
MIN_BETS_FIT = 150    # 方針を選ぶのに最低限必要な賭け数


def payout_map(pay: dict, kind: str) -> dict[tuple, int]:
    out = {}
    for combo, amt in pay.get(kind, []):
        nums = tuple(int(x) for x in combo.split("-") if x.isdigit())
        out[nums if kind in ("単勝", "馬単", "三連単") else tuple(sorted(nums))] = amt
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", default=",".join(map(str, THRESHOLDS)), help="動作確認用に下げられる")
    args = ap.parse_args()
    thresholds = [float(x) for x in args.thresholds.split(",")]
    oos = pd.read_csv(OOS, dtype={"race_id": str})
    con = sqlite3.connect(DB)
    pays = {rid: json.loads(p or "{}") for rid, p in con.execute("SELECT race_id, payouts FROM races WHERE fetched=1")}
    con.close()

    bets = []   # 1行 = 1点: race_id, year, kind, prob, ev, payout
    n_races = 0
    for rid, grp in oos.groupby("race_id"):
        pay = pays.get(rid)
        if not pay or "単勝" not in pay:
            continue
        n_races += 1
        year = str(grp.date.iloc[0])[:4]
        um = grp.umaban_id.astype(int).tolist()
        p_b = dict(zip(um, grp.p_blend))
        p_m = dict(zip(um, grp.p_market))
        odds = dict(zip(um, grp.odds))
        cb, cm = strategy.combo_probs(p_b), strategy.combo_probs(p_m)
        tables = {"単勝": ({(u,): p for u, p in p_b.items()}, None), "馬連": (cb["umaren"], cm["umaren"]),
                  "ワイド": (cb["wide"], cm["wide"]), "馬単": (cb["exacta"], cm["exacta"]),
                  "三連複": (cb["trio"], cm["trio"]), "三連単": (cb["trifecta"], cm["trifecta"])}
        for kind, (tb, tm) in tables.items():
            pm = payout_map(pay, kind)
            rows = []
            for combo, p in tb.items():
                if p < PMIN[kind]:
                    continue
                if kind == "単勝":
                    o = odds.get(combo[0]) or 0.0
                else:
                    q = tm.get(combo, 0.0)
                    o = (1 - TAKEOUT[kind]) / q if q > 0 else 0.0
                if o <= 0:
                    continue
                rows.append((p * o, p, o, pm.get(combo, 0)))
            rows.sort(key=lambda r: -r[0])
            for ev, p, o, payout in rows[:MAX_POINTS]:
                if ev < thresholds[0]:
                    break
                bets.append(dict(race_id=rid, year=year, kind=kind, ev=ev, prob=p, odds=o, payout=payout))
    df = pd.DataFrame(bets, columns=["race_id", "year", "kind", "ev", "prob", "odds", "payout"])
    print(f"races {n_races}, candidate bets {len(df)}", flush=True)
    if df.empty:
        print("期待値が下限を超える組がありません。"); return

    def stat(d: pd.DataFrame) -> dict:
        if d.empty:
            return dict(bets=0, races=0, hit_rate=0.0, roi=0.0, profit=0)
        return dict(bets=int(len(d)), races=int(d.race_id.nunique()), hit_rate=float((d.payout > 0).mean()),
                    roi=float(d.payout.sum() / (100 * len(d))), profit=int(d.payout.sum() - 100 * len(d)))

    fit, test = df[df.year <= "2025"], df[df.year >= "2026"]
    if fit.empty or test.empty:
        mid = sorted(df.race_id.unique())[len(df.race_id.unique()) // 2]
        fit, test = df[df.race_id < mid], df[df.race_id >= mid]

    grid = []
    policy = {}
    print("\n== 券種 × 期待値の下限 (fit=方針を選ぶ期間 / test=検証) ==")
    for kind in TAKEOUT:
        best = None
        for k in thresholds:
            f = stat(fit[(fit.kind == kind) & (fit.ev >= k)])
            t = stat(test[(test.kind == kind) & (test.ev >= k)])
            grid.append(dict(kind=kind, threshold=k, fit=f, test=t))
            print(f"{kind} ev>={k:.1f}: fit {f['bets']:5d}点 的中{f['hit_rate']*100:5.1f}% 回収{f['roi']*100:6.1f}% | "
                  f"test {t['bets']:5d}点 的中{t['hit_rate']*100:5.1f}% 回収{t['roi']*100:6.1f}%")
            if f["bets"] >= MIN_BETS_FIT and (best is None or f["roi"] > best[1]["roi"]):
                best = (k, f)
        if best and best[1]["roi"] >= 1.0:
            policy[kind] = dict(threshold=best[0], pmin=PMIN[kind], fit=best[1])
    print("\n== 採用 (fit で回収率100%以上の券種と下限) ==")
    for kind, v in policy.items():
        print(f"  {kind}: ev >= {v['threshold']}  (fit {v['fit']['bets']}点 回収 {v['fit']['roi']*100:.1f}%)")

    def portfolio(d: pd.DataFrame) -> dict:
        parts = [d[(d.kind == k) & (d.ev >= v["threshold"])] for k, v in policy.items()]
        return stat(pd.concat(parts) if parts else d.iloc[0:0])

    result = dict(
        policy={k: dict(threshold=v["threshold"], pmin=v["pmin"]) for k, v in policy.items()},
        max_points=MAX_POINTS, takeout=TAKEOUT,
        fit=portfolio(fit), test=portfolio(test),
        fit_period=[str(oos[oos.date.astype(str).str[:4] <= "2025"].date.min()), str(oos[oos.date.astype(str).str[:4] <= "2025"].date.max())],
        test_period=[str(oos[oos.date.astype(str).str[:4] >= "2026"].date.min()), str(oos[oos.date.astype(str).str[:4] >= "2026"].date.max())],
        test_by_kind={k: stat(test[(test.kind == k) & (test.ev >= v["threshold"])]) for k, v in policy.items()},
        grid=grid, races_fit=int(fit.race_id.nunique()) if not fit.empty else 0,
        races_test=int(test.race_id.nunique()) if not test.empty else 0,
    )
    print("\nfit  :", result["fit"])
    print("test :", result["test"])
    for k, v in result["test_by_kind"].items():
        print(f"  test {k}: {v}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=1)
    print("saved", OUT)


if __name__ == "__main__":
    main()
