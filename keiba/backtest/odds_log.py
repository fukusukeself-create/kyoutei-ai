"""オッズの時系列を記録する。発走前の「オッズの動き」を将来の統計で使うための材料集め。

    python backtest/odds_log.py 20261011 --from 09:00 --until 16:45 --every 10
    python backtest/odds_log.py 20261011 --once          # 今すぐ1回だけ

data/odds_log/<日付>.jsonl.gz に、1行 = 1レースの1時点 {t, race_id, win, place, umaren, wide, sanrenpuku} を追記する。
発走を過ぎたレースは記録しない。同じ日のうちに何度でも再実行できる (追記)。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("KEIBA_PREFER_CURL", "1")

import scraper  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
KINDS = ("win", "place", "umaren", "wide", "sanrenpuku")
OUT_DIR = os.path.join(ROOT, "data", "odds_log")


def snapshot(date: str) -> int:
    """全レースの現在のオッズを1回記録する。記録したレース数を返す。"""
    now = dt.datetime.now(JST)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{date}.jsonl.gz")
    n = 0
    rows = []
    for m in scraper.fetch_meetings(date):
        for r in m.races:
            start = getattr(r, "start", "") or ""
            if start and date == now.strftime("%Y%m%d") and start <= now.strftime("%H:%M"):
                continue   # 発走済み
            rec = {k: scraper.fetch_odds(r.race_id, k) for k in KINDS}
            if not rec["win"]:
                continue
            rows.append(dict(t=now.strftime("%Y-%m-%d %H:%M"), race_id=r.race_id, venue=m.venue, rno=r.rno, start=start, **rec))
            n += 1
    with gzip.open(path, "at", encoding="utf-8") as fp:
        for x in rows:
            fp.write(json.dumps(x, ensure_ascii=False) + "\n")
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--from", dest="t_from", default="00:00")
    ap.add_argument("--until", default="23:59")
    ap.add_argument("--every", type=float, default=10, help="分")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    while True:
        now = dt.datetime.now(JST).strftime("%H:%M")
        if a.once or a.t_from <= now <= a.until:
            n = snapshot(a.date)
            print(f"{dt.datetime.now(JST):%H:%M} {n}レース記録", flush=True)
        if a.once or now > a.until:
            break
        time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
