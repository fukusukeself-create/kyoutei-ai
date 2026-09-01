import streamlit as st
import requests
from bs4 import BeautifulSoup
import datetime
import os
import re
from google import genai

# ----------------------------------------------------
# ページ基本設定
# ----------------------------------------------------
st.set_page_config(
    page_title="BOAT RACE",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ----------------------------------------------------
# 日本標準時 (JST)
# ----------------------------------------------------
JST = datetime.timezone(datetime.timedelta(hours=9))
now_jst = datetime.datetime.now(JST)
today_jst_str = now_jst.strftime("%Y%m%d")

# ----------------------------------------------------
# 公式アプリ 完全再現CSS
# ----------------------------------------------------
st.markdown("""
<style>
    .stApp {
        background-color: #f1f3f7;
        font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", Meiryo, sans-serif;
    }
    .block-container {
        padding: 0.2rem 0.3rem 2rem 0.3rem !important;
        max-width: 100% !important;
    }

    /* 最上部 公式ブルーヘッダー */
    .br-top-bar {
        background: #0066cc;
        color: white;
        padding: 8px 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 4px;
        margin-bottom: 4px;
    }
    .br-logo-title {
        font-size: 1.2rem;
        font-weight: 900;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .br-sub-links {
        font-size: 0.75rem;
        font-weight: bold;
        display: flex;
        gap: 10px;
    }

    /* 開催中カード: ナイター (蒲郡・住之江・下関・若松) */
    .card-night-box {
        border-radius: 6px;
        border: 1.5px solid #6366f1;
        overflow: hidden;
        background: #ffffff;
        margin-bottom: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .card-night-head {
        background: linear-gradient(180deg, #c7d2fe 0%, #a5b4fc 100%);
        padding: 3px 4px;
        font-weight: 900;
        font-size: 0.88rem;
        color: #1e1b4b;
        text-align: center;
    }

    /* 開催中カード: 大村専用 */
    .card-omura-box {
        border-radius: 6px;
        border: 1.5px solid #0284c7;
        overflow: hidden;
        background: #ffffff;
        margin-bottom: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .card-omura-head {
        background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
        padding: 3px 4px;
        font-weight: 900;
        font-size: 0.88rem;
        color: white;
        text-align: center;
        text-shadow: 0 1px 2px rgba(0,0,0,0.3);
    }

    /* 発売終了カード */
    .card-closed-box {
        border-radius: 6px;
        border: 1px solid #94a3b8;
        overflow: hidden;
        background: #ffffff;
        margin-bottom: 4px;
    }
    .card-closed-head {
        background: #94a3b8;
        padding: 3px 4px;
        font-weight: 800;
        font-size: 0.82rem;
        color: #0f172a;
        text-align: center;
    }
    .card-closed-body {
        background: #ffffff;
        color: #334155;
        font-weight: 800;
        font-size: 0.8rem;
        text-align: center;
        padding: 4px 2px;
    }

    /* 非開催カード (--) */
    .card-none-box {
        border-radius: 6px;
        background: #d8dde6;
        border: 1px solid #cbd5e1;
        text-align: center;
        padding: 10px 2px;
        margin-bottom: 4px;
        color: #475569;
        font-weight: bold;
        font-size: 0.85rem;
    }

    /* G3バッジ */
    .badge-g3 {
        background-color: #0066cc;
        color: white;
        font-size: 0.65rem;
        padding: 1px 3px;
        border-radius: 2px;
        font-weight: bold;
        margin-right: 2px;
    }

    /* 締切順リストカード */
    .dl-row {
        border-radius: 6px;
        border: 1.5px solid #0066cc;
        margin-bottom: 6px;
        overflow: hidden;
        background: #ffffff;
        display: flex;
        align-items: center;
    }
    .dl-row-urgent {
        border: 2px solid #ef4444;
        background: #fff1f2;
    }
    .dl-left-part {
        width: 30%;
        padding: 8px 4px;
        font-weight: 900;
        font-size: 0.9rem;
        text-align: center;
        border-right: 1px solid #cbd5e1;
    }
    .dl-right-part {
        width: 70%;
        padding: 6px 8px;
    }

    /* ボタン共通 */
    div[data-testid="stButton"] > button {
        width: 100%;
        border-radius: 4px;
        padding: 2px 4px;
        min-height: 26px;
        font-size: 0.8rem;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 24場 定義データ
# ----------------------------------------------------
VENUES = [
    {"code": "01", "name": "桐生", "type": "ナイター"},
    {"code": "02", "name": "戸田", "type": "デイ"},
    {"code": "03", "name": "江戸川", "type": "デイ"},
    {"code": "04", "name": "平和島", "type": "デイ"},
    {"code": "05", "name": "多摩川", "type": "デイ"},
    {"code": "06", "name": "浜名湖", "type": "デイ"},
    {"code": "07", "name": "蒲郡", "type": "ナイター"},
    {"code": "08", "name": "常滑", "type": "デイ"},
    {"code": "09", "name": "津", "type": "デイ"},
    {"code": "10", "name": "三国", "type": "モーニング"},
    {"code": "11", "name": "びわこ", "type": "デイ"},
    {"code": "12", "name": "住之江", "type": "ナイター"},
    {"code": "13", "name": "尼崎", "type": "デイ"},
    {"code": "14", "name": "鳴門", "type": "モーニング"},
    {"code": "15", "name": "丸亀", "type": "ナイター"},
    {"code": "16", "name": "児島", "type": "デイ"},
    {"code": "17", "name": "宮島", "type": "デイ"},
    {"code": "18", "name": "徳山", "type": "モーニング"},
    {"code": "19", "name": "下関", "type": "ナイター"},
    {"code": "20", "name": "若松", "type": "ナイター"},
    {"code": "21", "name": "芦屋", "type": "モーニング"},
    {"code": "22", "name": "福岡", "type": "デイ"},
    {"code": "23", "name": "唐津", "type": "モーニング"},
    {"code": "24", "name": "大村", "type": "ナイター"},
]

# ----------------------------------------------------
# セッションとブラウザ完全偽装ヘッダー
# ----------------------------------------------------
session = requests.Session()
CUSTOM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,ja-JP;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.boatrace.jp/",
    "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# ----------------------------------------------------
# データ取得モジュール
# ----------------------------------------------------
def fetch_all_venues_status(hd: str):
    """当日の開催情報・締切情報を安定取得"""
    url = f"https://www.boatrace.jp/owpc/pc/race/index?hd={hd}"
    venue_status = {}
    for v in VENUES:
        venue_status[v["code"]] = {
            "name": v["name"],
            "type": v["type"],
            "status": "none",
            "grade": "",
            "day_text": "",
            "next_rno": 1,
            "deadline": "",
            "race_name": "予選"
        }
    deadline_list = []

    try:
        res = session.get(url, headers=CUSTOM_HEADERS, timeout=8)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        # 全てのテーブル行をチェック
        for tr in soup.find_all("tr"):
            row_html = str(tr)
            row_text = " ".join(tr.get_text().split())
            
            for v in VENUES:
                code = v["code"]
                if f"jcd={code}" in row_html:
                    # グレード
                    grade = ""
                    if "SG" in row_text: grade = "SG"
                    elif "G1" in row_text or "GI" in row_text: grade = "G1"
                    elif "G2" in row_text or "GII" in row_text: grade = "G2"
                    elif "G3" in row_text or "GIII" in row_text: grade = "G3"

                    # 日程
                    day_m = re.search(r"(初日|\d+日目|最終日)", row_text)
                    day_text = day_m.group(1) if day_m else "一般"

                    # 締切時刻
                    time_m = re.findall(r"(\d{1,2}:\d{2})", row_text)
                    rno_m = re.findall(r"(\d{1,2})R", row_text)

                    if time_m:
                        dtime = time_m[0]
                        rno = int(rno_m[0]) if rno_m else 11
                        rname_m = re.search(rf"{rno}R\s*([^\d\s]{{2,8}})", row_text)
                        rname = rname_m.group(1) if rname_m else "予選"

                        venue_status[code]["status"] = "active"
                        venue_status[code]["grade"] = grade
                        venue_status[code]["day_text"] = day_text
                        venue_status[code]["next_rno"] = rno
                        venue_status[code]["deadline"] = dtime
                        venue_status[code]["race_name"] = rname

                        if not any(d["jcd"] == code for d in deadline_list):
                            deadline_list.append({
                                "jcd": code,
                                "name": venue_status[code]["name"],
                                "rno": rno,
                                "deadline": dtime,
                                "type": v["type"],
                                "grade": grade,
                                "day_text": day_text,
                                "race_name": rname
                            })
                    elif ("発売終了" in row_text) or ("終了" in row_text):
                        if venue_status[code]["status"] != "active":
                            venue_status[code]["status"] = "closed"
                            venue_status[code]["grade"] = grade
                            venue_status[code]["day_text"] = day_text

        deadline_list.sort(key=lambda x: x["deadline"])
    except Exception:
        pass

    return venue_status, deadline_list

def fetch_racelist(jcd: str, rno: int, hd: str):
    url = f"https://www.boatrace.jp/owpc/pc/race/racelist?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = session.get(url, headers=CUSTOM_HEADERS, timeout=8)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        table = soup.find("div", class_="table1")
        if not table:
            return None, "出走表が見つかりませんでした。"
        rows = table.find_all("tbody")
        racers = []
        for i, row in enumerate(rows[:6], 1):
            racers.append(f"【{i}号艇】: " + " ".join(row.get_text().split()))
        return "\n".join(racers), None
    except Exception as e:
        return None, f"出走表取得エラー: {str(e)}"

def fetch_beforeinfo(jcd: str, rno: int, hd: str):
    url = f"https://www.boatrace.jp/owpc/pc/race/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = session.get(url, headers=CUSTOM_HEADERS, timeout=8)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        weather_box = soup.find("div", class_="weather1")
        weather_text = " ".join(weather_box.get_text().split()) if weather_box else "気象情報なし"
        tables = soup.find_all("div", class_="table1")
        info_text = "".join(["\n" + " ".join(t.get_text().split()) for t in tables])
        if not info_text:
            return None, "直前情報がまだ公開されていません。"
        return f"【水面・気象】: {weather_text}\n【直前展示データ】:\n{info_text}", None
    except Exception as e:
        return None, f"直前情報取得エラー: {str(e)}"

# ----------------------------------------------------
# Gemini AI 予想エンジン (gemini-3.6-flash)
# ----------------------------------------------------
def analyze_with_gemini(api_key: str, venue_name: str, rno: int, racelist_data: str, before_data: str, focus_type: str):
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたは競艇の回収率最大化を追求するプロ予想AIです。
以下の出走表、直前情報（展示タイム・スタート展示進入・気象風速・チルト）を徹底分析し、
指定スタンス【{focus_type}】に最適な【三連単フォーメーション】を算出してください。

### 対象レース
- 開催場: ボートレース{venue_name} 第{rno}R
- スタンス: {focus_type}

### 取得データ
【出走表】:
{racelist_data}

【直前情報（展示・気象）】:
{before_data}

---
### 出力フォーマット（Markdown形式）

## 1. 進入隊形予想 & 1マーク展開シミュレーション
- スリット隊形予想（例: 123/456）とイン逃げ信頼度（S/A/B/C）
- 展開の攻め手となる艇と仕掛けシナリオ

## 2. 展示タイム・機力評価
- 各艇の伸び足・回り足・出足のハイライト

## 3. 🎯 厳選 三連単フォーメーション買い目

### 【本線フォーメーション】（4〜8点）
- 買い目: `1 - 2,3 - 2,3,4`
- 推奨資金配分比率（合計100%）

### 【抑え / 穴狙いフォーメーション】（2〜4点）
- 買い目: `3 - 1,4 - 1,4,5`
- 狙う理由

## 4. 💡 勝負の決め手
"""
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    return response.text

# ----------------------------------------------------
# 状態管理
# ----------------------------------------------------
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "開催一覧"
if "selected_jcd" not in st.session_state:
    st.session_state.selected_jcd = "12"
if "selected_rno" not in st.session_state:
    st.session_state.selected_rno = 11
if "racelist_data" not in st.session_state:
    st.session_state.racelist_data = ""
if "before_data" not in st.session_state:
    st.session_state.before_data = ""
if "prediction_result" not in st.session_state:
    st.session_state.prediction_result = ""

api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))

