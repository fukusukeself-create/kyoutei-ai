"""データ取得。

取得元:
  1. ボートレース公式サイト (www.boatrace.jp)
       開催一覧 / 出走表(今節成績つき) / 直前情報(展示・気象) / 3連単オッズ /
       当日の結果一覧(決まり手) / 場のコース別成績
  2. BOATCAST (race.boatcast.jp)  オリジナル展示データ（一周・まわり足・直線）
  3. Boatrace Open API (boatraceopenapi.github.io)
       公式サイトに繋がらない時の予備。出走表・直前情報・結果をJSONで取得

ボートレース日和(kyoteibiyori.com)は利用規約で自動取得を禁止しているため、
このアプリからはアクセスしない（レースページへのリンクを出すだけ）。

HTMLの解析(parse_*)は通信と分けてあるので、単体でテストできる。
"""
from __future__ import annotations

import os
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from . import openapi

OFFICIAL = "https://www.boatrace.jp/owpc/pc"
BOATCAST = "https://race.boatcast.jp"

VENUES = {
    1: "桐生", 2: "戸田", 3: "江戸川", 4: "平和島", 5: "多摩川", 6: "浜名湖",
    7: "蒲郡", 8: "常滑", 9: "津", 10: "三国", 11: "びわこ", 12: "住之江",
    13: "尼崎", 14: "鳴門", 15: "丸亀", 16: "児島", 17: "宮島", 18: "徳山",
    19: "下関", 20: "若松", 21: "芦屋", 22: "福岡", 23: "唐津", 24: "大村",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,ja-JP;q=0.9",
}


class SourceError(Exception):
    """データ元から取得できなかった時のエラー（画面に理由を出すために使う）。"""


_session = requests.Session()

# 公式サイトが遅い・繋がらない時は、しばらく公式へのアクセスをやめて予備データに任せる。
# （遅い相手に何度もアクセスして待たされるのを防ぎ、相手のサーバーにも負荷をかけない）
OFFICIAL_TIMEOUT = float(os.environ.get("KYOTEI_OFFICIAL_TIMEOUT", "7"))  # 秒。これ以上待たされたら予備データへ
OFFICIAL_PAUSE_SEC = 180.0
_official_paused_until = 0.0


def official_paused() -> bool:
    return time.time() < _official_paused_until


def http_get(url: str, timeout: float = 10.0, retries: int = 1) -> requests.Response:
    global _official_paused_until
    is_official = url.startswith("https://www.boatrace.jp/")
    if is_official:
        if official_paused():
            raise SourceError("公式サイトの応答が遅いため、一時的に予備データを使っています。")
        timeout, retries = OFFICIAL_TIMEOUT, 0
    last = None
    for attempt in range(retries + 1):
        try:
            res = _session.get(url, headers=HEADERS, timeout=timeout)
            if res.status_code == 404:
                raise SourceError(f"ページがありません (404): {url}")
            if res.status_code >= 400:
                last = SourceError(f"HTTP {res.status_code}: {url}")
            else:
                return res
        except requests.RequestException as e:
            last = SourceError(f"接続できませんでした: {url} ({type(e).__name__})")
        if attempt < retries:
            time.sleep(0.8 * (attempt + 1))
    if is_official:
        _official_paused_until = time.time() + OFFICIAL_PAUSE_SEC
    raise last


def _get_html(url: str) -> str:
    res = http_get(url)
    res.encoding = "utf-8"
    return res.text


def _get_json(url: str):
    res = http_get(url, timeout=15.0)
    try:
        return res.json()
    except ValueError as e:
        raise SourceError(f"JSONを読めませんでした: {url}") from e


# ---------------------------------------------------------------------------
# 小道具
# ---------------------------------------------------------------------------
def _text(el) -> str:
    if el is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", el.get_text(" ")).split())


def _clean_name(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s or "").split())


def _float(s, default=None):
    if s is None:
        return default
    m = re.search(r"-?\d+(?:\.\d+)?|-?\.\d+", str(s))
    if not m:
        return default
    try:
        return float(m.group(0))
    except ValueError:
        return default


