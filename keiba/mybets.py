"""my収支: 実際に買った馬券の記録と集計 (netkeiba の「マイ収支」のように)。

記録はブラウザ (スマホ) の localStorage に保存する (app.py)。サーバーには置かないので、
他の人には見えず、アプリの更新でも消えない。ブラウザのデータを消すと消えるので、CSV で控えを取れる。
ここは保存先に依存しない計算だけ: 記録の作成・結果の照合 (払戻)・集計・CSV。
"""
from __future__ import annotations

import csv
import io
import time
from typing import Callable, Optional

KINDS = ["単勝", "複勝", "ワイド", "馬連", "馬単", "三連複", "三連単"]
NEEDS = {"単勝": 1, "複勝": 1, "ワイド": 2, "馬連": 2, "馬単": 2, "三連複": 3, "三連単": 3}
ORDERED = ("単勝", "複勝", "馬単", "三連単")   # 着順どおりに比べる券種
FIELDS = ["id", "date", "race_id", "venue", "rno", "race_name", "kind", "combo", "stake", "odds", "prob", "mark",
          "memo", "settled", "payout", "result"]


def new_bet(race_id: str, date: str, venue: str, rno: int, race_name: str, kind: str, combo: list[int],
            stake: int, odds: Optional[float] = None, prob: Optional[float] = None, mark: str = "", memo: str = "") -> dict:
    return dict(id=f"{int(time.time() * 1000)}", date=date, race_id=race_id, venue=venue, rno=int(rno), race_name=race_name,
                kind=kind, combo=[int(x) for x in combo], stake=int(stake), odds=odds, prob=prob, mark=mark, memo=memo,
                settled=False, payout=0, result="")


def payout_per100(kind: str, combo: list[int], payouts: dict) -> int:
    """的中なら 100円あたりの払戻、外れなら 0。"""
    for label, amount in payouts.get(kind, []):
        nums = [int(x) for x in str(label).split("-") if x.strip().isdigit()]
        if (nums == list(combo)) if kind in ORDERED else (sorted(nums) == sorted(combo)):
            return int(amount)
    return 0


def settle(bets: list[dict], fetch_result: Callable[[str], object]) -> int:
    """未確定の記録に結果を付ける (bets をその場で書き換える)。戻りは確定した件数。"""
    n = 0
    cache: dict = {}
    for b in bets:
        if b.get("settled"):
            continue
        rid = b["race_id"]
        if rid not in cache:
            try:
                cache[rid] = fetch_result(rid)
            except Exception:
                cache[rid] = None
        res = cache[rid]
        if res is None:
            continue
        b["payout"] = payout_per100(b["kind"], b["combo"], res.payouts) * int(b["stake"]) // 100
        b["result"] = "-".join(str(o["umaban"]) for o in res.order[:3])
        b["settled"] = True
        n += 1
    return n


def _agg(bs: list[dict]) -> dict:
    inv = sum(int(b["stake"]) for b in bs)
    ret = sum(int(b.get("payout") or 0) for b in bs)
    hits = sum(1 for b in bs if (b.get("payout") or 0) > 0)
    return dict(n=len(bs), hits=hits, invested=inv, returned=ret, profit=ret - inv,
                hit_rate=hits / len(bs) if bs else 0.0, roi=ret / inv if inv else 0.0)


def summary(bets: list[dict]) -> dict:
    """確定分の集計 (全体・月別・券種別・印別) と、未確定の件数・金額。"""
    done = [b for b in bets if b.get("settled")]
    pend = [b for b in bets if not b.get("settled")]
    by = lambda key: {k: _agg([b for b in done if key(b) == k]) for k in sorted({key(b) for b in done}, reverse=True)}
    return dict(total=_agg(done), pending=len(pend), pending_stake=sum(int(b["stake"]) for b in pend),
                by_month=by(lambda b: f"{b['date'][:4]}/{b['date'][4:6]}"),
                by_day=by(lambda b: f"{b['date'][4:6]}/{b['date'][6:8]}"),
                by_kind=by(lambda b: b["kind"]),
                by_mark=by(lambda b: {"◎": "◎ 勝負", "○": "○ いつもの1頭"}.get(b.get("mark") or "", "自分で選んだ")))


def ipat_memo(b: dict) -> str:
    """即PAT で入力する順 (通常投票: 競馬場 → レース → 式別 → 馬番 → 金額)。"""
    sep = "→" if b["kind"] in ("馬単", "三連単") else "-"
    return f"{b['venue']} {b['rno']}R ／ {b['kind']} ／ {sep.join(map(str, b['combo']))} ／ {int(b['stake']):,}円"


def to_csv(bets: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    for b in bets:
        w.writerow({**{k: b.get(k, "") for k in FIELDS}, "combo": "-".join(map(str, b["combo"]))})
    return buf.getvalue()


def from_csv(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        try:
            out.append(dict(id=r["id"], date=r["date"], race_id=r["race_id"], venue=r["venue"], rno=int(r["rno"]),
                            race_name=r.get("race_name", ""), kind=r["kind"],
                            combo=[int(x) for x in r["combo"].split("-") if x.strip()], stake=int(float(r["stake"])),
                            odds=float(r["odds"]) if r.get("odds") else None, prob=float(r["prob"]) if r.get("prob") else None,
                            mark=r.get("mark", ""), memo=r.get("memo", ""), settled=r.get("settled") in ("True", "1", "true"),
                            payout=int(float(r.get("payout") or 0)), result=r.get("result", "")))
        except (KeyError, ValueError):
            continue
    return out


def merge(a: list[dict], b: list[dict]) -> list[dict]:
    """id で重ねる (CSV の取り込み用)。"""
    seen = {x["id"]: x for x in a}
    for x in b:
        seen.setdefault(x["id"], x)
    return sorted(seen.values(), key=lambda x: (x["date"], x["id"]))