# ----------------------------------------------------
# 公式ヘッダー
# ----------------------------------------------------
st.markdown("""
<div class="br-top-bar">
    <div class="br-logo-title">🚤 BOAT RACE <span style="font-size:1.05rem; font-weight:normal; margin-left:4px;">トップ</span></div>
    <div class="br-sub-links">
        <span>📍 ピン留め</span>
        <span>💳 入金</span>
        <span>📋 マイページ</span>
    </div>
</div>
""", unsafe_allow_html=True)

# 開催状況データを取得
venue_status, deadline_list = fetch_all_venues_status(today_jst_str)

# ナビゲーションタブ
c_nav1, c_nav2, c_nav3 = st.columns([1, 1, 1.2])
with c_nav1:
    if st.button("🚩 開催一覧", type="primary" if st.session_state.active_tab == "開催一覧" else "secondary", use_container_width=True):
        st.session_state.active_tab = "開催一覧"
        st.rerun()
with c_nav2:
    if st.button("⏰ 締切順", type="primary" if st.session_state.active_tab == "締切順" else "secondary", use_container_width=True):
        st.session_state.active_tab = "締切順"
        st.rerun()
with c_nav3:
    if st.button("🎯 AIフォーメーション予想", type="primary" if st.session_state.active_tab == "AI予想" else "secondary", use_container_width=True):
        st.session_state.active_tab = "AI予想"
        st.rerun()