def _class_number(el, prefix: str):
    """class="is-boatColor3" のような指定から末尾の数字を取り出す。"""
    if el is None:
        return None
    for c in el.get("class") or []:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", c)
        if m:
            return int(m.group(1))
    return None


def _parse_st(s: str):
    """'.08' -> 0.08, 'F.01' -> -0.01, 'L' や空 -> None"""
    s = unicodedata.normalize("NFKC", s or "").strip().upper()
    if not s or s.startswith("L"):
        return None
    v = _float(s)
    if v is None:
        return None
    return -abs(v) if s.startswith("F") else v


GRADE_CLASSES = [("is-SG", "SG"), ("is-G1", "G1"), ("is-G2", "G2"), ("is-G3", "G3"), ("is-ippan", "一般")]


def _grade_from_classes(classes) -> str:
    for c in classes or []:
        for prefix, label in GRADE_CLASSES:
            if c.startswith(prefix):
                return label
    return ""


def official_url(page: str, jcd: int, rno: int | None, hd: str) -> str:
    q = (f"rno={rno}&" if rno else "") + f"jcd={int(jcd):02d}&hd={hd}"
    return f"{OFFICIAL}/race/{page}?{q}"


def biyori_url(jcd: int, rno: int, hd: str) -> str:
    """ボートレース日和のレースページ（利用者が自分で開いて見るためのリンク）。"""
    return f"https://kyoteibiyori.com/race_ichiran.php?place_no={int(jcd)}&race_no={int(rno)}&hiduke={hd}"


