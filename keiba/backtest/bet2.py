"""「馬券2点以内」で何をどう買うのが的中率・回収率ともに良いかを、学習外の予想で比べる。

    python backtest/bet2.py

train.py が残した oos.csv (学習に使っていない期間の勝率) と、収集した払戻を使う。
券種 (単勝・馬連・馬単・ワイド・三連複・三連単) と点数 (1〜2点) の全組合せを、
全レース一律に買った場合と、本命の勝率の帯ごとに券種を切り替えた場合で比較する。
帯ごとの切り替え方は 2025年の予想で決め、2026年の予想で確かめる (見てから決めない)。
結果は models/bet2.json に書き、アプリの「2点勝負」がそれに従う。
"""

from __future__ import annotations

import itertools
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
OUT = os.path.join(ROOT, "models", "bet2.json")

KINDS = ["単勝", "馬連", "馬単", "ワイド", "三連複", "三連単"]
# 比べる買い方 (2点以内)
CHOICES = [f"{k}1" for k in KINDS] + [f"{k}2" for k in KINDS] + [
    "単勝1+ワイド1", "単勝1+馬連1", "単勝1+三連複1", "馬連1+ワイド1", "ワイド1+三連複1", "馬連1+三連複1", "馬単1+ワイド1",
]
BUCKETS = [0.0, 0.20, 0.30, 0.40, 1.01]


def payout_of(pay: dict, t: strategy.Ticket) -> int:
    for combo, amt in pay.get(t.kind, []):
        nums = [int(x) for x in combo.split("-") if x.isdigit()]
        if t.kind in ("単勝", "馬単", "三連単"):
            if nums == list(t.combo):
                return amt
        elif sorted(nums) == sorted(t.combo):
            return amt
    return 0


def main():
    oos = pd.read_csv(OOS, dtype={"race_id": str})
    con = sqlite3.connect(DB)
    pays = {rid: json.loads(p or "{}") for rid, p in con.execute("SELECT race_id, payouts FROM races WHERE fetched=1")}
    heads = dict(con.execute("SELECT race_id, heads FROM races"))
    con.close()

    # レースごとに候補と払戻を前計算
    rows = []
    for rid, grp in oos.groupby("race_id"):
        pay = pays.get(rid)
        if not pay or "単勝" not in pay:
            continue
        win = dict(zip(grp.umaban_id.astype(int), grp.p_blend))
        cands = strategy.candidates(win)
        p_top = max(win.values())
        rec = dict(race_id=rid, date=str(grp.date.iloc[0]), year=str(grp.date.iloc[0])[:4], p_top=p_top,
                   heads=heads.get(rid, len(win)))
        for k, ts in cands.items():
            for i, t in enumerate(ts[:2]):
                rec[f"{k}{i+1}_pay"] = payout_of(pay, t)
        rows.append(rec)
    df = pd.DataFrame(rows)
    df["bucket"] = pd.cut(df.p_top, BUCKETS, right=False, labels=False)
    print(f"races {len(df)}  years {df.year.value_counts().to_dict()}", flush=True)

    def choice_return(d: pd.DataFrame, choice: str):
        """(投資, 払戻, 的中レース数)"""
        inv = ret = 0
        hit = pd.Series(False, index=d.index)
        for kind, n in strategy.parse_choice(choice):
            for i in range(1, n + 1):
                col = f"{kind}{i}_pay"
                inv += 100 * len(d)
                ret += d[col].sum()
                hit |= d[col] > 0
        return inv, ret, int(hit.sum())

    def table(d: pd.DataFrame) -> pd.DataFrame:
        out = []
        for c in CHOICES:
            inv, ret, hit = choice_return(d, c)
            out.append(dict(choice=c, races=len(d), points=inv // 100, hit_rate=hit / len(d) if len(d) else 0,
                            roi=ret / inv if inv else 0))
        return pd.DataFrame(out)

    print("\n== 全レース一律 (学習外 全期間) ==")
    t_all = table(df).sort_values("roi", ascending=False)
    print(t_all.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    # 帯ごとの最良を 2025 で選び、2026 で検証
    fit_df, test_df = df[df.year <= "2025"], df[df.year >= "2026"]
    if test_df.empty or fit_df.empty:            # 動作確認用: データが片寄っていれば日付の中央で分ける
        mid = df.date.sort_values().iloc[len(df) // 2]
        fit_df, test_df = df[df.date < mid], df[df.date >= mid]
    policy_choice = []
    print("\n== 帯ごとの成績 (2025 で選ぶ) ==")
    for b in range(len(BUCKETS) - 1):
        d = fit_df[fit_df.bucket == b]
        t = table(d)
        # 的中率と回収率の両方: 回収率 0.8 以上の中で的中率が最も高いもの。無ければ回収率最大
        ok = t[t.roi >= 0.80]
        best = (ok.sort_values("hit_rate", ascending=False) if len(ok) else t.sort_values("roi", ascending=False)).iloc[0]
        policy_choice.append(best.choice)
        print(f"帯 {BUCKETS[b]:.2f}-{BUCKETS[b+1]:.2f}: {len(d)}レース → {best.choice} 的中率 {best.hit_rate:.3f} 回収率 {best.roi:.3f}")
        print(t.sort_values("roi", ascending=False).head(6).to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    def adaptive(d: pd.DataFrame, choices: list[str]):
        inv = ret = hit = 0
        for b, c in enumerate(choices):
            sub = d[d.bucket == b]
            i, r, h = choice_return(sub, c)
            inv, ret, hit = inv + i, ret + r, hit + h
        return dict(races=len(d), points=inv // 100, hit_rate=hit / len(d) if len(d) else 0, roi=ret / inv if inv else 0)

    result = dict(buckets=BUCKETS, choice=policy_choice,
                  fit=adaptive(fit_df, policy_choice), test=adaptive(test_df, policy_choice),
                  fit_period=[str(fit_df.date.min()), str(fit_df.date.max())],
                  test_period=[str(test_df.date.min()), str(test_df.date.max())],
                  uniform_test={r.choice: dict(hit_rate=r.hit_rate, roi=r.roi) for r in table(test_df).itertuples()},
                  uniform_all={r.choice: dict(hit_rate=r.hit_rate, roi=r.roi, points=int(r.points)) for r in t_all.itertuples()})
    # 帯ごとの検証成績 (上の内包表記は読みにくいので素直に)
    result["by_bucket_test"] = []
    for b in range(len(BUCKETS) - 1):
        sub = test_df[test_df.bucket == b]
        i, r, h = choice_return(sub, policy_choice[b])
        result["by_bucket_test"].append(dict(bucket=[BUCKETS[b], BUCKETS[b + 1]], choice=policy_choice[b], races=len(sub),
                                             hit_rate=h / len(sub) if len(sub) else 0, roi=r / i if i else 0))
    print("\n== 切り替え方針 ==", policy_choice)
    print("2025 (選んだ期間):", result["fit"])
    print("2026 (検証):     ", result["test"])
    for r in result["by_bucket_test"]:
        print("  ", r)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=1)
    print("saved", OUT)


if __name__ == "__main__":
    main()
