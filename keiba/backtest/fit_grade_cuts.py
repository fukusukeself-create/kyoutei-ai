"""分野ごとの S〜G の境目を、実際の開催日の全出走馬の分布から決めて models/grade_cuts.json に書く。

    python backtest/fit_grade_cuts.py 20261017 20261018

上位5%が S、次の10%が A、15% B、20% C、20% D、15% E、10% F、残り G になるように境目を置く。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import evidence  # noqa: E402
import grades as G  # noqa: E402
import scraper  # noqa: E402
import statmodel  # noqa: E402


def main() -> None:
    m = statmodel.load()
    bw = m["stats"].get("base_win", 0.072)
    scores = []
    for date in sys.argv[1:]:
        for mt in scraper.fetch_meetings(date):
            for r in mt.races:
                try:
                    race = scraper.fetch_shutuba(r.race_id, date)
                    scraper.attach_past(race, scraper.fetch_past(r.race_id))
                    scraper.attach_oikiri(race, scraper.fetch_oikiri(r.race_id))
                except scraper.ScrapeError:
                    continue
                sm = statmodel.predict(race)
                if not sm:
                    continue
                wk = evidence.race_workouts(race)
                for h in race.horses:
                    f = sm["features"].get(h.umaban)
                    if f is not None:
                        scores.append(G.factor_scores(f, h, wk.get(h.umaban), bw))
        print(date, len(scores), flush=True)
    cuts = G.fit_cuts(scores)
    with open(os.path.join(ROOT, "models", "grade_cuts.json"), "w", encoding="utf-8") as fp:
        json.dump(dict(dates=sys.argv[1:], horses=len(scores), cuts=cuts), fp, ensure_ascii=False, indent=1)
    for k, v in cuts.items():
        print(k, [round(x, 4) for x in v])


if __name__ == "__main__":
    main()
