"""その日の全レース (芝・ダート) を予想し、単勝で買うべき馬の一覧を出す。

    python picks.py              # 今日 (日本時間)
    python picks.py 20261011     # 日付指定
    python picks.py 20261011 --all   # 買い目なしのレースも、勝率1位と期待値最大の馬を表示
    python picks.py 20261011 --hit   # 的中重視: オッズを見ずに、本命の勝率が高いレースの本命を複勝で (モンテカルロで当たる数も)

買う条件は models/profit.json の検証済みの方針 (単勝・期待値しきい値・勝率下限・新馬/未勝利は除外)。
オッズは発走前に動くので、買う直前にもう一度実行する。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("KEIBA_PREFER_CURL", "1")

import features as F  # noqa: E402
import montecarlo as mc  # noqa: E402
import workouts  # noqa: E402
import scraper  # noqa: E402
import statmodel  # noqa: E402
import strategy  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
HIT_BUY, HIT_CAND = 0.50, 0.40   # 的中重視: 本命の勝率がこれ以上なら複勝で買う / 候補 (backtest/hit_first.py)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=dt.datetime.now(JST).strftime("%Y%m%d"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--hit", action="store_true", help="的中重視: オッズを見ずに、本命の勝率が高いレースの本命を複勝で買う")
    a = ap.parse_args()
    if not statmodel.available():
        sys.exit("統計モデルを読み込めません (pip install -r requirements.txt)")
    pol = (statmodel.load() or {}).get("value_policy")
    rule = ((pol or strategy.DEFAULT_VALUE_POLICY)["policy"].get("単勝") or {})
    if a.hit:
        print(f"{a.date} 的中重視: 本命の勝率 {HIT_BUY*100:.0f}%以上 → 複勝で買い / {HIT_CAND*100:.0f}%以上 → 候補 (オッズは見ない)")
    else:
        print(f"{a.date} 単勝 買い条件: 期待値 {rule.get('threshold', 1.3)} 以上 / 勝率 {rule.get('pmin', 0.05)*100:.0f}% 以上 / 新馬・未勝利は除く")
    n_races = n_buy = n_work = 0
    day_bets: list[dict] = []
    hit_ps: list[float] = []
    for m in scraper.fetch_meetings(a.date):
        for r in m.races:
            try:
                race = scraper.fetch_shutuba(r.race_id, a.date)
                try:
                    scraper.attach_past(race, scraper.fetch_past(r.race_id))
                except scraper.ScrapeError:
                    pass
                odds = scraper.fetch_all_odds(r.race_id)
                sm = statmodel.predict(race, odds.get("win"))
                if sm:
                    adj = workouts.adjust(sm["probs"], {h.umaban: h.horse_id for h in race.horses}, race.date or a.date)
                    if adj:
                        sm["probs"] = adj["probs"]
                        n_work += 1
            except Exception as e:  # 1レースの失敗で全体を止めない
                print(f"{m.venue}{r.rno}R 取得失敗: {e}")
                continue
            if a.hit and sm:
                bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
                skip = strategy.skip_reason(race, bands)
                u = max(sm["probs"], key=sm["probs"].get)
                pw = sm["probs"][u]
                head = f"{m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m"
                name_u = next((h.name for h in race.horses if h.umaban == u), "")
                if skip:
                    pl = mc.probs(sm["probs"], 20000)["place"].get(u, 0)
                    print(f"  見送り推奨 {head}  (参考の本命 {u}番 {name_u} 勝率{pw*100:.0f}% 3着内{pl*100:.0f}%): {skip}")
                    continue
                n_races += 1
                pl = mc.probs(sm["probs"], 20000)["place"].get(u, 0)
                mark = "◎複勝で買い" if pw >= HIT_BUY else "○候補" if pw >= HIT_CAND else "  見送り"
                if pw >= HIT_CAND or a.all:
                    print(f"{mark} {head}  {u}番 {{names}}  勝率{pw*100:.0f}% 3着内{pl*100:.0f}%".replace("{names}", next((h.name for h in race.horses if h.umaban == u), "")))
                if pw >= HIT_BUY:
                    n_buy += 1
                    hit_ps.append(pl)
                continue
            if not sm or not odds.get("win"):
                print(f"{m.venue}{r.rno}R オッズ未発売 (買い目は発売後に)")
                continue
            bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
            skip = strategy.skip_reason(race, bands)
            if skip:
                print(f"  見送り推奨 {m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m {race.cls or race.grade}: {skip}")
                continue
            n_races += 1
            names = {h.umaban: h.name for h in race.horses}
            buys = strategy.value_bets(sm["probs"], odds, pol, bands).get("単勝", [])
            head = f"{m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m {race.cls or race.grade}"
            sim = mc.probs(sm["probs"], 20000) if buys else None
            for t in buys:
                n_buy += 1
                u = t.combo[0]
                print(f"★買い {head}  {u}番 {names.get(u, '')}  勝率{t.prob*100:.1f}% 3着内{sim['place'].get(u, 0)*100:.0f}% "
                      f"オッズ{t.odds:.1f} 期待値{t.ev:.2f}")
                day_bets.append(dict(race_id=r.race_id, win=sm["probs"], kind="単勝", umaban=u, odds=t.odds, stake=100))
            if not buys and a.all:
                u = max(sm["probs"], key=sm["probs"].get)
                print(f"  見送り {head}  本命 {u}番 {names.get(u, '')} 勝率{sm['probs'][u]*100:.0f}%")
    print(f"対象 {n_races}レース / 買い {n_buy}点 (1点100円なら {n_buy*100}円)")
    wk = workouts.load()
    print(f"調教タイム補正 (JRA-VAN): {n_work}レースに適用" if n_work else
          ("調教タイム補正: この日の調教データが無いので未適用 (jravan/load_weekly.py で取り込む)" if wk else
           "調教タイム補正: 調教データ未取り込み (data/jv_private) のため未適用"))
    if a.hit:
        import numpy as np
        rng = np.random.default_rng(0)
        hits = (rng.random((50000, len(hit_ps))) < np.array(hit_ps)).sum(axis=1) if hit_ps else np.zeros(1)
        print(f"\nモンテカルロ (5万回): 当たる数の平均 {hits.mean():.1f}点 / 1点も当たらない確率 {(hits == 0).mean()*100:.1f}% / "
              f"全部当たる確率 {(hits == len(hit_ps)).mean()*100:.0f}%")
        print("検証 (学習外 2025〜2026年): 本命の勝率50%以上の複勝は 589レースで 的中87.6% 回収95.0%、"
              "60%以上は 187レースで 的中92.5% 回収97.8%。当たりやすいが、平均では少し負ける。")
        return
    if day_bets:
        s = mc.day_pnl(day_bets, 50000)
        print(f"\nモンテカルロ (この日の買い目を5万回模擬、勝率はモデルどおりと仮定): 収支の平均 {s['mean']:+.0f}円 / "
              f"プラスで終わる確率 {s['p_profit']*100:.0f}% / 全部外れる確率 {s['p_zero_hit']*100:.0f}% / "
              f"収支の幅 (5%〜95%) {s['q05']:+.0f}円〜{s['q95']:+.0f}円")
        print("※ 勝率はモデルの推定値。検証では、この買い方の回収率は90%前後 (平均ではマイナス) だった。")


if __name__ == "__main__":
    main()
