"""公式サイトHTMLの解析テスト。実ページと同じ構造の小さなHTMLを組み立てて確認する（通信はしない）。"""
import pytest

from kyotei import sources


def racelist_html(n_boats=6):
    def tbody(n):
        series_r = "".join(f'<td class="is-boatColor{(n + i) % 6 + 1}">{i + 1}</td>' for i in range(2)) + "<td></td>" * 12
        blank = "<td></td>" * 12
        return f"""
<tbody class="is-fs12">
 <tr>
  <td class="is-boatColor{n} is-fs14" rowspan="4">{'１２３４５６'[n - 1]}</td>
  <td rowspan="4"><img src="x.jpg"></td>
  <td rowspan="4">
    <div class="is-fs11">44{n}0 / <span>A{1 if n < 3 else 2}</span></div>
    <div class="is-fs18 is-fBold"><a href="/p">山田　　太郎{n}</a></div>
    <div class="is-fs11">愛媛/愛媛<br>3{n}歳/5{n}.5kg</div>
  </td>
  <td class="is-lineH2" rowspan="4">F{n % 2}<br>L0<br>0.1{n}</td>
  <td class="is-lineH2" rowspan="4">6.{n}0<br>40.{n}0<br>55.{n}0</td>
  <td class="is-lineH2" rowspan="4">5.{n}0<br>30.{n}0<br>45.{n}0</td>
  <td class="is-lineH2" rowspan="4">{10 + n}<br>35.{n}0<br>50.{n}0</td>
  <td class="is-lineH2" rowspan="4">{20 + n}<br>33.{n}0<br>48.{n}0</td>
  <td rowspan="4"></td>
  {series_r}
  <td rowspan="4">7R</td>
 </tr>
 <tr><td>{n}</td><td>3</td>{blank}</tr>
 <tr><td>.1{n}</td><td>F.01</td>{blank}</tr>
 <tr><td>２</td><td>Ｆ</td>{blank}</tr>
</tbody>"""
    times = "".join(f"<td>{10 + i}:{i:02d}</td>" for i in range(12))
    return f"""<html><body>
<div class="heading2_title is-G1b"><h2 class="heading2_titleName">第１回テスト杯</h2></div>
<h3 class="title16_titleDetail__add2020">予選　　　　1800m</h3>
<div class="table1 h-mt10"><table><thead><tr><th>レース</th><th>1R</th></tr></thead>
<tbody><tr><td>締切予定時刻</td>{times}</tr></tbody></table></div>
<div class="table1 is-tableFixed__3rdadd"><table>{''.join(tbody(n) for n in range(1, n_boats + 1))}</table></div>
</body></html>"""


def test_parse_racelist():
    race = sources.parse_racelist(racelist_html(), 15, 3, "20261126")
    assert race["title"] == "第1回テスト杯" and race["grade"] == "G1" and race["distance"] == 1800
    assert race["subtitle"] == "予選" and race["deadline"] == "12:02" and len(race["deadlines"]) == 12
    assert [b["boat"] for b in race["boats"]] == [1, 2, 3, 4, 5, 6]
    b = race["boats"][2]
    assert b["name"] == "山田 太郎3" and b["toban"] == 4430 and b["cls"] == "A2" and b["branch"] == "愛媛"
    assert b["age"] == 33 and b["weight"] == 53.5 and b["f"] == 1 and b["avg_st"] == 0.13
    assert (b["nat_win"], b["nat_2"], b["nat_3"]) == (6.3, 40.3, 55.3)
    assert (b["loc_win"], b["motor_no"], b["motor_2"], b["boat_no"], b["boat_2"]) == (5.3, 13, 35.3, 23, 33.3)
    assert b["series"][0] == {"rno": 1, "lane": 4, "course": 3, "st": 0.13, "place": 2, "place_text": "2"}
    # フライングなど数字でない着順は place=None で文字だけ残す
    assert b["series"][1]["place"] is None and b["series"][1]["place_text"] == "F" and b["series"][1]["st"] == -0.01


def test_parse_racelist_rejects_incomplete_page():
    with pytest.raises(sources.SourceError):
        sources.parse_racelist("<html><body>データがありません</body></html>", 1, 1, "20261126")
    with pytest.raises(sources.SourceError):
        sources.parse_racelist(racelist_html(5), 1, 1, "20261126")


