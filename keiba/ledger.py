"""予想と結果の台帳 (SQLite)。1点100円で買ったとして的中率・回収率を集計する。

保存先は環境変数 DATA_DIR (既定は keiba/data)。Streamlit Community Cloud や Render の
無料プランでは再デプロイで消えるので、CSV で書き出せるようにしてある。
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

DATA_DIR = os.getenv("DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "keiba_ledger.db")
UNIT = 100  # 1点あたりの金額 (円)
JST = timezone(timedelta(hours=9))

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    race_id TEXT NOT NULL,
    race_date TEXT NOT NULL,
    venue TEXT, rno INTEGER, race_name TEXT,
    style TEXT, ai_model TEXT,
    marks_json TEXT, tickets_json TEXT, probs_json TEXT,
    settled INTEGER DEFAULT 0,
    invested INTEGER DEFAULT 0,
    returned INTEGER DEFAULT 0,
    hit INTEGER,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pred_race ON predictions(race_id);
"""


@contextmanager
def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def save_prediction(race, plan, ai_model: str = "") -> int:
    tickets = [dict(kind=t.kind, combo=list(t.combo), prob=round(t.prob, 5), odds=t.odds)
               for t in plan.all_tickets()]
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO predictions (created_at, race_id, race_date, venue, rno, race_name, style, ai_model,"
            " marks_json, tickets_json, probs_json, invested) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), race.race_id, race.date, race.venue, race.rno,
             race.name, plan.style, ai_model, json.dumps(plan.marks, ensure_ascii=False),
             json.dumps(tickets, ensure_ascii=False),
             json.dumps({str(k): round(v, 5) for k, v in plan.win_probs.items()}),
             UNIT * len(tickets)),
        )
        return int(cur.lastrowid)


def _ticket_hit(kind: str, combo: list[int], payouts: dict) -> int:
    """的中していれば払戻 (100円あたり)、外れなら 0。"""
    for label, amount in payouts.get(kind, []):
        nums = [int(x) for x in label.split("-") if x.strip().isdigit()]
        if kind in ("単勝", "複勝"):
            if nums == combo:
                return amount
        else:
            if sorted(nums) == sorted(combo):
                return amount
    return 0


def settle(fetch_result) -> int:
    """未確定の予想に結果を付ける。fetch_result(race_id) -> Result|None。戻りは確定件数。"""
    n = 0
    with _conn() as con:
        rows = con.execute("SELECT id, race_id, tickets_json FROM predictions WHERE settled=0").fetchall()
        cache: dict[str, object] = {}
        for r in rows:
            rid = r["race_id"]
            if rid not in cache:
                try:
                    cache[rid] = fetch_result(rid)
                except Exception:
                    cache[rid] = None
            res = cache[rid]
            if res is None:
                continue
            payouts = res.payouts
            tickets = json.loads(r["tickets_json"])
            returned = 0
            for t in tickets:
                returned += _ticket_hit(t["kind"], t["combo"], payouts)
                t["payout"] = _ticket_hit(t["kind"], t["combo"], payouts)
            top3 = [o["umaban"] for o in res.order[:3]]
            con.execute(
                "UPDATE predictions SET settled=1, returned=?, hit=?, result_json=?, tickets_json=? WHERE id=?",
                (returned, 1 if returned > 0 else 0,
                 json.dumps(dict(top3=top3, payouts=payouts), ensure_ascii=False),
                 json.dumps(tickets, ensure_ascii=False), r["id"]),
            )
            n += 1
    return n


def summary() -> dict:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) n, SUM(hit) hits, SUM(invested) inv, SUM(returned) ret FROM predictions WHERE settled=1"
        ).fetchone()
        pending = con.execute("SELECT COUNT(*) FROM predictions WHERE settled=0").fetchone()[0]
        by_kind: dict[str, dict] = {}
        for r in con.execute("SELECT tickets_json FROM predictions WHERE settled=1"):
            for t in json.loads(r["tickets_json"]):
                k = by_kind.setdefault(t["kind"], dict(points=0, hits=0, inv=0, ret=0))
                k["points"] += 1
                k["inv"] += UNIT
                k["ret"] += t.get("payout", 0)
                k["hits"] += 1 if t.get("payout", 0) > 0 else 0
    n = row["n"] or 0
    return dict(
        races=n, hits=row["hits"] or 0, invested=row["inv"] or 0, returned=row["ret"] or 0,
        hit_rate=(row["hits"] or 0) / n if n else 0.0,
        roi=(row["ret"] or 0) / row["inv"] if row["inv"] else 0.0,
        pending=pending, by_kind=by_kind,
    )


def recent(limit: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM predictions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tickets"] = json.loads(d.pop("tickets_json") or "[]")
        d["marks"] = json.loads(d.pop("marks_json") or "{}")
        d["result"] = json.loads(d.pop("result_json") or "null")
        d.pop("probs_json", None)
        out.append(d)
    return out


def export_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "race_date", "venue", "rno", "race_name", "style", "ai_model",
                "kind", "combo", "prob", "odds", "payout", "settled"])
    for p in recent(100000):
        for t in p["tickets"]:
            w.writerow([p["id"], p["created_at"], p["race_date"], p["venue"], p["rno"], p["race_name"],
                        p["style"], p["ai_model"], t["kind"], "-".join(map(str, t["combo"])), t["prob"],
                        t.get("odds"), t.get("payout"), p["settled"]])
    return buf.getvalue()


def delete_prediction(pred_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM predictions WHERE id=?", (pred_id,))
