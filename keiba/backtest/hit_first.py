"""的中率重視の買い方の検証: オッズを見ずに、モデルの本命 (勝率1位) だけを単勝・複勝で買ったら。

    python backtest/hit_first.py

学習外の予想 (oos.csv) と実際の払戻で、的中率と回収率を数える。本命の勝率の高さ (自信) で区切って比べる。
"""
from __future__ import annotations

import json
import os
import sqlite3

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    d = pd.read_csv(os.path.join(HERE, "data", "oos.csv"), dtype={"race_id": str})
    d = d[d.finish > 0]
    c = sqlite3.connect(os.path.join(HERE, "data", "races.sqlite"))
    pay = {r: json.loads(p or "{}") for r, p in c.execute("SELECT race_id, payouts FROM races")}
    info = pd.read_sql("SELECT race_id, surface, cls FROM races", c)
    d["p"] = d.p_model / d.groupby("race_id").p_model.transform("sum")
    top = d.sort_values("p", ascending=False).groupby("race_id").head(1).merge(info, on="race_id")
    top["newbie"] = top.cls.fillna("").str.contains("新馬|未勝利")

    def ret(row, kind):
        for combo, yen in pay.get(row.race_id, {}).get(kind, []):
            if str(combo) == str(int(row.umaban_id)):
                return yen
        return 0
    top["tan"] = [ret(r, "単勝") for r in top.itertuples()]
    top["fuku"] = [ret(r, "複勝") for r in top.itertuples()]
    print(f"学習外 {len(top):,}レース (2025〜2026年) で、モデルの本命を毎レース100円ずつ買った場合\n")
    print(f"{'本命の勝率':<12}{'レース':>7}{'単勝 的中':>10}{'単勝 回収':>10}{'複勝 的中':>10}{'複勝 回収':>10}")
    for lo, hi, name in ((0, 1, "全部"), (0.3, 1, "30%以上"), (0.4, 1, "40%以上"), (0.5, 1, "50%以上"), (0.6, 1, "60%以上")):
        g = top[(top.p >= lo) & (top.p < hi)]
        if len(g):
            print(f"{name:<12}{len(g):>7}{(g.tan > 0).mean()*100:>9.1f}%{g.tan.mean():>9.1f}%"
                  f"{(g.fuku > 0).mean()*100:>9.1f}%{g.fuku.mean():>9.1f}%")
    print("\n(参考) 新馬・未勝利を除く")
    g = top[~top.newbie]
    print(f"{'全部':<12}{len(g):>7}{(g.tan > 0).mean()*100:>9.1f}%{g.tan.mean():>9.1f}%{(g.fuku > 0).mean()*100:>9.1f}%{g.fuku.mean():>9.1f}%")
    for y in ("2025", "2026"):
        g = top[top.date.astype(str).str[:4] == y]
        g = g[g.p >= 0.4]
        print(f"本命の勝率40%以上 {y}年: {len(g)}レース 複勝 的中 {(g.fuku > 0).mean()*100:.1f}% 回収 {g.fuku.mean():.1f}%")


if __name__ == "__main__":
    main()
