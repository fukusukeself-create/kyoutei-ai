"""中央競馬 AI予想 — Streamlit UI。

開催一覧 → レース選択 → 予想 (統計モデル + 任意で Gemini) → 買い目 → 台帳・成績。
スマホ幅 (380〜450px) を想定。
"""

from __future__ import annotations

import datetime as dt
import os
import time

import pandas as pd
import streamlit as st

import ledger
import model
import predictor
import scraper
import strategy

st.set_page_config(page_title="中央競馬 AI予想", page_icon="🏇", layout="centered",
                   initial_sidebar_state="collapsed")

JST = dt.timezone(dt.timedelta(hours=9))


def now_jst() -> dt.datetime:
    return dt.datetime.now(JST)


# ---------------------------------------------------------------- CSS
st.markdown("""
<style>
  .stApp { background: #f3f5f2; font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", Meiryo, sans-serif; }
  .block-container { padding: 0.6rem 0.6rem 3rem 0.6rem !important; max-width: 760px !important; }
  header[data-testid="stHeader"] { height: 0; background: transparent; }
  /* 列をスマホ幅でも横並びのまま縮める (wrap=False と併用) */
  [data-testid="stColumn"] { min-width: 0 !important; }
  .kb-top { background: #1b5e3a; color: #fff; padding: 10px 14px; border-radius: 8px; display: flex;
            justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .kb-top .t { font-size: 1.15rem; font-weight: 900; }
  .kb-top .d { font-size: 0.8rem; opacity: 0.9; }
  .kb-card { background: #fff; border: 1px solid #d9e2dc; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px;
             box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
  .kb-venue { font-weight: 900; font-size: 1.05rem; color: #1b5e3a; }
  .kb-sub { font-size: 0.78rem; color: #52605a; }
  .kb-race { display: flex; justify-content: space-between; align-items: center; padding: 6px 2px;
             border-bottom: 1px dashed #e2e8e4; font-size: 0.86rem; }
  .kb-race:last-child { border-bottom: none; }
  .kb-race .n { font-weight: 900; width: 2.6em; color: #1b5e3a; }
  .kb-race .nm { flex: 1; padding: 0 6px; }
  .kb-race .tm { color: #b91c1c; font-weight: 700; width: 3.6em; text-align: right; }
  .kb-race.done { color: #9aa39e; }
  .kb-race.done .n, .kb-race.done .tm { color: #9aa39e; }
  .kb-race.next { background: #fff7e6; border-radius: 4px; }
  .badge { display: inline-block; font-size: 0.68rem; font-weight: 800; padding: 1px 5px; border-radius: 3px;
           margin-left: 4px; color: #fff; background: #6b7280; vertical-align: middle; }
  .badge.g1 { background: #1d4ed8; } .badge.g2 { background: #b91c1c; } .badge.g3 { background: #15803d; }
  .badge.op { background: #7c3aed; }
  .kb-head { background: #1b5e3a; color: #fff; padding: 10px 12px; border-radius: 8px; margin-bottom: 8px; }
  .kb-head .r { font-size: 1.1rem; font-weight: 900; }
  .kb-head .c { font-size: 0.82rem; opacity: 0.92; }
  .tk { display: flex; justify-content: space-between; align-items: center; padding: 6px 8px; margin: 4px 0;
        background: #fff; border: 1px solid #d9e2dc; border-radius: 6px; font-size: 0.92rem; }
  .tk .cmb { font-weight: 900; font-size: 1.02rem; letter-spacing: 0.02em; }
  .tk .meta { font-size: 0.78rem; color: #52605a; text-align: right; }
  .tk.good { border-color: #16a34a; background: #f0fdf4; }
  .sec { font-weight: 900; color: #1b5e3a; margin: 10px 0 4px 0; font-size: 0.98rem; border-left: 4px solid #1b5e3a; padding-left: 6px; }
  .ai-box { background: #fff; border: 1px solid #d9e2dc; border-left: 4px solid #7c3aed; border-radius: 6px;
            padding: 8px 10px; margin: 6px 0; font-size: 0.88rem; }
  .ai-box b { color: #7c3aed; }
  .note { font-size: 0.78rem; color: #6b7280; }
  .waku1{background:#fff;color:#000;border:1px solid #999} .waku2{background:#222;color:#fff} .waku3{background:#dc2626;color:#fff}
  .waku4{background:#2563eb;color:#fff} .waku5{background:#facc15;color:#000} .waku6{background:#16a34a;color:#fff}
  .waku7{background:#f97316;color:#fff} .waku8{background:#f9a8d4;color:#000}
  .wk { display:inline-block; width:1.5em; text-align:center; border-radius:3px; font-weight:800; font-size:0.8rem; }
  div[data-testid="stButton"] > button { border-radius: 6px; font-weight: 700; }
  div[data-testid="stMetric"] { background: #fff; border: 1px solid #d9e2dc; border-radius: 8px; padding: 6px 10px; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- データ取得 (キャッシュ)
@st.cache_data(ttl=300, show_spinner=False)
def cached_meetings(date: str):
    return scraper.fetch_meetings(date)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_kaisai_dates(year: int, month: int):
    try:
        return scraper.fetch_kaisai_dates(year, month)
    except scraper.ScrapeError:
        return []


@st.cache_data(ttl=600, show_spinner=False)
def cached_race(race_id: str, date: str):
    race = scraper.fetch_shutuba(race_id, date)
    try:
        scraper.attach_past(race, scraper.fetch_past(race_id))
    except scraper.ScrapeError:
        pass
    return race


@st.cache_data(ttl=60, show_spinner=False)
def cached_odds(race_id: str, _bucket: int):
    return scraper.fetch_all_odds(race_id)


@st.cache_data(ttl=600, show_spinner=False)
def cached_result(race_id: str):
    return scraper.fetch_result(race_id)


def next_kaisai_date(today: dt.date) -> str | None:
    """今日以降で最初の開催日 (今月・来月のカレンダーから)。"""
    for add in (0, 1):
        y, m = today.year, today.month + add
        if m > 12:
            y, m = y + 1, 1
        for d in cached_kaisai_dates(y, m):
            if d >= today.strftime("%Y%m%d"):
                return d
    return None


def grade_badge(grade: str) -> str:
    if not grade:
        return ""
    cls = {"GI": "g1", "GII": "g2", "GIII": "g3", "OP": "op", "L": "op"}.get(grade, "")
    return f'<span class="badge {cls}">{grade}</span>'


def waku_html(waku: int) -> str:
    return f'<span class="wk waku{waku}">{waku}</span>'


# ---------------------------------------------------------------- 状態
ss = st.session_state
ss.setdefault("tab", "開催一覧")
ss.setdefault("date", now_jst().strftime("%Y%m%d"))
ss.setdefault("race_id", None)
ss.setdefault("plan", None)
ss.setdefault("est", None)
ss.setdefault("ai", None)
ss.setdefault("ai_error", "")
ss.setdefault("saved_id", None)

def get_api_key() -> str:
    """環境変数 → Streamlit secrets の順。secrets.toml が無い環境では st.secrets が例外を投げる。"""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    try:
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return ""


api_key = get_api_key()

# ---------------------------------------------------------------- ヘッダー
today = now_jst()
st.markdown(f"""
<div class="kb-top">
  <div class="t">🏇 中央競馬 AI予想</div>
  <div class="d">{today.strftime('%Y/%m/%d %H:%M')} JST</div>
</div>
""", unsafe_allow_html=True)

c1, c2, c3 = st.columns([1, 1, 1], wrap=False)
if c1.button("📅 開催一覧", type="primary" if ss.tab == "開催一覧" else "secondary", use_container_width=True):
    ss.tab = "開催一覧"; st.rerun()
if c2.button("🎯 予想", type="primary" if ss.tab == "予想" else "secondary", use_container_width=True):
    ss.tab = "予想"; st.rerun()
if c3.button("📒 成績", type="primary" if ss.tab == "成績" else "secondary", use_container_width=True):
    ss.tab = "成績"; st.rerun()


def go_race(race_id: str, date: str):
    if ss.race_id != race_id:
        ss.plan = ss.est = ss.ai = None
        ss.ai_error = ""
        ss.saved_id = None
    ss.race_id, ss.date, ss.tab = race_id, date, "予想"
    st.rerun()


# ================================================================ 画面1: 開催一覧
if ss.tab == "開催一覧":
    d_default = dt.datetime.strptime(ss.date, "%Y%m%d").date()
    picked = st.date_input("開催日", value=d_default, format="YYYY/MM/DD")
    date = picked.strftime("%Y%m%d")
    ss.date = date
    with st.spinner("開催情報を取得中..."):
        try:
            meetings = cached_meetings(date)
        except scraper.ScrapeError as e:
            meetings = []
            st.error(str(e))
    if not meetings:
        nxt = next_kaisai_date(picked)
        if nxt and nxt != date:
            nd = dt.datetime.strptime(nxt, "%Y%m%d")
            st.info(f"{picked.strftime('%m/%d')} は中央競馬の開催がありません。次の開催は {nd.strftime('%m/%d (%a)')} です。")
            if st.button(f"➡ {nd.strftime('%m/%d')} の開催を見る", use_container_width=True):
                ss.date = nxt; st.rerun()
        else:
            st.info("開催情報が見つかりませんでした。")
    now_hm = today.strftime("%H:%M") if date == today.strftime("%Y%m%d") else ("99:99" if date < today.strftime("%Y%m%d") else "00:00")
    for m in meetings:
        cond = " / ".join(x for x in [f"天候 {m.weather}" if m.weather else "", f"芝 {m.turf}" if m.turf else "", f"ダ {m.dirt}" if m.dirt else ""] if x)
        st.markdown(f'<div class="kb-card"><div class="kb-venue">{m.venue} <span class="kb-sub">{m.kai} {m.day}</span></div>'
                    f'<div class="kb-sub">{cond}</div></div>', unsafe_allow_html=True)
        next_found = False
        for r in m.races:
            done = r.start and r.start < now_hm
            is_next = (not done) and (not next_found) and date == today.strftime("%Y%m%d")
            if is_next:
                next_found = True
            cls = "done" if done else ("next" if is_next else "")
            cc1, cc2 = st.columns([4.2, 1], wrap=False, vertical_alignment="center")
            cc1.markdown(f'<div class="kb-race {cls}"><span class="n">{r.rno}R</span>'
                         f'<span class="nm">{r.name}{grade_badge(r.grade)}<br><span class="kb-sub">{r.course} {r.heads}頭</span></span>'
                         f'<span class="tm">{r.start}</span></div>', unsafe_allow_html=True)
            if cc2.button("予想", key=f"go_{r.race_id}", type="primary" if is_next else "secondary", use_container_width=True):
                go_race(r.race_id, date)

# ================================================================ 画面2: 予想
elif ss.tab == "予想":
    if not ss.race_id:
        st.info("開催一覧からレースを選んでください。")
        st.stop()
    try:
        with st.spinner("出馬表・馬柱を取得中..."):
            race = cached_race(ss.race_id, ss.date)
    except scraper.ScrapeError as e:
        st.error(str(e))
        st.stop()
    bucket = int(time.time() // 60)
    odds = cached_odds(race.race_id, bucket)
    has_odds = bool(odds.get("win"))

    st.markdown(f"""
    <div class="kb-head">
      <div class="r">{race.venue} {race.rno}R {race.name}{grade_badge(race.grade)}</div>
      <div class="c">{race.start}発走 / {race.course} ({race.turn}) / {race.cls} / {race.heads}頭<br>
      天候 {race.weather or '-'} / 馬場 {race.condition or '-'} {'/ オッズ取得済' if has_odds else '/ オッズ未発売 (モデルのみで予想)'}</div>
    </div>
    """, unsafe_allow_html=True)

    # レース移動
    meetings = cached_meetings(ss.date)
    all_races = [(m.venue, r) for m in meetings for r in m.races]
    idx = next((i for i, (_, r) in enumerate(all_races) if r.race_id == race.race_id), None)
    if idx is not None:
        labels = [f"{v} {r.rno}R {r.name}" for v, r in all_races]
        sel = st.selectbox("レース", labels, index=idx, label_visibility="collapsed")
        if labels.index(sel) != idx:
            go_race(all_races[labels.index(sel)][1].race_id, ss.date)

    s1, s2 = st.columns([1.3, 1], wrap=False, vertical_alignment="center")
    style = s1.selectbox("スタイル", list(strategy.STYLES), index=1, label_visibility="collapsed")
    use_ai = s2.toggle("AI併用", value=bool(api_key), help="Gemini に馬柱を読ませて評価を上乗せする")

    if st.button("🔮 予想する", type="primary", use_container_width=True):
        ss.saved_id = None
        ss.ai_error = ""
        est = model.estimate(race, odds.get("win"))
        ai = None
        if use_ai:
            if not api_key:
                ss.ai_error = "GEMINI_API_KEY が未設定のため、モデルのみで予想しました。"
            else:
                with st.spinner("🤖 Gemini が馬柱と展開を読んでいます..."):
                    try:
                        ai = predictor.analyze(api_key, race, est["model_probs"])
                        est = model.estimate(race, odds.get("win"), ai["ratings"])
                    except predictor.PredictionError as e:
                        ss.ai_error = f"AI評価は取得できませんでした: {e}"
        ss.est, ss.ai = est, ai
        ss.plan = strategy.build_plan(est["probs"], odds, style)
        ss.plan_race_id = race.race_id

    if ss.plan and getattr(ss, "plan_race_id", None) == race.race_id:
        plan: strategy.Plan = ss.plan
        est = ss.est
        ai = ss.ai
        if ss.ai_error:
            st.warning(ss.ai_error)
        if plan.style != style:
            plan = strategy.build_plan(est["probs"], odds, style)
            ss.plan = plan

        # --- 印と勝率
        st.markdown('<div class="sec">印・推定勝率</div>', unsafe_allow_html=True)
        rows = []
        for h in sorted(race.horses, key=lambda h: -plan.win_probs.get(h.umaban, 0)):
            u = h.umaban
            p = plan.win_probs.get(u, 0)
            o = odds.get("win", {}).get(f"{u:02d}")
            rows.append({
                "印": plan.marks.get(u, ""), "馬番": u, "馬名": h.name,
                "勝率": f"{p*100:.1f}%", "単勝": f"{o:.1f}" if o else "-", "期待値": f"{p*o:.2f}" if o else "-",
                "AI": "★" * (ai["ratings"].get(u, 0)) if ai else "",
                "騎手": h.jockey, "3着内": f"{plan.place_probs.get(u, 0)*100:.0f}%",
                "根拠": " / ".join(est["notes"].get(u, [])),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"馬番": st.column_config.NumberColumn(width="small"),
                                    "印": st.column_config.TextColumn(width="small")})
        st.markdown('<div class="note">勝率 = 馬柱レーティングと単勝オッズ (市場) の混合。期待値 = 勝率×単勝オッズ (1.0 超で市場より妙味)。</div>',
                    unsafe_allow_html=True)

        # --- AI 見解
        if ai:
            st.markdown('<div class="sec">AIの見解</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="ai-box"><b>ペース</b> {ai["pace"]}<br><b>展開</b> {ai["scenario"]}<br>'
                        f'<b>ポイント</b> {ai["key_point"]}<br><b>危険な人気馬</b> {ai["danger"]}</div>', unsafe_allow_html=True)
            with st.expander("馬別コメント"):
                for h in sorted(race.horses, key=lambda h: -ai["ratings"].get(h.umaban, 0)):
                    c = ai["comments"].get(h.umaban)
                    if c:
                        st.markdown(f'{waku_html(h.waku)} **{h.umaban} {h.name}** {"★"*ai["ratings"].get(h.umaban,0)}  \n{c}',
                                    unsafe_allow_html=True)

        # --- 買い目
        st.markdown(f'<div class="sec">買い目 ({plan.style})</div>', unsafe_allow_html=True)
        total_pts = 0
        for kind in ("単勝", "馬連", "ワイド", "三連複"):
            ts = plan.tickets.get(kind, [])
            if not ts:
                if kind == "単勝":
                    st.markdown('<div class="note">単勝: 期待値1.1倍以上の馬なし (見送り)</div>', unsafe_allow_html=True)
                continue
            sm = strategy.summarize(ts)
            total_pts += sm["points"]
            ev_txt = f" / 期待回収率 {sm['ev']*100:.0f}%" if sm["ev"] else ""
            st.markdown(f'<div class="note"><b>{kind}</b> {sm["points"]}点 / 的中率 {sm["hit"]*100:.0f}%{ev_txt}</div>',
                        unsafe_allow_html=True)
            for t in ts:
                good = " good" if (t.ev or 0) >= 1.0 else ""
                meta = f"{t.prob*100:.1f}%" + (f" / {t.odds:.1f}倍 / EV {t.ev:.2f}" if t.odds else " / オッズ未発売")
                st.markdown(f'<div class="tk{good}"><span class="cmb">{kind} {t.label}</span><span class="meta">{meta}</span></div>',
                            unsafe_allow_html=True)
        st.markdown(f'<div class="note">合計 {total_pts}点 (1点100円なら {total_pts*100:,}円)。控除率20〜27.5%のため、期待回収率100%超の買い目だけが理論上のプラス。</div>',
                    unsafe_allow_html=True)

        b1, b2 = st.columns(2, wrap=False)
        if ss.saved_id:
            b1.success(f"台帳に保存済み (#{ss.saved_id})")
        elif b1.button("📒 台帳に保存", use_container_width=True):
            ss.saved_id = ledger.save_prediction(race, plan, ai["model"] if ai else "")
            st.rerun()
        if b2.button("🔄 オッズを更新して組み直す", use_container_width=True):
            cached_odds.clear()
            odds = cached_odds(race.race_id, int(time.time()))
            est = model.estimate(race, odds.get("win"), ai["ratings"] if ai else None)
            ss.est = est
            ss.plan = strategy.build_plan(est["probs"], odds, style)
            st.rerun()

    # --- 出馬表・馬柱
    with st.expander("出馬表・近5走", expanded=not bool(ss.plan)):
        for h in race.horses:
            past = " ｜ ".join(
                f"{p['date'][5:]} {p['venue']}{p['surface']}{p['distance']} {p['finish']}着/{p['heads']}頭 {p['ninki']}人"
                + (f" ({p['margin']:+.1f})" if p.get('margin') is not None else "")
                for p in h.past[:5]) or "近走なし"
            st.markdown(
                f'{waku_html(h.waku)} **{h.umaban} {h.name}** <span class="note">{h.sex_age} {h.weight}kg {h.jockey} / {h.trainer}'
                f' / {h.body_weight or "馬体重未発表"} / {h.style or "-"} {h.interval}</span><br><span class="note">{past}</span>',
                unsafe_allow_html=True)

    # --- 結果 (確定後)
    res = None
    if ss.date <= today.strftime("%Y%m%d"):
        try:
            res = cached_result(race.race_id)
        except scraper.ScrapeError:
            res = None
    if res:
        st.markdown('<div class="sec">結果</div>', unsafe_allow_html=True)
        st.markdown(" → ".join(f"**{o['umaban']}** {o['name']} ({o['ninki']}人)" for o in res.order[:3]))
        pay = " / ".join(f"{k} {', '.join(f'{c} {a:,}円' for c, a in v)}" for k, v in res.payouts.items()
                         if k in ("単勝", "馬連", "ワイド", "三連複"))
        st.markdown(f'<div class="note">{pay}</div>', unsafe_allow_html=True)

# ================================================================ 画面3: 成績
elif ss.tab == "成績":
    if st.button("✅ 結果を取り込んで答え合わせ", use_container_width=True):
        with st.spinner("結果を取得中..."):
            n = ledger.settle(scraper.fetch_result)
        st.success(f"{n}件を確定しました。")
    sm = ledger.summary()
    m1, m2 = st.columns(2, wrap=False)
    m1.metric("確定レース", f"{sm['races']}")
    m2.metric("的中率", f"{sm['hit_rate']*100:.0f}%")
    m3, m4 = st.columns(2, wrap=False)
    m3.metric("回収率", f"{sm['roi']*100:.0f}%")
    m4.metric("収支", f"{sm['returned']-sm['invested']:+,}円")
    if sm["pending"]:
        st.caption(f"未確定 {sm['pending']}件")
    if sm["by_kind"]:
        kd = [{"券種": k, "点数": v["points"], "的中": v["hits"], "的中率": f"{v['hits']/v['points']*100:.0f}%",
               "回収率": f"{v['ret']/v['inv']*100:.0f}%" if v["inv"] else "-"} for k, v in sm["by_kind"].items()]
        st.dataframe(pd.DataFrame(kd), hide_index=True, use_container_width=True)
    recs = ledger.recent(50)
    if not recs:
        st.info("まだ予想を保存していません。予想画面の「台帳に保存」で記録できます。")
    for p in recs:
        status = ("的中 🎯" if p["hit"] else "不的中") if p["settled"] else "未確定"
        pl = f" {p['returned']-p['invested']:+,}円" if p["settled"] else ""
        with st.expander(f"{p['race_date'][4:6]}/{p['race_date'][6:]} {p['venue']}{p['rno']}R {p['race_name']} [{p['style']}] {status}{pl}"):
            for t in p["tickets"]:
                hit = f" → {t['payout']:,}円" if t.get("payout") else ""
                st.markdown(f"- {t['kind']} **{'-'.join(map(str, t['combo']))}** ({t['prob']*100:.1f}%"
                            + (f" / {t['odds']}倍" if t.get("odds") else "") + f"){hit}")
            if p["result"]:
                st.caption(f"結果: {'-'.join(map(str, p['result']['top3']))}")
            if st.button("削除", key=f"del_{p['id']}"):
                ledger.delete_prediction(p["id"]); st.rerun()
    if recs:
        st.download_button("CSVで書き出す", ledger.export_csv().encode("utf-8-sig"),
                           file_name="keiba_ledger.csv", mime="text/csv", use_container_width=True)

st.markdown('<div class="note" style="margin-top:18px;">データ: netkeiba (出馬表・馬柱・オッズ・結果)。予想は参考情報であり、馬券の購入は自己責任で。20歳未満の馬券購入は法律で禁止されています。</div>',
            unsafe_allow_html=True)
