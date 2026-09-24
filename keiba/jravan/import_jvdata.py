"""JRA-VAN の書き出し (keiba/jravan/data/*.txt) を races.sqlite に取り込む。

    python jravan/import_jvdata.py

坂路 (HC, 58文字) とウッドチップ (WC, 103文字) の調教タイムを workouts テーブルに入れる。
血統登録番号は netkeiba の馬ID と同じ番号なので、runners.horse_id と結び付けられる。
タイムは 0.1秒単位 (0553 = 55.3秒)。0 は計測なし。
"""
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(os.path.dirname(HERE), "backtest", "data", "races.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workouts (
  horse_id TEXT, date TEXT, time TEXT, center INTEGER, kind TEXT,
  f4 REAL, f3 REAL, f2 REAL, lap1 REAL, lap2 REAL,
  PRIMARY KEY (horse_id, date, time, kind)
);
CREATE INDEX IF NOT EXISTS idx_workouts_horse ON workouts(horse_id, date);
"""


def t(s: str) -> float | None:
    try:
        v = int(s)
    except ValueError:
        return None
    return v / 10.0 if v > 0 else None


def parse_hc(line: str):
    # HC | 区分1 | 作成日8 | トレセン1 | 調教日8 | 時刻4 | 血統登録番号10 | 4F4 lap3 | 3F4 lap3 | 2F4 lap3 | lap3
    if len(line) < 58 or not line.startswith("HC"):
        return None
    return (line[24:34], line[12:20], line[20:24], int(line[11]), "坂路",
            t(line[34:38]), t(line[41:45]), t(line[48:52]), t(line[55:58]), t(line[52:55]))


def parse_wc(line: str):
    # 先頭34文字は HC と同じ。末尾から 4F4 lap3 3F4 lap3 2F4 lap3 lap3 (合計24文字)
    if len(line) < 60 or not line.startswith("WC"):
        return None
    tail = line[-24:]
    return (line[24:34], line[12:20], line[20:24], int(line[11]), "ウッド",
            t(tail[0:4]), t(tail[7:11]), t(tail[14:18]), t(tail[21:24]), t(tail[18:21]))


def main():
    con = sqlite3.connect(DB, timeout=300)
    con.executescript(SCHEMA)
    for fname, parser in (("SLOP_HC.txt", parse_hc), ("WOOD_WC.txt", parse_wc)):
        path = os.path.join(DATA, fname)
        if not os.path.exists(path):
            print("なし:", fname)
            continue
        batch, n = [], 0
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                rec = parser(line.rstrip("\n"))
                if rec:
                    batch.append(rec)
                if len(batch) >= 50000:
                    con.executemany("INSERT OR REPLACE INTO workouts VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                    con.commit(); n += len(batch); batch = []
        if batch:
            con.executemany("INSERT OR REPLACE INTO workouts VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
            con.commit(); n += len(batch)
        print(fname, n, "件", flush=True)
    matched = con.execute("SELECT COUNT(DISTINCT horse_id) FROM workouts WHERE horse_id IN (SELECT horse_id FROM runners)").fetchone()[0]
    total = con.execute("SELECT COUNT(DISTINCT horse_id) FROM runners").fetchone()[0]
    print(f"出走馬 {total} 頭のうち、調教タイムがある馬 {matched} 頭")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