# ----------------------------------------------------
# 画面1: 開催一覧
# ----------------------------------------------------
if st.session_state.active_tab == "開催一覧":
    cols = st.columns(4)
    for idx, v in enumerate(VENUES):
        col = cols[idx % 4]
        code = v["code"]
        stat = venue_status.get(code, {})
        
        # 開催中
        if stat.get("status") == "active":
            box_cls = "card-omura-box" if code == "24" else "card-night-box"
            head_cls = "card-omura-head" if code == "24" else "card-night-head"
            icon = "🌟 " if code == "24" else ("🌙 " if v["type"] == "ナイター" else "")
            badge = f'<span class="badge-g3">{stat["grade"]}</span>' if stat.get("grade") else ""
            
            card_html = f"""
            <div class="{box_cls}">
                <div class="{head_cls}">
                    {badge}{icon}{v['name']}
                </div>
                <div style="text-align:center; padding:3px 2px; background:white;">
                    <div style="font-size:0.75rem; color:#475569;">{stat['day_text']}</div>
                    <div style="font-weight:900; color:#dc2626; font-size:0.88rem;">{stat['next_rno']}R {stat['deadline']}</div>
                </div>
            </div>
            """
            col.markdown(card_html, unsafe_allow_html=True)
            if col.button("選択", key=f"btn_act_{code}", use_container_width=True):
                st.session_state.selected_jcd = code
                st.session_state.selected_rno = stat["next_rno"]
                st.session_state.active_tab = "AI予想"
                st.rerun()

        # 発売終了
        elif stat.get("status") == "closed":
            badge = f'<span class="badge-g3">{stat["grade"]}</span>' if stat.get("grade") else ""
            card_html = f"""
            <div class="card-closed-box">
                <div class="card-closed-head">
                    {badge}{v['name']} {stat['day_text']}
                </div>
                <div class="card-closed-body">
                    発売終了
                </div>
            </div>
            """
            col.markdown(card_html, unsafe_allow_html=True)
            if col.button("終了", key=f"btn_cls_{code}", disabled=True, use_container_width=True):
                pass

        # 非開催
        else:
            card_html = f"""
            <div class="card-none-box">
                <div>{v['name']}</div>
                <div style="margin-top:2px;">--</div>
            </div>
            """
            col.markdown(card_html, unsafe_allow_html=True)
            if col.button("--", key=f"btn_none_{code}", disabled=True, use_container_width=True):
                pass

