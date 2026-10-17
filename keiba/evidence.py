"""予想の根拠を、数字つきの文で出す。

- 勝率の内訳: 統計モデルは「単勝オッズ (市場) の勝率」を土台に、特徴量で上げ下げする。
  LightGBM の SHAP 値 (pred_contrib) で、どの材料がどれだけ上げ下げしたかを分野ごとにまとめる。
  レース内の平均との差にしているので「他の馬と比べて」の上げ下げになる。
- 騎手の当日・前日の成績 (netkeiba の確定結果から)。検証 (backtest/jockey_day_eval.py) では
  勝率の予想を良くしなかったので、表示だけ (勝率には足さない)。前日までの結果は「直近60日」に足す (学習と同じ扱い)。
- JRA-VAN の調教タイム (jravan/data にある手元だけ。公開リポジトリには置けないので、アプリでは出ない)。
"""
from __future__ import annotations

import datetime as dt
import glob
import math
import os
from typing import Optional

import features as F
import scraper

HERE = os.path.dirname(os.path.abspath(__file__))
JV = os.path.join(HERE, "jravan", "data")

GROUPS = [
    ("近走・タイム", ("n_past", "perf_w", "perf_cls", "last_fin", "last_margin", "last_ninki", "last_cls_diff", "last_agari",
                    "best_fin", "mean_margin", "wins", "top3", "outrun", "class_up", "same_", "big_field_perf", "tidx",
                    "last_agari_idx", "fresh_", "second_", "n_same_surf_runs", "is_debut", "car_")),
    ("騎手", ("jockey_", "jt_combo")),
    ("厩舎", ("trainer_",)),
    ("血統", ("sire_", "damsire_", "nick_")),
    ("調教評価", ("oik_",)),
    ("枠・脚質・展開", ("waku", "umaban", "style", "n_nige", "n_front", "early_pos", "last_early_pos", "front_pressure", "course_", "cond_style")),
    ("当日の馬場", ("day_",)),
    ("馬体・斤量・ローテ", ("sex", "age", "weight", "body_weight", "bw_", "interval_w", "turf_to_dirt", "dirt_to_turf",
                       "first_surface", "dist_change")),
    ("人気の偏り", ("mkt_",)),
]


def group_of(feat: str) -> str:
    for g, keys in GROUPS:
        if any(feat.startswith(k) or (k in feat and k in ("tidx", "same_")) for k in keys):
            return g
    return "レース条件との相性"


def grouped(contrib: dict[int, dict[str, float]]) -> dict[int, dict[str, float]]:
    """馬番 → {分野: レース平均との差 (対数オッズ)}。"""
    raw = {u: {} for u in contrib}
    for u, c in contrib.items():
        for k, v in c.items():
            g = group_of(k)
            raw[u][g] = raw[u].get(g, 0.0) + v
    gs = {g for d in raw.values() for g in d}
    mean = {g: sum(d.get(g, 0.0) for d in raw.values()) / max(1, len(raw)) for g in gs}
    return {u: {g: d.get(g, 0.0) - mean[g] for g in gs} for u, d in raw.items()}


# ---------------------------------------------------------------- 騎手の当日・前日
_rides: dict = {}


def day_rides(date: str, before: str = "") -> list[dict]:
    """その日の確定したレースの全騎乗 [{date, start, venue, rno, jockey, trainer, finish, ninki, odds}]。
    before ("HH:MM") を渡すと、その時刻より前に発走したレースだけ取りに行く。"""
    if date in _rides:
        return _rides[date]
    out = []
    try:
        ms = scraper.fetch_meetings(date)
    except scraper.ScrapeError:
        return []
    for m in ms:
        for r in m.races:
            if before and (r.start or "99:99") >= before:
                continue
            try:
                res = scraper.fetch_result(r.race_id)
            except scraper.ScrapeError:
                res = None
            if not res:
                continue
            for o in res.order:
                out.append(dict(date=date, start=r.start, venue=m.venue, rno=r.rno, jockey=o.get("jockey") or "",
                                trainer=o.get("trainer") or "", finish=o.get("finish") or 0, ninki=o.get("ninki"),
                                odds=o.get("odds")))
    if out and not before and date < dt.date.today().strftime("%Y%m%d"):
        _rides[date] = out   # 過去の日だけ覚える (当日は結果が増えていく)
    return out


