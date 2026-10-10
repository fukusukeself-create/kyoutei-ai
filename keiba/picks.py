"""その日の全レース (芝・ダート) を予想し、単勝で買うべき馬の一覧を出す。

    python picks.py              # 今日 (日本時間)
    python picks.py 20261011     # 日付指定
    python picks.py 20261011 --all   # 買い目なしのレースも、勝率1位と期待値最大の馬を表示

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
import scraper  # noqa: E402
import statmodel  # noqa: E402
import strategy  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=dt.datetime.now(JST).strftime("%Y%m%d"))
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if not statmodel.available():
        sys.exit("統計モデルを読み込めません (pip install -r requirements.txt)")
    pol = (statmodel.load() or {}).get("value_policy")
    rule = ((pol or strategy.DEFAULT_VALUE_POLICY)["policy"].get("単勝") or {})
    print(f"{a.date} 単勝 買い条件: 期待値 {rule.get('threshold', 1.3)} 以上 / 勝率 {rule.get('pmin', 0.05)*100:.0f}% 以上 / 新馬・未勝利は除く")
    n_races = n_buy = 0
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
            except Exception as e:  # 1レースの失敗で全体を止めない
                print(f"{m.venue}{r.rno}R 取得失敗: {e}")
                continue
            if not sm or not odds.get("win"):
                print(f"{m.venue}{r.rno}R オッズ未発売 (買い目は発売後に)")
                continue
            if race.surface == "障":
                print(f"{m.venue}{r.rno}R 障害は学習対象外のため買い目なし")
                continue
            n_races += 1
            bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
            names = {h.umaban: h.name for h in race.horses}
            buys = strategy.value_bets(sm["probs"], odds, pol, bands).get("単勝", [])
            head = f"{m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m {race.cls or race.grade}"
            for t in buys:
                n_buy += 1
                print(f"★買い {head}  {t.combo[0]}番 {names.get(t.combo[0], '')}  勝率{t.prob*100:.1f}% オッズ{t.odds:.1f} 期待値{t.ev:.2f}")
            if not buys and a.all:
                u = max(sm["probs"], key=sm["probs"].get)
                print(f"  見送り {head}  本命 {u}番 {names.get(u, '')} 勝率{sm['probs'][u]*100:.0f}%")
    print(f"対象 {n_races}レース / 買い {n_buy}点 (1点100円なら {n_buy*100}円)")


if __name__ == "__main__":
    main()