# ---------------------------------------------------------------------------
# 開催一覧
# ---------------------------------------------------------------------------
def parse_index(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    table = soup.select_one("div.table1")
    if table is None:
        return out
    for tbody in table.select("tbody"):
        link = tbody.find("a", href=re.compile(r"jcd=\d{2}"))
        if link is None:
            continue
        jcd = int(re.search(r"jcd=(\d{2})", link["href"]).group(1))
        tr = tbody.find("tr")
        tds = tr.find_all("td") if tr else []
        grade = ""
        for td in tds:
            grade = grade or _grade_from_classes(td.get("class"))
        row_text = _text(tr)
        title_link = tbody.find("a", href=re.compile(r"raceindex\?"))
        day = re.search(r"(\d{1,2}/\d{1,2})\s*-\s*(\d{1,2}/\d{1,2})\s*(初日|\d+日目|最終日)?", row_text)
        status = _text(tds[1]) if len(tds) > 1 else ""
        # 開催中は、タイトルより前のセルに「5R」「締切予定 12:26」のように次のレースが出る
        head_text = ""
        for td in tds:
            if title_link is not None and td.find("a", href=re.compile(r"raceindex\?")):
                break
            head_text += " " + _text(td)
        m_rno = re.search(r"(?<!\d)(\d{1,2})\s*R", head_text)
        m_time = re.search(r"(?<!\d)(\d{1,2}:\d{2})(?!\d)", head_text)
        classes = {c for td in tds for c in (td.get("class") or [])}
        if "is-nighter" in classes:
            time_zone = "ナイター"
        elif "is-morning" in classes:
            time_zone = "モーニング"
        elif "is-summer" in classes or "is-midnight" in classes:
            time_zone = "ミッドナイト" if "is-midnight" in classes else "サマータイム"
        else:
            time_zone = ""
        out.append({
            "jcd": jcd,
            "name": VENUES.get(jcd, str(jcd)),
            "grade": grade,
            "title": _text(title_link),
            "day_text": (day.group(3) or "") if day else "",
            "period": f"{day.group(1)}-{day.group(2)}" if day else "",
            "status_text": status,
            "next_rno": int(m_rno.group(1)) if m_rno and m_time and 1 <= int(m_rno.group(1)) <= 12 else None,
            "next_deadline": m_time.group(1) if m_rno and m_time else "",
            "time_zone": time_zone,
            "cancelled": "中止" in status,
        })
    return out


def fetch_venues(hd: str) -> list[dict]:
    return parse_index(_get_html(f"{OFFICIAL}/race/index?hd={hd}"))


# ---------------------------------------------------------------------------
# 出走表
# ---------------------------------------------------------------------------
def _parse_deadline_table(soup) -> dict[int, str]:
    for table in soup.select("div.table1"):
        if "締切予定時刻" not in table.get_text():
            continue
        times = re.findall(r"\d{1,2}:\d{2}", _text(table))
        if len(times) >= 1:
            return {i + 1: t for i, t in enumerate(times[:12])}
    return {}


def parse_racelist(html: str, jcd: int, rno: int, hd: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    main = None
    for table in soup.select("div.table1"):
        if len(table.select("tbody")) >= 6 and table.select_one("td[class*=is-boatColor]"):
            main = table
            break
    if main is None:
        raise SourceError("出走表がまだ公開されていないか、レースがありません。")

    boats = []
    for tbody in main.select("tbody")[:6]:
        trs = tbody.find_all("tr")
        if not trs:
            continue
        tds = trs[0].find_all("td")
        if len(tds) < 8:
            continue
        boat_no = _class_number(tds[0], "is-boatColor") or int(_float(_text(tds[0]), 0))
        prof = tds[2]
        ptxt = _text(prof)
        m_id = re.search(r"(\d{4})\s*/\s*([AB][12])", ptxt)
        name_el = prof.select_one(".is-fs18") or prof.find("a")
        m_age = re.search(r"(\d+)\s*歳\s*/\s*([\d.]+)\s*kg", ptxt)
        m_branch = re.search(r"([^\s/\d]+)\s*/\s*([^\s/\d]+)\s+\d+\s*歳", ptxt)
        fl = _text(tds[3])
        m_f = re.search(r"F\s*(\d+)", fl)
        m_l = re.search(r"L\s*(\d+)", fl)
        m_st = re.search(r"(\d?\.\d+)\s*$", fl)
        nat = re.findall(r"\d+\.\d+", _text(tds[4]))
        loc = re.findall(r"\d+\.\d+", _text(tds[5]))
        mot = _text(tds[6]).split()
        bot = _text(tds[7]).split()

        # 今節成績: 1行目=レース番号(色が枠番) 2行目=進入 3行目=ST 4行目=着順
        series = []
        r_cells = tds[9:23]
        rows = [tr.find_all("td") for tr in trs[1:4]]
        if len(rows) == 3:
            for i, cell in enumerate(r_cells):
                place_txt = _text(rows[2][i]) if i < len(rows[2]) else ""
                rno_txt = _text(cell)
                if not place_txt and not rno_txt:
                    continue
                place = _float(place_txt) if re.fullmatch(r"\d", place_txt or "") else None
                series.append({
                    "rno": int(_float(rno_txt)) if _float(rno_txt) is not None else None,
                    "lane": _class_number(cell, "is-boatColor"),
                    "course": int(_float(_text(rows[0][i]))) if i < len(rows[0]) and _float(_text(rows[0][i])) else None,
                    "st": _parse_st(_text(rows[1][i])) if i < len(rows[1]) else None,
                    "place": int(place) if place else None,
                    "place_text": place_txt,
                })

        boats.append({
            "boat": boat_no,
            "name": _clean_name(name_el.get_text() if name_el else ""),
            "toban": int(m_id.group(1)) if m_id else None,
            "cls": m_id.group(2) if m_id else "",
            "branch": m_branch.group(1) if m_branch else "",
            "age": int(m_age.group(1)) if m_age else None,
            "weight": float(m_age.group(2)) if m_age else None,
            "f": int(m_f.group(1)) if m_f else 0,
            "l": int(m_l.group(1)) if m_l else 0,
            "avg_st": float(m_st.group(1)) if m_st else None,
            "nat_win": float(nat[0]) if len(nat) > 0 else None,
            "nat_2": float(nat[1]) if len(nat) > 1 else None,
            "nat_3": float(nat[2]) if len(nat) > 2 else None,
            "loc_win": float(loc[0]) if len(loc) > 0 else None,
            "loc_2": float(loc[1]) if len(loc) > 1 else None,
            "loc_3": float(loc[2]) if len(loc) > 2 else None,
            "motor_no": int(_float(mot[0])) if len(mot) > 0 and _float(mot[0]) is not None else None,
            "motor_2": _float(mot[1]) if len(mot) > 1 else None,
            "motor_3": _float(mot[2]) if len(mot) > 2 else None,
            "boat_no": int(_float(bot[0])) if len(bot) > 0 and _float(bot[0]) is not None else None,
            "boat_2": _float(bot[1]) if len(bot) > 1 else None,
            "boat_3": _float(bot[2]) if len(bot) > 2 else None,
            "series": series,
        })
    boats.sort(key=lambda b: b["boat"])
    if len(boats) != 6:
        raise SourceError("出走表を読み取れませんでした（6艇そろっていません）。")

    title_el = soup.select_one(".heading2_titleName")
    grade_el = soup.select_one(".heading2_title")
    detail = _text(soup.select_one(".title16_titleDetail__add2020"))
    m_dist = re.search(r"(\d{3,4})\s*m", detail)
    deadlines = _parse_deadline_table(soup)
    return {
        "date": hd,
        "jcd": int(jcd),
        "rno": int(rno),
        "title": _text(title_el),
        "subtitle": re.sub(r"\s*\d{3,4}\s*m.*$", "", detail).strip(),
        "grade": _grade_from_classes(grade_el.get("class") if grade_el else []),
        "distance": int(m_dist.group(1)) if m_dist else None,
        "deadline": deadlines.get(int(rno), ""),
        "deadlines": deadlines,
        "fixed_course": "進入固定" in detail,
        "boats": boats,
        "preview": None,
        "source": "ボートレース公式サイト",
    }


def fetch_racelist(jcd: int, rno: int, hd: str) -> dict:
    return parse_racelist(_get_html(official_url("racelist", jcd, rno, hd)), jcd, rno, hd)


# ---------------------------------------------------------------------------
# 直前情報
# ---------------------------------------------------------------------------
def parse_beforeinfo(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    main = None
    for table in soup.select("div.table1"):
        if len(table.select("tbody")) >= 6 and table.select_one("td[class*=is-boatColor]"):
            main = table
            break
    if main is None:
        return None
    boats = {}
    for tbody in main.select("tbody")[:6]:
        trs = tbody.find_all("tr")
        tds = trs[0].find_all("td") if trs else []
        if len(tds) < 8:
            continue
        boat_no = _class_number(tds[0], "is-boatColor") or int(_float(_text(tds[0]), 0))
        adj = None
        if len(trs) >= 3:
            a = trs[2].find_all("td")
            if a:
                adj = _float(_text(a[0]))
        boats[boat_no] = {
            "course": None,
            "st": None,
            "weight": _float(_text(tds[3])),
            "ex_time": _float(_text(tds[4])),
            "tilt": _float(_text(tds[5])),
            "propeller": _text(tds[6]),
            "parts": _text(tds[7]),
            "weight_adj": adj,
        }
    # スタート展示（上から 1コース, 2コース, ...）
    for course, el in enumerate(soup.select(".table1_boatImage1"), start=1):
        num = el.select_one(".table1_boatImage1Number")
        b = int(_float(_text(num), 0)) if num else 0
        if b in boats:
            boats[b]["course"] = course
            boats[b]["st"] = _parse_st(_text(el.select_one(".table1_boatImage1Time")))
    if not any(v["ex_time"] for v in boats.values()):
        return None  # まだ展示前

    w = soup.select_one("div.weather1")
    weather = {"wind": None, "wind_dir": None, "wave": None, "weather": "", "temp": None, "water_temp": None, "as_of": ""}
    if w is not None:
        m = re.search(r"(\d{1,2}:\d{2})\s*現在", _text(w.select_one(".weather1_title")))
        weather["as_of"] = m.group(1) if m else ""
        for unit in w.select(".weather1_bodyUnit"):
            cls = unit.get("class") or []
            title = _text(unit.select_one(".weather1_bodyUnitLabelTitle"))
            data = _text(unit.select_one(".weather1_bodyUnitLabelData"))
            if "is-weather" in cls:
                weather["weather"] = title
            elif "is-windDirection" in cls:
                weather["wind_dir"] = _class_number(unit.find("p"), "is-wind")
            elif title == "気温":
                weather["temp"] = _float(data)
            elif title == "風速":
                weather["wind"] = _float(data)
            elif title == "水温":
                weather["water_temp"] = _float(data)
            elif title == "波高":
                weather["wave"] = _float(data)
    weather["boats"] = boats
    weather["source"] = "ボートレース公式サイト"
    return weather


def fetch_beforeinfo(jcd: int, rno: int, hd: str) -> dict | None:
    return parse_beforeinfo(_get_html(official_url("beforeinfo", jcd, rno, hd)))


# ---------------------------------------------------------------------------
# オリジナル展示 (BOATCAST)
# ---------------------------------------------------------------------------
def parse_original_exhibition(text: str) -> dict | None:
    """タブ区切りテキスト。3行目が見出し（一周 / まわり足 / 直線 など。場によって項目が違う）。"""
    lines = [ln for ln in text.replace("\r", "").split("\n") if ln.strip()]
    if lines and lines[0].startswith("data="):
        lines[0] = lines[0][5:]
        if not lines[0].strip():
            lines = lines[1:]
    header = None
    boats = {}
    for ln in lines:
        cells = [c.strip() for c in ln.split("\t")]
        if header is None:
            if any(re.search(r"[^\d.\s]", c) for c in cells) and not re.fullmatch(r"[1-6]", cells[0]):
                header = [re.sub(r"\s+", "", _clean_name(c)) for c in cells if c]
            continue
        if re.fullmatch(r"[1-6]", cells[0]) and len(cells) >= 3:
            vals = [_float(c) for c in cells[2:]]
            boats[int(cells[0])] = {h: v for h, v in zip(header, vals) if v}
    boats = {b: v for b, v in boats.items() if v}
    if not header or len(boats) < 4:
        return None
    return {"columns": header, "boats": boats, "source": "BOATCAST オリジナル展示"}


def fetch_original_exhibition(jcd: int, rno: int, hd: str) -> dict | None:
    url = f"{BOATCAST}/txt/{int(jcd):02d}/bc_oriten_{hd}_{int(jcd):02d}_{int(rno):02d}.txt"
    try:
        res = http_get(url, timeout=6.0, retries=0)
    except SourceError:
        return None
    res.encoding = "utf-8"
    return parse_original_exhibition(res.text)


# ---------------------------------------------------------------------------
# 3連単オッズ
# ---------------------------------------------------------------------------
def parse_odds3t(html: str) -> dict[str, float]:
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.select("div.table1"):
        if len(t.select("td.oddsPoint")) >= 100:
            table = t
            break
    if table is None:
        return {}
    odds = {}
    second = [None] * 6
    for tr in table.select("tbody tr"):
        col = 0
        third = None
        for td in tr.find_all("td"):
            cls = td.get("class") or []
            if "oddsPoint" in cls:
                v = _float(_text(td))
                if col < 6 and second[col] and third and v is not None:
                    odds[f"{col + 1}-{second[col]}-{third}"] = v
                col += 1
                third = None
            elif td.get("rowspan"):
                if col < 6:
                    second[col] = int(_float(_text(td), 0)) or None
            else:
                third = int(_float(_text(td), 0)) or None
    return odds


def fetch_odds3t(jcd: int, rno: int, hd: str) -> dict[str, float]:
    return parse_odds3t(_get_html(official_url("odds3t", jcd, rno, hd)))


# ---------------------------------------------------------------------------
# 当日の結果一覧（その場の今日の傾向を見る）
# ---------------------------------------------------------------------------
KIMARITE = ["逃げ", "差し", "まくり差し", "まくり", "抜き", "恵まれ"]


def parse_resultlist(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.select("div.table1")
    results = {}
    for table in tables:
        head = _text(table.select_one("thead"))
        if "3連勝単式" in head:
            for tbody in table.select("tbody"):
                tds = tbody.find_all("td")
                if len(tds) < 3:
                    continue
                m = re.fullmatch(r"(\d{1,2})R", _text(tds[0]))
                combo = re.fullmatch(r"([1-6])\s*-\s*([1-6])\s*-\s*([1-6])", _text(tds[1]))
                if not m or not combo:
                    continue
                pay = re.sub(r"[^\d]", "", _text(tds[2]))
                results.setdefault(int(m.group(1)), {}).update(
                    top3=tuple(int(x) for x in combo.groups()),
                    payout=int(pay) if pay else None,
                )
        elif "決まり手" in head:
            for tbody in table.select("tbody"):
                tds = tbody.find_all("td")
                if not tds:
                    continue
                m = re.fullmatch(r"(\d{1,2})R", _text(tds[0]))
                if not m:
                    continue
                kim = ""
                for td in tds:
                    if td.get("rowspan") and _text(td) in KIMARITE:
                        kim = _text(td)
                results.setdefault(int(m.group(1)), {})["technique"] = kim
    out = []
    for rno in sorted(results):
        r = results[rno]
        if r.get("top3"):
            out.append({"rno": rno, "top3": r["top3"], "payout": r.get("payout"), "technique": r.get("technique", "")})
    return out


def fetch_resultlist(jcd: int, hd: str) -> list[dict]:
    return parse_resultlist(_get_html(f"{OFFICIAL}/race/resultlist?jcd={int(jcd):02d}&hd={hd}"))


# ---------------------------------------------------------------------------
# 場のコース別成績（最近3か月）
# ---------------------------------------------------------------------------
def parse_stadium(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.select("div.table1"):
        if "コース別決まり手" not in _text(table.select_one("thead")):
            continue
        courses = {}
        for tr in table.select("tbody tr"):
            vals = [_float(_text(td)) for td in tr.find_all("td")]
            if len(vals) >= 13 and vals[0] and 1 <= vals[0] <= 6:
                courses[int(vals[0])] = {
                    "place": vals[1:7],  # 1着〜6着の率(%)
                    "kimarite": dict(zip(["逃げ", "まくり", "差し", "まくり差し", "抜き", "恵まれ"], vals[7:13])),
                }
        if len(courses) == 6:
            return {"courses": courses, "source": "ボートレース公式サイト（最近3か月）"}
    return None


def fetch_stadium(jcd: int) -> dict | None:
    return parse_stadium(_get_html(f"{OFFICIAL}/data/stadium?jcd={int(jcd):02d}"))


# ---------------------------------------------------------------------------
# Boatrace Open API（予備）
# ---------------------------------------------------------------------------
_openapi_cache: dict = {}
_OPENAPI_TTL = {"programs": 600.0, "previews": 60.0, "results": 60.0}


def fetch_openapi_day(kind: str, hd: str) -> list[dict]:
    """kind: programs / previews / results。その日のぶんが無ければ空リスト。

    1日ぶんが1ファイル(数百KB)なので、レースを切り替えるたびに取り直さないよう短時間だけ覚えておく。
    """
    hit = _openapi_cache.get((kind, hd))
    if hit and hit[0] > time.time():
        return hit[1]
    recs = _fetch_openapi_day(kind, hd)
    ttl = _OPENAPI_TTL.get(kind, 60.0) if recs else 120.0
    _openapi_cache[(kind, hd)] = (time.time() + ttl, recs)
    for key in [k for k, v in _openapi_cache.items() if v[0] <= time.time()]:
        del _openapi_cache[key]
    return recs


def _fetch_openapi_day(kind: str, hd: str) -> list[dict]:
    try:
        data = _get_json(openapi.day_url(kind, hd))
        return data.get(kind, [])
    except SourceError:
        pass
    try:
        data = _get_json(openapi.today_url(kind))
    except SourceError:
        return []
    recs = data.get(kind, [])
    return [r for r in recs if (r.get("race_date") or "").replace("-", "") == hd]


def find_openapi_race(recs: list[dict], jcd: int, rno: int):
    for r in recs:
        if int(r.get("race_stadium_number", 0)) == int(jcd) and int(r.get("race_number", 0)) == int(rno):
            return r
    return None
