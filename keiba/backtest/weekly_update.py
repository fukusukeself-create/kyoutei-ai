"""毎週の更新: 前回の更新以降に確定したレースを netkeiba から取り込み、アプリが使う統計を更新する。

    python backtest/weekly_update.py            # 前回の続きから、昨日までに確定したレースを取り込む
    python backtest/weekly_update.py --dry-run  # 取り込むレースの一覧だけ表示

リポジトリの中のファイルだけで動く (大きなレースDBは不要)。更新するもの:
  models/stats.json          種牡馬・母父・騎手・調教師などの勝率表 (予想の特徴量)
  models/form.json           騎手・調教師の直近60日の成績 (画面の「騎手60日」と特徴量)
  models/display_stats.json  コースの特徴 (枠・脚質の有利不利、馬場×脚質)
  models/lines.json          新しく出てきた種牡馬・母父の系統
  models/update_state.json   どこまで取り込んだか
  data/weekly/<日付>.jsonl.gz 取り込んだ出走馬の記録 (将来の再学習用)
学習済みモデル (win.txt 等) 自体は作り直さない。作り直しには大きなレースDBと約1時間の計算が要る。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("KEIBA_PREFER_CURL", "1")   # netkeiba がこの種の環境の Python クライアントを弾くため

import features as F  # noqa: E402
import scraper  # noqa: E402
from collect import fetch_one  # noqa: E402

MODELS = os.path.join(ROOT, "models")
WEEKLY = os.path.join(ROOT, "data", "weekly")
STATE = os.path.join(MODELS, "update_state.json")
JST = dt.timezone(dt.timedelta(hours=9))
DISPLAY_KEYS = ("course_waku", "course_style", "cond_style")


def load(name, default):
    try:
        with open(os.path.join(MODELS, name), encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return default


def save(name, obj):
    with open(os.path.join(MODELS, name), "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, separators=(",", ":"))


def race_days(after: str, until: str) -> list[str]:
    """after より後、until 以前の開催日。"""
    a = dt.date(int(after[:4]), int(after[4:6]), int(after[6:8]))
    b = dt.date(int(until[:4]), int(until[4:6]), int(until[6:8]))
    days, y, m = set(), a.year, a.month
    while (y, m) <= (b.year, b.month):
        for d in scraper.fetch_kaisai_dates(y, m):
            if after < d <= until:
                days.add(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(days)


def sire_line_of(horse_id: str) -> tuple[str, str] | None:
    """血統ページから (父の系統, 母父の系統)。母父の系統は母父自身の父系。"""
    from bs4 import BeautifulSoup
    from collect_extra import fetch   # db.netkeiba.com は EUC-JP
    html = fetch(f"https://db.netkeiba.com/horse/ped/{horse_id}/", "euc-jp")
    if not html:
        return None
    s = BeautifulSoup(html, "html.parser")
    tbl = s.select_one("table.blood_table")
    if tbl is None:
        return None
    cells = tbl.select("td")
    halves = [i for i, td in enumerate(cells) if td.get("rowspan") == "16"]
    if len(halves) < 2:
        return None
    m = re.search(r"(\S+系)", cells[halves[0]].get_text(" ", strip=True))
    return (m.group(1) if m else ""), ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--until", help="取り込む最後の日 (YYYYMMDD)。既定は昨日")
    args = ap.parse_args()

    state = load("update_state.json", {"last_date": "20260913"})
    until = args.until or (dt.datetime.now(JST).date() - dt.timedelta(days=1)).strftime("%Y%m%d")
    days = race_days(state["last_date"], until)
    print(f"前回の取り込み: {state['last_date']} / 今回の対象: {days or 'なし'}", flush=True)
    if not days:
        return 0

    races = []   # (date, race_id)
    for d in days:
        for m in scraper.fetch_meetings(d):
            races += [(d, r.race_id) for r in m.races]
    print(f"{len(races)} レース", flush=True)
    if args.dry_run:
        return 0

    stats = load("stats.json", {})
    form = load("form.json", {"j": {}, "t": {}})
    display = load("display_stats.json", {})
    lines = load("lines.json", {"sire": {}, "damsire": {}})
    base = {k: stats.get(k, {}) for k in F.RATE_TABLES}          # 今あるモデルの表だけ更新する
    disp = {k: display.setdefault(k, {}) for k in DISPLAY_KEYS}

    done, failed, n_run, n_win = [], [], 0, 0
    archive: dict[str, list] = {}
    for d, rid in races:
        got = None
        for attempt in range(3):
            try:
                got = fetch_one(rid)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {rid} 取得失敗 ({attempt + 1}回目): {str(e)[-80:]}", flush=True)
                time.sleep(10 * (attempt + 1))
        if not got:
            failed.append(rid)
            continue
        hdr, runners, results, _pay = got
        if not runners or not results or hdr["surface"] not in ("芝", "ダ"):   # 学習と同じく障害は除く
            continue
        ordn = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).toordinal()
        for r in runners:
            res = results.get(r["umaban"]) or {}
            fin = res.get("finish")
            if not fin:
                continue
            rec = {**r, "finish": fin, "surface": hdr["surface"], "distance": hdr["distance"], "venue": hdr["venue"],
                   "condition": hdr["condition"], "sire_line": lines["sire"].get(r.get("sire") or "", ""),
                   "damsire_line": lines["damsire"].get(r.get("damsire") or "", "")}
            tmp = {k: {} for k in F.RATE_TABLES}
            F.stats_add(tmp, rec)
            for k, tbl in tmp.items():                # 足し込み (表が無い種類は無視)
                target = base[k] if k in stats else (disp[k] if k in disp else None)
                if target is None:
                    continue
                for key, (n, w, t3) in tbl.items():
                    v = target.setdefault(key, [0, 0, 0])
                    v[0] += n; v[1] += w; v[2] += t3
            win = 1 if fin == 1 else 0
            F.form_update(form["j"].setdefault(F.jockey_key(r.get("jockey")), []), ordn, win)
            F.form_update(form["t"].setdefault(F.trainer_key(r.get("trainer")), []), ordn, win)
            n_run += 1
            n_win += win
            archive.setdefault(d, []).append({k: rec.get(k) for k in (
                "horse_id", "name", "umaban", "waku", "finish", "jockey", "trainer", "sire", "damsire", "style",
                "weight", "body_weight", "surface", "distance", "venue", "condition")} | {"race_id": rid, "odds": res.get("odds"),
                                                                                         "ninki": res.get("ninki"), "time": res.get("time")})
        done.append(rid)
        time.sleep(0.5)

    # 新しい種牡馬の系統 (1頭につき1ページ)
    new_sires = {}
    import glob
    for path in sorted(glob.glob(os.path.join(WEEKLY, "*.jsonl.gz"))):
        with gzip.open(path, "rt", encoding="utf-8") as fp:
            for line in fp:
                r = json.loads(line)
                if r.get("sire") and r["sire"] not in lines["sire"] and r["sire"] not in new_sires:
                    new_sires[r["sire"]] = r["horse_id"]
    for rows in archive.values():
        for r in rows:
            if r.get("sire") and r["sire"] not in lines["sire"] and r["sire"] not in new_sires:
                new_sires[r["sire"]] = r["horse_id"]
    for sire, hid in list(new_sires.items())[:30]:
        try:
            got = sire_line_of(hid)
            if got and got[0]:
                lines["sire"][sire] = got[0]
        except Exception:  # noqa: BLE001
            pass

    for k in F.RATE_TABLES:
        if k in stats:
            stats[k] = base[k]
    save("stats.json", stats)
    save("form.json", form)
    save("display_stats.json", display)
    save("lines.json", lines)
    os.makedirs(WEEKLY, exist_ok=True)
    for d, rows in archive.items():
        with gzip.open(os.path.join(WEEKLY, f"{d}.jsonl.gz"), "wt", encoding="utf-8") as fp:
            for r in rows:
                fp.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 失敗したレースがあれば、その日から次回やり直す
    last = until if not failed else min(d for d, rid in races if rid in failed)
    if failed:
        last = (dt.date(int(last[:4]), int(last[4:6]), int(last[6:8])) - dt.timedelta(days=1)).strftime("%Y%m%d")
    state.update(last_date=last, updated_at=dt.datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
                 last_run=dict(days=days, races=len(done), failed=failed, runners=n_run, new_sires=len(new_sires)))
    save("update_state.json", state)
    print(f"取り込み {len(done)} レース / 出走 {n_run} 頭 / 失敗 {len(failed)} / 新しい種牡馬 {len(new_sires)} / 次回は {last} の翌日から", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
