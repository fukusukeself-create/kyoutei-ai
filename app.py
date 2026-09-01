import os
import json
import streamlit as st
from google import genai
from google.genai import types

# ページ基本設定
st.set_page_config(page_title="BoatAI - 競艇予想", layout="wide", initial_sidebar_state="expanded")

# Gemini クライアント初期化
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not GEMINI_API_KEY:
    GEMINI_API_KEY = st.sidebar.text_input("Gemini API Key を入力", type="password")

# 全24競艇場リスト
STADIUMS = [
    "桐生", "戸田", "江戸川", "平和島", "多摩川", "浜名湖", "蒲郡", "常滑",
    "津", "三国", "びわこ", "住之江", "尼崎", "鳴門", "丸亀", "児島",
    "宮島", "徳山", "下関", "若松", "芦屋", "福岡", "唐津", "大村"
]

def analyze_race_with_gemini(stadium, race_no, race_data, api_key):
    client = genai.Client(api_key=api_key)
    
    prompt = f"""
あなたはプロの競艇データアナリストです。
以下のレース情報をもとに、展開予測と最適な買い目を導き出してください。

【開催場】: {stadium} 競艇場
【レース】: {race_no}R
【出走データ】:
{json.dumps(race_data, ensure_ascii=False, indent=2)}

以下のJSONフォーマットのみを出力してください:
{{
  "race_summary": "レース展開の総評",
  "confidence_score": 85,
  "recommendations": {{
    "honmei": ["1-2-3", "1-2-4", "1-3-2", "1-3-4"],
    "osae": ["1-4-2", "1-4-3"],
    "ana": ["3-1-4", "4-1-2"]
  }}
}}
"""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2
        )
    )
    return json.loads(response.text)

# UI
st.title("🚤 Gemini 競艇AI予想システム")
st.caption("全国24場対応 / 展開予測 & 買い目")

st.sidebar.header("レース選択")
selected_stadium = st.sidebar.selectbox("競艇場を選択", STADIUMS)
selected_race = st.sidebar.slider("レース番号", 1, 12, 1)

sample_race_data = {
    "weather": {"wind": "追風 2m", "wave": "1cm", "weather": "晴"},
    "boats": [
        {"num": 1, "name": "選手A", "rank": "A1", "win_rate": 7.35, "motor_rate": 42.5, "st_avg": 0.13},
        {"num": 2, "name": "選手B", "rank": "A2", "win_rate": 6.12, "motor_rate": 33.1, "st_avg": 0.16},
        {"num": 3, "name": "選手C", "rank": "B1", "win_rate": 5.40, "motor_rate": 28.9, "st_avg": 0.18},
        {"num": 4, "name": "選手D", "rank": "A1", "win_rate": 6.88, "motor_rate": 51.2, "st_avg": 0.12},
        {"num": 5, "name": "選手E", "rank": "B1", "win_rate": 4.95, "motor_rate": 30.0, "st_avg": 0.19},
        {"num": 6, "name": "選手F", "rank": "B2", "win_rate": 3.80, "motor_rate": 22.4, "st_avg": 0.21}
    ]
}

st.subheader(f"📍 {selected_stadium} {selected_race}R 出走表")
cols = st.columns(6)
for i, b in enumerate(sample_race_data["boats"]):
    with cols[i]:
        st.markdown(f"**{b['num']}号艇 ({b['rank']})**")
        st.write(b['name'])
        st.caption(f"勝率: {b['win_rate']}%")
        st.caption(f"モーター: {b['motor_rate']}%")
        st.caption(f"ST: {b['st_avg']}")

if st.button("🚀 Gemini でレースを分析", use_container_width=True):
    if not GEMINI_API_KEY:
        st.error("Gemini API Key を入力してください。")
    else:
        with st.spinner("AIが展開を分析中..."):
            try:
                result = analyze_race_with_gemini(selected_stadium, selected_race, sample_race_data, GEMINI_API_KEY)
                st.divider()
                st.subheader("📊 AI 展開分析結果")
                st.info(f"**展開予測:** {result['race_summary']}")
                st.metric("AI 自信度", f"{result['confidence_score']} / 100")
                
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.success("🎯 **本命**")
                    for buy in result['recommendations']['honmei']:
                        st.write(f"- `{buy}`")
                with c2:
                    st.warning("🛡️ **抑え**")
                    for buy in result['recommendations']['osae']:
                        st.write(f"- `{buy}`")
                with c3:
                    st.error("⚡ **穴**")
                    for buy in result['recommendations']['ana']:
                        st.write(f"- `{buy}`")
            except Exception as e:
                st.error(f"エラー: {e}")