def prev_kaisai(date: str) -> Optional[str]:
    """直前の開催日 (3日以内)。"""
    d0 = dt.date(int(date[:4]), int(date[4:6]), int(date[6:8]))
    for k in range(1, 4):
        d = d0 - dt.timedelta(days=k)
        try:
            if d.strftime("%Y%m%d") in scraper.fetch_kaisai_dates(d.year, d.month):
                return d.strftime("%Y%m%d")
        except scraper.ScrapeError:
            return None
    return None


def extra_form(rides: list[dict]) -> dict:
    """確定結果 → statmodel.predict の extra_form。"""
    out: dict = {"j": {}, "t": {}}
    for r in sorted(rides, key=lambda x: (x["date"], x["start"])):
        if not r["finish"]:
            continue
        o = dt.date(int(r["date"][:4]), int(r["date"][4:6]), int(r["date"][6:8])).toordinal()
        win = 1 if r["finish"] == 1 else 0
        out["j"].setdefault(F.jockey_key(r["jockey"]), []).append((o, win))
        out["t"].setdefault(F.trainer_key(r["trainer"]), []).append((o, win))
    return out


def jockey_record(rides: list[dict], jockey: str, before: str = "99:99") -> dict:
    """騎手のその日の成績 (before より前に発走したレースだけ)。人気より上の着順だった数も。"""
    k = F.jockey_key(jockey)
    rs = [r for r in rides if F.jockey_key(r["jockey"]) == k and r["start"] < before and r["finish"]]
    rs.sort(key=lambda r: r["start"])
    return dict(n=len(rs), win=sum(r["finish"] == 1 for r in rs), top3=sum(r["finish"] <= 3 for r in rs),
                beat=sum(1 for r in rs if r["ninki"] and r["finish"] < r["ninki"]),
                exp=sum(1 / r["odds"] * 0.8 for r in rs if r["odds"]),   # 単勝オッズから見た勝つはずの数 (控除率20%で補正)
                seq=" ".join(f"{r['finish']}着({r['ninki']}人)" for r in rs))


def record_text(rec: dict) -> str:
    if not rec["n"]:
        return "騎乗なし"
    s = f"{rec['n']}戦{rec['win']}勝・3着内{rec['top3']}回"
    if rec["exp"]:
        s += f" (オッズから見た勝ち数の目安 {rec['exp']:.1f})"
    return s


# ---------------------------------------------------------------- JRA-VAN の調教タイム (手元のみ)
_wk: dict = {}


def _t(s: str) -> Optional[float]:
    try:
        v = int(s)
    except ValueError:
        return None
    return v / 10 if v > 0 else None


def workouts(since: str) -> dict:
    """horse_id → [(日付, 場所, 坂路/ウッド, 4F, ラスト1F, 4F の同日同コース順位%)]。jravan/data が無ければ空。"""
    if since in _wk:
        return _wk[since]
    rows = []
    for f in sorted(glob.glob(os.path.join(JV, "SLOP_HC_*.txt"))):
        if f[-8:-4] < since[:4]:
            continue
        for l in open(f, encoding="utf-8"):
            if len(l.rstrip()) >= 58 and l[12:20] >= since:
                rows.append((l[24:34], l[12:20], int(l[11]), "坂路", _t(l[34:38]), _t(l[55:58])))
    for f in sorted(glob.glob(os.path.join(JV, "WOOD_WC_*.txt"))):
        if f[-8:-4] < since[:4]:
            continue
        for l in open(f, encoding="utf-8"):
            l = l.rstrip()
            if len(l) >= 60 and l[12:20] >= since:
                tail = l[-24:]
                rows.append((l[24:34], l[12:20], int(l[11]), "ウッド", _t(tail[0:4]), _t(tail[21:24])))
    rows = [r for r in rows if r[4]]
    grp: dict = {}
    for r in rows:
        grp.setdefault((r[1], r[2], r[3]), []).append(r[4])
    for v in grp.values():
        v.sort()
    out: dict = {}
    for hid, d, c, kind, f4, l1 in rows:
        v = grp[(d, c, kind)]
        rank = (sum(1 for x in v if x < f4) + 0.5) / len(v) * 100
        out.setdefault(hid, []).append((d, "美浦" if c == 0 else "栗東", kind, f4, l1, rank, len(v)))
    _wk[since] = out
    return out


