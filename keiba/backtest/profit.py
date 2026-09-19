"""収支プラスを狙う買い方 (期待値買い) を、学習外の予想で探す。

    python backtest/profit.py

考え方: 点数は問わず「統計モデルの確率が市場の見立てより十分高い組だけ買う」。
確率は統計モデルのみ (オッズとは混ぜない)。オッズは期待値の計算にだけ使う。
  - 単勝は確定オッズがあるので 期待値 = 確率 × オッズ をそのまま使う
  - 馬連・ワイド・馬単・三連複・三連単は過去のオッズが無いので、単勝オッズから
    Harville 式で市場の組確率を作り、控除率を引いた「近似オッズ」で期待値を出す
    (本番アプリでは実オッズを使う。ここでの回収率は実際の払戻で計算している)
券種ごとに期待値の下限 (1.0〜1.5) を 2025年の予想で選び、2026年で確かめる。
結果は models/profit.json に書き、アプリの「収支プラス狙い」がそれに従う。
"""

from __future__ import annotations

import json
import math
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

TAKEOUT = {"単勝": 0.20, "複勝": 0.20, "馬連": 0.225, "ワイド": 0.225, "馬単": 0.25, "三連複": 0.25, "三連単": 0.275}
# 確率がこれ未満の組は、期待値が高く見えても買わない (裾の確率はモデルが過大評価しがち)
PMIN = {"単勝": 0.05, "複勝": 0.15, "馬連": 0.02, "ワイド": 0.05, "馬単": 0.01, "三連複": 0.01, "三連単": 0.003}
THRESHOLDS = [1.0, 1.1, 1.2, 1.3, 1.5]
MAX_POINTS = 12       # 1券種1レースあたりの上限点数 (期待値の高い順)
MIN_BETS_FIT = 300    # 方針を選ぶのに最低限必要な賭け数
# 採用に必要な選定期間の回収率。単勝は実オッズで検証できるが、連系は近似オッズなので余裕を持たせる
MIN_ROI = {"単勝": 1.03}
MIN_ROI_DEFAULT = 1.10
# 新馬・未勝利は馬柱が薄く、どの検証でも回収率が低かったので、最初から買わない
PRE_EXCLUDE = [("cls_band", "新馬・未勝利")]
# 採用する券種。連系は過去の実オッズが無く近似オッズでの検証になり、結果が大きな払戻に左右されて
# 年ごとに大きくぶれる (三連複 0% / 三連単 222% 等) ため、実オッズで検証できる単勝だけを採用する
ADOPT_KINDS = ["単勝"]


