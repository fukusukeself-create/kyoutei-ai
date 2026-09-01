import streamlit as st
import requests
from bs4 import BeautifulSoup
import datetime
import os
import re
from google import genai

# ----------------------------------------------------
# ページ基本設定（モバイル全画面最適化）
# ----------------------------------------------------
st.set_page_config(
    page_title="BOAT RACE",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ----------------------------------------------------
# 公式アプリ 完全再現CSS
# ----------------------------------------------------
st.markdown("""
<style>
    /* 全体背景 & 余白リセット */
    .stApp {
        background-color: #f2f4f7;
        font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Meiryo", sans-serif;
    }
    .block-container {
        padding-top: 0.5rem !important;
        padding-bottom: 2rem !important;
        padding-left: 0.5rem !important;
        padding-right: 0.5rem !important;
    }

    /* 最上部 公式ブルーヘッダー */
    .br-header {
        background-color: #0066cc;
        color: white;
        padding: 10px 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-radius: 4px;
        margin-bottom: 4px;
    }
    .br-logo {
        font-size: 1.25rem;
        font-weight: 900;
        letter-spacing: -0.5px;
        display: flex;
        align-items: center;
        gap: 6px;
    }

    /* サブナビゲーション・タブ */
    .tab-bar {
        display: flex;
        background: white;
        border-bottom: 2px solid #0066cc;
        margin-bottom: 8px;
    }
    .tab-item {
        flex: 1;
        text-align: center;
        padding: 8px 2px;
        font-size: 0.85rem;
        font-weight: bold;
        color: #333;
        cursor: pointer;
    }
    .tab-item.active {
        color: #0066cc;
        border-bottom: 3px solid #0066cc;
    }

    /* 24場カード共通スタイル */
    .venue-box {
        border-radius: 6px;
        border: 1.5px solid #0066cc;
        overflow: hidden;
        margin-bottom: 6px;
        background: white;
        font-size: 0.8rem;
        box-shadow: 0 1px 2px rgba(0,0,0,0.08);
    }
    .venue-box-off {
        border-radius: 6px;
        border: 1px solid #d1d5db;
        background: #e5e7eb;
        text-align: center;
        padding: 12px 2px;
        margin-bottom: 6px;
        color: #4b5563;
        font-weight: bold;
        font-size: 0.9rem;
    }
    
    /* カード内ヘッダー */
    .v-header-night {
        background: linear-gradient(180deg, #c7d2fe 0%, #a5b4fc 100%);
        padding: 3px 4px;
        font-weight: 900;
        font-size: 0.9rem;
        color: #1e1b4b;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .v-header-omura {
        background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%);
        padding: 3px 4px;
        font-weight: 900;
        font-size: 0.9rem;
        color: white;
        text-shadow: 0 1px 2px rgba(0,0,0,0.4);
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .v-header-closed {
        background: #9ca3af;
        padding: 3px 4px;
        font-weight: bold;
        font-size: 0.85rem;
        color: #1f2937;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    
    /* カード内ボディ */
    .v-body {
        padding: 3px 4px;
        text-align: center;
        background: white;
    }
    .v-body-closed {
        background: white;
        color: #111827;
        font-weight: bold;
        padding: 4px 2px;
    }

    /* G3などのグレードバッジ */
    .badge-g3 {
        background-color: #0066cc;
        color: white;
        font-size: 0.65rem;
        padding: 1px 3px;
        border-radius: 2px;
        font-weight: bold;
    }

    /* 締切順リストカード */
    .deadline-card {
        border-radius: 6px;
        border: 1.5px solid #0066cc;
        margin-bottom: 6px;
        overflow: hidden;
        background: white;
        display: flex;
    }
    .deadline-card-urgent {
        border: 1.5px solid #ef4444;
        background: #fef2f2;
    }
    .dl-left {
        width: 32%;
        padding: 6px 8px;
        font-weight: 900;
        font-size: 0.95rem;
        display: flex;
        flex-direction: column;
        justify-content: center;
        border-right: 1px solid #cbd5e1;
    }
    .dl-right {
        width: 68%;
        padding: 6px 10px;
        display: flex;
        flex-direction: column;
        justify-content: center;
    }

    /* StreamlitボタンのUI上書き */
    div[data-testid="stButton"] > button {
        width: 100%;
        border-radius: 4px;
        padding: 2px 4px;
        min-height: 28px;
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ----------------------------------------------------
# データ取得モジュール
# ----------------------------------------------------
@st.cache_data(ttl=30)
def fetch_all_venues_status(hd: str):
    """当日の24場開催・締切状況を公式から取得"""
    url = f"https://www.boatrace.jp/owpc/pc/race/index?hd={hd}"
    venue_status = {v["code"]: {"active": False, "closed": False, "grade": "", "day": "", "next_rno": 1, "deadline": "", "title": ""} for v in VENUES}
    deadline_list = []
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                text = row.get_text()
                for v in VENUES:
                    if v["name"] in text:
                        code = v["code"]
                        if "発売終了" in text or "終了" in text:
                            venue_status[code]["closed"] = True
                            venue_status[code]["active"] = False
                            if "G3" in text: venue_status[code]["grade"] = "G3"
                        else:
                            venue_status[code]["active"] = True
                            if "G3" in text: venue_status[code]["grade"] = "G3"
                            time_match = re.search(r"(\d{1,2})R\s*(\d{1,2}:\d{2})", text)
                            if time_match:
                                rno = int(time_match.group(1))
                                dtime = time_match.group(2)
                                venue_status[code]["next_rno"] = rno
                                venue_status[code]["deadline"] = dtime
                                deadline_list.append({
                                    "jcd": code,
                                    "name": v["name"],
                                    "rno": rno,
                                    "deadline": dtime,
                                    "type": v["type"],
                                    "grade": venue_status[code]["grade"]
                                })
        deadline_list.sort(key=lambda x: x["deadline"])
    except Exception:
        pass
    
    return venue_status, deadline_list

def fetch_racelist(jcd: str, rno: int, hd: str):
    url = f"https://www.boatrace.jp/owpc/pc/race/racelist?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
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
        res = requests.get(url, headers=HEADERS, timeout=10)
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
# Gemini AI 予想ロジック
# ----------------------------------------------------
def analyze_with_gemini(api_key: str, venue_name: str, rno: int, racelist_data: str, before_data: str, focus_type: str):
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたは競艇の回収率最大化を追求するプロ予想AIです。
以下の出走表、直前情報（展示タイム・スタート展示進入・気象風速・チルト）を徹底的に論理分析し、
ユーザーが指定した【{focus_type}】に最適な【三連単フォーメーション】を算出してください。

### 対象レース
- 場名: ボートレース{venue_name}
- レース: 第{rno}レース
- スタンス: {focus_type}

### 取得データ
【出走表】:
{racelist_data}

【直前情報・水面気象・展示】:
{before_data}

---
### 出力フォーマット（Markdown）

## 1. 進入隊形予想 & 1マーク展開シミュレーション
- スリット隊形予想（例: 123/456）とイン逃げ信頼度（S/A/B/C）
- 攻め手となる艇の展開シナリオ

## 2. 展示・気象評価
- 展示タイム・チルト・風向風速の影響評価

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
    st.session_state.selected_jcd = "12"  # 住之江
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
# ヘッダー部
# ----------------------------------------------------
st.markdown("""
<div class="br-header">
    <div class="br-logo">🚤 BOAT RACE <span style="font-size:1.1rem; font-weight:normal; margin-left:4px;">トップ</span></div>
    <div style="font-size: 0.8rem; font-weight:bold;">ピン留め / マイページ</div>
</div>
""", unsafe_allow_html=True)

# 日付設定
today_str = datetime.date.today().strftime("%Y%m%d")
venue_status, deadline_list = fetch_all_venues_status(today_str)

# ナビゲーションタブ
c_tab1, c_tab2, c_tab3 = st.columns([1, 1, 1.2])
with c_tab1:
    if st.button("🚩 開催一覧", type="primary" if st.session_state.active_tab == "開催一覧" else "secondary", use_container_width=True):
        st.session_state.active_tab = "開催一覧"
        st.rerun()
with c_tab2:
    if st.button("⏰ 締切順", type="primary" if st.session_state.active_tab == "締切順" else "secondary", use_container_width=True):
        st.session_state.active_tab = "締切順"
        st.rerun()
with c_tab3:
    if st.button("🎯 AI予想", type="primary" if st.session_state.active_tab == "AI予想" else "secondary", use_container_width=True):
        st.session_state.active_tab = "AI予想"
        st.rerun()

# ----------------------------------------------------
# 画面1: 開催一覧（スクショ1枚目の完全再現）
# ----------------------------------------------------
if st.session_state.active_tab == "開催一覧":
    cols = st.columns(4)
    for idx, v in enumerate(VENUES):
        col = cols[idx % 4]
        code = v["code"]
        stat = venue_status.get(code, {})
        
        # 1. 開催中（ナイター・大村・デイ）
        if stat.get("active"):
            head_class = "v-header-omura" if code == "24" else "v-header-night"
            icon = "🌟 " if code == "24" else ("🌙 " if v["type"] == "ナイター" else "")
            badge = f'<span class="badge-g3">{stat["grade"]}</span>' if stat.get("grade") else ""
            
            card_html = f"""
            <div class="venue-box">
                <div class="{head_class}">
                    <span>{badge} {icon}{v['name']}</span>
                </div>
                <div class="v-body">
                    <div style="font-size:0.75rem; color:#475569;">一般 初日</div>
                    <div style="font-weight:900; color:#dc2626; font-size:0.85rem;">{stat['next_rno']}R {stat['deadline']}</div>
                </div>
            </div>
            """
            col.markdown(card_html, unsafe_allow_html=True)
            if col.button(f"選択", key=f"btn_act_{code}", use_container_width=True):
                st.session_state.selected_jcd = code
                st.session_state.selected_rno = stat["next_rno"]
                st.session_state.active_tab = "AI予想"
                st.rerun()
                
        # 2. 発売終了
        elif stat.get("closed"):
            badge = f'<span class="badge-g3">{stat["grade"]}</span>' if stat.get("grade") else ""
            card_html = f"""
            <div class="venue-box">
                <div class="v-header-closed">
                    <span>{badge} {v['name']}</span>
                    <span style="font-size:0.7rem;">終了</span>
                </div>
                <div class="v-body v-body-closed">
                    発売終了
                </div>
            </div>
            """
            col.markdown(card_html, unsafe_allow_html=True)
            if col.button(f"終了", key=f"btn_cls_{code}", disabled=True, use_container_width=True):
                pass
                
        # 3. 非開催 (--)
        else:
            card_html = f"""
            <div class="venue-box-off">
                <div>{v['name']}</div>
                <div style="margin-top:2px;">--</div>
            </div>
            """
            col.markdown(card_html, unsafe_allow_html=True)
            if col.button(f"--", key=f"btn_none_{code}", disabled=True, use_container_width=True):
                pass

# ----------------------------------------------------
# 画面2: 締切順一覧（スクショ2枚目の完全再現）
# ----------------------------------------------------
elif st.session_state.active_tab == "締切順":
    if deadline_list:
        for item in deadline_list:
            is_urgent = (item == deadline_list[0])
            card_urgent_cls = "deadline-card-urgent" if is_urgent else ""
            bg_left = "background: linear-gradient(180deg, #38bdf8 0%, #0284c7 100%); color:white;" if item["jcd"] == "24" else "background: #c7d2fe; color:#1e1b4b;"
            badge_html = f'<span class="badge-g3">{item["grade"]}</span> ' if item.get("grade") else ""
            
            c_card, c_btn = st.columns([3.5, 1])
            with c_card:
                st.markdown(f"""
                <div class="deadline-card {card_urgent_cls}">
                    <div class="dl-left" style="{bg_left}">
                        <div>{badge_html}{item['name']} 🌙</div>
                        <div style="font-size:0.75rem; font-weight:normal;">初日</div>
                    </div>
                    <div class="dl-right">
                        <div style="font-weight:900; font-size:1rem; color:#0f172a;">{item['rno']}R 予選特賞</div>
                        <div style="font-weight:bold; color:#dc2626; font-size:0.9rem;">締切予定時刻 {item['deadline']}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with c_btn:
                if st.button("予想", key=f"dl_btn_{item['jcd']}_{item['rno']}", type="primary" if is_urgent else "secondary", use_container_width=True):
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
    <div style="background:#0066cc; color:white; padding:8px 12px; border-radius:6px; font-weight:bold; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
        <span>📍 ボートレース{curr_v['name']} 【第{st.session_state.selected_rno}R】</span>
        <span>{today_str}</span>
    </div>
    """, unsafe_allow_html=True)

    c_rno, c_stance = st.columns([1, 1.5])
    with c_rno:
        st.session_state.selected_rno = st.selectbox("レース番号", list(range(1, 13)), index=st.session_state.selected_rno - 1)
    with c_stance:
        focus_type = st.selectbox("狙い目", ["バランス（本線＋抑え）", "本命重視（イン逃げ・絞り）", "高配当狙い（まくり・波乱）"])

    c_btn1, c_btn2 = st.columns([1, 1])
    with c_btn1:
        if st.button("📡 公式データ取得", use_container_width=True):
            with st.spinner("出走表・展示情報をロード中..."):
                r_data, _ = fetch_racelist(st.session_state.selected_jcd, st.session_state.selected_rno, today_str)
                b_data, _ = fetch_beforeinfo(st.session_state.selected_jcd, st.session_state.selected_rno, today_str)
                st.session_state.racelist_data = r_data or ""
                st.session_state.before_data = b_data or ""
                st.success("データ取得完了！")

    with c_btn2:
        if st.button("🔥 AIフォーメーション予想を実行", type="primary", use_container_width=True):
            if not api_key:
                st.error("Gemini APIキーを設定してください。")
            else:
                if not st.session_state.racelist_data:
                    with st.spinner("出走表・直前情報を自動取得中..."):
                        r_data, _ = fetch_racelist(st.session_state.selected_jcd, st.session_state.selected_rno, today_str)
                        b_data, _ = fetch_beforeinfo(st.session_state.selected_jcd, st.session_state.selected_rno, today_str)
                        st.session_state.racelist_data = r_data or "出走表取得エラー"
                        st.session_state.before_data = b_data or "直前情報なし"

                with st.spinner("🤖 Gemini 3.6 Flash がスリット隊形・展示・気象を解析中..."):
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
