"""追加情報の収集: 調教評価 (追い切りページ) と 血統の系統。

    python backtest/collect_extra.py oikiri --workers 2     # 全レースの調教評価 (ランク A〜D と短評)
    python backtest/collect_extra.py pedigree              # 父・母父の系統 (Halo系 など)

中断しても再実行すれば続きから取る。netkeiba はこの環境の Python クライアントを弾きやすいので curl で取る。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "races.sqlite")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

SCHEMA = """
CREATE TABLE IF NOT EXISTS oikiri (race_id TEXT, umaban INTEGER, critic TEXT, rank TEXT, PRIMARY KEY (race_id, umaban));
CREATE TABLE IF NOT EXISTS oikiri_done (race_id TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS ped_horse (horse_id TEXT PRIMARY KEY, sire TEXT, sire_line TEXT, damsire TEXT, damsire_id TEXT, family TEXT);
CREATE TABLE IF NOT EXISTS damsire_line (damsire_id TEXT PRIMARY KEY, name TEXT, line TEXT);
"""
lock = threading.Lock()


def fetch(url: str, encoding: str, tries: int = 4) -> str | None:
    """curl で取る。中身の無い 400 などは間を置いて取り直す。"""
    for i in range(tries):
        try:
            r = subprocess.run(["curl", "-sS", "-A", UA, "--max-time", "25", "-w", "\n%{http_code}", url],
                               capture_output=True, timeout=40)
            body, _, code = r.stdout.rpartition(b"\n")
            if code.strip() == b"200" and len(body) > 1000:
                return body.decode(encoding, "replace")
        except (subprocess.SubprocessError, OSError):
            pass
        time.sleep(3 * (i + 1))
    return None


def db():
    con = sqlite3.connect(DB, timeout=120, check_same_thread=False)
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- 調教評価
def parse_oikiri(html: str) -> list[tuple[int, str, str]]:
    s = BeautifulSoup(html, "html.parser")
    out = []
    for tr in s.select("table.OikiriTable tr.HorseList"):
        u = tr.select_one(".Umaban")
        if not u or not u.get_text(strip=True).isdigit():
            continue
        critic = tr.select_one(".Training_Critic")
        rank = tr.select_one("td[class^=Rank_]")
        out.append((int(u.get_text(strip=True)), critic.get_text(strip=True) if critic else "",
                    rank.get_text(strip=True) if rank else ""))
    return out


def run_oikiri(workers: int, sleep: float):
    con = db()
    todo = [r[0] for r in con.execute(
        "SELECT race_id FROM races WHERE fetched=1 AND race_id NOT IN (SELECT race_id FROM oikiri_done) ORDER BY race_id DESC")]
    print(f"oikiri todo {len(todo)}", flush=True)
    t0, done = time.time(), 0

    def work(rid):
        html = fetch(f"https://race.netkeiba.com/race/oikiri.html?race_id={rid}", "utf-8")
        if html is None:
            return rid, None
        time.sleep(sleep)
        return rid, parse_oikiri(html)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(work, r) for r in todo]):
            rid, rows = f.result()
            done += 1
            if rows is None:
                print(rid, "err", flush=True)
                continue
            with lock:
                con.executemany("INSERT OR REPLACE INTO oikiri VALUES (?,?,?,?)", [(rid, u, c, k) for u, c, k in rows])
                con.execute("INSERT OR REPLACE INTO oikiri_done VALUES (?,?)", (rid, len(rows)))
                con.commit()
            if done % 200 == 0:
                el = time.time() - t0
                print(f"{done}/{len(todo)} {el/60:.1f}min eta {(len(todo)-done)*el/done/60:.0f}min", flush=True)
    print("finished", flush=True)


# ---------------------------------------------------------------- 血統の系統
def parse_ped(html: str) -> dict | None:
    s = BeautifulSoup(html, "html.parser")
    tbl = s.select_one("table.blood_table")
    if tbl is None:
        return None
    cells = tbl.select("td")
    halves = [i for i, td in enumerate(cells) if td.get("rowspan") == "16"]
    if len(halves) < 2:
        return None

    def name_of(td):
        a = td.select_one("a")
        return a.get_text(strip=True) if a else td.get_text(" ", strip=True).split(" ")[0]

    def id_of(td):
        a = td.select_one("a[href*='/horse/']")
        m = re.search(r"/horse/(?:ped/)?(\w+)", a["href"]) if a else None
        return m.group(1) if m else ""

    sire_td, dam_td = cells[halves[0]], cells[halves[1]]
    damsire_td = cells[halves[1] + 1]
    line = re.search(r"(\S+系)", sire_td.get_text(" ", strip=True))
    fam = re.search(r"(\d+-[a-z]|\d+)\s*$", dam_td.get_text(" ", strip=True))
    return dict(sire=name_of(sire_td), sire_line=line.group(1) if line else "",
                damsire=name_of(damsire_td), damsire_id=id_of(damsire_td), family=fam.group(1) if fam else "")


def run_pedigree(workers: int, sleep: float):
    con = db()
    # 1) 父・母父の組をできるだけ少ない馬で覆う: 父ごと・母父ごとに1頭ずつ
    rows = con.execute("SELECT horse_id, sire, damsire FROM runners WHERE horse_id != '' GROUP BY horse_id").fetchall()
    have = {r[0] for r in con.execute("SELECT horse_id FROM ped_horse")}
    seen_s = {r[0] for r in con.execute("SELECT sire FROM ped_horse")}
    seen_d = {r[0] for r in con.execute("SELECT damsire FROM ped_horse")}
    pick = []
    for hid, sire, dsire in rows:
        if hid in have:
            continue
        if sire not in seen_s or dsire not in seen_d:
            pick.append(hid)
            seen_s.add(sire)
            seen_d.add(dsire)
    print(f"pedigree: offspring pages to fetch {len(pick)}", flush=True)

    def work(hid):
        html = fetch(f"https://db.netkeiba.com/horse/ped/{hid}/", "euc-jp")
        time.sleep(sleep)
        return hid, (parse_ped(html) if html else None)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(work, h) for h in pick]), 1):
            hid, p = f.result()
            if p:
                with lock:
                    con.execute("INSERT OR REPLACE INTO ped_horse VALUES (?,?,?,?,?,?)",
                                (hid, p["sire"], p["sire_line"], p["damsire"], p["damsire_id"], p["family"]))
                    con.commit()
            if i % 200 == 0:
                print(f"offspring {i}/{len(pick)}", flush=True)
    # 2) 母父それぞれの血統ページ → 母父自身の父の系統 = 母父の系統
    have_d = {r[0] for r in con.execute("SELECT damsire_id FROM damsire_line")}
    ds = [(i, n) for i, n in con.execute("SELECT DISTINCT damsire_id, damsire FROM ped_horse WHERE damsire_id != ''") if i not in have_d]
    print(f"pedigree: damsire pages to fetch {len(ds)}", flush=True)

    def work2(item):
        did, name = item
        html = fetch(f"https://db.netkeiba.com/horse/ped/{did}/", "euc-jp")
        time.sleep(sleep)
        p = parse_ped(html) if html else None
        return did, name, (p["sire_line"] if p else None)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, f in enumerate(as_completed([ex.submit(work2, d) for d in ds]), 1):
            did, name, line = f.result()
            if line is not None:
                with lock:
                    con.execute("INSERT OR REPLACE INTO damsire_line VALUES (?,?,?)", (did, name, line))
                    con.commit()
            if i % 200 == 0:
                print(f"damsire {i}/{len(ds)}", flush=True)
    print("finished", flush=True)


if __name__ == "__main__":
    if not shutil.which("curl"):
        sys.exit("curl が必要です")
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["oikiri", "pedigree"])
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--sleep", type=float, default=0.5)
    a = ap.parse_args()
    (run_oikiri if a.what == "oikiri" else run_pedigree)(a.workers, a.sleep)
