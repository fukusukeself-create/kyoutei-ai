"""データ取得の段取り（公式サイト優先・ダメなら Open API）と、予想に使う集計。"""
from __future__ import annotations

import concurrent.futures as cf
import copy
import datetime
import threading
import time

from . import formation, model, openapi, sources
from .sources import SourceError

JST = datetime.timezone(datetime.timedelta(hours=9))


def now_jst() -> datetime.datetime:
    return datetime.datetime.now(JST)


def today_hd() -> str:
    return now_jst().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 開催一覧・締切時刻
# ---------------------------------------------------------------------------
def get_schedule(hd: str) -> dict:
    """その日の開催場と、各レースの締切予定時刻。

    戻り値: {"venues": [{jcd, name, grade, title, day_text, deadlines{rno: "HH:MM"}, ...}], "notes": [...]}
    """
    notes = []
    venues = []
    try:
        venues = sources.fetch_venues(hd)
    except SourceError:
        notes.append("公式サイトの応答が遅い（または繋がらない）ため、予備データ(Boatrace Open API)で表示しています。")

    programs = sources.fetch_openapi_day("programs", hd)
    by_venue = {}
    for rec in programs:
        r = openapi.program_to_race(rec)
        v = by_venue.setdefault(r["jcd"], {"deadlines": {}, "title": r["title"], "grade": r["grade"]})
        if r["deadline"]:
            v["deadlines"][r["rno"]] = r["deadline"]

    if not venues:
        for jcd in sorted(by_venue):
            venues.append({
                "jcd": jcd, "name": sources.VENUES.get(jcd, str(jcd)), "grade": by_venue[jcd]["grade"],
                "title": by_venue[jcd]["title"], "day_text": "", "period": "", "status_text": "",
                "time_zone": "", "cancelled": False,
            })

    # 締切時刻は Open API の出走表（1日ぶんを1回で取れる）から
    for v in venues:
        v["deadlines"] = dict(by_venue.get(v["jcd"], {}).get("deadlines", {}))
    if not venues:
        notes.append("この日の開催情報が見つかりませんでした。")
    return {"venues": venues, "notes": notes}


def next_race(deadlines: dict, hd: str, now: datetime.datetime | None = None):
    """締切前でいちばん近いレース番号と締切時刻。全て締切済みなら (None, None)。"""
    now = now or now_jst()
    today = now.strftime("%Y%m%d")
    if not deadlines or hd < today:
        return None, None
    if hd > today:
        first = min(deadlines)
        return first, deadlines[first]
    cur = now.strftime("%H:%M")
    for rno in sorted(deadlines):
        if _hm(deadlines[rno]) > _hm(cur):
            return rno, deadlines[rno]
    return None, None


def _hm(s: str) -> str:
    h, m = s.split(":")
    return f"{int(h):02d}:{int(m):02d}"


def upcoming(venues: list[dict], hd: str, now: datetime.datetime | None = None) -> list[dict]:
    """全場ぶんの「これから締切のレース」を締切が早い順に並べる。"""
    now = now or now_jst()
    is_today = hd == now.strftime("%Y%m%d")
    cur = now.strftime("%H:%M")
    out = []
    for v in venues:
        for rno, t in (v.get("deadlines") or {}).items():
            if is_today and _hm(t) <= _hm(cur):
                continue
            if hd < now.strftime("%Y%m%d"):
                continue
            out.append({"jcd": v["jcd"], "name": v["name"], "rno": rno, "deadline": _hm(t),
                        "grade": v.get("grade", ""), "day_text": v.get("day_text", "")})
    out.sort(key=lambda x: (x["deadline"], x["jcd"]))
    return out


