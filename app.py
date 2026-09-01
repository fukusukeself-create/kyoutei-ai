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
    page_title="BOAT RACE AI フォーメーション予想",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ----------------------------------------------------
# 公式ライクなカスタムCSS
# ----------------------------------------------------
st.markdown("""
<style>
    .stApp {
        background-color: #f1f5f9;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
    }
    .app-header {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
        color: #ffffff;
        padding: 12px;
        border-radius: 8px;
        text-align: center;
        font-weight: 800;
        font-size: 1.25rem;
        margin-bottom: 12px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    
    /* 開催ステータスカード */
    .venue-card-active {
        background: #ffffff;
        border: 2px solid #2563eb;
        border-radius: 8px;
        padding: 8px 4px;
        text-align: center;
        margin-bottom: 8px;
    }
    .venue-card-night {
        background: #eff6ff;
        border: 2px solid #818cf8;
        border-radius: 8px;
        padding: 8px 4px;
        text-align: center;
        margin-bottom: 8px;
    }
    .venue-card-closed {
        background: #e2e8f0;
        border: 1px solid #94a3b8;
        border-radius: 8px;
        padding: 8px 4px;
        text-align: center;
        color: #64748b;
        margin-bottom: 8px;
    }
    .venue-card-none {
        background: #f8fafc;
        border: 1px dashed #cbd5e1;
        border-radius: 8px;
        padding: 8px 4px;
        text-align: center;
        color: #94a3b8;
        margin-bottom: 8px;
    }
    
    /* 締切順アイテム */
    .deadline-card {
        background: #ffffff;
        border-left: 6px solid #2563eb;
        border-radius: 6px;
        padding: 10px 14px;
        margin-bottom: 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .stButton>button {
        border-radius: 6px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 24場 定義
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
# 全場開催・締切状況の取得スクレイピング
# ----------------------------------------------------
@st.cache_data(ttl=60)
def fetch_all_venues_status(hd: str):
    """当日の全場開催状況・直前締切一覧を取得"""
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
                        else:
                            venue_status[code]["active"] = True
                            # 締切時間・R抽出
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
                                    "type": v["type"]
                                })
        deadline_list.sort(key=lambda x: x["deadline"])
    except Exception:
        pass
    
    return venue_status, deadline_list

# ----------------------------------------------------
# 出走表 & 直前情報スクレイピング
# ----------------------------------------------------
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
            text_data = " ".join(row.get_text().split())
            racers.append(f"【{i}号艇】: {text_data}")
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
        info_text = ""
        for t in tables:
            info_text += "\n" + " ".join(t.get_text().split())
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
あなたは競艇（ボートレース）の回収率最大化を追求するトッププロ予想AIです。
以下のレース情報、出走表、直前情報（展示タイム・スタート展示進入・気象・風速）を徹底的に論理分析し、
ユーザーが指定したスタンス【{focus_type}】に最適な【三連単フォーメーション】を算出してください。

### 対象レース
- 開催場: ボートレース{venue_name}
- レース: 第{rno}レース
- スタンス: {focus_type}

### 取得データ
【出走表・選手データ】:
{racelist_data}

【直前情報・水面気象・展示】:
{before_data}

---
### 分析フォーマット（Markdown出力）

## 1. 進入隊形予想 & 1マーク展開シミュレーション
- スリット進入予想（例: 123/456）とイン逃げ信頼度（S/A/B/C）
- まくり・まくり差しなど攻め手の艇と展開シナリオ

## 2. 機力 & 直前気象評価
- 展示タイム・スタート展示・チルト・風速（向かい風/追い風/波高）から導く有利不利

## 3. 🎯 厳選 三連単フォーメーション買い目

### 【本線フォーメーション】（4〜8点）
- 買い目: `1 - 2,3 - 2,3,4`
- 推奨資金配分比率（合計100%になるようにパーセント指定）

### 【抑え / 穴狙いフォーメーション】（2〜4点）
- 買い目: `3 - 1,4 - 1,4,5`
- 狙う展開トリガー

## 4. 💡 勝負の決め手
- レースの最終結論
"""
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    return response.text

# ----------------------------------------------------
# UI & 状態管理
# ----------------------------------------------------
st.markdown('<div class="app-header">🚤 BOAT RACE AI FORMATION PREDICTOR</div>', unsafe_allow_html=True)

# セッション状態の初期化
if "selected_jcd" not in st.session_state:
    st.session_state.selected_jcd = "19"
if "selected_rno" not in st.session_state:
    st.session_state.selected_rno = 10
if "racelist_data" not in st.session_state:
    st.session_state.racelist_data = ""
if "before_data" not in st.session_state:
    st.session_state.before_data = ""
if "prediction_result" not in st.session_state:
    st.session_state.prediction_result = ""

api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if not api_key:
    with st.expander("🔑 Gemini APIキー設定", expanded=False):
        api_key = st.text_input("API Key を入力", type="password")

# 日付・条件選択
col_d, col_sync = st.columns([2, 1])
with col_d:
    selected_date = st.date_input("📅 開催日", datetime.date.today(), label_visibility="collapsed")
    hd_str = selected_date.strftime("%Y%m%d")
with col_sync:
    if st.button("🔄 開催情報更新", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# 開催状況・締切順データの取得
venue_status, deadline_list = fetch_all_venues_status(hd_str)

# メインタブ
tab_grid, tab_deadline, tab_ai = st.tabs(["🏁 開催一覧（24場）", "⏰ 締切順一覧", "🎯 AIフォーメーション予想"])

# ----------------------------------------------------
# TAB 1: 開催一覧（24場 グリッドパネルUI）
# ----------------------------------------------------
with tab_grid:
    st.caption("※タップすると対象場と現在のレースが即座にセットされます。")
    cols = st.columns(4)
    for idx, v in enumerate(VENUES):
        col = cols[idx % 4]
        code = v["code"]
        stat = venue_status.get(code, {})
        is_selected = (st.session_state.selected_jcd == code)
        
        # 状態別のテキストとボタンスタイル
        if stat.get("active"):
            time_txt = f"{stat['next_rno']}R {stat['deadline']}"
            night_icon = "🌙" if v["type"] == "ナイター" else ("🌅" if v["type"] == "モーニング" else "")
            btn_label = f"{v['name']}{night_icon}\n{time_txt}"
            btn_type = "primary" if is_selected else "secondary"
        elif stat.get("closed"):
            btn_label = f"{v['name']}\n[発売終了]"
            btn_type = "secondary"
        else:
            btn_label = f"{v['name']}\n--"
            btn_type = "secondary"
            
        if col.button(btn_label, key=f"grid_{code}", type=btn_type, use_container_width=True):
            st.session_state.selected_jcd = code
            if stat.get("next_rno"):
                st.session_state.selected_rno = stat["next_rno"]
            st.rerun()

# ----------------------------------------------------
# TAB 2: 締切順一覧（直近レースのタイムテーブル）
# ----------------------------------------------------
with tab_deadline:
    if deadline_list:
        st.markdown(f"##### ⏰ まもなく締切のレース（{len(deadline_list)}件）")
        for item in deadline_list:
            night_badge = "🌙" if item["type"] == "ナイター" else ""
            c_info, c_btn = st.columns([3, 1])
            with c_info:
                st.markdown(f"""
                <div class="deadline-card">
                    <div>
                        <b>{item['name']} {night_badge}</b> <span style="color:#2563eb; font-weight:bold; font-size:1.1rem; margin-left:6px;">{item['rno']}R</span>
                    </div>
                    <div style="font-weight:bold; color:#dc2626; font-size:1.05rem;">
                        締切予定 {item['deadline']}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with c_btn:
                if st.button("予想へ", key=f"dl_{item['jcd']}_{item['rno']}", use_container_width=True):
                    st.session_state.selected_jcd = item["jcd"]
                    st.session_state.selected_rno = item["rno"]
                    st.rerun()
    else:
        st.info("現在進行中のレース情報がありません（非開催または全レース終了）。")

# ----------------------------------------------------
# 選択中レース表示 & AI予想実行セクション
# ----------------------------------------------------
current_venue = next((v for v in VENUES if v["code"] == st.session_state.selected_jcd), VENUES[0])

st.markdown("---")
col_s1, col_s2, col_s3 = st.columns([1.5, 1, 1.5])
with col_s1:
    st.markdown(f"📍 **ボートレース{current_venue['name']}**")
with col_s2:
    st.session_state.selected_rno = st.selectbox(
        "レース番号",
        options=list(range(1, 13)),
        index=st.session_state.selected_rno - 1,
        label_visibility="collapsed"
    )
with col_s3:
    focus_type = st.selectbox(
        "スタンス",
        ["バランス（本線＋抑え）", "本命重視（イン逃げ・点数絞り）", "高配当狙い（センター・まくり）"],
        label_visibility="collapsed"
    )

# 予想タブ
with tab_ai:
    st.markdown(f"#### 🎯 {current_venue['name']} 第{st.session_state.selected_rno}R フォーメーションAI予想")
    
    col_act1, col_act2 = st.columns([1, 1])
    with col_act1:
        if st.button("📡 公式データを取得", use_container_width=True):
            with st.spinner("出走表・展示情報を取得中..."):
                r_data, r_err = fetch_racelist(st.session_state.selected_jcd, st.session_state.selected_rno, hd_str)
                b_data, b_err = fetch_beforeinfo(st.session_state.selected_jcd, st.session_state.selected_rno, hd_str)
                
                if r_err:
                    st.error(r_err)
                else:
                    st.session_state.racelist_data = r_data
                    st.session_state.before_data = b_data or "（直前情報なし：展示開始前）"
                    st.success("データ取得完了！")

    with col_act2:
        if st.button("🔥 AI予想を実行", type="primary", use_container_width=True):
            if not api_key:
                st.error("Gemini APIキーを設定してください。")
            else:
                if not st.session_state.racelist_data:
                    with st.spinner("レースデータを取得中..."):
                        r_data, _ = fetch_racelist(st.session_state.selected_jcd, st.session_state.selected_rno, hd_str)
                        b_data, _ = fetch_beforeinfo(st.session_state.selected_jcd, st.session_state.selected_rno, hd_str)
                        st.session_state.racelist_data = r_data or "出走表データ取得失敗"
                        st.session_state.before_data = b_data or "直前情報なし"

                with st.spinner("🤖 Gemini 3.6 Flash が展開・展示・機力をシミュレーション中..."):
                    try:
                        res = analyze_with_gemini(
                            api_key=api_key,
                            venue_name=current_venue["name"],
                            rno=st.session_state.selected_rno,
                            racelist_data=st.session_state.racelist_data,
                            before_data=st.session_state.before_data,
                            focus_type=focus_type
                        )
                        st.session_state.prediction_result = res
                    except Exception as e:
                        st.error(f"予想生成エラー: {str(e)}")

    # 取得データ確認用
    if st.session_state.racelist_data:
        with st.expander("📋 取得済みデータ（出走表・直前展示）", expanded=False):
            st.text_area("出走表", st.session_state.racelist_data, height=100)
            st.text_area("直前情報・気象", st.session_state.before_data, height=100)

    # 予想結果の表示
    if st.session_state.prediction_result:
        st.markdown("---")
        st.markdown(st.session_state.prediction_result)