def race_workouts(race, days: int = 14) -> dict[int, dict]:
    """出走馬ごとの、レース前 days 日の調教 (JRA-VAN)。馬番 → {lines, best (同日同コースでの速さの上位%), rank, n}。
    rank は出走馬の中で「一番速い調教の上位%」が何番目か。jravan/data が無ければ空。"""
    d0 = dt.date(int(race.date[:4]), int(race.date[4:6]), int(race.date[6:8]))
    wk = workouts((d0 - dt.timedelta(days=days)).strftime("%Y%m%d"))
    if not wk:
        return {}
    out = {}
    for h in race.horses:
        ws = sorted((w for w in wk.get(h.horse_id or "", []) if w[0] < race.date), key=lambda w: w[5])
        if not ws:
            continue
        lines = [f"{int(d[4:6])}/{int(d[6:8])} {c}{kind} 4F {f4:.1f}秒" + (f"・ラスト1F {l1:.1f}秒" if l1 else "") +
                 f" (この日の{c}{kind} {n}本中 " + (f"上位{rank:.0f}%)" if rank >= 1 else "上位1%以内)") for d, c, kind, f4, l1, rank, n in ws[:2]]
        out[h.umaban] = dict(lines=lines, best=ws[0][5], count=len(ws))
    order = sorted(out, key=lambda u: out[u]["best"])
    for i, u in enumerate(order):
        out[u]["rank"], out[u]["n"] = i + 1, len(order)
    return out


# ---------------------------------------------------------------- 根拠の文
def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _mult(v: float) -> str:
    m = math.exp(v)
    return f"×{m:.2f}"


def fact(group: str, horse, f: dict, stats: dict, race, ctx: dict) -> str:
    """分野ごとの、事実の数字。"""
    bw = stats.get("base_win", 0.08)
    if group == "近走・タイム":
        ps = [p for p in (horse.past or [])[:5] if p.get("finish")]
        if not ps:
            return "初出走 (近走なし)"
        s = " → ".join(f"{p['finish']}着" + (f"({p['ninki']}人)" if p.get("ninki") else "") for p in ps[:3])
        p0 = ps[0]
        t = f"近走 {s}"
        if p0.get("venue"):
            t += f"。前走 {p0.get('venue')}{p0.get('surface')}{p0.get('distance') or ''}"
            if p0.get("margin") is not None:
                t += f" 着差{p0['margin']:+.1f}秒"
        bt = f.get("best_tidx")
        if bt == bt and bt is not None:
            t += f"。タイム指数の最高 {bt:+.1f} (0が平均、+1で上位約16%)"
        return t
    if group == "騎手":
        t = f"{horse.jockey}: 勝率 {_pct(f.get('jockey_win', 0))} (全体平均 {_pct(bw)})"
        if f.get("jockey_form_n"):
            t += f"、直近60日 {_pct(f.get('jockey_form_win', 0))} ({int(f['jockey_form_n'])}騎乗)"
        t += f"、{race.venue}で {_pct(f.get('jockey_venue_win', 0))}"
        if f.get("jt_combo_n", 0) >= 5:
            t += f"、この厩舎とのコンビ {_pct(f.get('jt_combo_win', 0))}"
        if f.get("jockey_change"):
            t += "。乗り替わり"
        return t
    if group == "厩舎":
        t = f"{horse.trainer}: 勝率 {_pct(f.get('trainer_win', 0))}"
        if f.get("trainer_form_n"):
            t += f"、直近60日 {_pct(f.get('trainer_form_win', 0))} ({int(f['trainer_form_n'])}頭)"
        t += f"、{race.surface}・この距離帯 {_pct(f.get('trainer_sb_win', 0))}"
        return t
    if group == "血統":
        return (f"父 {horse.sire}: {race.surface}・この距離帯の勝率 {_pct(f.get('sire_sb_win', 0))}"
                f"、母父 {horse.damsire}: {race.surface}で {_pct(f.get('damsire_s_win', 0))} (全体平均 {_pct(bw)})")
    if group == "調教評価":
        return f"調教評価 {horse.oik_rank or '－'} {horse.oik_critic or ''}".strip()
    if group == "枠・脚質・展開":
        style = horse.style or "不明"
        return (f"{horse.waku}枠{horse.umaban}番・脚質 {style}。このコースでこの枠帯の勝率 {_pct(f.get('course_waku_win', 0))}"
                f"、{style}の勝率 {_pct(f.get('course_style_win', 0))}。逃げ・先行 {ctx.get('n_front', 0)}頭")
    if group == "当日の馬場":
        d = ctx.get("day") or {}
        if not d.get("done"):
            return "今日のこの場はまだ結果なし"
        return (f"今日の{race.venue}で先に終わった{d['done']}レース: 逃げ・先行の勝ち {d['front_win'] * d['done']:.0f}回"
                f"、1〜3枠の勝ち {d['inner_win'] * d['done']:.0f}回")
    if group == "馬体・斤量・ローテ":
        t = f"斤量 {horse.weight:g}kg、馬体重 {horse.body_weight or '未発表'}"
        if horse.interval:
            t += f"、{horse.interval}"
        return t
    if group == "人気の偏り":
        return "単勝オッズの付き方の癖 (人気馬・人気薄の過大/過小評価) の補正"
    return f"{race.course}・{race.condition or ''} での相性"


