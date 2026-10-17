"""JRA-VAN のデータ (jravan/data/RACE_*.txt・DIFN_UM_all.txt) から、学習用のレースDBを作る。

    python jravan/build_db.py                     # → backtest/data/races_jv.sqlite
    python backtest/train.py --db backtest/data/races_jv.sqlite

netkeiba から集めた races.sqlite と同じ形 (races・runners、runners.past は出走時点の近5走) にするので、
features.py・train.py をそのまま使える。netkeiba より古い 2015年からのレースが入る。
名前の書き方は本番の予想 (netkeiba の出馬表) と揃える: 騎手・調教師・父・母父は、2021年以降の重なりで
JRA-VAN のコード → netkeiba の表記 の対応表を作って置き換える (対応が無いものは JRA-VAN の表記のまま)。
netkeiba にしか無い追い切り評価 (oikiri) と系統表 (ped_horse・damsire_line) は races.sqlite から写す。
JV-Data は Shift_JIS のバイト位置で項目が決まっているので、1行を cp932 に戻してから切り出す。
"""
from __future__ import annotations

import collections
import glob
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "data")
NK_DB = os.path.join(ROOT, "backtest", "data", "races.sqlite")
OUT_DB = os.path.join(ROOT, "backtest", "data", "races_jv.sqlite")

VENUES = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
          "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"}
WEATHER = {"1": "晴", "2": "曇", "3": "雨", "4": "小雨", "5": "雪", "6": "小雪"}
GOING = {"1": "良", "2": "稍", "3": "重", "4": "不"}
GRADE = {"A": "GI", "B": "GII", "C": "GIII", "L": "L", "F": "GI", "G": "GII", "H": "GIII"}
AGE = {"11": "２歳", "12": "３歳", "13": "３歳以上", "14": "４歳以上", "18": "３歳以上", "19": "４歳以上"}
COND = {"701": "新馬", "702": "未出走", "703": "未勝利", "005": "１勝クラス", "010": "２勝クラス", "016": "３勝クラス",
        "999": "オープン"}
SEX = {"1": "牡", "2": "牝", "3": "セ"}
STYLE = {"1": "逃", "2": "先", "3": "差", "4": "追"}
AREA = {"1": "美浦", "2": "栗東", "3": "地方", "4": "外国"}

SCHEMA_RACES = """CREATE TABLE races (
  race_id TEXT PRIMARY KEY, date TEXT, venue TEXT, rno INTEGER, name TEXT, start TEXT,
  course TEXT, surface TEXT, distance INTEGER, turn TEXT, weather TEXT, condition TEXT,
  cls TEXT, grade TEXT, heads INTEGER, payouts TEXT, fetched INTEGER,
  laps TEXT, first3f REAL, last3f REAL)"""
SCHEMA_RUNNERS = """CREATE TABLE runners (
  race_id TEXT, umaban INTEGER, waku INTEGER, name TEXT, horse_id TEXT, sex_age TEXT, jockey TEXT,
  weight REAL, trainer TEXT, sire TEXT, dam TEXT, damsire TEXT, style TEXT, interval TEXT,
  body_weight TEXT, rest_note TEXT, past TEXT, finish INTEGER, ninki INTEGER, odds REAL, time TEXT,
  margin TEXT, jockey_code TEXT, trainer_code TEXT, sire_id TEXT, dam_id TEXT, damsire_id TEXT,
  corners TEXT, agari REAL, run_style TEXT, PRIMARY KEY (race_id, umaban))"""


class Rec:
    """JV-Data の1レコード (Shift_JIS のバイト位置で切り出す。位置は仕様書どおり 1 始まり)。"""
    __slots__ = ("b",)

    def __init__(self, line: str):
        self.b = line.rstrip("\r\n").encode("cp932", errors="replace")

    def s(self, pos: int, n: int) -> str:
        return self.b[pos - 1:pos - 1 + n].decode("cp932", errors="replace").replace("　", " ").strip()

    def i(self, pos: int, n: int):
        v = self.s(pos, n)
        return int(v) if v.isdigit() else None

    def race_id(self) -> str:
        return self.s(12, 4) + self.s(20, 2) + self.s(22, 2) + self.s(24, 2) + self.s(26, 2)

    def date(self) -> str:
        return self.s(12, 4) + self.s(16, 4)

    def key(self):
        """同じレース・同じ馬の版が複数あるとき、新しい版を選ぶための順序 (作成日)。"""
        return self.s(4, 8)


