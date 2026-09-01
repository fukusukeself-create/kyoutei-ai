import streamlit as st
import requests
from bs4 import BeautifulSoup
import datetime
import os
import re
from google import genai
from google.genai import types

# ----------------------------------------------------
# ページ基本設定（モバイル・大画面レスポンシブ対応）
# ----------------------------------------------------
st.set_page_config(
    page_title="BOAT RACE AI フォーメーション予想",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ----------------------------------------------------
# カスタムCSS（公式アプリ風UI & スタイリング）
# ----------------------------------------------------
st.markdown("""
<style>
    .main-header {
        font-size: 1.4rem;
        font-weight: bold;
        color: #1a3a6c;
        text-align: center;
        padding: 8px 0;
        background: linear-gradient(180deg, #f0f4f8 0%, #d9e2ec 100%);
        border-radius: 8px;
        margin-bottom: 15px;
    }
    .stButton>button {
        width: 100%;
        border-radius: 6px;
        font-weight: bold;
    }
    .venue-btn {
        background-color: #f8fafc;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        padding: 6px;
        text-align: center;
        font-weight: bold;
        cursor: pointer;
    }
    .boat-card {
        padding: 12px;
        border-radius: 8px;
        margin-bottom: 10px;
        border-left: 6px solid #1a3a6c;
        background-color: #ffffff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    .odds-box {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        padding: 10px;
        border-radius: 6px;
        margin-top: 10px;
    }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------
# 定数データ（全国24場）
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
# データ取得モジュール（公式スクレイピング）
# ----------------------------------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_racelist(jcd: str, rno: int, hd: str):
    """出走表データの取得"""
    url = f"https://www.boatrace.jp/owpc/pc/race/racelist?rno={rno}&jcd={jcd}&hd={hd}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")
        
        table = soup.find("div", class_="table1")
        if not table:
            return None, "出走表が見つかりませんでした（開催なしまたは準備中）。"
        
        rows = table.find_all("tbody")
        racers = []
        for i, row in enumerate(rows[:6], 1):
            text_data = " ".join(row.get_text().split())
            racers.append(f"【{i}号艇】: {text_data}")
            
        return "\n".join(racers), None
    except Exception as e:
        return None, f"出走表取得エラー: {str(e)}"

def fetch_beforeinfo(jcd: str, rno: int, hd: str):
    """直前情報（展示タイム・スタート展示・気象情報）の取得"""
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
# Gemini AI 予想ロジック
# ----------------------------------------------------
def analyze_with_gemini(api_key: str, venue_name: str, rno: int, racelist_data: str, before_data: str, focus_type: str):
    client = genai.Client(api_key=api_key)
    
    prompt = f"""
あなたは競艇（ボートレース）の回収率最大化を追求するトッププロ予想AIです。
以下のレース情報、出走表、直前情報（展示タイム・スタート展示進入・気象・風速）を徹底的に論理分析し、
最も勝率・回収率の高い【三連単フォーメーション】を算出してください。

### 対象レース
- 開催場: ボートレース{venue_name}
- レース: 第{rno}レース
- ユーザー希望スタンス: {focus_type}

### 取得データ
【出走表・選手データ】:
{racelist_data}

【直前情報・水面気象・展示】:
{before_data}

---
### 分析および出力フォーマット（必ず以下の構成でMarkdown出力してください）

## 1. 水面状況 & 進入スリット隊形予想
- 風向・風速・潮（海水場の場合）・チルト角度がレースに与える影響
- スタート展示に基づく本番進入予想と隊形シミュレーション

## 2. 1マーク展開シミュレーション
- イン逃げ成否判定（信頼度: S/A/B/C）
- 攻め手（まくり/まくり差し）となる艇の特定と展開の有利不利

## 3. 機力・展示総合評価
- 各艇の伸び足・回り足・出足の評価（展示タイムとのギャップ含む）

## 4. 厳選 三連単フォーメーション買い目
（※点数を無駄に広げず、期待値の高い組み合わせに絞り込むこと）

### 【本線（本命）フォーメーション】（4〜8点）
- 買い目: 例 `1 - 2,3 - 2,3,4`
- 各買い目の推奨資金配分比率（合計100%になるように配分）

### 【抑え / 狙い目（高配当・穴）】（2〜4点）
- 買い目: 例 `3 - 1,4 - 1,4,5`
- 狙う理由・展開トリガー

## 5. レース総括 & 勝負の決め手
- 一言でまとめる勝負ポイント
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return response.text

# ----------------------------------------------------
# メイン画面 UI
# ----------------------------------------------------
st.markdown('<div class="main-header">🚤 BOAT RACE AI 三連単フォーメーション予想</div>', unsafe_allow_html=True)

# APIキー設定（Streamlit Secrets または 画面入力）
api_key = st.secrets.get("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
if not api_key:
    with st.expander("🔑 Gemini APIキー設定", expanded=True):
        api_key = st.text_input("Gemini API Key を入力してください", type="password")

# 日付・レース場・レース番号選択
col_date, col_rno, col_mode = st.columns([1, 1, 1.2])

with col_date:
    selected_date = st.date_input("開催日", datetime.date.today())
    hd_str = selected_date.strftime("%Y%m%d")

with col_rno:
    selected_rno = st.selectbox("レース番号", options=list(range(1, 13)), index=9)

with col_mode:
    focus_type = st.selectbox(
        "予想スタンス",
        ["バランス（本線＋抑え）", "本命重視（イン鉄板・点数絞り）", "穴・高配当狙い（展開波乱・まくり重視）"]
    )

# 24場選択グリッドUI（公式アプリ風レイアウト）
st.write("▼ **開催場を選択してください**")
selected_venue_code = st.session_state.get("selected_jcd", "20") # デフォルト若松

# 4列×6行グリッド
cols = st.columns(4)
for idx, v in enumerate(VENUES):
    col = cols[idx % 4]
    type_badge = "🌙" if v["type"] == "ナイター" else ("🌅" if v["type"] == "モーニング" else "")
    btn_label = f"{v['name']} {type_badge}"
    is_active = (selected_venue_code == v["code"])
    
    if col.button(btn_label, key=f"btn_venue_{v['code']}", type="primary" if is_active else "secondary"):
        st.session_state["selected_jcd"] = v["code"]
        selected_venue_code = v["code"]
        st.rerun()

current_venue = next((v for v in VENUES if v["code"] == selected_venue_code), VENUES[0])

st.info(f"📍 選択中: **ボートレース{current_venue['name']}** 【第{selected_rno}レース】（{selected_date.strftime('%Y/%m/%d')}）")

# データ取得 & AI予想セクション
st.markdown("---")

col_btn1, col_btn2 = st.columns([1, 1])

if "racelist_data" not in st.session_state:
    st.session_state.racelist_data = ""
if "before_data" not in st.session_state:
    st.session_state.before_data = ""

with col_btn1:
    if st.button("📡 公式レースデータ自動取得", use_container_width=True):
        with st.spinner("出走表・直前情報を取得中..."):
            r_data, r_err = fetch_racelist(selected_venue_code, selected_rno, hd_str)
            b_data, b_err = fetch_beforeinfo(selected_venue_code, selected_rno, hd_str)
            
            if r_err:
                st.error(r_err)
            else:
                st.session_state.racelist_data = r_data
                
            if b_err:
                st.warning(b_err)
                st.session_state.before_data = "（直前情報なし：展示前または未取得）"
            else:
                st.session_state.before_data = b_data
                st.success("データ取得完了！")

# 取得データの確認アコーディオン
if st.session_state.racelist_data:
    with st.expander("📋 取得済みデータプレビュー", expanded=False):
        st.text_area("出走表データ", st.session_state.racelist_data, height=120)
        st.text_area("直前展示・気象データ", st.session_state.before_data, height=120)

with col_btn2:
    execute_prediction = st.button("🔥 AIフォーメーション予想を実行", type="primary", use_container_width=True)

# 予想実行と結果表示
if execute_prediction:
    if not api_key:
        st.error("Gemini APIキーを設定してください。")
    else:
        # データが未取得なら自動取得を試みる
        if not st.session_state.racelist_data:
            with st.spinner("レースデータを取得中..."):
                r_data, _ = fetch_racelist(selected_venue_code, selected_rno, hd_str)
                b_data, _ = fetch_beforeinfo(selected_venue_code, selected_rno, hd_str)
                st.session_state.racelist_data = r_data or "出走表データ取得失敗（手動確認推奨）"
                st.session_state.before_data = b_data or "直前情報なし"

        with st.spinner("🤖 Geminiが展開シミュレーション・展示タイム・機力を解析中..."):
            try:
                prediction_result = analyze_with_gemini(
                    api_key=api_key,
                    venue_name=current_venue["name"],
                    rno=selected_rno,
                    racelist_data=st.session_state.racelist_data,
                    before_data=st.session_state.before_data,
                    focus_type=focus_type
                )
                
                st.markdown("### 🎯 AIフォーメーション予想結果")
                st.markdown(prediction_result)
                
            except Exception as e:
                st.error(f"予想生成エラー: {str(e)}")