# ---------------------------------------------------------------------------
# レース単位のデータ
# ---------------------------------------------------------------------------
def get_program(jcd: int, rno: int, hd: str) -> dict:
    """出走表。公式サイト → だめなら Open API。"""
    try:
        race = sources.fetch_racelist(jcd, rno, hd)
    except SourceError as e:
        rec = sources.find_openapi_race(sources.fetch_openapi_day("programs", hd), jcd, rno)
        if rec is None:
            raise SourceError(f"出走表を取得できませんでした。{e}") from e
        race = openapi.program_to_race(rec)
        if len(race["boats"]) != 6:
            raise SourceError("出走表を取得できませんでした（6艇そろっていません）。") from e
        race["note"] = "公式サイトに接続できなかったため、予備データ(Boatrace Open API)の出走表です。今節成績は表示されません。"
    model.attach_racer_stats(race)
    return race


def get_preview(jcd: int, rno: int, hd: str) -> dict | None:
    """直前情報（展示タイム・スタート展示・気象）。まだ無ければ None。"""
    try:
        pv = sources.fetch_beforeinfo(jcd, rno, hd)
        if pv:
            return pv
    except SourceError:
        pass
    rec = sources.find_openapi_race(sources.fetch_openapi_day("previews", hd), jcd, rno)
    return openapi.preview_to_dict(rec) if rec else None


def get_original_exhibition(jcd: int, rno: int, hd: str) -> dict | None:
    try:
        return sources.fetch_original_exhibition(jcd, rno, hd)
    except Exception:
        return None


def get_odds(jcd: int, rno: int, hd: str) -> dict:
    try:
        return sources.fetch_odds3t(jcd, rno, hd)
    except SourceError:
        return {}


def get_results(jcd: int, hd: str) -> list[dict]:
    """その場の当日の結果（1Rから順）。"""
    try:
        res = sources.fetch_resultlist(jcd, hd)
        if res:
            return res
    except SourceError:
        pass
    out = []
    for rec in sources.fetch_openapi_day("results", hd):
        if int(rec.get("race_stadium_number", 0)) != int(jcd):
            continue
        r = openapi.result_to_dict(rec)
        if r and r["top3"]:
            pay = (r["payouts"].get("trifecta") or [(None, None)])[0][1]
            out.append({"rno": int(rec["race_number"]), "top3": r["top3"], "payout": pay, "technique": r["technique"]})
    out.sort(key=lambda x: x["rno"])
    return out


def get_stadium(jcd: int) -> dict | None:
    try:
        return sources.fetch_stadium(jcd)
    except SourceError:
        return None


# ---------------------------------------------------------------------------
# 1レースぶんをまとめて取得（同時に取りに行くので、1ページ10秒かかる時でも全体で約10秒）
# ---------------------------------------------------------------------------
# 何度も同じページを取りに行かないための短時間キャッシュ。
# 直前情報・オッズは刻々と変わるので短く、出走表・場データは長く持つ。
TTL_PROGRAM = 600.0
TTL_STADIUM = 86400.0
TTL_PREVIEW = 30.0
TTL_PREVIEW_EMPTY = 20.0   # 展示がまだ出ていない時は早めに取り直す
TTL_ODDS = 30.0
TTL_RESULTS = 90.0

_cache: dict = {}
_cache_lock = threading.Lock()


def _cached(key, fn, ttl, ttl_empty=None):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fn()
    keep = ttl if (value or ttl_empty is None) else ttl_empty
    with _cache_lock:
        _cache[key] = (time.time() + keep, value)
        if len(_cache) > 2000:
            for k in [k for k, v in _cache.items() if v[0] <= now]:
                del _cache[k]
    return value


def preview_cached(jcd: int, rno: int, hd: str) -> dict | None:
    return _cached(("preview", jcd, rno, hd), lambda: get_preview(jcd, rno, hd), TTL_PREVIEW, TTL_PREVIEW_EMPTY)


def clear_live(jcd: int, rno: int, hd: str) -> None:
    """「最新に更新」用。直前情報・オッズ・結果のキャッシュを捨てる。"""
    with _cache_lock:
        for key in [("preview", jcd, rno, hd), ("original", jcd, rno, hd), ("odds", jcd, rno, hd), ("results", jcd, hd)]:
            _cache.pop(key, None)


