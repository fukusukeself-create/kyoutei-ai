"""中央競馬 統計予想 — Streamlit UI (競艇日和アプリと同じ操作感: 開催一覧 → 場・R → ⚡予想)。

予想は AI を使わず、過去レースから学習した統計モデル (models/) で行う。
models/ が無い環境では簡易レーティング (model.py) に落ちる。
"""

from __future__ import annotations

import datetime as dt
import os
import time

import pandas as pd
import streamlit as st

import ledger
import model as heuristic
import scraper
import statmodel
import strategy

st.set_page_config(page_title="中央競馬 統計予想", page_icon="🏇", layout="centered",
                   initial_sidebar_state="collapsed")
JST = dt.timezone(dt.timedelta(hours=9))
VENUE_ORDER = ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]

st.markdown("""
<style>
.block-container { padding: 0.6rem 0.7rem 3rem 0.7rem !important; max-width: 520px; }
header[data-testid="stHeader"] { height: 0; background: transparent; }
h1, h2, h3 { margin: 0.2rem 0 0.4rem 0 !important; }
[data-testid="stColumn"] { min-width: 0 !important; }
div[data-testid="stPills"] button { min-height: 38px; font-size: 15px; padding: 2px 10px; }
.st-key-venuegrid button { padding: 4px 2px !important; min-height: 74px; line-height: 1.3; white-space: normal !important; }
.st-key-venuegrid button p { font-size: 12px !important; }
.tk { display:flex; justify-content:space-between; align-items:center; padding:6px 9px; margin:4px 0;
      background:#fff; color:#111827; border:1px solid #d9e2dc; border-radius:6px; font-size:0.93rem; }
.tk .cmb { font-weight:900; font-size:1.03rem; }
.tk .meta { font-size:0.78rem; color:#52605a; text-align:right; }
.tk.good { border-color:#16a34a; background:#f0fdf4; color:#14532d; }
.sec { font-weight:900; color:#1b5e3a; margin:10px 0 4px 0; font-size:0.98rem; border-left:4px solid #1b5e3a; padding-left:6px; }
.note { font-size:0.78rem; color:#6b7280; }
.fm-card { background:#fff; color:#111827; border:1px solid #d9e2dc; border-left:4px solid #1b5e3a; border-radius:8px; padding:8px 10px; margin:6px 0; }
.fm-title { font-weight:900; color:#1b5e3a; font-size:0.95rem; }
.fm-text { font-size:1.35rem; font-weight:800; letter-spacing:1px; margin:4px 0; }
.fm-sub { font-size:0.78rem; color:#52605a; }
.judge { background:#1b5e3a; color:#fff; border-radius:8px; padding:10px 12px; margin:6px 0; }
.stApp { background:#f3f5f2; color:#111827; }
.judge .h { font-size:1.15rem; font-weight:900; }
.judge .s { font-size:0.82rem; opacity:0.92; }
.waku1{background:#fff;color:#000;border:1px solid #999} .waku2{background:#222;color:#fff} .waku3{background:#dc2626;color:#fff}
.waku4{background:#2563eb;color:#fff} .waku5{background:#facc15;color:#000} .waku6{background:#16a34a;color:#fff}
.waku7{background:#f97316;color:#fff} .waku8{background:#f9a8d4;color:#000}
.wk { display:inline-block; width:1.5em; text-align:center; border-radius:3px; font-weight:800; font-size:0.8rem; }
div[data-testid="stMetric"] { background:#fff; border:1px solid #d9e2dc; border-radius:8px; padding:6px 10px; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- 取得 (キャッシュ)
@st.cache_data(ttl=180, show_spinner=False)
def load_meetings(date: str):
    return scraper.fetch_meetings(date)


@st.cache_data(ttl=3600, show_spinner=False)
def load_kaisai_dates(year: int, month: int):
    try:
        return scraper.fetch_kaisai_dates(year, month)
    except scraper.ScrapeError:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def load_race(race_id: str, date: str):
    race = scraper.fetch_shutuba(race_id, date)
    try:
        scraper.attach_past(race, scraper.fetch_past(race_id))
    except scraper.ScrapeError:
        pass
    try:
        scraper.attach_oikiri(race, scraper.fetch_oikiri(race_id))
    except scraper.ScrapeError:
        pass
    return race


@st.cache_data(ttl=60, show_spinner=False)
def load_odds(race_id: str, _bucket: int):
    return scraper.fetch_all_odds(race_id)


@st.cache_data(ttl=600, show_spinner=False)
def load_result(race_id: str):
    return scraper.fetch_result(race_id)


def next_kaisai_date(today: dt.date):
    for add in (0, 1):
        y, m = today.year, today.month + add
        if m > 12:
            y, m = y + 1, 1
        for d in load_kaisai_dates(y, m):
            if d >= today.strftime("%Y%m%d"):
                return d
    return None


def waku_html(w: int) -> str:
    return f'<span class="wk waku{w}">{w}</span>'


@st.cache_data(ttl=600, show_spinner=False)
def load_day_bias(date: str, venue: str, before_rno: int):
    """同じ日・同じ場で先に終わったレースの、勝ち馬の脚質・枠と3着内の枠。"""
    import features as F
    finished = []
    try:
        ms = load_meetings(date)
    except scraper.ScrapeError:
        return F.day_bias([])
    m = next((x for x in ms if x.venue == venue), None)
    if not m:
        return F.day_bias([])
    for r in m.races:
        if r.rno >= before_rno:
            continue
        try:
            res = load_result(r.race_id)
            if not res:
                continue
            past = scraper.fetch_past(r.race_id)
            win = res.order[0]
            finished.append(dict(winner_style=(past.get(win["umaban"]) or {}).get("style"), winner_waku=win.get("waku"),
                                 heads=len(res.order), top3_wakus=[o.get("waku") for o in res.order[:3] if o.get("waku")]))
        except scraper.ScrapeError:
            continue
    return F.day_bias(finished)


def run_prediction(race, odds, style):
    """統計モデル (無ければ簡易レーティング) → 買い目。"""
    try:
        day = load_day_bias(race.date, race.venue, race.rno)
    except Exception:
        day = None
    sm = statmodel.predict(race, odds.get("win"), day)
    if sm:
        est = dict(probs=sm["probs"], model_probs=sm["model_probs"], market_probs=sm["market_probs"],
                   notes={u: statmodel.explain(f, statmodel.load()["stats"]) for u, f in sm["features"].items()},
                   engine=sm.get("engine", "統計モデル"), blend_w=sm["blend_w"], top3=sm["top3_probs"])
    else:
        e = heuristic.estimate(race, odds.get("win"))
        est = dict(probs=e["probs"], model_probs=e["model_probs"], market_probs=e["market_probs"],
                   notes=e["notes"], engine="簡易レーティング", blend_w=0.6 if e["market_probs"] else 0.0, top3=None)
    plan = strategy.build_plan(est["probs"], odds, style)
    return est, plan


# ---------------------------------------------------------------- ヘッダー
now = dt.datetime.now(JST)
meta = (statmodel.load() or {}).get("meta")
st.markdown("### 🏇 中央競馬 統計予想")
if meta:
    st.caption(f"統計モデル: {meta['races']:,}レース ({meta['data_period'][0][:4]}〜{meta['data_period'][1][:4]}年) から学習。AIは使わない。")
else:
    st.caption("統計モデル未学習 (models/ なし)。馬柱の簡易レーティングで予想します。")

ss = st.session_state
ss.setdefault("race_date", now.date())
ss.setdefault("auto_run", False)
ss.setdefault("result", None)

d1, d2 = st.columns([3, 1], wrap=False, vertical_alignment="bottom")
race_date = d1.date_input("開催日", value=ss.race_date, format="YYYY/MM/DD")
ss.race_date = race_date
date_str = race_date.strftime("%Y%m%d")
if d2.button("🔄 更新", use_container_width=True):
    load_meetings.clear(); load_odds.clear(); st.rerun()

with st.spinner("開催情報を取得中…"):
    try:
        meetings = load_meetings(date_str)
        sched_error = ""
    except scraper.ScrapeError as e:
        meetings, sched_error = [], str(e)
if sched_error:
    st.warning(f"開催情報を取得できませんでした。少し待って「更新」を押してください。({sched_error})")

is_today = date_str == now.strftime("%Y%m%d")
now_hm = now.strftime("%H:%M")
by_venue = {m.venue: m for m in meetings}


def next_race(m):
    if not is_today:
        return m.races[0] if m.races else None
    for r in m.races:
        if r.start >= now_hm:
            return r
    return None


def select_race(venue: str, rno):
    ss["venue"] = venue
    if rno:
        ss["race"] = f"{rno}R"
        ss["auto_run"] = True


if not meetings and not sched_error:
    nxt = next_kaisai_date(race_date)
    if nxt and nxt != date_str:
        nd = dt.datetime.strptime(nxt, "%Y%m%d")
        st.info(f"{race_date.strftime('%-m/%-d')} は中央競馬の開催がありません。次の開催は {nd.strftime('%-m/%-d')} です。")
        if st.button(f"➡ {nd.strftime('%-m/%-d')} の開催を見る", use_container_width=True):
            ss.race_date = nd.date(); st.rerun()

tab_grid, tab_time = st.tabs(["🏁 開催一覧", "⏰ 発走順"])
with tab_grid, st.container(key="venuegrid"):
    for row_start in range(0, len(VENUE_ORDER), 5):
        cols = st.columns(5, gap="small", wrap=False)
        for col, name in zip(cols, VENUE_ORDER[row_start:row_start + 5]):
            with col:
                m = by_venue.get(name)
                if not m:
                    st.button(f"{name}  \n－－  \n　", key=f"pick_{name}", use_container_width=True, disabled=True)
                    continue
                nr = next_race(m)
                sub = f"{nr.rno}R {nr.start}" if nr else "終了"
                main = next((r for r in m.races if r.grade in ("GI", "GII", "GIII")), None)
                g = main.grade if main else (m.races[-2].grade if len(m.races) >= 2 and m.races[-2].grade else "")
                st.button(f"**{name}**  \n{m.day} {g}  \n{sub}", key=f"pick_{name}", use_container_width=True,
                          on_click=select_race, args=(name, nr.rno if nr else None))
with tab_time:
    upcoming = sorted([(r, m) for m in meetings for r in m.races if (not is_today) or r.start >= now_hm],
                      key=lambda x: (x[0].start, x[1].venue))
    if not upcoming:
        st.info("発走前のレースがありません。")
    for r, m in upcoming[:24]:
        g = f" [{r.grade}]" if r.grade else ""
        st.button(f"⏰{r.start}  {m.venue} {r.rno}R {r.name}{g}  {r.course} {r.heads}頭", key=f"tm_{r.race_id}",
                  use_container_width=True, on_click=select_race, args=(m.venue, r.rno))

st.divider()
venues_today = [v for v in VENUE_ORDER if v in by_venue] or VENUE_ORDER
if ss.get("venue") not in venues_today:
    ss["venue"] = venues_today[0]
venue = st.pills("開催場", venues_today, selection_mode="single", key="venue")
race_labels = [f"{i}R" for i in range(1, 13)]
if ss.get("race") not in race_labels:
    ss["race"] = "11R"
race_label = st.pills("レース", race_labels, selection_mode="single", key="race")
mode = "単勝中心"
style = "バランス"
value_policy = (statmodel.load() or {}).get("value_policy") or None

m_sel = by_venue.get(venue or "")
rno = int((race_label or "11R").rstrip("R"))
rs = next((r for r in (m_sel.races if m_sel else []) if r.rno == rno), None)
run = st.button("⚡ 予想する", type="primary", use_container_width=True, disabled=rs is None)
if ss.pop("auto_run", False):
    run = True

if rs is None:
    st.caption("開催一覧から場を選ぶと、その場の次のレースが入ります。")
else:
    st.caption(f"{venue} {rno}R {rs.name} / {rs.start}発走 / {rs.course} / {rs.heads}頭")

if run and rs is not None:
    with st.spinner("出馬表・馬柱・オッズを取得して計算中…"):
        try:
            race = load_race(rs.race_id, date_str)
            odds = load_odds(rs.race_id, int(time.time() // 60))
            est, plan = run_prediction(race, odds, style)
            ss.result = dict(race_id=rs.race_id, race=race, odds=odds, est=est, plan=plan, style=style, saved=None,
                             at=now.strftime("%H:%M"))
        except scraper.ScrapeError as e:
            ss.result = None
            st.error(str(e))

res = ss.result
if res and res["race_id"] == (rs.race_id if rs else None):
    race, odds, est, plan = res["race"], res["odds"], res["est"], res["plan"]
    if res["style"] != style:
        plan = strategy.build_plan(est["probs"], odds, style)
        res["plan"], res["style"] = plan, style
    has_odds = bool(odds.get("win"))
    ranked = sorted(race.horses, key=lambda h: -plan.win_probs.get(h.umaban, 0))
    top = ranked[0]
    p_top = plan.win_probs.get(top.umaban, 0)
    conf = "自信あり" if p_top >= 0.35 else "普通" if p_top >= 0.22 else "混戦"
    st.markdown(f"""
    <div class="judge"><div class="h">{race.venue} {race.rno}R {race.name}</div>
    <div class="s">{race.start}発走 / {race.course} ({race.turn}) / {race.cls} / {race.heads}頭 / 天候 {race.weather or '-'} 馬場 {race.condition or '-'}</div>
    <div class="s" style="margin-top:6px;">本命 <b style="font-size:1.1rem">{top.umaban} {top.name}</b> 勝率 {p_top*100:.0f}% ・ {conf}
    ・ {est['engine']}{' + オッズ' if has_odds else ' (オッズ未発売)'} ・ {res['at']}時点</div></div>
    """, unsafe_allow_html=True)
    if getattr(race, "pre_draw", False):
        st.warning("枠順確定前のため、馬番は出馬表の掲載順の仮番号です。枠順が出たら (通常は前日) 予想し直してください。")

    # --- 買い目: 単勝が基本。上位3頭が堅いレースだけ三連複も
    import features as F
    bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
    pol = value_policy or strategy.DEFAULT_VALUE_POLICY
    tickets: dict = {}
    if race.surface == "障":
        st.warning("障害レースは学習の対象外 (芝・ダートのみで学習) のため、勝率と買い目は参考値です。")

    def show(ts):
        for t in ts:
            good = " good" if (t.ev or 0) >= 1.0 else ""
            meta_t = f"{t.prob*100:.1f}%" + (f" / {t.odds:.1f}倍 / 期待値 {t.ev:.2f}" if t.odds else " / オッズ未発売")
            name = next((h.name for h in race.horses if h.umaban == t.combo[0]), "") if t.kind == "単勝" else ""
            st.markdown(f'<div class="tk{good}"><span class="cmb">{t.kind} {t.label} {name}</span><span class="meta">{meta_t}</span></div>',
                        unsafe_allow_html=True)

    st.markdown('<div class="sec">単勝</div>', unsafe_allow_html=True)
    if not has_odds:
        u0 = ranked[0].umaban
        tan = [strategy.Ticket("単勝", (u0,), plan.win_probs.get(u0, 0), None)]
        st.markdown('<div class="note">オッズ未発売のため、ひとまず勝率1位の単勝を出す。発売後に「オッズ更新」を押すと期待値で選び直す。</div>',
                    unsafe_allow_html=True)
    else:
        vb = strategy.value_bets(plan.win_probs, odds, value_policy, bands)
        tan = vb.get("単勝", [])
        rule = pol["policy"].get("単勝") or {}
        if tan:
            st.markdown(f'<div class="note"><b>勝負</b>: 期待値 (勝率 × オッズ) が {rule.get("threshold", 1.3):.1f} 以上の馬。市場が過小評価している馬を買う。</div>',
                        unsafe_allow_html=True)
            tv = (value_policy or {}).get("test") or {}
            if tv.get("bets"):
                st.markdown(f'<div class="note">この買い方の検証 ({value_policy["test_period"][0][:4]}年・学習に使っていない {value_policy["races_test"]:,}レース): '
                            f'{tv["bets"]:,}点 的中率 {tv["hit_rate"]*100:.0f}% / 回収率 {tv["roi"]*100:.0f}% (1点100円)</div>', unsafe_allow_html=True)
        else:
            ns = strategy.noskip_bets(plan.win_probs, odds, "単勝 上位1点", (value_policy or {}).get("pmin"))
            tan = [t for t in ns if t.kind == "単勝"][:1]
            why = ("このレース区分 (新馬・未勝利) は検証で期待値買いの成績が悪い" if any(bands.get(c) == v for c, v in rule.get("exclude", []))
                   else "期待値が下限を超える馬がいない")
            st.markdown(f'<div class="note">{why}ため、見送らずに期待値が最も高い馬を1点 (勝負度は低め)。</div>', unsafe_allow_html=True)
            nsv = ((value_policy or {}).get("noskip") or {}).get("test") or {}
            if nsv.get("bets"):
                st.markdown(f'<div class="note">この買い方の検証 ({value_policy["test_period"][0][:4]}年 {nsv["races"]:,}レース全部): 的中率 {nsv["hit_rate"]*100:.0f}% / 回収率 {nsv["roi"]*100:.0f}%</div>',
                            unsafe_allow_html=True)
    show(tan)
    tickets["単勝"] = tan

    # 三連複: 上位3頭の勝率合計が基準以上の「カチカチに固い」レースだけ
    trio_rule = (statmodel.load() or {}).get("trio") or {}
    th = float(trio_rule.get("top3sum_min", 0.85))
    t1 = float(trio_rule.get("top1_min", 0.50))
    k = int(trio_rule.get("k", 2))
    probs_sorted = sorted(plan.win_probs.values(), reverse=True)
    top1p, top3sum = probs_sorted[0], sum(probs_sorted[:3])
    if top1p >= t1 and top3sum >= th:
        trio = strategy.trio_top(plan.win_probs, odds, k)
        sm = strategy.summarize(trio)
        ev_txt = f" / 期待回収率 {sm['ev']*100:.0f}%" if sm["ev"] else ""
        st.markdown('<div class="sec">三連複 (カチカチに固いレース)</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="fm-card"><div class="fm-title">三連複フォーメーション ({len(trio)}点)</div>'
                    f'<div class="fm-text">{strategy.exact_formation("三連複", [t.combo for t in trio]).replace(" / ", "<br>")}</div>'
                    f'<div class="fm-sub">的中率 {sm["hit"]*100:.0f}%{ev_txt} / 本命の勝率 {top1p*100:.0f}% (基準 {t1*100:.0f}%) ・上位3頭の合計 {top3sum*100:.0f}% (基準 {th*100:.0f}%)</div></div>',
                    unsafe_allow_html=True)
        if trio_rule.get("test"):
            st.markdown(f'<div class="note">検証 (確率の高い順に{k}点・実際の払戻で答え合わせ): 2025年 回収率 {trio_rule["fit"]["roi"]*100:.0f}% 的中率 {trio_rule["fit"]["hit"]*100:.0f}% / '
                        f'2026年 回収率 {trio_rule["test"]["roi"]*100:.0f}% 的中率 {trio_rule["test"]["hit"]*100:.0f}%。当てにいく補助の買い方で、利益の柱は単勝。</div>',
                        unsafe_allow_html=True)
        show(trio)
        tickets["三連複"] = trio
    else:
        st.markdown(f'<div class="note">三連複: 本命の勝率 {top1p*100:.0f}% (基準 {t1*100:.0f}%)・上位3頭の合計 {top3sum*100:.0f}% (基準 {th*100:.0f}%)。'
                    'カチカチ (本命が圧倒的で相手も絞れている) ではないので出さない。</div>', unsafe_allow_html=True)
    plan.tickets = tickets
    total = sum(len(v) for v in tickets.values())
    st.markdown(f'<div class="note">合計 {total}点 (1点100円で {total*100:,}円)。同額で買う。賭け金を増やすと自分でオッズを下げて優位が消えるため 1点1,000円程度まで。緑枠は期待値1.0超。</div>',
                unsafe_allow_html=True)

    b1, b2 = st.columns(2, wrap=False)
    if res.get("saved"):
        b1.success(f"台帳に保存済み #{res['saved']}")
    elif b1.button("📒 台帳に保存", use_container_width=True):
        res["saved"] = ledger.save_prediction(race, plan, f"{est['engine']}/{mode}"); st.rerun()
    if b2.button("🔄 オッズ更新", use_container_width=True):
        load_odds.clear()
        odds = load_odds(race.race_id, int(time.time()))
        est, plan = run_prediction(race, odds, style)
        res.update(odds=odds, est=est, plan=plan, at=dt.datetime.now(JST).strftime("%H:%M")); st.rerun()

    # --- 印・勝率
    st.markdown('<div class="sec">印・推定勝率</div>', unsafe_allow_html=True)
    info = statmodel.horse_info(race) if statmodel.available() else {}
    rows = []
    for h in ranked:
        u = h.umaban
        p = plan.win_probs.get(u, 0)
        o = odds.get("win", {}).get(f"{u:02d}")
        hi = info.get(u, {})
        jn, jw = hi.get("jockey_form", (0, 0))
        rows.append({"印": plan.marks.get(u, ""), "馬番": u, "馬名": h.name, "勝率": f"{p*100:.1f}%",
                     "3着内": f"{plan.place_probs.get(u, 0)*100:.0f}%", "単勝": f"{o:.1f}" if o else "-",
                     "期待値": f"{p*o:.2f}" if o else "-",
                     "調教": f"{h.oik_rank} {h.oik_critic}".strip() or "-",
                     "騎手": h.jockey, "騎手60日": f"{jw}/{jn}" if jn else "-",
                     "父系統": hi.get("sire_line") or "-", "母父系統": hi.get("damsire_line") or "-",
                     "根拠": " / ".join(est["notes"].get(u, []))})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={"馬番": st.column_config.NumberColumn(width="small"), "印": st.column_config.TextColumn(width="small")})
    # --- コースの特徴 (過去データ)
    if statmodel.available() and race.surface in ("芝", "ダ"):
        import features as F
        prof = F.course_profile(statmodel.load()["stats"], race.venue, race.surface, race.distance, race.condition)
        def cell(v):
            r, n = v
            if r is None or n < 30:
                return "-"
            mark = " ◎" if r >= prof["base"] * 1.25 else " ×" if r <= prof["base"] * 0.75 else ""
            return f"{r*100:.1f}%{mark}" + (" (少)" if n < 150 else "")
        rows_c = [
            {"区分": "枠", **{k: cell(v) for k, v in prof["waku"].items()}},
        ]
        rows_s = [
            {"区分": f"{race.venue}{race.course}", **{k: cell(v) for k, v in prof["style"].items()}},
            {"区分": f"{race.surface} {'重・不良' if prof['wet'] == '重' else '良・稍重'}", **{k: cell(v) for k, v in prof["cond_style"].items()}},
        ]
        st.markdown(f'<div class="sec">コースの特徴 ({race.venue} {race.course}・馬場 {race.condition or "-"}・天候 {race.weather or "-"})</div>',
                    unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(rows_c), hide_index=True, use_container_width=True)
        st.dataframe(pd.DataFrame(rows_s), hide_index=True, use_container_width=True)
        st.markdown(f'<div class="note">過去レースでの勝率 (平均 {prof["base"]*100:.1f}%)。◎ = 平均の1.25倍以上で有利、× = 0.75倍以下で不利、(少) = 件数が少なく参考程度。'
                    '内枠 = 1〜3枠、中 = 4〜5枠、外 = 6〜8枠。脚質は各馬のふだんの脚質。</div>', unsafe_allow_html=True)

    if info.get("_form_asof"):
        st.markdown(f'<div class="note">調教 = 追い切り評価 (A〜D と短評)。騎手60日 = 直近60日の 勝利/騎乗 ({info["_form_asof"]} までのデータ)。系統は父・母父の父系。</div>',
                    unsafe_allow_html=True)
    if est["engine"] == "統計+市場補正":
        st.markdown('<div class="note">勝率は市場 (単勝オッズ) を土台に、馬柱・血統・騎手・時計・展開の統計で補正し、補正のずれを検証で決めた比率に縮めたもの。期待値 = 勝率 × 単勝オッズで、1.0 超は市場が過小評価していると判断した馬。</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="note">勝率は統計データのみから推定 (オッズ未発売のため市場補正なし)。期待値 = 勝率 × 単勝オッズ。</div>', unsafe_allow_html=True)

    with st.expander("出馬表・近5走・血統"):
        for h in race.horses:
            past = " ｜ ".join(
                f"{p['date'][5:]} {p['venue']}{p['surface']}{p['distance']} {p['finish']}着/{p['heads']}頭 {p['ninki']}人"
                + (f" ({p['margin']:+.1f})" if p.get('margin') is not None else "") for p in h.past[:5]) or "近走なし"
            st.markdown(
                f'{waku_html(h.waku)} **{h.umaban} {h.name}** <span class="note">{h.sex_age} {h.weight}kg {h.jockey} / {h.trainer}'
                f' / {h.body_weight or "馬体重未発表"} / {h.style or "-"} {h.interval}<br>父 {h.sire or "-"} 母父 {h.damsire or "-"}'
                f' / 調教 {(h.oik_rank + " " + h.oik_critic).strip() or "-"}</span>'
                f'<br><span class="note">{past}</span>', unsafe_allow_html=True)

    if date_str <= now.strftime("%Y%m%d"):
        try:
            result = load_result(race.race_id)
        except scraper.ScrapeError:
            result = None
        if result:
            st.markdown('<div class="sec">結果</div>', unsafe_allow_html=True)
            st.markdown(" → ".join(f"**{o['umaban']}** {o['name']} ({o['ninki']}人)" for o in result.order[:3]))
            pay = " / ".join(f"{k} {', '.join(f'{c} {a:,}円' for c, a in v)}" for k, v in result.payouts.items()
                             if k in ("単勝", "馬連", "ワイド", "三連複"))
            st.markdown(f'<div class="note">{pay}</div>', unsafe_allow_html=True)

# ---------------------------------------------------------------- 成績
st.divider()
with st.expander("📒 成績・答え合わせ (台帳)"):
    if st.button("✅ 結果を取り込んで答え合わせ", use_container_width=True):
        with st.spinner("結果を取得中…"):
            n = ledger.settle(scraper.fetch_result)
        st.success(f"{n}件を確定しました。")
    sm = ledger.summary()
    a, b = st.columns(2, wrap=False)
    a.metric("確定レース", f"{sm['races']}")
    b.metric("レース的中率", f"{sm['hit_rate']*100:.0f}%")
    c, d = st.columns(2, wrap=False)
    c.metric("回収率", f"{sm['roi']*100:.0f}%")
    d.metric("収支 (1点100円)", f"{sm['returned']-sm['invested']:+,}円")
    if sm["pending"]:
        st.caption(f"未確定 {sm['pending']}件")
    if sm["by_kind"]:
        kd = [{"券種": k, "点数": v["points"], "的中": v["hits"], "的中率": f"{v['hits']/v['points']*100:.0f}%",
               "回収率": f"{v['ret']/v['inv']*100:.0f}%" if v["inv"] else "-"} for k, v in sm["by_kind"].items()]
        st.dataframe(pd.DataFrame(kd), hide_index=True, use_container_width=True)
    recs = ledger.recent(30)
    if not recs:
        st.caption("まだ予想を保存していません。「台帳に保存」で記録できます。")
    for p in recs:
        status = ("的中 🎯" if p["hit"] else "不的中") if p["settled"] else "未確定"
        pl = f" {p['returned']-p['invested']:+,}円" if p["settled"] else ""
        st.markdown(f"**{p['race_date'][4:6]}/{p['race_date'][6:]} {p['venue']}{p['rno']}R {p['race_name']}** [{p['style']}] {status}{pl}")
        st.caption("　".join(f"{t['kind']}{'-'.join(map(str, t['combo']))}" + (f"→{t['payout']:,}円" if t.get("payout") else "")
                             for t in p["tickets"]))
    if recs:
        st.download_button("CSVで書き出す", ledger.export_csv().encode("utf-8-sig"), file_name="keiba_ledger.csv",
                           mime="text/csv", use_container_width=True)

if meta and meta.get("metrics"):
    mt = meta["metrics"]
    with st.expander("📈 モデルの検証成績"):
        st.caption(f"時系列検証: 2025年前半・後半・2026年をそれぞれ「それ以前のデータだけで学習」して予想 / 検証 {mt['valid_period'][0]}〜{mt['valid_period'][1]} ({mt['races']:,}レース)")
        st.markdown(f"- 本命の勝率: 統計モデル {mt['top1_hit_model']*100:.1f}% / 市場 (1番人気) {mt['top1_hit_market']*100:.1f}%\n"
                    f"- 本命を単勝で買い続けた回収率: 統計モデル {mt['top1_roi_model']*100:.0f}% / 1番人気 {mt['top1_roi_market']*100:.0f}%\n"
                    f"- 対数損失 (低いほど良い): 統計モデル {mt['logloss_model']:.3f} / 市場 {mt['logloss_market']:.3f}\n"
                    f"- 単勝の期待値買い (統計モデルの勝率×オッズ 1.1以上): {mt['tansho_model_bets']:,}点 的中率 {(mt['tansho_model_hit'] or 0)*100:.1f}% 回収率 {(mt['tansho_model_roi'] or 0)*100:.0f}%")
        vp = (statmodel.load() or {}).get("value_policy")
        if vp and vp.get("policy"):
            st.markdown(f"**収支プラス狙い (期待値買い)** 券種ごとの期待値の下限は{vp['fit_period'][0][:4]}年の予想で決め、{vp['test_period'][0][:4]}年で検証")
            st.markdown(f"- 選定期間 {vp['races_fit']:,}レース: {vp['fit']['bets']:,}点 的中率 {vp['fit']['hit_rate']*100:.0f}% 回収率 {vp['fit']['roi']*100:.0f}%")
            st.markdown(f"- 検証期間 {vp['races_test']:,}レース: {vp['test']['bets']:,}点 的中率 {vp['test']['hit_rate']*100:.0f}% 回収率 {vp['test']['roi']*100:.0f}% 収支 {vp['test']['profit']:+,}円")
            rows_ = [{"券種": k, "期待値の下限": v["threshold"], "検証 点数": vp["test_by_kind"][k]["bets"],
                      "的中率": f"{vp['test_by_kind'][k]['hit_rate']*100:.0f}%", "回収率": f"{vp['test_by_kind'][k]['roi']*100:.0f}%"}
                     for k, v in vp["policy"].items()]
            st.dataframe(pd.DataFrame(rows_), hide_index=True, use_container_width=True)
        elif vp:
            st.markdown("**収支プラス狙い**: 検証で回収率100%を超える券種・期待値の下限はありませんでした。")
            g = sorted(vp.get("grid", []), key=lambda r: -r["test"]["roi"])[:8]
            if g:
                st.dataframe(pd.DataFrame([{"券種": r["kind"], "期待値の下限": r["threshold"], "検証 点数": r["test"]["bets"],
                                            "的中率": f"{r['test']['hit_rate']*100:.1f}%", "回収率": f"{r['test']['roi']*100:.0f}%"} for r in g]),
                             hide_index=True, use_container_width=True)
        ns = (vp or {}).get("noskip")
        if ns:
            st.markdown(f"**見送り無し**: 全レースで買った中で最も損の少なかった買い方は「{ns['label']}」。"
                        f"検証 {ns['test']['races']:,}レース: {ns['test']['bets']:,}点 的中率 {ns['test']['hit_rate']*100:.1f}% 回収率 {ns['test']['roi']*100:.0f}% 収支 {ns['test']['profit']:+,}円 (1点100円)")
            g = sorted(ns.get("grid", []), key=lambda r: -r["test"]["roi"])[:8]
            st.dataframe(pd.DataFrame([{"買い方": r["label"], "検証 点数": r["test"]["bets"], "的中率": f"{r['test']['hit_rate']*100:.1f}%",
                                        "回収率": f"{r['test']['roi']*100:.0f}%"} for r in g]), hide_index=True, use_container_width=True)

st.caption("データ: netkeiba。予想は参考情報であり、購入は自己責任で。20歳未満の馬券購入は法律で禁止されています。")