def beforeinfo_html(with_times=True):
    def tbody(n):
        t = f"6.{70 + n}" if with_times else "&nbsp;"
        return f"""<tbody><tr>
<td class="is-boatColor{n} is-fs14" rowspan="4">{n}</td><td rowspan="4"></td><td rowspan="4">選手{n}</td>
<td rowspan="2">5{n}.0kg</td><td rowspan="4">{t}</td><td rowspan="4">-0.5</td><td rowspan="4"></td>
<td rowspan="4">{'リング×２' if n == 2 else ''}</td><td>R</td><td></td></tr>
<tr><td>進入</td><td></td></tr><tr><td rowspan="2">{'1.5' if n == 5 else '0.0'}</td><td>ST</td><td></td></tr>
<tr><td>着順</td><td></td></tr></tbody>"""
    order = [1, 2, 4, 3, 5, 6]
    sts = ["F.01", ".05", ".12", ".08", "L", ".20"]
    slit = "".join(f"""<tr><td><div class="table1_boatImage1">
<span class="table1_boatImage1Number is-type{b}">{b}</span>
<span class="table1_boatImage1Time">{s}</span></div></td></tr>""" for b, s in zip(order, sts))
    return f"""<html><body>
<div class="table1 h-mt10"><table><tbody><tr><td>締切予定時刻</td></tr></tbody></table></div>
<div class="table1"><table>{''.join(tbody(n) for n in range(1, 7))}</table></div>
<div class="table1"><table><tbody class="is-p10-0">{slit}</tbody></table></div>
<div class="weather1"><p class="weather1_title">水面気象情報　15:51現在</p><div class="weather1_body">
<div class="weather1_bodyUnit is-direction"><p class="weather1_bodyUnitImage is-direction9"></p>
 <div class="weather1_bodyUnitLabel"><span class="weather1_bodyUnitLabelTitle">気温</span><span class="weather1_bodyUnitLabelData">14.0℃</span></div></div>
<div class="weather1_bodyUnit is-weather"><p class="weather1_bodyUnitImage is-weather1"></p>
 <div class="weather1_bodyUnitLabel"><span class="weather1_bodyUnitLabelTitle">晴</span></div></div>
<div class="weather1_bodyUnit is-wind"><div class="weather1_bodyUnitLabel">
 <span class="weather1_bodyUnitLabelTitle">風速</span><span class="weather1_bodyUnitLabelData">4m</span></div></div>
<div class="weather1_bodyUnit is-windDirection"><p class="weather1_bodyUnitImage is-wind13"></p></div>
<div class="weather1_bodyUnit is-waterTemperature"><div class="weather1_bodyUnitLabel">
 <span class="weather1_bodyUnitLabelTitle">水温</span><span class="weather1_bodyUnitLabelData">16.0℃</span></div></div>
<div class="weather1_bodyUnit is-wave"><div class="weather1_bodyUnitLabel">
 <span class="weather1_bodyUnitLabelTitle">波高</span><span class="weather1_bodyUnitLabelData">3cm</span></div></div>
</div></div></body></html>"""


def test_parse_beforeinfo():
    pv = sources.parse_beforeinfo(beforeinfo_html())
    assert (pv["wind"], pv["wind_dir"], pv["wave"], pv["weather"], pv["temp"], pv["water_temp"]) == (4.0, 13, 3.0, "晴", 14.0, 16.0)
    assert pv["as_of"] == "15:51"
    b = pv["boats"]
    assert b[1]["ex_time"] == 6.71 and b[1]["tilt"] == -0.5 and b[1]["weight"] == 51.0
    assert b[2]["parts"] == "リング×2" and b[5]["weight_adj"] == 1.5
    # スタート展示: 上から内コース順。4号艇が3コース、3号艇が4コース
    assert [b[n]["course"] for n in range(1, 7)] == [1, 2, 4, 3, 5, 6]
    assert b[1]["st"] == -0.01 and b[4]["st"] == 0.12 and b[5]["st"] is None


def test_parse_beforeinfo_before_exhibition_returns_none():
    assert sources.parse_beforeinfo(beforeinfo_html(with_times=False)) is None
    assert sources.parse_beforeinfo("<html></html>") is None