def get_bundle(jcd: int, rno: int, hd: str) -> dict:
    """予想に使うデータ一式。出走表が取れなければ SourceError。

    戻り値: {race(直前情報は race["preview"]), original, odds, results, stadium}
    """
    jobs = {
        "race": lambda: _cached(("program", jcd, rno, hd), lambda: get_program(jcd, rno, hd), TTL_PROGRAM),
        "preview": lambda: preview_cached(jcd, rno, hd),
        "original": lambda: _cached(("original", jcd, rno, hd), lambda: get_original_exhibition(jcd, rno, hd), TTL_PREVIEW, TTL_PREVIEW_EMPTY),
        "odds": lambda: _cached(("odds", jcd, rno, hd), lambda: get_odds(jcd, rno, hd), TTL_ODDS, TTL_PREVIEW_EMPTY),
        "results": lambda: _cached(("results", jcd, hd), lambda: get_results(jcd, hd), TTL_RESULTS),
        "stadium": lambda: _cached(("stadium", jcd), lambda: get_stadium(jcd), TTL_STADIUM, 300.0),
    }
    with cf.ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        futures = {name: ex.submit(fn) for name, fn in jobs.items()}
        out = {name: f.result() for name, f in futures.items()}
    race = copy.deepcopy(out["race"])
    race["preview"] = copy.deepcopy(out["preview"])
    return {"race": race, "original": out["original"], "odds": out["odds"] or {},
            "results": out["results"] or [], "stadium": out["stadium"]}


def minutes_to_deadline(deadline: str, hd: str, now: datetime.datetime | None = None):
    """締切までの残り分数。当日でない・時刻不明なら None。締切を過ぎていれば負の数。"""
    now = now or now_jst()
    if not deadline or hd != now.strftime("%Y%m%d"):
        return None
    h, m = map(int, deadline.split(":"))
    return (h * 60 + m) - (now.hour * 60 + now.minute)


# ---------------------------------------------------------------------------
# 集計・予想
# ---------------------------------------------------------------------------
def trend(results: list[dict], before_rno: int) -> dict | None:
    """当日ここまでの傾向（1号艇の1着回数、決まり手の内訳）。"""
    done = [r for r in results if r["rno"] < before_rno]
    if not done:
        return None
    kim = {}
    for r in done:
        if r.get("technique"):
            kim[r["technique"]] = kim.get(r["technique"], 0) + 1
    pays = [r["payout"] for r in done if r.get("payout")]
    return {
        "races": len(done),
        "lane1_wins": sum(1 for r in done if r["top3"][0] == 1),
        "lane1_top2": sum(1 for r in done if 1 in r["top3"][:2]),
        "kimarite": kim,
        "avg_payout": round(sum(pays) / len(pays)) if pays else None,
        "list": done,
    }


def analyze(race: dict, style: str = formation.DEFAULT_STYLE, odds: dict | None = None) -> dict:
    """データ予想（統計AI）を実行して、印・確率・買い目をまとめる。"""
    pred = model.predict(race)
    sug = formation.suggest(pred["P"], style)
    cards = []
    if sug["main"]:
        cards.append(formation.describe(sug["main"], pred["P"], odds, "本線"))
    if sug["sub"]:
        cards.append(formation.describe(sug["sub"], pred["P"], odds, "抑え"))
    return {
        "pred": pred,
        "marks": formation.marks(pred["win"], pred["top3"]),
        "cards": cards,
        "others": formation.other_bets(pred["P"]),
        "style": style,
    }


def check_hit(cards: list[dict], top3) -> str | None:
    """結果が出ているレースで、買い目が当たったかどうか。"""
    if not top3:
        return None
    key = "-".join(str(x) for x in top3)
    for c in cards:
        if any(t["ticket"] == key for t in c["tickets"]):
            return c["label"]
    return ""
