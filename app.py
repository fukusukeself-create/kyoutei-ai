import streamlit as st
import requests
from bs4 import BeautifulSoup
import datetime
import os
import re
from google import genai
from google.genai import types

# ----------------------------------------------------
# ページ基本設定
# ----------------------------------------------------
st.set_page_config(
    page_title="BOAT RACE AI 予想",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ----------------------------------------------------
# 公式ライクなカスタムCSS & 艇番カラー定義
# ----------------------------------------------------
st.markdown("""
<style>
    /* 全体フォント・ベーススタイル */
    .stApp {
        background-color: #f4f6f9;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* ヘッダーバー */
    .app-header {
        background: linear-gradient(135deg, #0b3c5d 0%, #1d2731 100%);
        color: #ffffff;
        padding: 12px 16px;
        border-radius: 10px;
        text-align: center;
        font-weight: 800;
        font-size: 1.25rem;
        letter-spacing: 0.05em;
        margin-bottom: 12px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    }
    
    /* 艇番バッジ（公式カラー規格） */
    .boat-badge {
        display: inline-block;
        width: 26px;
        height: 26px;
        line-height: 24px;
        text-align: center;
        font-weight: 900;
        border-radius: 4px;
        margin: 0 2px;
        font-size: 0.95rem;
    }
    .b-1 { background-color: #ffffff; color: #000000; border: 1.5px solid #000000; }
    .b-2 { background-color: #000000; color: #ffffff; border: 1.5px solid #000000; }
    .b-3 { background-color: #e53935; color: #ffffff; border: 1.5px solid #b71c1c; }
    .b-4 { background-color: #1e88e5; color: #ffffff; border: 1.5px solid #0d47a1; }
    .b-5 { background-color: #fdd835; color: #000000; border: 1.5px solid #fbc02d; }
    .b-6 { background-color: #43a047; color: #ffffff; border: 1.5px solid #1b5e20; }

    /* レース情報サマリーカード */
    .race-summary-card {
        background-color: #ffffff;
        border: 1px solid #dce2e6;
        border-left: 6px solid #0b3c5d;
        border-radius: 8px;
        padding: 12px 16px;
        margin-bottom: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }

    /* 買い目カード */
    .formation-card {
        background: #ffffff;
        border-radius: 8px;
        border: 1px solid #e0e6ed;
        padding: 14px;
        margin-bottom: 12px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.04);
    }
    .formation-title {
        font-weight: bold;
        font-size: 1.05rem;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    
    /* ボタンスタイル調整 */
    .stButton>button {
        border-radius: 8px;
        font-weight: 700;
        height: 42px;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 定数データ（24場）
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
# スクレイピング関数
# ----------------------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_racelist(jcd: str, rno: int, hd: str):
    url = f"https://www.boatrace.jp/owpc/pc/race/racelist?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        table = soup.find("div", class_="table1")
        if not table:
            return None, "出走表が取得できませんでした（非開催または公開前）。"
        
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
# Gemini AI 予想エンジン
# ----------------------------------------------------
def analyze_with_gemini(api_key: str, venue_name: str, rno: int, racelist_data: str, before_data: str, focus_type: str):
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたは回収率を極限まで追求する競艇専門のデータサイエンティスト兼プロ予想AIです。
提供された出走表、直前情報（展示タイム・スタート展示スリット隊形・風速・波高・チルト・部品交換）を総合的に分析し、
ユーザーが指定した【{focus_type}】に最適な三連単フォーメーション買い目を算出してください。

### 対象レース
- 開催場: ボートレース{venue_name}
- レース: 第{rno}レース
- 狙い方スタンス: {focus_type}

### レースデータ
【出走表】:
{racelist_data}

【直前情報（展示・気象）】:
{before_data}

---
### 出力フォーマット（Markdown形式）

## 1. 隊形 & 1マーク展開シミュレーション
- **スリット進入隊形予想**: （例: 123/456、チルトやピット離れ考慮）
- **イン逃げ信頼度**: 【S / A / B / C】（理由を簡潔に）
- **仕掛け艇・波乱要因**: まくり/まくり差しの展開トリガー

## 2. 展示タイム・機力評価
- 各艇の足色（出足・伸び足・回り足）のハイライト

## 3. 🎯 厳選 三連単フォーメーション買い目

### 【本線・主力フォーメーション】（4〜8点）
- **買い目構成**: `1 - 2,3 - 2,3,4` （フォーメーション形式）
- **推奨資金配分比率**:
  - `1-2-3`: 35%
  - `1-2-4`: 25%
  - `1-3-2`: 25%
  - `1-3-4`: 15%

### 【高回収・抑えフォーメーション】（2〜4点）
- **買い目構成**: `3 - 1,4 - 1,4,5`
- **狙い目根拠**: 展開が崩れた場合のシナリオ

## 4. 💡 勝負の決め手（ワンポイント）
"""
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    return response.text

# ----------------------------------------------------
# ヘッダー & APIキー管理
# ----------------------------------------------------
st.markdown('<div class="app-header">🚤 BOAT RACE AI FORMATION PREDICTOR</div>', unsafe_allow_html=True)

api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if not api_key:
    with st.expander("🔑 APIキー設定", expanded=True):
        api_key = st.text_input("Gemini API Key", type="password")

# ----------------------------------------------------
# メイン画面（タブ構成）
# ----------------------------------------------------
tab_select, tab_data, tab_ai = st.tabs(["🏟️ レース選択", "📋 出走・直前データ", "🎯 AIフォーメーション予想"])

if "selected_jcd" not in st.session_state:
    st.session_state.selected_jcd = "19"  # 下関
if "racelist_data" not in st.session_state:
    st.session_state.racelist_data = ""
if "before_data" not in st.session_state:
    st.session_state.before_data = ""
if "prediction_result" not in st.session_state:
    st.session_state.prediction_result = ""

# --- TAB 1: レース選択 ---
with tab_select:
    c1, c2, c3 = st.columns([1.2, 1, 1.5])
    with c1:
        selected_date = st.date_input("📅 開催日", datetime.date.today())
        hd_str = selected_date.strftime("%Y%m%d")
    with c2:
        selected_rno = st.selectbox("🏁 レース", options=list(range(1, 13)), index=9)
    with c3:
        focus_type = st.selectbox(
            "🎯 予想スタンス",
            ["バランス（本線＋抑え）", "本命重視（イン逃げ・点数絞り）", "高配当狙い（センター・ダッシュ攻め）"]
        )

    st.write("▼ **開催場を選択**")
    venue_cols = st.columns(4)
    for idx, v in enumerate(VENUES):
        col = venue_cols[idx % 4]
        is_active = (st.session_state.selected_jcd == v["code"])
        type_icon = "🌙" if v["type"] == "ナイター" else ("🌅" if v["type"] == "モーニング" else "")
        btn_text = f"{v['name']} {type_icon}"
        
        if col.button(btn_text, key=f"v_{v['code']}", type="primary" if is_active else "secondary", use_container_width=True):
            st.session_state.selected_jcd = v["code"]
            st.rerun()

    current_venue = next((v for v in VENUES if v["code"] == st.session_state.selected_jcd), VENUES[0])
    
    st.markdown(f"""
    <div class="race-summary-card">
        <div>
            <span style="font-size: 1.2rem; font-weight: bold; color: #0b3c5d;">ボートレース{current_venue['name']}</span>
            <span style="font-size: 1.1rem; font-weight: bold; margin-left: 8px;">第 {selected_rno} レース</span>
        </div>
        <div style="color: #64748b; font-weight: bold;">{selected_date.strftime('%Y/%m/%d')}</div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("📡 公式データを取得して準備", type="primary", use_container_width=True):
        with st.spinner("出走表・直前情報を自動取得中..."):
            r_data, r_err = fetch_racelist(st.session_state.selected_jcd, selected_rno, hd_str)
            b_data, b_err = fetch_beforeinfo(st.session_state.selected_jcd, selected_rno, hd_str)
            
            if r_err:
                st.error(r_err)
            else:
                st.session_state.racelist_data = r_data
                st.session_state.before_data = b_data or "（直前情報：展示開始前または未公開）"
                st.success("データの読み込みが完了しました！「AIフォーメーション予想」タブへ進んでください。")

# --- TAB 2: 出走表・直前データプレビュー ---
with tab_data:
    current_venue = next((v for v in VENUES if v["code"] == st.session_state.selected_jcd), VENUES[0])
    st.markdown(f"#### 📋 {current_venue['name']} 第{selected_rno}R 公式データ")
    
    if st.session_state.racelist_data:
        st.markdown("**【出走表・選手一覧】**")
        st.text_area("", st.session_state.racelist_data, height=180, label_visibility="collapsed")
        
        st.markdown("**【直前情報（展示タイム・気象・進入）】**")
        st.text_area("", st.session_state.before_data, height=140, label_visibility="collapsed")
    else:
        st.info("「レース選択」タブでデータ取得ボタンを押すか、直接予想を実行してください。")

# --- TAB 3: AIフォーメーション予想 ---
with tab_ai:
    current_venue = next((v for v in VENUES if v["code"] == st.session_state.selected_jcd), VENUES[0])
    
    st.markdown(f"""
    <div style="background: #ffffff; padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; border: 1px solid #e2e8f0;">
        <b>対象:</b> {current_venue['name']} 第{selected_rno}R ／ <b>スタンス:</b> {focus_type}
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("🔥 三連単フォーメーションをAI解析", type="primary", use_container_width=True):
        if not api_key:
            st.error("Gemini APIキーを設定してください。")
        else:
            # データ未取得の場合は自動取得
            if not st.session_state.racelist_data:
                with st.spinner("レースデータを取得中..."):
                    r_data, _ = fetch_racelist(st.session_state.selected_jcd, selected_rno, hd_str)
                    b_data, _ = fetch_beforeinfo(st.session_state.selected_jcd, selected_rno, hd_str)
                    st.session_state.racelist_data = r_data or "出走表データ取得失敗"
                    st.session_state.before_data = b_data or "直前情報なし"

            with st.spinner("🤖 Geminiが展開・展示・機力をシミュレーション中..."):
                try:
                    result = analyze_with_gemini(
                        api_key=api_key,
                        venue_name=current_venue["name"],
                        rno=selected_rno,
                        racelist_data=st.session_state.racelist_data,
                        before_data=st.session_state.before_data,
                        focus_type=focus_type
                    )
                    st.session_state.prediction_result = result
                except Exception as e:
                    st.error(f"予想生成エラー: {str(e)}")

    if st.session_state.prediction_result:
        st.markdown("---")
        st.markdown(st.session_state.prediction_result)
