"""過去レースの収集。netkeiba から 馬柱 (出走時点の情報) と 結果・払戻 を取り、SQLite に貯める。

    python backtest/collect.py --from 2023-01 --to 2026-09 --workers 3

中断しても再実行すれば続きから取る (取得済みレースは飛ばす)。
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scraper  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "races.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
  race_id TEXT PRIMARY KEY, date TEXT, venue TEXT, rno INTEGER, name TEXT, start TEXT,
  course TEXT, surface TEXT, distance INTEGER, turn TEXT, weather TEXT, condition TEXT,
  cls TEXT, grade TEXT, heads INTEGER, payouts TEXT, fetched INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runners (
  race_id TEXT, umaban INTEGER, waku INTEGER, name TEXT, horse_id TEXT, sex_age TEXT,
  jockey TEXT, weight REAL, trainer TEXT, sire TEXT, dam TEXT, damsire TEXT, style TEXT,
  interval TEXT, body_weight TEXT, rest_note TEXT, past TEXT,
  finish INTEGER, ninki INTEGER, odds REAL, time TEXT, margin TEXT,
  PRIMARY KEY (race_id, umaban)
);
CREATE TABLE IF NOT EXISTS days (date TEXT PRIMARY KEY, listed INTEGER DEFAULT 0);
"""

lock = threading.Lock()


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=60, check_same_thread=False)
    con.executescript(SCHEMA)
    return con


def months(frm: str, to: str):
    y, m = map(int, frm.split("-"))
    y2, m2 = map(int, to.split("-"))
    while (y, m) <= (y2, m2):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def list_days(con, frm: str, to: str, today: str):
    for y, m in months(frm, to):
        try:
            ds = scraper.fetch_kaisai_dates(y, m)
        except scraper.ScrapeError as e:
            print("calendar", y, m, e, flush=True)
            continue
        for d in ds:
            if d < today:
                con.execute("INSERT OR IGNORE INTO days(date) VALUES (?)", (d,))
        con.commit()
        time.sleep(0.5)


