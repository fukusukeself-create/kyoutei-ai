"""競艇AI予想アプリ（Streamlit）。

  開催一覧 / 締切順 からレースを選ぶ
    → 公式サイトなどから出走表・直前情報・オッズ・当日結果を集める
    → データ予想モデル（過去約6万レースから学習）が3連単の確率を計算
    → Gemini が展開を読んで最終的な買い目（フォーメーション）を決める
"""
import datetime
import html
import os

import streamlit as st

from kyotei import ai, formation, model, service, sources
from kyotei.sources import SourceError, VENUES

st.set_page_config(page_title="競艇AI予想", page_icon="🚤", layout="centered", initial_sidebar_state="collapsed")

# ----------------------------------------------------
# 見た目
# ----------------------------------------------------
st.markdown("""
<style>
.stApp { background:#f1f3f7; font-family:-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",Meiryo,sans-serif; }
.block-container { padding:3.6rem 0.6rem 3rem 0.6rem !important; max-width:760px !important; }
.top-bar { background:#0066cc; color:#fff; padding:10px 14px; border-radius:8px; margin-bottom:8px;
           display:flex; justify-content:space-between; align-items:center; }
.top-bar .t { font-size:1.15rem; font-weight:900; }
.top-bar .d { font-size:0.8rem; opacity:.9; }
.race-head { background:#0b3d91; color:#fff; padding:10px 14px; border-radius:8px; margin:6px 0 10px 0; }
.race-head .v { font-size:1.2rem; font-weight:900; }
.race-head .s { font-size:0.8rem; opacity:.9; margin-top:2px; }
.race-head .dl { float:right; text-align:right; font-size:0.8rem; }
.race-head .dl b { font-size:1.15rem; }
.badge { display:inline-block; font-size:0.68rem; font-weight:800; padding:1px 6px; border-radius:3px;
         background:#f59e0b; color:#111; margin-left:6px; vertical-align:middle; }
.sec { font-weight:900; font-size:1.0rem; color:#0f172a; margin:14px 0 6px 0; border-left:5px solid #0066cc; padding-left:8px; }
.card { background:#fff; border:1px solid #d5dbe5; border-radius:10px; padding:12px; margin-bottom:10px; color:#0f172a; }
.note { font-size:0.78rem; color:#475569; }

/* 艇番のチップ（公式の枠色） */
.bt { display:inline-flex; align-items:center; justify-content:center; width:1.9em; height:1.9em; border-radius:6px;
      font-weight:900; margin:1px 2px; border:1.5px solid #94a3b8; font-size:1em; line-height:1; }
.b1 { background:#fff; color:#111; } .b2 { background:#111; color:#fff; border-color:#111; }
.b3 { background:#e02424; color:#fff; border-color:#e02424; } .b4 { background:#1d4ed8; color:#fff; border-color:#1d4ed8; }
.b5 { background:#facc15; color:#111; border-color:#eab308; } .b6 { background:#16a34a; color:#fff; border-color:#16a34a; }
.bt.sm { width:1.5em; height:1.5em; font-size:0.8em; border-radius:4px; margin:0 1px; }

/* フォーメーション */
.fm { background:#fff; border:2px solid #0066cc; border-radius:12px; padding:12px; margin-bottom:12px; color:#0f172a; }
.fm.sub { border-color:#f59e0b; }
.fm-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
.fm-tag { background:#0066cc; color:#fff; font-weight:900; padding:2px 12px; border-radius:999px; font-size:0.95rem; }
.fm.sub .fm-tag { background:#f59e0b; color:#111; }
.fm-pts { font-weight:900; font-size:1.05rem; }
.fm-pts small { font-weight:600; color:#475569; font-size:0.75rem; }
.fm-grid { display:flex; align-items:stretch; gap:4px; }
.fm-col { flex:1; background:#f1f5f9; border-radius:8px; padding:6px 4px; text-align:center; }
.fm-col .p { font-size:0.75rem; font-weight:800; color:#334155; margin-bottom:4px; }
.fm-col .bs { font-size:1.15rem; }
.fm-arrow { display:flex; align-items:center; color:#64748b; font-weight:900; }
.fm-how { margin-top:8px; font-size:0.88rem; background:#fffbeb; border-radius:6px; padding:6px 8px; }
.fm-reason { margin-top:6px; font-size:0.85rem; color:#334155; }
.tk { display:inline-block; background:#f8fafc; border:1px solid #cbd5e1; border-radius:6px; padding:2px 6px;
      margin:2px 3px 2px 0; font-size:0.82rem; font-weight:700; white-space:nowrap; }
.tk small { font-weight:500; color:#64748b; }

/* 表 */
.tw { overflow-x:auto; }
table.t { border-collapse:collapse; width:100%; font-size:0.8rem; background:#fff; color:#0f172a; }
table.t th { background:#e2e8f0; padding:4px 5px; border:1px solid #cbd5e1; white-space:nowrap; font-size:0.72rem; }
table.t td { padding:4px 5px; border:1px solid #e2e8f0; text-align:center; white-space:nowrap; }
table.t td.l { text-align:left; }
.best { color:#dc2626; font-weight:900; }
.bar { background:#e2e8f0; border-radius:4px; height:8px; width:100%; min-width:60px; }
.bar > div { background:#0066cc; height:8px; border-radius:4px; }
.hit { background:#dcfce7; border:2px solid #16a34a; color:#14532d; border-radius:10px; padding:10px; font-weight:800; margin-bottom:10px; }
.miss { background:#fee2e2; border:2px solid #dc2626; color:#7f1d1d; border-radius:10px; padding:10px; font-weight:800; margin-bottom:10px; }
div[data-testid="stButton"] > button { width:100%; border-radius:8px; padding-left:4px; padding-right:4px; }
/* スマホでも横並びを崩さない（Streamlitは幅が狭いと縦に積んでしまう） */
div[data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:0.4rem !important; }
div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] { min-width:0 !important; flex:1 1 0 !important; width:auto !important; }
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------
# データ取得（キャッシュつき。同じレースを何度開いても取得元に負荷をかけない）
# ----------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def load_schedule(hd):
    return service.get_schedule(hd)


def get_api_key():
    try:
        key = st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        key = ""
    return key or os.environ.get("GEMINI_API_KEY", "")


# ----------------------------------------------------
# HTML部品
# ----------------------------------------------------
def show_html(s):
    """HTMLを表示する。空行や字下げがあるとMarkdownがHTMLを途中で打ち切るので、1行に詰めてから渡す。"""
    st.markdown(" ".join(line.strip() for line in str(s).splitlines() if line.strip()), unsafe_allow_html=True)


def esc(s):
    return html.escape(str(s if s is not None else ""))


def chip(b, small=False):
    return f'<span class="bt b{int(b)}{" sm" if small else ""}">{int(b)}</span>'


def chips(bs):
    return "".join(chip(b) for b in bs)


def ticket_html(key):
    return "".join(chip(x, small=True) for x in key.split("-"))


def fnum(v, fmt="{:.2f}", none="-"):
    try:
        if v is None or v == "":
            return none
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return none


def formation_card(card, sub=False, show_prob=True):
    f = card["formation"]
    names = "・".join
    how = (f"1着に <b>{names(map(str, f['first']))}</b>、2着に <b>{names(map(str, f['second']))}</b>、"
           f"3着に <b>{names(map(str, f['third']))}</b> をマーク")
    dup = f"（本線と重なる{card['dups']}点は除く）" if card.get("dups") else ""
    prob = f"　<small>当たる見込み 約{card['prob'] * 100:.0f}%</small>" if show_prob and card.get("prob") is not None else ""
    tickets = ""
    for t in card["tickets"]:
        extra = []
        if t.get("prob") is not None:
            extra.append(f"{t['prob'] * 100:.1f}%")
        if t.get("odds"):
            extra.append(f"{t['odds']:.1f}倍")
        tickets += f'<span class="tk">{ticket_html(t["ticket"])} <small>{" / ".join(extra)}</small></span>'
    reason = f'<div class="fm-reason">💬 {esc(card["reason"])}</div>' if card.get("reason") else ""
    return f"""
<div class="fm{' sub' if sub else ''}">
  <div class="fm-head"><span class="fm-tag">{esc(card['label'])}</span>
    <span class="fm-pts">3連単 {card['points']}点 <small>{card['points'] * 100:,}円(1点100円){prob}</small></span></div>
  <div class="fm-grid">
    <div class="fm-col"><div class="p">1着</div><div class="bs">{chips(f['first'])}</div></div>
    <div class="fm-arrow">▶</div>
    <div class="fm-col"><div class="p">2着</div><div class="bs">{chips(f['second'])}</div></div>
    <div class="fm-arrow">▶</div>
    <div class="fm-col"><div class="p">3着</div><div class="bs">{chips(f['third'])}</div></div>
  </div>
  <div class="fm-how">📝 買い方: {how} → <b>{card['points']}点</b>{dup}</div>
  {reason}
  <details style="margin-top:6px;"><summary class="note">買い目を1点ずつ見る（{card['points']}点）</summary>
  <div style="margin-top:4px;">{tickets}</div></details>
</div>"""


def prob_table(race, analysis, ai_result=None):
    pred = analysis["pred"]
    ai_marks = {b["boat"]: b for b in (ai_result or {}).get("boats", [])}
    rows = ""
    for i, b in enumerate(race["boats"]):
        n = b["boat"]
        mark = ai_marks.get(n, {}).get("mark") if ai_result else analysis["marks"].get(n, "")
        comment = ai_marks.get(n, {}).get("comment", "")
        w = pred["win"][i] * 100
        rows += (
            f"<tr><td>{chip(n)}</td><td><b>{esc(mark or '')}</b></td>"
            f"<td class='l'><b>{esc(b['name'])}</b> <small>{esc(b.get('cls', ''))}</small>"
            + (f"<br><small style='white-space:normal;color:#475569;'>{esc(comment)}</small>" if comment else "")
            + f"</td><td><b>{w:.0f}%</b><div class='bar'><div style='width:{min(w, 100):.0f}%'></div></div></td>"
            f"<td>{pred['top2'][i] * 100:.0f}%</td><td>{pred['top3'][i] * 100:.0f}%</td></tr>"
        )
    return ("<div class='tw'><table class='t'><tr><th>枠</th><th>印</th><th>選手</th><th>1着の確率</th>"
            "<th>2着以内</th><th>3着以内</th></tr>" + rows + "</table></div>")


def program_table(race, courses):
    rows = ""
    for i, b in enumerate(race["boats"]):
        series = " ".join(esc(s.get("place_text") or s.get("place") or "") for s in b.get("series") or [] if s.get("place_text") or s.get("place"))
        c = courses[i]
        cs = (b.get("course_stats") or {}).get(str(c))
        cs_txt = f"{cs[1] / cs[0] * 100:.0f}% <small>({cs[0]}走)</small>" if cs and cs[0] else "-"
        fl = f"F{b.get('f') or 0}" + (f" L{b['l']}" if b.get("l") else "")
        rows += (
            f"<tr><td>{chip(b['boat'])}</td><td class='l'><b>{esc(b['name'])}</b><br><small>{esc(b.get('cls', ''))} "
            f"{esc(b.get('branch', ''))} {esc(b.get('age') or '')}歳</small></td>"
            f"<td>{fnum(b.get('nat_win'))}<br><small>{fnum(b.get('nat_2'), '{:.0f}')}%</small></td>"
            + (f"<td>{fnum(b.get('loc_win'))}<br><small>{fnum(b.get('loc_2'), '{:.0f}')}%</small></td>" if b.get("loc_win") else "<td>-</td>") +
            f"<td>{fnum(b.get('motor_2'), '{:.0f}')}%<br><small>No.{esc(b.get('motor_no') or '-')}</small></td>"
            f"<td>{fnum(b.get('avg_st'))}<br><small>{esc(fl)}</small></td>"
            f"<td>{cs_txt}</td><td class='l'>{series or '-'}</td></tr>"
        )
    return ("<div class='tw'><table class='t'><tr><th>枠</th><th>選手</th><th>全国勝率<br>2連率</th><th>当地勝率<br>2連率</th>"
            "<th>モーター<br>2連率</th><th>平均ST<br>F/L</th><th>このコースの<br>1着率</th><th>今節の着順</th></tr>"
            + rows + "</table></div>")


def preview_table(race, pv, original):
    ex = {b: v.get("ex_time") for b, v in pv["boats"].items() if v.get("ex_time")}
    rank = {b: i + 1 for i, (b, _) in enumerate(sorted(ex.items(), key=lambda kv: kv[1]))}
    cols = original["columns"] if original else []
    best_o = {c: min((v.get(c) for v in original["boats"].values() if v.get(c)), default=None) for c in cols} if original else {}
    rows = ""
    for b in race["boats"]:
        p = pv["boats"].get(b["boat"], {})
        course = p.get("course")
        if course and course != b["boat"]:
            c_txt = f"<span class='best'>{course}{'↑' if course < b['boat'] else '↓'}</span>"
        else:
            c_txt = esc(course or "-")
        st_v = p.get("st")
        st_txt = "-" if st_v is None else (f"<span class='best'>F{abs(st_v):.2f}</span>" if st_v < 0 else f"{st_v:.2f}")
        et = p.get("ex_time")
        r = rank.get(b["boat"])
        et_txt = (f"<span class='{'best' if r == 1 else ''}'>{et:.2f}</span> <small>{r}位</small>") if et and r else "-"
        o_txt = ""
        for c in cols:
            v = (original["boats"].get(b["boat"]) or {}).get(c)
            o_txt += "<td>" + (f"<span class='best'>{v:.2f}</span>" if v and best_o.get(c) and v <= best_o[c] else fnum(v)) + "</td>"
        parts = " ".join(x for x in [p.get("parts"), p.get("propeller")] if x)
        adj = f" 調整{p['weight_adj']:.1f}kg" if p.get("weight_adj") else ""
        rows += (f"<tr><td>{chip(b['boat'])}</td><td>{c_txt}</td><td>{st_txt}</td><td>{et_txt}</td>"
                 f"{o_txt}<td>{fnum(p.get('tilt'), '{:.1f}')}</td><td class='l'>{esc(parts + adj)}</td></tr>")
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)
    wind = model.wind_label(pv.get("wind"), pv.get("wind_dir"))
    as_of = f"（{esc(pv['as_of'])}）" if pv.get("as_of") else ""
    weather = (f"<div style='margin-bottom:6px;font-size:0.88rem;'>{esc(pv.get('weather') or '')}　気温{fnum(pv.get('temp'), '{:.0f}')}℃　"
               f"<b>風 {esc(wind)} {fnum(pv.get('wind'), '{:.0f}')}m</b>　波{fnum(pv.get('wave'), '{:.0f}')}cm　"
               f"水温{fnum(pv.get('water_temp'), '{:.0f}')}℃ <small>{as_of}</small></div>")
    slit = sorted(race["boats"], key=lambda b: pv["boats"].get(b["boat"], {}).get("course") or b["boat"])
    slit_html = "".join(chip(b["boat"], small=True) for b in slit)
    return (weather + f"<div style='margin-bottom:6px;font-size:0.88rem;'>スタート展示の進入（内から） {slit_html}</div>"
            "<div class='tw'><table class='t'><tr><th>枠</th><th>進入</th><th>展示ST</th><th>展示タイム</th>"
            + head + "<th>チルト</th><th>部品交換など</th></tr>" + rows + "</table></div>"
            "<div class='note'>赤字＝その項目で一番良い艇／進入が枠と違う艇（↑内へ ↓外へ）／フライング(F)。タイムは小さいほど速い。</div>")


@st.fragment(run_every=30)
def wait_for_exhibition(jcd, rno, hd, deadline):
    """展示がまだ出ていない間、30秒ごとに直前情報を確認し、出たら画面全体を自動で更新する。"""
    left = service.minutes_to_deadline(deadline, hd)
    if left is None or left < 0:
        return
    if left > 45:
        st.caption(f"⏳ 展示は締切の約30分前から出ます（締切まであと{left}分）。近づいたら自動で確認を始めます。")
        return
    pv = service.preview_cached(jcd, rno, hd)
    if pv and any(b.get("ex_time") for b in pv["boats"].values()):
        st.rerun()
    st.caption(f"⏳ 展示が出るのを自動で確認中…（{service.now_jst():%H:%M:%S} 確認・締切まであと{left}分）出たら自動で予想を更新します。")


# ----------------------------------------------------
# 状態
# ----------------------------------------------------
TODAY = service.today_hd()
ss = st.session_state
ss.setdefault("tab", "開催一覧")
ss.setdefault("hd", TODAY)
ss.setdefault("jcd", None)
ss.setdefault("rno", 1)
ss.setdefault("style", formation.DEFAULT_STYLE)
ss.setdefault("ai_results", {})

# URLに ?hd=20261125&jcd=5&rno=1 を付けると、その日・その場・そのレースを直接開ける
if not ss.get("_query_read"):
    ss["_query_read"] = True
    q = st.query_params
    try:
        if q.get("hd") and len(q["hd"]) == 8:
            datetime.datetime.strptime(q["hd"], "%Y%m%d")
            ss.hd = q["hd"]
        if q.get("jcd") and int(q["jcd"]) in VENUES:
            ss.jcd, ss.tab = int(q["jcd"]), "予想"
            if q.get("rno") and 1 <= int(q["rno"]) <= 12:
                ss.rno = int(q["rno"])
    except ValueError:
        pass


def go_race(jcd, rno):
    ss.jcd, ss.rno, ss.tab = jcd, rno, "予想"


def hd_label(hd):
    d = datetime.date(int(hd[:4]), int(hd[4:6]), int(hd[6:]))
    return f"{d.month}/{d.day}（{'月火水木金土日'[d.weekday()]}）"


show_html(f"""<div class="top-bar"><span class="t">🚤 競艇AI予想</span>
<span class="d">{hd_label(ss.hd)}{'・本日' if ss.hd == TODAY else ''}</span></div>""")

nav = st.columns(3)
for col, name, label in zip(nav, ["開催一覧", "締切順", "予想"], ["🚩 開催一覧", "⏰ 締切順", "🎯 AI予想"]):
    if col.button(label, type="primary" if ss.tab == name else "secondary", use_container_width=True, key=f"nav_{name}"):
        ss.tab = name
        st.rerun()

try:
    schedule = load_schedule(ss.hd)
except Exception as e:  # 取得元が全滅していても画面は出す
    schedule = {"venues": [], "notes": [f"開催情報を取得できませんでした: {e}"]}
venues = schedule["venues"]
for n in schedule["notes"]:
    st.warning(n)

# ----------------------------------------------------
# 画面1: 開催一覧
# ----------------------------------------------------
if ss.tab == "開催一覧":
    picked = st.date_input("開催日", value=datetime.date(int(ss.hd[:4]), int(ss.hd[4:6]), int(ss.hd[6:])), format="YYYY/MM/DD")
    new_hd = picked.strftime("%Y%m%d")
    if new_hd != ss.hd:
        ss.hd = new_hd
        ss.jcd = None
        st.rerun()

    if not venues:
        st.info("この日の開催はありません。")
    show_html('<div class="note">場を選ぶと、次に締切を迎えるレースの予想画面を開きます（「終了」の場は結果の確認ができます）。</div>')
    cols = st.columns(2)
    for i, v in enumerate(venues):
        rno, dl = service.next_race(v.get("deadlines") or {}, ss.hd)
        icon = {"ナイター": "🌙 ", "モーニング": "🌅 ", "ミッドナイト": "🌃 "}.get(v.get("time_zone"), "")
        grade = f"[{v['grade']}] " if v.get("grade") and v["grade"] != "一般" else ""
        if v.get("cancelled"):
            state = "中止"
        elif rno:
            state = f"次{rno}R {dl}"
        elif ss.hd < TODAY or v.get("deadlines"):
            state = "終了"
        else:
            state = "出走表"
        label = f"{icon}{grade}{v['name']}　{state}"
        if cols[i % 2].button(label, key=f"v_{v['jcd']}", use_container_width=True, disabled=bool(v.get("cancelled"))):
            go_race(v["jcd"], rno or (12 if state == "終了" else 1))
            st.rerun()

# ----------------------------------------------------
# 画面2: 締切順
# ----------------------------------------------------
elif ss.tab == "締切順":
    ups = service.upcoming(venues, ss.hd)
    if not ups:
        if ss.hd >= TODAY and venues and not any(v.get("deadlines") for v in venues):
            st.info("締切時刻のデータがまだ届いていません（当日の朝に更新されます）。開催一覧から場を選ぶと出走表と予想は見られます。")
        else:
            st.info("これから締切を迎えるレースはありません。開催一覧から日付や場を選んでください。")
    now = service.now_jst()
    for item in ups[:30]:
        mins = ""
        if ss.hd == TODAY:
            h, m = map(int, item["deadline"].split(":"))
            left = (h * 60 + m) - (now.hour * 60 + now.minute)
            mins = f"（あと{left}分）"
        grade = f"[{item['grade']}] " if item.get("grade") and item["grade"] != "一般" else ""
        if st.button(f"⏰ {item['deadline']}　{grade}{item['name']} {item['rno']}R　{mins}",
                     key=f"u_{item['jcd']}_{item['rno']}", use_container_width=True):
            go_race(item["jcd"], item["rno"])
            st.rerun()

# ----------------------------------------------------
# 画面3: AI予想
# ----------------------------------------------------
else:
    open_jcds = [v["jcd"] for v in venues if not v.get("cancelled")] or sorted(VENUES)
    if ss.jcd not in open_jcds:
        ss.jcd = open_jcds[0]
    c1, c2, c3 = st.columns([1.2, 0.9, 1.3])
    jcd = c1.selectbox("場", open_jcds, index=open_jcds.index(ss.jcd), format_func=lambda j: f"{VENUES[j]}")
    rno = c2.selectbox("レース", list(range(1, 13)), index=int(ss.rno) - 1, format_func=lambda r: f"{r}R")
    style = c3.selectbox("買い方", list(formation.STYLES), index=list(formation.STYLES).index(ss.style),
                         help="的中重視: 20点前後まで広く買う / バランス: 12点前後 / 少点数: 6点前後に絞る")
    if (jcd, rno, style) != (ss.jcd, ss.rno, ss.style):
        ss.jcd, ss.rno, ss.style = jcd, rno, style
        st.rerun()
    hd = ss.hd

    if st.button("🔄 最新の展示・オッズに更新", use_container_width=True):
        service.clear_live(jcd, rno, hd)
        st.rerun()

    try:
        with st.spinner("出走表・直前情報（展示）・オッズ・本日の結果を取得中...（10秒ほどかかることがあります）"):
            bundle = service.get_bundle(jcd, rno, hd)
    except SourceError as e:
        st.error(str(e))
        st.stop()
    race = bundle["race"]
    pv = race["preview"]
    live = model.has_preview(race)
    original = bundle["original"] if live else None
    odds = bundle["odds"]
    results = bundle["results"]
    stadium = bundle["stadium"]
    trend = service.trend(results, rno)
    result = next((r for r in results if r["rno"] == rno), None)
    analysis = service.analyze(race, style, odds)
    pred = analysis["pred"]

    venue_info = next((v for v in venues if v["jcd"] == jcd), {})
    grade = race.get("grade") or venue_info.get("grade", "")
    badge = f'<span class="badge">{esc(grade)}</span>' if grade and grade != "一般" else ""
    dl = race.get("deadline") or (venue_info.get("deadlines") or {}).get(rno, "")
    show_html(f"""<div class="race-head"><div class="dl">締切予定<br><b>{esc(dl or '--:--')}</b></div>
<div class="v">{esc(VENUES[jcd])} {rno}R{badge}</div>
<div class="s">{esc(race.get('title', ''))}　{esc(race.get('subtitle', ''))}　{esc(venue_info.get('day_text', ''))}
{'　※進入固定' if race.get('fixed_course') else ''}</div></div>""")
    if race.get("note"):
        st.warning(race["note"])

    # ---------- 直前情報（展示）----------
    show_html('<div class="sec">⏱ 直前情報（展示）</div>')
    if live:
        show_html(f'<div class="card">{preview_table(race, pv, original)}</div>')
        st.success("✅ 展示タイム・スタート展示・風を反映した予想です。")
    else:
        st.info("展示（直前情報）はまだ出ていません。いまは出走表だけの予想です。")
        wait_for_exhibition(jcd, rno, hd, dl)

    # ---------- AI（Gemini）----------
    ai_key = (hd, jcd, rno, style, live)
    ai_result = ss.ai_results.get(ai_key)
    api_key = get_api_key()
    memo = st.text_area("AIに伝えたいメモ（任意）", key=f"memo_{hd}_{jcd}_{rno}", height=68,
                        placeholder="例: 3号艇は前検から伸びが良いらしい / 1号艇は足落ち など。気づいたことがあれば書くとAIが考慮します。")
    if live and not ai_result and ss.ai_results.get((hd, jcd, rno, style, False)):
        st.warning("展示が出ました。もう一度AIボタンを押すと、展示を反映した買い目に更新されます。")
    if st.button("🤖 AIに展開を読んでもらい、最終の買い目を出す", type="primary", use_container_width=True):
        if not api_key:
            st.error("Gemini APIキーが設定されていません（Streamlitの Secrets に GEMINI_API_KEY を登録してください）。下のデータ予想はAPIキーなしで使えます。")
        else:
            with st.spinner("🤖 AIが進入・スタート・機力・風から展開を予想中...（10〜30秒）"):
                try:
                    ai_result = ai.predict_with_gemini(api_key, race, analysis, style, original, stadium, trend, odds, memo)
                    ss.ai_results[ai_key] = ai_result
                except Exception as e:
                    st.error(f"AI予想でエラーが出ました: {e}　下のデータ予想は使えます。")

    # ---------- 買い目 ----------
    if ai_result:
        cards = [formation.describe(ai_result["main"], pred["P"], odds, "本線")]
        if ai_result.get("sub"):
            sub_card = formation.describe(ai_result["sub"], pred["P"], odds, "抑え", exclude=formation.expand(ai_result["main"]))
            if sub_card["points"]:
                cards.append(sub_card)
        source_label = "🤖 AI（Gemini）の最終予想"
    else:
        cards = analysis["cards"]
        source_label = "📊 データ予想（統計AI）"

    if result:
        hit = service.check_hit(cards, result["top3"])
        res_txt = (f"結果 {ticket_html('-'.join(map(str, result['top3'])))} "
                   f"{esc(result.get('technique', ''))}　3連単 {result['payout']:,}円" if result.get("payout")
                   else f"結果 {ticket_html('-'.join(map(str, result['top3'])))}")
        if hit:
            show_html(f'<div class="hit">🎯 的中！（{esc(hit)}）　{res_txt}</div>')
        else:
            show_html(f'<div class="miss">ハズレ　{res_txt}</div>')

    show_html(f'<div class="sec">🎯 買い目　<small style="font-weight:600;color:#475569;">{source_label}</small></div>')
    if ai_result:
        trust = f"　イン信頼度 <b>{esc(ai_result['in_trust'])}</b>" if ai_result.get("in_trust") else ""
        slit = f"　進入予想 <b>{esc(ai_result['slit'])}</b>" if ai_result.get("slit") else ""
        show_html(f"""<div class="card"><b>{esc(ai_result['summary'])}</b>
<div class="note" style="margin-top:4px;">{trust}{slit}</div></div>""")
    total_pts = sum(c["points"] for c in cards)
    total_prob = formation.coverage(pred["P"], [tuple(map(int, t["ticket"].split("-"))) for c in cards for t in c["tickets"]])
    for i, c in enumerate(cards):
        show_html(formation_card(c, sub=(i > 0)))
    show_html(f"""<div class="card">合計 <b>{total_pts}点</b>（1点100円なら <b>{total_pts * 100:,}円</b>）
どれかが当たる見込み <b>約{total_prob * 100:.0f}%</b>
<div class="note">見込みは過去レースの統計から計算した目安です。1点ごとの％は「買い目を1点ずつ見る」で確認できます。</div></div>""")

    with st.expander("❓ フォーメーションの読み方・買い方"):
        st.markdown("""
**フォーメーション**は「1着に来そうな艇」「2着に来そうな艇」「3着に来そうな艇」をそれぞれ選んで、まとめて買う方法です。

例：1着 **1** ▶ 2着 **2・3** ▶ 3着 **2・3・4**
→ 1-2-3、1-2-4、1-3-2、1-3-4 の **4点**（同じ艇は2回使えないので 1-2-2 などは自動で除かれます）

**買う手順（テレボート／公式アプリ）**
1. 場とレースを選ぶ
2. 勝式で **「3連単」**、方式で **「フォーメーション」** を選ぶ
3. 1着の列・2着の列・3着の列で、上の表示と同じ艇番をタップ
4. 表示された点数がこの画面の点数と同じことを確認して、1点あたりの金額を入れる

**本線**は一番ありそうな展開、**抑え**は本線が崩れた時のための保険です。両方買うのが基本ですが、予算が少ない時は本線だけでも構いません。
""")

    # ---------- 展開 ----------
    if ai_result:
        show_html('<div class="sec">🌊 AIの展開予想</div>')
        show_html(f"""<div class="card">{esc(ai_result['scenario'])}
<div style="margin-top:8px;"><b>💡 決め手</b>　{esc(ai_result['key_point'])}</div></div>""")

    show_html('<div class="sec">📊 各艇の評価</div>')
    show_html(prob_table(race, analysis, ai_result))
    show_html('<div class="note">◎本命 ○対抗 ▲単穴 △連下。確率はデータ予想モデルの計算値です。</div>')

    with st.expander("もっと当てやすい券種で買うなら（2連単・2連複・3連複・単勝）"):
        show_html('<div class="note">3連単は1〜3着を順番どおり当てる一番むずかしい券種です。当たりやすさを優先するなら下の券種もあります。</div>')
        desc = {"単勝": "1着を当てる", "2連単": "1着と2着を順番どおり", "2連複": "1着と2着（順番は不問）", "3連複": "1〜3着（順番は不問）"}
        html_o = ""
        for kind, items in analysis["others"].items():
            tks = "".join(
                f'<span class="tk">{"".join(chip(x, small=True) for x in t["ticket"].replace("=", "-").split("-"))} '
                f'<small>約{t["prob"] * 100:.0f}%</small></span>' for t in items)
            html_o += f'<div style="margin-bottom:6px;"><b>{kind}</b> <span class="note">{desc[kind]}</span><br>{tks}</div>'
        show_html(f'<div class="card">{html_o}</div>')

    if ai_result:
        with st.expander("データ予想（統計AI）だけの買い目も見る"):
            for i, c in enumerate(analysis["cards"]):
                show_html(formation_card(c, sub=(i > 0)))

    # ---------- データ ----------
    show_html('<div class="sec">📋 集めたデータ</div>')
    with st.expander("出走表（勝率・モーター・今節成績）", expanded=True):
        show_html(program_table(race, pred["courses"]))
        stats_meta = model.load_racer_stats().get("meta", {})
        show_html(f'<div class="note">「このコースの1着率」は過去約1年の実績（{esc(stats_meta.get("until", ""))}まで）。</div>')
    if trend or stadium:
        with st.expander("今日の傾向・この場のコース別成績"):
            if trend:
                kim = "　".join(f"{k} {v}回" for k, v in trend["kimarite"].items())
                res_list = "".join(f'<span class="tk">{r["rno"]}R {ticket_html("-".join(map(str, r["top3"])))} <small>{esc(r.get("technique", ""))}</small></span>' for r in trend["list"])
                show_html(f"""<div class="card"><b>今日ここまで {trend['races']}レース</b>：1号艇の1着 {trend['lane1_wins']}回　{esc(kim)}
<div style="margin-top:6px;">{res_list}</div></div>""")
            if stadium:
                rows = ""
                for c in range(1, 7):
                    s = stadium["courses"].get(c)
                    if s:
                        kim = " ".join(f"{k}{v:.0f}%" for k, v in s["kimarite"].items() if v >= 5)
                        rows += f"<tr><td>{chip(c)}</td><td>{s['place'][0]:.1f}%</td><td>{s['place'][1]:.1f}%</td><td>{s['place'][2]:.1f}%</td><td class='l'>{esc(kim)}</td></tr>"
                show_html("<div class='tw'><table class='t'><tr><th>コース</th><th>1着</th><th>2着</th><th>3着</th><th>勝った時の決まり手</th></tr>"
                            + rows + "</table></div><div class='note'>公式サイト集計（最近3か月）</div>")
    if ai_result and ai_result.get("race_text"):
        with st.expander("AIに渡した資料（全文）"):
            st.text(ai_result["race_text"])

    show_html(f"""<div class="note" style="margin-top:10px;">
データ元: {esc(race.get('source', ''))}{' / ' + esc(pv.get('source', '')) if pv else ''}{' / BOATCAST' if original else ''}<br>
🔗 <a href="{sources.official_url('racelist', jcd, rno, hd)}" target="_blank">公式サイトの出走表</a>
<a href="{sources.official_url('odds3t', jcd, rno, hd)}" target="_blank">公式オッズ</a>
<a href="{sources.biyori_url(jcd, rno, hd)}" target="_blank">ボートレース日和で見る</a></div>""")

show_html("""<div class="note" style="margin-top:18px;text-align:center;">
予想は的中を保証するものではありません。舟券は20歳以上、無理のない金額で。</div>""")
