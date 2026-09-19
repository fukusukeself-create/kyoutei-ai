"""netkeiba (race.netkeiba.com) から中央競馬の開催・出馬表・馬柱・オッズ・結果を取得する。

JRA公式サイトはクラウド環境からのアクセスを拒否する (403) ため、公開されている
netkeiba のレースページを使う。取得するのは会員登録なしで見られる範囲だけ。

- 開催日カレンダー : top/calendar.html?year=&month=
- 開催・レース一覧 : top/race_list_sub.html?kaisai_date=YYYYMMDD
- 出馬表          : race/shutuba.html?race_id=
- 馬柱 (近5走)     : race/shutuba_past.html?race_id=
- オッズ          : api/api_get_jra_odds.html?race_id=&type=  (1:単複 4:馬連 5:ワイド 6:馬単 7:三連複 8:三連単)
- 結果・払戻       : race/result.html?race_id=

race_id は 12桁: 年4 + 場コード2 + 開催回2 + 日目2 + レース番号2。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE = "https://race.netkeiba.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,ja-JP;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://race.netkeiba.com/",
}

# 場コード (race_id の 5〜6桁目)
VENUES = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

GRADE_RANK = {"GI": 6, "GII": 5, "GIII": 4, "OP": 3, "L": 3, "3勝": 2, "2勝": 1.5,
              "1勝": 1, "未勝利": 0, "新馬": 0}

_session = requests.Session()
_session.headers.update(HEADERS)


class ScrapeError(Exception):
    pass


def _get(url: str, timeout: float = 12.0, retries: int = 2) -> str:
    last: Optional[Exception] = None
    for i in range(retries + 1):
        try:
            res = _session.get(url, timeout=timeout)
            if res.status_code == 200:
                res.encoding = "utf-8"
                return res.text
            last = ScrapeError(f"HTTP {res.status_code}: {url}")
        except requests.RequestException as e:  # 通信断・タイムアウト
            last = e
        if i < retries:
            time.sleep(1.0 * (i + 1))
    raise ScrapeError(f"取得に失敗しました: {url} ({last})")


def _text(el) -> str:
    return " ".join(el.get_text(" ").split()) if el is not None else ""


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def _to_int(s: str) -> Optional[int]:
    m = re.search(r"-?\d+", s or "")
    return int(m.group()) if m else None


# ---------------------------------------------------------------- カレンダー

def fetch_kaisai_dates(year: int, month: int) -> list[str]:
    """その月の開催日 (YYYYMMDD) を返す。"""
    html = _get(f"{BASE}/top/calendar.html?year={year}&month={month:02d}")
    return sorted(set(re.findall(r"kaisai_date=(\d{8})", html)))


# ---------------------------------------------------------------- 開催一覧

@dataclass
class RaceSummary:
    race_id: str
    rno: int
    name: str
    start: str          # "10:00"
    course: str         # "ダ1200m"
    heads: int
    grade: str = ""     # "GI" 等


@dataclass
class Meeting:
    venue_code: str
    venue: str
    kai: str            # "4回"
    day: str            # "5日目"
    weather: str
    turf: str           # "良"
    dirt: str
    races: list[RaceSummary] = field(default_factory=list)


def fetch_meetings(date: str) -> list[Meeting]:
    """開催日 (YYYYMMDD) の全開催場とレース一覧。開催が無い日は空リスト。"""
    html = _get(f"{BASE}/top/race_list_sub.html?kaisai_date={date}")
    soup = BeautifulSoup(html, "html.parser")
    meetings: list[Meeting] = []
    for dl in soup.select("dl.RaceList_DataList"):
        title = dl.select_one(".RaceList_DataTitle")
        if not title:
            continue
        smalls = [_text(s) for s in title.select("small")]
        kai = smalls[0] if smalls else ""
        day = smalls[1] if len(smalls) > 1 else ""
        venue = _text(title).replace(kai, "").replace(day, "").strip()
        desc = dl.select_one(".RaceList_DataDesc")
        weather = ""
        turf = dirt = ""
        if desc:
            w = desc.select_one(".Icon_Weather")
            if w:
                m = re.search(r"Weather(\d+)", " ".join(w.get("class", [])))
                weather = {"01": "晴", "02": "曇", "03": "雨", "04": "小雨", "05": "雪", "06": "小雪"}.get(
                    m.group(1) if m else "", "")
            t = desc.select_one(".Shiba")
            d = desc.select_one(".Da")
            turf = _text(t).split("：")[-1] if t else ""
            dirt = _text(d).split("：")[-1] if d else ""
        races: list[RaceSummary] = []
        venue_code = ""
        for li in dl.select("li.RaceList_DataItem"):
            a = li.select_one("a[href*='race_id=']")
            if not a:
                continue
            m = re.search(r"race_id=(\d{12})", a["href"])
            if not m:
                continue
            rid = m.group(1)
            venue_code = rid[4:6]
            rno = _to_int(_text(li.select_one(".Race_Num"))) or int(rid[-2:])
            name = _text(li.select_one(".ItemTitle"))
            grade_el = li.select_one(".Icon_GradeType")
            grade = ""
            if grade_el:
                m2 = re.search(r"Icon_GradeType(\d+)", " ".join(grade_el.get("class", [])))
                grade = {"1": "GI", "2": "GII", "3": "GIII", "5": "OP", "15": "L",
                         "16": "3勝", "17": "2勝", "18": "1勝"}.get(m2.group(1) if m2 else "", "")
            start = _text(li.select_one(".RaceList_Itemtime"))
            course = _text(li.select_one(".RaceList_ItemLong"))
            heads = _to_int(_text(li.select_one(".RaceList_Itemnumber"))) or 0
            races.append(RaceSummary(rid, rno, name, start, course, heads, grade))
        races.sort(key=lambda r: r.rno)
        meetings.append(Meeting(venue_code, venue or VENUES.get(venue_code, venue_code),
                                kai, day, weather, turf, dirt, races))
    return meetings


# ---------------------------------------------------------------- 出馬表

@dataclass
class Horse:
    waku: int
    umaban: int
    name: str
    horse_id: str
    sex_age: str        # "牡4"
    weight: float       # 斤量
    jockey: str
    trainer: str        # "栗東 西園翔"
    body_weight: str    # "520(-2)" 未発表なら ""
    win_odds: Optional[float] = None
    ninki: Optional[int] = None
    # 馬柱から
    sire: str = ""
    dam: str = ""
    damsire: str = ""
    style: str = ""     # 逃/先/差/追
    interval: str = ""  # "中12週"
    rest_note: str = ""
    past: list[dict] = field(default_factory=list)


@dataclass
class Race:
    race_id: str
    date: str
    venue: str
    rno: int
    name: str
    start: str
    course: str         # "ダ1800m"
    surface: str        # "芝" / "ダ" / "障"
    distance: int
    turn: str           # "右"/"左"
    weather: str
    condition: str      # 馬場
    kai: str
    day: str
    cls: str            # "オープン" "3勝クラス" 等
    grade: str
    heads: int
    horses: list[Horse] = field(default_factory=list)


def _parse_race_header(soup: BeautifulSoup, race_id: str) -> dict:
    name = _text(soup.select_one(".RaceName"))
    d1 = _text(soup.select_one(".RaceData01"))
    d2 = [_text(s) for s in soup.select(".RaceData02 span")]
    start = (re.search(r"(\d{1,2}:\d{2})", d1) or [None, ""])[1]
    course_m = re.search(r"(芝|ダ|障)\s*(\d{3,4})m", d1)
    surface, distance = (course_m.group(1), int(course_m.group(2))) if course_m else ("", 0)
    turn_m = re.search(r"\((右|左|直)", d1)
    weather_m = re.search(r"天候:(\S+)", d1)
    cond_m = re.search(r"馬場:(\S+)", d1)
    grade = ""
    g = soup.select_one(".RaceName .Icon_GradeType")
    if g:
        m2 = re.search(r"Icon_GradeType(\d+)", " ".join(g.get("class", [])))
        grade = {"1": "GI", "2": "GII", "3": "GIII", "5": "OP", "15": "L"}.get(m2.group(1) if m2 else "", "")
    heads = 0
    for s in d2:
        if s.endswith("頭"):
            heads = _to_int(s) or 0
    return dict(
        race_id=race_id, date=race_id[:4] + "", venue=d2[1] if len(d2) > 1 else VENUES.get(race_id[4:6], ""),
        rno=int(race_id[-2:]), name=name, start=start,
        course=f"{surface}{distance}m" if surface else "", surface=surface, distance=distance,
        turn=turn_m.group(1) if turn_m else "", weather=weather_m.group(1) if weather_m else "",
        condition=cond_m.group(1) if cond_m else "", kai=d2[0] if d2 else "",
        day=d2[2] if len(d2) > 2 else "", cls=" ".join(d2[3:5]) if len(d2) > 4 else "",
        grade=grade, heads=heads,
    )


def fetch_shutuba(race_id: str, date: str = "") -> Race:
    html = _get(f"{BASE}/race/shutuba.html?race_id={race_id}")
    soup = BeautifulSoup(html, "html.parser")
    hdr = _parse_race_header(soup, race_id)
    hdr["date"] = date
    race = Race(**hdr)
    tbl = soup.select_one("table.Shutuba_Table")
    if tbl is None:
        raise ScrapeError("出馬表がまだ公開されていません。")
    for tr in tbl.select("tr.HorseList"):
        tds = tr.select("td")
        if len(tds) < 10:
            continue
        name_a = tr.select_one(".HorseName a")
        hid = ""
        if name_a and name_a.get("href"):
            m = re.search(r"/horse/(\d+)", name_a["href"])
            hid = m.group(1) if m else ""
        cancel = "Cancel" in " ".join(tr.get("class", []))
        odds = _to_float(_text(tr.select_one("td.Popular span")))
        ninki = _to_int(_text(tr.select_one("td.Popular_Ninki span")))
        h = Horse(
            waku=_to_int(_text(tds[0])) or 0,
            umaban=_to_int(_text(tds[1])) or 0,
            name=_text(name_a) if name_a else _text(tds[3]),
            horse_id=hid,
            sex_age=_text(tds[4]),
            weight=_to_float(_text(tds[5])) or 0.0,
            jockey=_text(tds[6]),
            trainer=_text(tds[7]),
            body_weight=_text(tds[8]),
            win_odds=odds,
            ninki=ninki,
        )
        if cancel:
            h.rest_note = "出走取消"
        race.horses.append(h)
    race.horses.sort(key=lambda h: h.umaban)
    if not race.heads:
        race.heads = len(race.horses)
    return race


# ---------------------------------------------------------------- 馬柱 (近5走)

def _parse_past_cell(td) -> Optional[dict]:
    d1 = td.select_one(".Data01")
    if d1 is None or not _text(d1):
        return None
    date_venue = _text(d1.select_one("span"))
    finish = _to_int(_text(d1.select_one(".Num")))
    dv = date_venue.split()
    d2 = td.select_one(".Data02")
    race_name = _text(d2.select_one("a")) if d2 else ""
    grade = ""
    g = d2.select_one(".Icon_GradeType") if d2 else None
    if g:
        grade = _text(g)
        race_name = race_name.replace(grade, "").strip()
    d5 = _text(td.select_one(".Data05"))
    cm = re.search(r"(芝|ダ|障)(\d{3,4})", d5)
    tm = re.search(r"(\d:\d\d\.\d)", d5)
    cond = (re.search(r"(良|稍|重|不)\s*$", d5) or [None, ""])[1]
    d3 = _text(td.select_one(".Data03"))
    m3 = re.match(r"(\d+)頭\s*(\d+)番\s*(\d+)人\s*(\S+)\s*([\d.]+)", d3)
    d6 = _text(td.select_one(".Data06"))
    passing = (re.search(r"([\d\-]+)\s*\(", d6) or [None, ""])[1]
    agari = _to_float((re.search(r"\(([\d.]+)\)", d6) or [None, ""])[1])
    bw = (re.search(r"(\d{3}\([+\-]?\d+\))", d6) or [None, ""])[1]
    d7 = _text(td.select_one(".Data07"))
    winner = _text(td.select_one(".Data07 a"))
    margin = _to_float((re.search(r"\(([\-\d.]+)\)", d7) or [None, ""])[1])
    return dict(
        date=dv[0] if dv else "", venue=dv[1] if len(dv) > 1 else "", finish=finish,
        race_name=race_name, grade=grade,
        surface=cm.group(1) if cm else "", distance=int(cm.group(2)) if cm else 0,
        time=tm.group(1) if tm else "", condition=cond,
        heads=int(m3.group(1)) if m3 else None, umaban=int(m3.group(2)) if m3 else None,
        ninki=int(m3.group(3)) if m3 else None, jockey=m3.group(4) if m3 else "",
        weight=float(m3.group(5)) if m3 else None,
        passing=passing, agari=agari, body_weight=bw, winner=winner, margin=margin,
    )


def parse_past_page(html: str) -> list[dict]:
    """馬柱ページの全出走馬。各馬: waku, umaban, name, horse_id, sex_age, jockey, weight,
    trainer, sire, dam, damsire, style, interval, body_weight, rest_note, past[list]"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    tbl = soup.select_one("table.Shutuba_Past5_Table")
    if tbl is None:
        return out
    for tr in tbl.select("tr.HorseList"):
        tds = tr.select("td")
        if len(tds) < 6:
            continue
        umaban = _to_int(_text(tds[1]))
        if not umaban:
            continue
        info = tds[3]
        name_a = info.select_one(".Horse02 a")
        hid = ""
        if name_a and name_a.get("href"):
            m = re.search(r"/horse/(\d+)", name_a["href"])
            hid = m.group(1) if m else ""
        style = _text(info.select_one(".kyakusitu"))
        h06 = _text(info.select_one(".Horse06"))
        interval = h06.replace(style, "").strip()
        bw_m = re.search(r"(\d{3})kg\s*\(([+\-]?\d+)\)", _text(info))
        body_weight = f"{bw_m.group(1)}({bw_m.group(2)})" if bw_m else ""
        jk = tds[4]
        sex_age = _text(jk.select_one(".Barei"))
        sex_age = re.sub(r"(牡|牝|セ)(\d+).*", r"\1\2", sex_age)
        jockey = _text(jk.select_one("a"))
        wt = _to_float((re.search(r"(\d\d\.\d)", _text(jk)) or [None, ""])[1])
        rest = tr.select_one("td.Rest")
        rest_note = " / ".join(_text(d) for d in rest.select(".Data01")) if rest else ""
        past = []
        for td in tr.select("td.Past"):
            p = _parse_past_cell(td)
            if p:
                past.append(p)
        out.append(dict(
            waku=_to_int(_text(tds[0])) or 0, umaban=umaban,
            name=_text(name_a) if name_a else _text(info.select_one(".Horse02")), horse_id=hid,
            sex_age=sex_age, jockey=jockey, weight=wt or 0.0,
            trainer=_text(info.select_one(".Horse05")).replace("・", " "),
            sire=_text(info.select_one(".Horse01")), dam=_text(info.select_one(".Horse03")),
            damsire=_text(info.select_one(".Horse04")).strip("()（）"),
            style=style, interval=interval, body_weight=body_weight, rest_note=rest_note, past=past,
        ))
    return out