def latest(paths: list[str], keyf) -> dict:
    """同じキーのレコードは、作成日が新しいものを残す。"""
    out: dict = {}
    kinds = collections.Counter()
    for p in paths:
        for line in open(p, encoding="utf-8"):
            r = Rec(line)
            kinds[r.s(3, 1)] += 1
            k = keyf(r)
            if k is None:
                continue
            if k not in out or r.key() >= out[k].key():
                out[k] = r
    print(f"  {os.path.basename(paths[0])[:7]}…: {sum(kinds.values()):,}行 データ区分 {dict(kinds)} → {len(out):,}件", flush=True)
    return out


def track(code: str) -> tuple[str, str]:
    """トラックコード → (芝/ダ/障, 右/左/直線)。"""
    c = int(code) if code.isdigit() else 0
    if 10 <= c <= 22:
        return "芝", "直線" if c == 10 else ("左" if c <= 16 else "右")
    if 23 <= c <= 29:
        return "ダ", {23: "左", 24: "右", 25: "左", 26: "右", 27: "左", 28: "右", 29: "直線"}[c]
    if 51 <= c <= 59:
        return "障", ""
    return "", ""


def fmt_time(v: str) -> str:
    return f"{v[0]}:{v[1:3]}.{v[3]}" if len(v) == 4 and v.isdigit() and v != "0000" else ""