def list_races(con):
    days = [r[0] for r in con.execute("SELECT date FROM days WHERE listed=0 ORDER BY date")]
    for d in days:
        try:
            ms = scraper.fetch_meetings(d)
        except scraper.ScrapeError as e:
            print("list", d, e, flush=True)
            continue
        for m in ms:
            for r in m.races:
                con.execute("INSERT OR IGNORE INTO races(race_id, date, venue, rno, name, start, course, heads, grade)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (r.race_id, d, m.venue, r.rno, r.name, r.start, r.course, r.heads, r.grade))
        con.execute("UPDATE days SET listed=1 WHERE date=?", (d,))
        con.commit()
        print("listed", d, sum(len(m.races) for m in ms), flush=True)
        time.sleep(0.3)


def fetch_one(race_id: str):
    html_p = scraper._get(f"{scraper.BASE}/race/shutuba_past.html?race_id={race_id}", retries=4)
    runners = [r for r in scraper.parse_past_page(html_p) if r["umaban"]]
    html_r = scraper._get(f"{scraper.BASE}/race/result.html?race_id={race_id}", retries=4)
    soup = BeautifulSoup(html_r, "html.parser")
    hdr = scraper._parse_race_header(soup, race_id)
    tbl = soup.select_one("table.RaceTable01")
    results = {}
    if tbl is not None:
        for tr in tbl.select("tr.HorseList"):
            tds = [scraper._text(td) for td in tr.select("td")]
            if len(tds) < 11:
                continue
            u = scraper._to_int(tds[2])
            if u:
                results[u] = dict(finish=scraper._to_int(tds[0]), time=tds[7], margin=tds[8],
                                  ninki=scraper._to_int(tds[9]), odds=scraper._to_float(tds[10]),
                                  body_weight=tds[14] if len(tds) > 14 else "")
    payouts = {}
    label = {"Tansho": "単勝", "Fukusho": "複勝", "Wakuren": "枠連", "Umaren": "馬連",
             "Wide": "ワイド", "Umatan": "馬単", "Fuku3": "三連複", "Tan3": "三連単"}
    for tr in soup.select("table.Payout_Detail_Table tr"):
        cls = next((c for c in tr.get("class", []) if c in label), None)
        if not cls:
            continue
        res_td, pay_td = tr.select_one("td.Result"), tr.select_one("td.Payout")
        if res_td is None or pay_td is None:
            continue
        pays = [scraper._to_int(p) for p in re.findall(r"[\d,]+円", scraper._text(pay_td).replace(",", ""))]
        if res_td.select("ul"):
            nums = [[scraper._text(li) for li in ul.select("li") if scraper._text(li)] for ul in res_td.select("ul")]
        elif cls == "Fukusho":
            nums = [[n] for n in [scraper._text(d) for d in res_td.select("div") if scraper._text(d)]]
        else:
            nums = [[scraper._text(d) for d in res_td.select("div") if scraper._text(d)]]
        combos = ["-".join(n) for n in nums if n]
        payouts[label[cls]] = list(zip(combos, [p for p in pays if p is not None]))
    return hdr, runners, results, payouts


def save(con, race_id, hdr, runners, results, payouts):
    with lock:
        con.execute("UPDATE races SET venue=?, name=?, start=?, course=?, surface=?, distance=?, turn=?, weather=?,"
                    " condition=?, cls=?, grade=?, heads=?, payouts=?, fetched=1 WHERE race_id=?",
                    (hdr["venue"], hdr["name"], hdr["start"], hdr["course"], hdr["surface"], hdr["distance"],
                     hdr["turn"], hdr["weather"], hdr["condition"], hdr["cls"], hdr["grade"],
                     hdr["heads"] or len(runners), json.dumps(payouts, ensure_ascii=False), race_id))
        for r in runners:
            res = results.get(r["umaban"], {})
            bw = r.get("body_weight") or res.get("body_weight", "")
            con.execute("INSERT OR REPLACE INTO runners VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (race_id, r["umaban"], r["waku"], r["name"], r["horse_id"], r["sex_age"], r["jockey"],
                         r["weight"], r["trainer"], r["sire"], r["dam"], r["damsire"], r["style"], r["interval"],
                         bw, r["rest_note"], json.dumps(r["past"], ensure_ascii=False),
                         res.get("finish"), res.get("ninki"), res.get("odds"), res.get("time"), res.get("margin")))
        con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2023-01")
    ap.add_argument("--to", dest="to", default=date.today().strftime("%Y-%m"))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--skip-list", action="store_true", help="開催日・レース一覧の取り直しをせず、未取得レースだけ取る")
    args = ap.parse_args()
    today = date.today().strftime("%Y%m%d")
    con = db()
    if not args.skip_list:
        list_days(con, args.frm, args.to, today)
        list_races(con)
    todo = [r[0] for r in con.execute("SELECT race_id FROM races WHERE fetched=0 ORDER BY race_id")]
    total = con.execute("SELECT COUNT(*) FROM races").fetchone()[0]
    print(f"races total={total} todo={len(todo)}", flush=True)
    done = 0
    t0 = time.time()

    def work(rid):
        try:
            hdr, runners, results, payouts = fetch_one(rid)
            if not runners or not results:
                return rid, "empty"
            save(con, rid, hdr, runners, results, payouts)
            time.sleep(args.sleep)
            return rid, "ok"
        except Exception as e:
            return rid, f"err {e}"

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, rid) for rid in todo]
        for f in as_completed(futs):
            rid, st = f.result()
            done += 1
            if st != "ok":
                print(rid, st, flush=True)
            if done % 100 == 0:
                el = time.time() - t0
                print(f"{done}/{len(todo)} {el/60:.1f}min eta {(len(todo)-done)*el/done/60:.0f}min", flush=True)
    print("finished", flush=True)


if __name__ == "__main__":
    main()