def fetch_past(race_id: str) -> dict[int, dict]:
    """馬番 -> 馬柱情報 (parse_past_page の要素)"""
    html = _get(f"{BASE}/race/shutuba_past.html?race_id={race_id}")
    return {r["umaban"]: r for r in parse_past_page(html)}


def attach_past(race: Race, past: dict[int, dict]) -> Race:
    for h in race.horses:
        p = past.get(h.umaban)
        if p:
            h.sire, h.dam, h.damsire = p["sire"], p["dam"], p["damsire"]
            h.style, h.interval = p["style"], p["interval"]
            h.rest_note = (h.rest_note + " " + p["rest_note"]).strip()
            h.past = p["past"]
            if not h.body_weight and p.get("body_weight"):
                h.body_weight = p["body_weight"]
    return race


# ---------------------------------------------------------------- オッズ

ODDS_TYPES = {"win": 1, "place": 2, "umaren": 4, "wide": 5, "umatan": 6, "sanrenpuku": 7, "sanrentan": 8}


def fetch_odds(race_id: str, kind: str) -> dict[str, float]:
    """kind: win/place/umaren/wide/umatan/sanrenpuku/sanrentan。
    戻りは 組番("01", "0102", "010203") -> オッズ。place と wide は下限値。
    未発売なら空 dict。"""
    t = ODDS_TYPES[kind]
    url = f"{BASE}/api/api_get_jra_odds.html?race_id={race_id}&type={t}&action=init"
    try:
        res = _session.get(url, timeout=12, headers={**HEADERS, "Referer": f"{BASE}/odds/index.html?race_id={race_id}"})
        data = res.json()
    except Exception:
        return {}
    # 発売中は "middle"、確定後は "result"。未発売や無効IDは "NG"
    if data.get("status") not in ("result", "middle"):
        return {}
    odds = (data.get("data") or {}).get("odds") or {}
    table = odds.get(str(t)) or {}
    out: dict[str, float] = {}
    for key, vals in table.items():
        v = _to_float(vals[0]) if vals else None
        if v and v > 0:
            out[key] = v
    return out