def main() -> None:
    print("読み込み", flush=True)
    ra = latest(sorted(glob.glob(os.path.join(DATA, "RACE_RA_*.txt"))), lambda r: r.race_id())
    se = latest(sorted(glob.glob(os.path.join(DATA, "RACE_SE_*.txt"))), lambda r: (r.race_id(), r.i(29, 2)))
    um = {}
    for line in open(os.path.join(DATA, "DIFN_UM_all.txt"), encoding="utf-8"):
        r = Rec(line)
        um[r.s(12, 10)] = (r.s(205, 10), r.s(215, 36), r.s(251, 10), r.s(261, 36), r.s(389, 10), r.s(399, 36))
    print(f"  競走馬マスタ {len(um):,}頭", flush=True)

    # ---- レース
    races = {}
    for rid, r in ra.items():
        surface, turn = track(r.s(706, 2))
        if surface not in ("芝", "ダ", "障") or r.s(20, 2) not in VENUES:
            continue
        cond_code = r.s(635, 3)
        age = AGE.get(r.s(617, 2), "")
        kind = "障害" if surface == "障" else "サラ系"
        cls = f"{kind}{age} {COND.get(cond_code, '')}".strip()
        grade = GRADE.get(r.s(615, 1), "")
        if not grade and cond_code == "999":
            grade = "OP"
        name = r.s(33, 60) or f"{age.translate(str.maketrans('０１２３４５６７８９', '0123456789'))}{COND.get(cond_code, '')}"
        laps = [r.i(891 + 3 * k, 3) for k in range(25)]
        laps = [x / 10 for x in laps if x]
        going = GOING.get(r.s(889, 1) if surface == "芝" else r.s(890, 1), "")
        st = r.s(874, 4)
        races[rid] = dict(
            race_id=rid, date=r.date(), venue=VENUES[r.s(20, 2)], rno=int(r.s(26, 2)), name=name,
            start=f"{st[:2]}:{st[2:]}" if len(st) == 4 else "", course=f"{surface}{r.i(698, 4) or 0}m", surface=surface,
            distance=r.i(698, 4) or 0, turn=turn, weather=WEATHER.get(r.s(888, 1), ""), condition=going, cls=cls,
            grade=grade, past_grade={"005": "1勝", "010": "2勝", "016": "3勝"}.get(cond_code, ""), heads=r.i(884, 2) or 0, payouts="{}", fetched=1, laps=json.dumps(laps),
            first3f=(r.i(970, 3) or 0) / 10 or None, last3f=(r.i(976, 3) or 0) / 10 or None)
    print(f"レース {len(races):,}", flush=True)

    # ---- 出走馬 (版をまとめた後)。馬ごとの出走履歴を日付順に
    rows = []
    hist = collections.defaultdict(list)
    for (rid, um_), r in se.items():
        if rid not in races or um_ is None:
            continue
        race = races[rid]
        fin = r.i(335, 2) or None
        abn = r.s(332, 1)
        rest = {"1": "取消", "2": "除外", "3": "除外"}.get(abn, "")
        if rest:
            fin = None
        bw = r.s(325, 3)
        sign, diff = r.s(328, 1), r.i(329, 3)
        body = f"{int(bw)}({'' if not diff else sign}{diff or 0})" if bw.isdigit() and bw != "000" else ""
        corners = [r.i(352 + 2 * k, 2) or 0 for k in range(4)]
        td = r.s(532, 4)
        margin = (-1 if td[:1] == "-" else 1) * int(td[1:]) / 10 if len(td) == 4 and td[1:].isdigit() else None
        hid = r.s(31, 10)
        row = dict(
            race_id=rid, umaban=um_, waku=r.i(28, 1), name=r.s(41, 36), horse_id=hid,
            sex_age=f"{SEX.get(r.s(79, 1), '')}{r.i(83, 2) or ''}", jockey=r.s(307, 8), jockey_raw=r.s(307, 8), weight=(r.i(289, 3) or 0) / 10 or None,
            trainer=f"{AREA.get(r.s(85, 1), '')} {r.s(91, 8)}".strip(), body_weight=body, rest_note=rest, finish=fin,
            ninki=r.i(364, 2), odds=(r.i(360, 4) or 0) / 10 or None, time=fmt_time(r.s(339, 4)),
            margin="" if margin is None else f"{margin:.1f}", jockey_code=r.s(297, 5), trainer_code=r.s(86, 5),
            corners="-".join(str(c) for c in corners if c), agari=(r.i(391, 3) or 0) / 10 or None,
            run_style=STYLE.get(r.s(553, 1), ""), winner=r.s(404, 36), margin_sec=margin)
        u = um.get(hid)
        row.update(sire_id=u[0] if u else "", sire=u[1] if u else "", dam_id=u[2] if u else "", dam=u[3] if u else "",
                   damsire_id=u[4] if u else "", damsire=u[5] if u else "")
        rows.append(row)
        if fin:
            hist[hid].append((race["date"], rid, row))
    for h in hist.values():
        h.sort(key=lambda x: x[0])
    print(f"出走馬 {len(rows):,} / 馬 {len(hist):,}頭 / 競走馬マスタに無い馬 {sum(1 for r in rows if not r['sire'])}", flush=True)

    # ---- netkeiba の表記に揃える対応表 (2021年以降の重なりから)
    nk = sqlite3.connect(NK_DB)
    nk_run = {(rid, u): (j, t, s, d, ds) for rid, u, j, t, s, d, ds in
              nk.execute("SELECT race_id, umaban, jockey, trainer, sire, dam, damsire FROM runners")}
    maps = {k: collections.defaultdict(collections.Counter) for k in ("jockey", "trainer", "sire", "dam", "damsire")}
    for row in rows:
        v = nk_run.get((row["race_id"], row["umaban"]))
        if not v:
            continue
        for k, code, val in (("jockey", row["jockey_code"], v[0]), ("trainer", row["trainer_code"], v[1]),
                             ("sire", row["sire_id"], v[2]), ("dam", row["dam_id"], v[3]), ("damsire", row["damsire_id"], v[4])):
            if code and val:
                maps[k][code][val] += 1
    best = {k: {c: cnt.most_common(1)[0][0] for c, cnt in m.items()} for k, m in maps.items()}
    print("対応表: " + ", ".join(f"{k} {len(v):,}" for k, v in best.items()), flush=True)
    for row in rows:
        row["jockey"] = best["jockey"].get(row["jockey_code"], row["jockey"])
        row["trainer"] = best["trainer"].get(row["trainer_code"], row["trainer"])
        for k in ("sire", "dam", "damsire"):
            row[k] = best[k].get(row[k + "_id"], row[k])

    # ---- 出走時点の近5走 (netkeiba の馬柱と同じ形)、脚質 (近走の脚質判定で最も多いもの)、間隔
    import datetime as dt
    D = lambda s: dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    for row in rows:
        race = races[row["race_id"]]
        prev = [x for x in hist.get(row["horse_id"], []) if x[0] < race["date"]][-5:][::-1]
        past = []
        for pdate, prid, p in prev:
            pr = races[prid]
            past.append(dict(
                date=f"{pdate[:4]}.{pdate[4:6]}.{pdate[6:]}", venue=pr["venue"], finish=p["finish"], race_name=pr["name"],
                grade=pr["grade"] or pr["past_grade"], surface=pr["surface"], distance=pr["distance"], time=p["time"], condition=pr["condition"],
                heads=pr["heads"], umaban=p["umaban"], ninki=p["ninki"], jockey=p.get("jockey_raw") or p["jockey"],
                weight=p["weight"], passing=p["corners"], agari=p["agari"], body_weight=p["body_weight"],
                winner=p["winner"], margin=p["margin_sec"]))
        row["past"] = json.dumps(past, ensure_ascii=False)
        styles = collections.Counter(p["run_style"] for _, _, p in prev if p["run_style"])
        row["style"] = styles.most_common(1)[0][0] if styles else ""
        rel = []
        for _, prid, p in prev:
            cs = [int(c) for c in (p["corners"] or "").split("-") if c]
            if cs:
                rel.append((cs[0] / max(races[prid]["heads"], 1), cs[-1] / max(races[prid]["heads"], 1), cs[0] == 1))
        row["_sf"] = [len(prev)] + [styles.get(k, 0) for k in "逃先差追"] + (
            [sum(x[0] for x in rel) / len(rel), min(x[0] for x in rel), rel[0][0], sum(x[1] for x in rel) / len(rel),
             sum(x[2] for x in rel) / len(rel)] if rel else [float("nan")] * 5)
        if prev:
            days = (D(race["date"]) - D(prev[0][0])).days
            row["interval"] = "連闘" if days <= 7 else f"中{-(-days // 7) - 1}週"   # netkeiba と同じ切り上げ
        else:
            row["interval"] = ""

    # ---- netkeiba と重なるレース・出走馬は netkeiba の値を使う (本番の予想と同じ材料にする)
    nk_cols = ["race_id", "umaban", "waku", "name", "horse_id", "sex_age", "jockey", "weight", "trainer", "sire", "dam",
               "damsire", "style", "interval", "body_weight", "rest_note", "past", "finish", "ninki", "odds", "time", "margin"]
    nk_rows = {(r[0], r[1]): dict(zip(nk_cols, r)) for r in nk.execute(f"SELECT {','.join(nk_cols)} FROM runners")}
    nk_races = {r[0]: r for r in nk.execute("SELECT race_id, name, start, course, surface, distance, turn, weather, condition, "
                                           "cls, grade, heads, payouts FROM races WHERE fetched=1")}
    for rid, v in nk_races.items():
        if rid in races:
            races[rid].update(zip(["name", "start", "course", "surface", "distance", "turn", "weather", "condition", "cls",
                                   "grade", "heads", "payouts"], v[1:]))
    import lightgbm as lgb
    import numpy as np
    labels = ["", "逃", "先", "差", "追", "大", "自"]
    X, y = [], []
    for row in rows:
        v = nk_rows.get((row["race_id"], row["umaban"]))
        if v is not None and v["style"] in labels:
            X.append(row["_sf"]); y.append(labels.index(v["style"]))
    X, y = np.array(X, dtype=float), np.array(y)
    cut = int(len(y) * 0.8)
    params = dict(objective="multiclass", num_class=len(labels), learning_rate=0.1, num_leaves=31, verbose=-1)
    m = lgb.train(params, lgb.Dataset(X[:cut], y[:cut]), 200)
    acc = float((m.predict(X[cut:]).argmax(1) == y[cut:]).mean())
    m = lgb.train(params, lgb.Dataset(X, y), 200)
    print(f"脚質: netkeiba の分類をまねる (重なり {len(y):,}頭で学習、確かめ用の一致率 {acc:.3f})", flush=True)
    pred = m.predict(np.array([row["_sf"] for row in rows], dtype=float)).argmax(1)
    n_nk = 0
    for row, k in zip(rows, pred):
        v = nk_rows.get((row["race_id"], row["umaban"]))
        if v is not None:
            row.update(v)
            n_nk += 1
        else:
            row["style"] = labels[k]
    print(f"netkeiba の値を使った出走馬 {n_nk:,} / JRA-VAN から作った出走馬 {len(rows) - n_nk:,}", flush=True)

    # ---- 書き出し
    if os.path.exists(OUT_DB):
        os.remove(OUT_DB)
    con = sqlite3.connect(OUT_DB)
    con.execute(SCHEMA_RACES)
    con.execute(SCHEMA_RUNNERS)
    rc = ["race_id", "date", "venue", "rno", "name", "start", "course", "surface", "distance", "turn", "weather", "condition",
          "cls", "grade", "heads", "payouts", "fetched", "laps", "first3f", "last3f"]
    con.executemany(f"INSERT INTO races VALUES ({','.join('?' * len(rc))})", [tuple(r[c] for c in rc) for r in races.values()])
    uc = ["race_id", "umaban", "waku", "name", "horse_id", "sex_age", "jockey", "weight", "trainer", "sire", "dam", "damsire",
          "style", "interval", "body_weight", "rest_note", "past", "finish", "ninki", "odds", "time", "margin", "jockey_code",
          "trainer_code", "sire_id", "dam_id", "damsire_id", "corners", "agari", "run_style"]
    con.executemany(f"INSERT INTO runners VALUES ({','.join('?' * len(uc))})", [tuple(r[c] for c in uc) for r in rows])
    # netkeiba にしか無い表を写す
    con.execute(f"ATTACH DATABASE '{NK_DB}' AS nk")
    for t in ("oikiri", "ped_horse", "damsire_line"):
        try:
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM nk.{t}")
        except sqlite3.OperationalError as e:
            print(f"  {t} は写せませんでした: {e}")
    con.commit()
    n = con.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM races").fetchone()
    print(f"完了: {OUT_DB} レース {n[0]:,} ({n[1]}〜{n[2]})", flush=True)


if __name__ == "__main__":
    main()