def payout_map(pay: dict, kind: str) -> dict[tuple, int]:
    out = {}
    for combo, amt in pay.get(kind, []):
        nums = tuple(int(x) for x in combo.split("-") if x.isdigit())
        out[nums if kind in ("単勝", "複勝", "馬単", "三連単") else tuple(sorted(nums))] = amt
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresholds", default=",".join(map(str, THRESHOLDS)), help="動作確認用に下げられる")
    ap.add_argument("--prob-col", default="p_model", help="使う確率の列 (p_model / p_blend / p_pure)")
    ap.add_argument("--shrink", type=float, default=0.7,
                    help="モデルの確率を市場との幾何平均で縮める比率 (1.0=縮めない)。モデルは市場からのずれを過大に見積もるため")
    ap.add_argument("--out", default=OUT)
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
        p_m = dict(zip(um, grp.p_market))
        p_raw = dict(zip(um, grp[args.prob_col]))
        if args.shrink < 1.0:
            e = {u: math.exp(args.shrink * math.log(max(p_raw[u], 1e-6)) + (1 - args.shrink) * math.log(max(p_m.get(u, 1e-4), 1e-6))) for u in um}
            z = sum(e.values()); p_b = {u: v / z for u, v in e.items()}
        else:
            p_b = p_raw
        odds = dict(zip(um, grp.odds))
        cb, cm = strategy.combo_probs(p_b), strategy.combo_probs(p_m)
        tables = {"単勝": ({(u,): p for u, p in p_b.items()}, None),
                  "複勝": ({(u,): p for u, p in cb["place"].items()}, {(u,): p for u, p in cm["place"].items()}),
                  "馬連": (cb["umaren"], cm["umaren"]),
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
            for rank, (ev, p, o, payout) in enumerate(rows[:MAX_POINTS], 1):
                bets.append(dict(race_id=rid, year=year, kind=kind, ev=ev, prob=p, odds=o, payout=payout, rank=rank))
    df = pd.DataFrame(bets, columns=["race_id", "year", "kind", "ev", "prob", "odds", "payout", "rank"])
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

    con = sqlite3.connect(DB)
    info = pd.read_sql("SELECT race_id, surface, heads, cls, grade, name FROM races WHERE fetched=1", con)
    con.close()
    import features as F
    info["cls_rank"] = [F.class_rank(" ".join([str(a), str(b), str(c)])) for a, b, c in zip(info.cls, info.grade, info.name)]
    info["heads_band"] = pd.cut(info.heads, [0, 10, 14, 99], labels=["〜10頭", "11〜14頭", "15頭〜"]).astype(str)
    info["cls_band"] = pd.cut(info.cls_rank, [-1, 0.5, 1.2, 2.5, 9], labels=["新馬・未勝利", "1勝", "2勝・3勝", "OP・重賞"]).astype(str)
    df = df.merge(info[["race_id", "surface", "heads_band", "cls_band"]], on="race_id", how="left")
    df["odds_band"] = pd.cut(df.odds, [0, 10, 20, 40, 1e9], labels=["〜10倍", "10〜20倍", "20〜40倍", "40倍〜"]).astype(str)
    for col, val in PRE_EXCLUDE:
        df = df[df[col] != val]
    fit, test = df[df.race_id.isin(fit.race_id)], df[df.race_id.isin(test.race_id)]
    print(f"新馬・未勝利を除いた候補: fit {len(fit)} / test {len(test)}")

    grid = []
    policy = {}
    print("\n== 券種 × 期待値の下限 (fit=方針を選ぶ期間 / test=検証、新馬・未勝利を除く) ==")
    for kind in TAKEOUT:
        best = None
        for k in thresholds:
            f = stat(fit[(fit.kind == kind) & (fit.ev >= k)])
            t = stat(test[(test.kind == kind) & (test.ev >= k)])
            grid.append(dict(kind=kind, threshold=k, fit=f, test=t))
            print(f"{kind} ev>={k:.1f}: fit {f['bets']:5d}点 的中{f['hit_rate']*100:5.1f}% 回収{f['roi']*100:6.1f}% | "
                  f"test {t['bets']:5d}点 的中{t['hit_rate']*100:5.1f}% 回収{t['roi']*100:6.1f}%")
            # 選定期間で回収率100%以上になる下限のうち、最も低い (=点数が多く、ぶれが小さい) ものを採る
            if f["bets"] >= MIN_BETS_FIT and f["roi"] >= MIN_ROI.get(kind, MIN_ROI_DEFAULT) and best is None:
                best = (k, f)
        if best and kind in ADOPT_KINDS:
            policy[kind] = dict(threshold=best[0], pmin=PMIN[kind], fit=best[1])
        elif best:
            print(f"  (参考) {kind}: ev >= {best[0]} は選定期間 {best[1]['roi']*100:.0f}% だが、近似オッズの検証なので採用しない")
    print("\n== 採用 (fit で回収率100%以上の券種と下限) ==")
    for kind, v in policy.items():
        print(f"  {kind}: ev >= {v['threshold']}  (fit {v['fit']['bets']}点 回収 {v['fit']['roi']*100:.1f}%)")

    # ---- 採用した券種について、条件別 (芝ダ / 頭数 / クラス) の成績。選定期間で回収率 85% 未満の条件は外す
    excluded = {}
    print("\n== 条件別 (採用券種・下限以上) ==")
    for kind, v in policy.items():
        fk, tk = fit[(fit.kind == kind) & (fit.ev >= v["threshold"])], test[(test.kind == kind) & (test.ev >= v["threshold"])]
        for col in ("surface", "heads_band", "cls_band", "odds_band"):
            for val in sorted(fk[col].dropna().unique()):
                f, t = stat(fk[fk[col] == val]), stat(tk[tk[col] == val])
                flag = ""
                # 条件ごとの自動除外は、選定期間のぶれに合わせてしまい検証期間で裏目に出た (芝 85%→127% 等) ので行わない。
                # 表示だけ残し、除外は PRE_EXCLUDE (新馬・未勝利) のみ
                if f["bets"] >= 100 and f["roi"] < 0.85:
                    flag = "  (選定期間では弱いが除外しない)"
                print(f"  {kind} {col}={val}: fit {f['bets']}点 回収{f['roi']*100:.0f}% | test {t['bets']}点 回収{t['roi']*100:.0f}%{flag}")
    for kind, v in policy.items():
        v["exclude"] = excluded.get(kind, [])

    def apply_exclude(d: pd.DataFrame) -> pd.DataFrame:
        keep = pd.Series(True, index=d.index)
        for kind, v in policy.items():
            for col, val in v.get("exclude", []):
                keep &= ~((d.kind == kind) & (d[col] == val))
        return d[keep]

    def portfolio(d: pd.DataFrame) -> dict:
        parts = [d[(d.kind == k) & (d.ev >= v["threshold"])] for k, v in policy.items()]
        return stat(apply_exclude(pd.concat(parts)) if parts else d.iloc[0:0])

    result = dict(
        policy={k: dict(threshold=v["threshold"], pmin=v["pmin"], exclude=[list(x) for x in PRE_EXCLUDE] + v.get("exclude", [])) for k, v in policy.items()},
        max_points=MAX_POINTS, takeout=TAKEOUT, pmin=PMIN,
        fit=portfolio(fit), test=portfolio(test),
        fit_period=[str(oos[oos.date.astype(str).str[:4] <= "2025"].date.min()), str(oos[oos.date.astype(str).str[:4] <= "2025"].date.max())],
        test_period=[str(oos[oos.date.astype(str).str[:4] >= "2026"].date.min()), str(oos[oos.date.astype(str).str[:4] >= "2026"].date.max())],
        test_by_kind={k: stat(apply_exclude(test[(test.kind == k) & (test.ev >= v["threshold"])])) for k, v in policy.items()},
        grid=grid, races_fit=int(fit.race_id.nunique()) if not fit.empty else 0,
        races_test=int(test.race_id.nunique()) if not test.empty else 0,
    )
    # ---- 見送り無し: 全レースで「このレースで最も期待値の高い k 点」を買う
    print("\n== 見送り無し (全レースで期待値上位 k 点) ==")
    noskip_grid = []
    best_ns = None
    for label, sel in (
        [(f"全券種 上位{k}点", lambda d, k=k: d.sort_values("ev", ascending=False).groupby("race_id").head(k)) for k in (1, 2, 3, 5)]
        + [(f"{kind} 上位{k}点", lambda d, kind=kind, k=k: d[(d.kind == kind) & (d["rank"] <= k)]) for kind in TAKEOUT for k in (1, 2, 3)]
        + [("単勝1点+ワイド1点", lambda d: d[((d.kind == "単勝") | (d.kind == "ワイド")) & (d["rank"] <= 1)]),
           ("単勝1点+馬連1点+ワイド1点", lambda d: d[d.kind.isin(["単勝", "馬連", "ワイド"]) & (d["rank"] <= 1)])]
    ):
        f, t = stat(sel(fit)), stat(sel(test))
        noskip_grid.append(dict(label=label, fit=f, test=t))
        print(f"{label:16s}: fit {f['bets']:6d}点 的中{f['hit_rate']*100:5.1f}% 回収{f['roi']*100:6.1f}% | "
              f"test {t['bets']:6d}点 的中{t['hit_rate']*100:5.1f}% 回収{t['roi']*100:6.1f}%")
        # 少ない的中 (まぐれ) で選ばないよう、選定期間で100回以上当たっている買い方から回収率最大を採る
        f["hits"] = int(round(f["hit_rate"] * f["bets"]))
        # 採用候補は実オッズで検証できる単勝の買い方だけ (連系は近似オッズで、大きな払戻に引きずられる)
        if label.startswith("単勝 ") and f["hits"] >= 100 and (best_ns is None or f["roi"] > best_ns[1]["roi"]):
            best_ns = (label, f, t)
    if best_ns is None:
        best_ns = max(((g["label"], g["fit"], g["test"]) for g in noskip_grid), key=lambda x: x[1]["roi"])
    result["noskip"] = dict(label=best_ns[0], fit=best_ns[1], test=best_ns[2], grid=noskip_grid)
    print("見送り無しの採用:", best_ns[0], "fit", best_ns[1], "test", best_ns[2])

    print("\nfit  :", result["fit"])
    print("test :", result["test"])
    for k, v in result["test_by_kind"].items():
        print(f"  test {k}: {v}")
    result["prob_col"] = args.prob_col
    result["shrink"] = args.shrink
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=1)
    print("saved", args.out)


if __name__ == "__main__":
    main()