def fetch_all_odds(race_id: str) -> dict[str, dict[str, float]]:
    return {k: fetch_odds(race_id, k) for k in ("win", "place", "umaren", "wide", "sanrenpuku")}


# ---------------------------------------------------------------- 結果

@dataclass
class Result:
    race_id: str
    order: list[dict]                       # [{finish, umaban, name, ninki, odds, time, margin}]
    payouts: dict[str, list[tuple[str, int]]]  # 券種 -> [(組番 "1-5", 払戻円)]


def fetch_result(race_id: str) -> Optional[Result]:
    """確定していなければ None。"""
    html = _get(f"{BASE}/race/result.html?race_id={race_id}")
    soup = BeautifulSoup(html, "html.parser")
    tbl = soup.select_one("table.RaceTable01")
    if tbl is None:
        return None
    order = []
    for tr in tbl.select("tr.HorseList"):
        tds = [_text(td) for td in tr.select("td")]
        if len(tds) < 11:
            continue
        order.append(dict(finish=_to_int(tds[0]), umaban=_to_int(tds[2]), name=tds[3],
                          time=tds[7], margin=tds[8], ninki=_to_int(tds[9]), odds=_to_float(tds[10])))
    if not order or order[0]["finish"] != 1:
        return None
    payouts: dict[str, list[tuple[str, int]]] = {}
    label = {"Tansho": "単勝", "Fukusho": "複勝", "Wakuren": "枠連", "Umaren": "馬連",
             "Wide": "ワイド", "Umatan": "馬単", "Fuku3": "三連複", "Tan3": "三連単"}
    for tr in soup.select("table.Payout_Detail_Table tr"):
        cls = next((c for c in tr.get("class", []) if c in label), None)
        if not cls:
            continue
        res_td = tr.select_one("td.Result")
        pay_td = tr.select_one("td.Payout")
        if res_td is None or pay_td is None:
            continue
        pays = [_to_int(p) for p in re.findall(r"[\d,]+円", _text(pay_td).replace(",", ""))]
        if cls in ("Fukusho", "Wide") or res_td.select("ul"):
            nums: list[list[str]] = []
            if res_td.select("ul"):
                for ul in res_td.select("ul"):
                    nums.append([_text(li) for li in ul.select("li") if _text(li)])
            else:
                # 複勝: div ごとに 1 馬番 (空 div が区切り)
                cur = [_text(d) for d in res_td.select("div") if _text(d)]
                nums = [[n] for n in cur]
        else:
            nums = [[_text(d) for d in res_td.select("div") if _text(d)]]
        combos = []
        for n in nums:
            if n:
                combos.append("-".join(n))
        payouts[label[cls]] = list(zip(combos, [p for p in pays if p is not None]))
    return Result(race_id=race_id, order=order, payouts=payouts)


def race_to_dict(race: Race) -> dict:
    return asdict(race)