def reasons(u: int, race, sm: dict, stats: dict, top: int = 4, race_wk: Optional[dict] = None,
            rides_today: Optional[list] = None, rides_prev: Optional[list] = None) -> dict:
    """1頭の根拠。戻り: {market, model, final, lines:[(分野, 倍率の文, 事実)], jockey_today, jockey_prev, workouts}。"""
    horse = next(h for h in race.horses if h.umaban == u)
    f = sm["features"][u]
    ctx = F.race_context(dict(surface=race.surface, distance=race.distance, venue=race.venue, condition=race.condition,
                              heads=race.heads, cls=race.cls, grade=race.grade, name=race.name, turn=race.turn),
                         [dict(style=h.style, weight=h.weight) for h in race.horses], (sm.get("day") or {}))
    lines = []
    shown = set()
    if sm.get("contrib"):
        g = grouped(sm["contrib"])[u]
        for name, v in sorted(g.items(), key=lambda x: -abs(x[1]))[:top]:
            if abs(v) < 0.015:
                continue
            shown.add(name)
            lines.append((name, ("プラス " if v > 0 else "マイナス ") + _mult(v), fact(name, horse, f, stats, race, ctx)))
    for name in ("近走・タイム", "騎手"):   # 補正が小さくても、近走と騎手の事実は出す
        if name not in shown:
            lines.append((name, "", fact(name, horse, f, stats, race, ctx)))
    out = dict(market=sm["market_probs"].get(u), model=sm["probs"].get(u), lines=lines)
    wk = (race_wk or {}).get(u)
    if wk:
        out["workouts"] = [f"出走馬{wk['n']}頭中 {wk['rank']}位の速さ (14日で{wk['count']}本)"] + wk["lines"]
    if rides_today is not None:
        out["jockey_today"] = record_text(jockey_record(rides_today, horse.jockey, race.start or "99:99"))
    if rides_prev is not None:
        out["jockey_prev"] = record_text(jockey_record(rides_prev, horse.jockey))
    return out


def text(r: dict, final: Optional[float] = None) -> list[str]:
    """reasons の戻りを、表示用の行に。"""
    out = []
    if r.get("market") is not None:
        s = f"オッズから見た勝率 {_pct(r['market'])} → 統計モデル {_pct(r['model'])}"
        fin = final if final is not None else r["model"]
        if final is not None and abs(final - r["model"]) > 1e-4:
            s += f" → 調教タイム補正後 {_pct(final)}"
        ratio = fin / r["market"] if r["market"] else 1
        s += ("。市場より高く評価" if ratio >= 1.03 else "。市場より低く評価" if ratio <= 0.97 else "。市場の評価とほぼ同じ") + f" (×{ratio:.2f})"
        out.append(s)
    for name, m, fct in r["lines"]:
        out.append(f"【{name}{' ' + m if m else ''}】{fct}")
    for w in r.get("workouts") or []:
        out.append(f"【調教タイム】{w}")
    if "jockey_prev" in r:
        out.append(f"【騎手の前日】{r['jockey_prev']}")
    if r.get("jockey_today") and r["jockey_today"] != "騎乗なし":
        out.append(f"【騎手の今日】{r['jockey_today']}")
    return out