# ----------------------------------------------------
# 画面2: 締切順一覧
# ----------------------------------------------------
elif st.session_state.active_tab == "締切順":
    if deadline_list:
        for idx, item in enumerate(deadline_list):
            is_first = (idx == 0)
            urgent_cls = "dl-row-urgent" if is_first else ""
            left_bg = "background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%); color:white;" if item["jcd"] == "24" else "background: #c7d2fe; color:#1e1b4b;"
            badge_html = f'<span class="badge-g3">{item["grade"]}</span>' if item.get("grade") else ""
            
            c_card, c_btn = st.columns([3.5, 1])
            with c_card:
                st.markdown(f"""
                <div class="dl-row {urgent_cls}">
                    <div class="dl-left-part" style="{left_bg}">
                        <div>{badge_html}{item['name']} 🌙</div>
                        <div style="font-size:0.75rem; font-weight:normal;">{item['day_text']}</div>
                    </div>
                    <div class="dl-right-part">
                        <div style="font-weight:900; font-size:0.98rem; color:#0f172a;">{item['rno']}R {item['race_name']}</div>
                        <div style="font-weight:900; color:#dc2626; font-size:0.9rem;">締切予定時刻 {item['deadline']}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with c_btn:
                if st.button("予想へ", key=f"dl_btn_{item['jcd']}_{item['rno']}", type="primary" if is_first else "secondary", use_container_width=True):
                    st.session_state.selected_jcd = item["jcd"]
                    st.session_state.selected_rno = item["rno"]
                    st.session_state.active_tab = "AI予想"
                    st.rerun()
    else:
        st.info("現在受付中のレースはありません。")

# ----------------------------------------------------
# 画面3: AIフォーメーション予想
# ----------------------------------------------------
elif st.session_state.active_tab == "AI予想":
    curr_v = next((v for v in VENUES if v["code"] == st.session_state.selected_jcd), VENUES[0])
    
    st.markdown(f"""
    <div style="background:#0066cc; color:white; padding:10px 12px; border-radius:6px; font-weight:bold; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
        <span style="font-size:1.1rem;">📍 ボートレース{curr_v['name']} 【第{st.session_state.selected_rno}R】</span>
        <span style="font-size:0.85rem;">{today_jst_str}</span>
    </div>
    """, unsafe_allow_html=True)

    c_rno, c_stance = st.columns([1, 1.5])
    with c_rno:
        st.session_state.selected_rno = st.selectbox("レース番号", list(range(1, 13)), index=st.session_state.selected_rno - 1)
    with c_stance:
        focus_type = st.selectbox("予想スタンス", ["バランス（本線＋抑え）", "本命重視（イン逃げ・点数絞り）", "高配当狙い（まくり・展開波乱）"])

    c_btn1, c_btn2 = st.columns([1, 1])
    with c_btn1:
        if st.button("📡 公式データを取得", use_container_width=True):
            with st.spinner("出走表・展示情報を取得中..."):
                r_data, _ = fetch_racelist(st.session_state.selected_jcd, st.session_state.selected_rno, today_jst_str)
                b_data, _ = fetch_beforeinfo(st.session_state.selected_jcd, st.session_state.selected_rno, today_jst_str)
                st.session_state.racelist_data = r_data or ""
                st.session_state.before_data = b_data or ""
                st.success("データ取得完了！")

    with c_btn2:
        if st.button("🔥 AI予想を実行", type="primary", use_container_width=True):
            if not api_key:
                st.error("Gemini APIキーを設定してください。")
            else:
                if not st.session_state.racelist_data:
                    with st.spinner("レースデータを自動取得中..."):
                        r_data, _ = fetch_racelist(st.session_state.selected_jcd, st.session_state.selected_rno, today_jst_str)
                        b_data, _ = fetch_beforeinfo(st.session_state.selected_jcd, st.session_state.selected_rno, today_jst_str)
                        st.session_state.racelist_data = r_data or "出走表取得エラー"
                        st.session_state.before_data = b_data or "直前情報なし"

                with st.spinner("🤖 Gemini 3.6 Flash がスリット隊形・展示・機力をシミュレーション中..."):
                    try:
                        res = analyze_with_gemini(
                            api_key=api_key,
                            venue_name=curr_v["name"],
                            rno=st.session_state.selected_rno,
                            racelist_data=st.session_state.racelist_data,
                            before_data=st.session_state.before_data,
                            focus_type=focus_type
                        )
                        st.session_state.prediction_result = res
                    except Exception as e:
                        st.error(f"予想生成エラー: {str(e)}")

    if st.session_state.prediction_result:
        st.markdown("---")
        st.markdown(st.session_state.prediction_result)