def test_parse_odds3t():
    # 公式と同じ並び: 列=1着(1〜6)、行は 2着(4行ごとに rowspan=4) x 3着
    rows = ""
    expect = {}
    for r in range(20):
        cells = ""
        for first in range(1, 7):
            others = [x for x in range(1, 7) if x != first]
            second = others[r // 4]
            third = [x for x in others if x != second][r % 4]
            odds = first * 100 + second * 10 + third + 0.5
            expect[f"{first}-{second}-{third}"] = odds
            if r % 4 == 0:
                cells += f'<td class="is-fs14 is-boatColor{second}" rowspan="4">{second}</td>'
            cells += f'<td class="is-boatColor{third}">{third}</td><td class="oddsPoint">{odds}</td>'
        rows += f"<tr>{cells}</tr>"
    html = f'<div class="table1"><table><tbody class="is-p3-0">{rows}</tbody></table></div>'
    odds = sources.parse_odds3t(html)
    assert len(odds) == 120 and odds == expect
    assert sources.parse_odds3t("<html></html>") == {}


def test_parse_resultlist():
    html = """
<div class="table1"><table><thead><tr><th>レース</th><th>3連勝単式</th><th>2連勝単式</th></tr></thead>
<tbody><tr><td>1R</td><td>3 - 4 - 1</td><td>¥2,450</td><td>3 - 4</td><td>¥1,050</td><td></td></tr></tbody>
<tbody><tr><td>2R</td><td>&nbsp;</td><td>&nbsp;</td><td></td><td></td><td>レース中止</td></tr></tbody></table></div>
<div class="table1"><table><thead><tr><th>レース</th><th>着順</th><th>決まり手</th></tr></thead>
<tbody><tr><td rowspan="2"><a>1R</a></td><td rowspan="2">一般</td><td>１着</td><td rowspan="2">まくり差し</td><td rowspan="2"></td></tr>
<tr><td><span>3</span><span>選手</span></td></tr></tbody></table></div>"""
    assert sources.parse_resultlist(html) == [{"rno": 1, "top3": (3, 4, 1), "payout": 2450, "technique": "まくり差し"}]


def test_parse_stadium_and_index():
    rows = "".join(f"<tr><td>{c}</td>" + "".join(f"<td>{10 + c}.{i}</td>" for i in range(12)) + "</tr>" for c in range(1, 7))
    st = sources.parse_stadium(f'<div class="table1"><table><thead><tr><th>コース</th><th>コース別決まり手</th></tr></thead><tbody>{rows}</tbody></table></div>')
    assert st["courses"][1]["place"][0] == 11.0 and st["courses"][6]["kimarite"]["差し"] == 16.8
    assert sources.parse_stadium("<html></html>") is None

    index = """<div class="table1"><table>
<tbody><tr><td class="is-arrow1"><a href="javascript:x"><img alt="丸亀"></a></td><td class="is-fBold">5R</td><td>締切予定時刻 16:32</td>
<td class="is-G3b"></td><td class="is-nighter"></td><td><a href="/owpc/pc/race/raceindex?jcd=15&hd=20261126">テスト杯</a></td>
<td>11/24-11/29 ３日目</td><td><a href="/owpc/pc/race/racelist?rno=5&jcd=15&hd=20261126">出走表</a></td></tr></tbody>
<tbody><tr><td><img alt="大村"></td><td>中止</td><td class="is-ippan"></td><td></td>
<td><a href="/owpc/pc/race/raceindex?jcd=24&hd=20261126">一般戦</a></td><td>11/26-11/30 初日</td></tr></tbody></table></div>"""
    v = sources.parse_index(index)
    assert [(x["jcd"], x["name"], x["grade"], x["day_text"], x["time_zone"], x["cancelled"]) for x in v] == [
        (15, "丸亀", "G3", "3日目", "ナイター", False), (24, "大村", "一般", "初日", "", True)]
    assert v[0]["title"] == "テスト杯" and v[0]["period"] == "11/24-11/29"
    # 開催中の場は次のレースと締切時刻が読める。中止の場は読めない
    assert (v[0]["next_rno"], v[0]["next_deadline"]) == (5, "16:32")
    assert (v[1]["next_rno"], v[1]["next_deadline"]) == (None, "")


def test_parse_original_exhibition():
    txt = "data=\n1\t3\n一\u3000周\tまわり足\t直\u3000線\n" + "".join(f"{n}\t選手{n}\t36.{n}0\t5.{n}0\t6.{n}0\n" for n in range(1, 7))
    o = sources.parse_original_exhibition(txt)
    assert o["columns"] == ["一周", "まわり足", "直線"] and o["boats"][3] == {"一周": 36.3, "まわり足": 5.3, "直線": 6.3}
    assert sources.parse_original_exhibition("data=\n") is None
    assert sources.parse_original_exhibition("<html>404</html>") is None


def test_urls():
    assert sources.official_url("racelist", 5, 1, "20261125").endswith("/race/racelist?rno=1&jcd=05&hd=20261125")
    assert sources.biyori_url(5, 1, "20261125") == "https://kyoteibiyori.com/race_ichiran.php?place_no=5&race_no=1&hiduke=20261125"
