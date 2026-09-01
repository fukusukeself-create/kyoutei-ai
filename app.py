import os
import json
import time
import streamlit as st
from google import genai
from google.genai import types

# ページ基本設定
st.set_page_config(
    page_title="BOAT RACE AI 予想ナビ",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# カスタムCSS（公式アプリ風デザイン）
st.markdown("""
<style>
    .stApp { background-color: #f4f6f9; }
    .main-header {
        background: linear-gradient(90deg, #005bac 0%, #0088cc 100%);
        color: white;
        padding: 12px 16px;
        border-radius: 8px;
        margin-bottom: 15px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    .card-grade { font-size: 10px; background: #005bac; color: white; border-radius: 3px; padding: 1px 4px; display: inline-block; }
    .form-box {
        background: #ffffff;
        padding: 12px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
        margin-bottom: 10px;
    }
    .point-badge {
        background-color: #d32f2f;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 13px;
        font-weight: bold;
        float: right;
    }
</style>
""", unsafe_allow_html=True)

# 1. APIキーの自動取得（Secrets優先）
GEMINI_API_KEY = ""
if "GEMINI_API_KEY" in st.secrets:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
elif os.environ.get("GEMINI_API_KEY"):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
else:
    GEMINI_API_KEY = st.sidebar.text_input("Gemini API Key を入力", type="password")

# 2. フォーメーション厳密計算エンジン（重複排除・昇順ソート・点数計算）
def parse_and_expand_formation(formation_str):
    try:
        parts = str(formation_str).strip().replace(" ", "").split("-")
        if len(parts) != 3:
            return str(formation_str), [], 0

        # 各桁の数字を昇順で重複排除
        first_ranks = sorted(list(set([int(c) for c in parts[0] if c.isdigit()])))
        second_ranks = sorted(list(set([int(c) for c in parts[1] if c.isdigit()])))
        third_ranks = sorted(list(set([int(c) for c in parts[2] if c.isdigit()])))

        clean_first = "".join(map(str, first_ranks))
        clean_second = "".join(map(str, second_ranks))
        clean_third = "".join(map(str, third_ranks))
        formatted_str = f"{clean_first}-{clean_second}-{clean_third}"

        # 3連単の買い目を重複なしで生成
        combinations = []
        for f in first_ranks:
            for s in second_ranks:
                if s == f:
                    continue
                for t in third_ranks:
                    if t == f or t == s:
                        continue
                    combinations.append(f"{f}-{s}-{t}")

        return formatted_str, combinations, len(combinations)
    except Exception:
        return str(formation_str), [], 0

# 全国24場マスターデータ
STADIUM_MASTER = [
    {"id": "01", "name": "桐生", "type": "night", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "02", "name": "戸田", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "03", "name": "江戸川", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "04", "name": "平和島", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "05", "name": "多摩川", "type": "day", "grade": "G3", "day_num": "5日目", "status": "発売終了", "r": "12R", "time": "終了"},
    {"id": "06", "name": "浜名湖", "type": "day", "grade": "G3", "day_num": "3日目", "status": "発売終了", "r": "12R", "time": "終了"},
    {"id": "07", "name": "蒲郡", "type": "night", "status": "一般 最終日", "r": "8R", "time": "18:28", "active": True},
    {"id": "08", "name": "常滑", "type": "day", "status": "一般 3日目", "r": "12R", "time": "終了"},
    {"id": "09", "name": "津", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "10", "name": "三国", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "11", "name": "びわこ", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "12", "name": "住之江", "type": "night", "status": "一般 初日", "r": "8R", "time": "18:22", "active": True},
    {"id": "13", "name": "尼崎", "type": "day", "status": "一般 初日", "r": "12R", "time": "終了"},
    {"id": "14", "name": "鳴門", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "15", "name": "丸亀", "type": "night", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "16", "name": "児島", "type": "day", "status": "一般 4日目", "r": "12R", "time": "終了"},
    {"id": "17", "name": "宮島", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "18", "name": "徳山", "type": "day", "status": "一般 2日目", "r": "12R", "time": "終了"},
    {"id": "19", "name": "下関", "type": "night", "status": "一般 4日目", "r": "7R", "time": "18:15", "active": True},
    {"id": "20", "name": "若松", "type": "night", "grade": "G3", "day_num": "2日目", "status": "8R", "r": "8R", "time": "18:40", "active": True},
    {"id": "21", "name": "芦屋", "type": "day", "status": "一般 3日目", "r": "12R", "time": "終了"},
    {"id": "22", "name": "福岡", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "23", "name": "唐津", "type": "day", "status": "開催なし", "r": "", "time": "--:--"},
    {"id": "24", "name": "大村", "type": "night", "status": "一般 初日", "r": "3R", "time": "18:34", "active": True},
]

TIMELINE_RACES = [
    {"stadium": "下関", "round": 7, "name": "ふく〜る特賞", "day": "4日目", "time": "18:15", "night": True},
    {"stadium": "住之江", "round": 8, "name": "予選", "day": "初日", "time": "18:22", "night": True},
    {"stadium": "蒲郡", "round": 8, "name": "一般戦", "day": "最終日", "time": "18:28", "night": True},
    {"stadium": "大村", "round": 3, "name": "予選", "day": "初日", "time": "18:34", "night": True},
    {"stadium": "若松", "round": 8, "name": "エイトビート", "day": "2日目", "grade": "G3", "time": "18:40", "night": True},
    {"stadium": "下関", "round": 8, "name": "一般戦", "day": "4日目", "time": "18:45", "night": True},
    {"stadium": "住之江", "round": 9, "name": "予選", "day": "初日", "time": "18:52", "night": True},
]

# AI 予想関数（3.7 Flash優先 ➔ 3.6 Flash フォールバック）
def analyze_with_ai(stadium, race_no, race_data, api_key):
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたはプロ競艇予想AIです。以下の直前情報・展示タイム・オッズから勝てる3連単フォーメーションを提案してください。
開催場: {stadium} {race_no}R
データ:
{json.dumps(race_data, ensure_ascii=False, indent=2)}

【必須ルール】
・フォーメーションは必ず「数字昇順」（例: 1-23-2345 のように各桁の数字を小さい順）で記述すること。
・本命（4〜8点目安）、抑え（2〜6点目安）、穴（6〜12点目安）のフォーメーション文字列を出力すること。

以下のJSON形式でのみ回答してください:
{{
  "summary": "スリット隊形と1マーク攻防の具体的予測",
  "confidence": 85,
  "flow": "逃げ / 差し / まくり差し",
  "honmei_raw": "1-23-2345",
  "osae_raw": "1-24-234",
  "ana_raw": "23-123-12345",
  "reason": "買い目の根拠"
}}
"""
    models_to_try = ["gemini-3.7-flash", "gemini-3.6-flash"]
    for m in models_to_try:
        try:
            res = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
            )
            return json.loads(res.text), m
        except Exception:
            time.sleep(1)
            continue
    raise Exception("一時的にサーバーが混雑しています。数十秒後に再試行してください。")

# --- UI 構築 ---
st.markdown("""
<div class="main-header">
    <div style="font-size:20px; font-weight:bold;">🚤 BOAT RACE AI ナビゲーター</div>
    <div style="font-size:12px;">3連単フォーメーション自動計算</div>
</div>
""", unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["🚩 開催一覧", "⏰ 締切順", "🎯 レース詳細・AI分析"])

# TAB 1: 開催一覧（KeyError対策済み）
with tab1:
    st.caption("本日の開催レース場")
    cols = st.columns(4)
    for idx, item in enumerate(STADIUM_MASTER):
        with cols[idx % 4]:
            is_night = "🌙 " if item.get("type") == "night" else ""
            grade_badge = f"<span class='card-grade'>{item.get('grade')}</span> " if item.get("grade") else ""
            active_border = "border: 2px solid #005bac;" if item.get("active") else "opacity: 0.6;"
            bg_color = "#eef5fc" if item.get("active") else "#fbfbfb"
            
            r_text = item.get("r", "")
            time_text = item.get("time", "")
            status_display = f"{r_text} {time_text}".strip()
            
            st.markdown(f"""
            <div style="background:{bg_color}; padding:10px; border-radius:8px; margin-bottom:8px; {active_border} text-align:center;">
                <div style="font-size:16px; font-weight:bold; color:#111;">{is_night}{item.get('name', '')}</div>
                <div style="font-size:11px; margin-top:2px;">{grade_badge}{item.get('status', '')}</div>
                <div style="font-size:13px; font-weight:bold; color:#d32f2f; margin-top:4px;">{status_display}</div>
            </div>
            """, unsafe_allow_html=True)

# TAB 2: 締切順
with tab2:
    st.subheader("⏱️ まもなく締切のレース")
    for r in TIMELINE_RACES:
        c1, c2, c3, c4 = st.columns([3, 4, 3, 2])
        with c1:
            night_icon = "🌙 " if r.get("night") else ""
            st.markdown(f"### {night_icon}{r.get('stadium', '')}")
            st.caption(f"{r.get('day', '')}")
        with c2:
            st.markdown(f"**{r.get('round', '')}R** {r.get('name', '')}")
        with c3:
            st.markdown(f"締切予定 **<span style='color:#d32f2f; font-size:18px;'>{r.get('time', '')}</span>**", unsafe_allow_html=True)
        with c4:
            if st.button("予想を見る", key=f"btn_{r.get('stadium')}_{r.get('round')}"):
                st.session_state["selected_stadium"] = r.get('stadium')
                st.session_state["selected_race"] = r.get('round')
                st.toast(f"{r.get('stadium')} {r.get('round')}R を読み込みました！")
        st.divider()

# TAB 3: レース詳細・AI分析
with tab3:
    col_sel1, col_sel2 = st.columns(2)
    with col_sel1:
        cur_stadium = st.selectbox("競艇場", ["住之江", "下関", "蒲郡", "若松", "大村", "多摩川", "浜名湖", "戸田", "平和島", "児島", "徳山"], index=0)
    with col_sel2:
        cur_race = st.slider("レース", 1, 12, value=8)

    race_info = {
        "weather": {"weather": "晴", "wind": "北西 3m（追風）", "wave": "2cm", "temp": "26℃"},
        "boats": [
            {"num": 1, "name": "峰 竜太", "rank": "A1", "branch": "佐賀", "motor_rate": 44.2, "ex_time": 6.68, "tilt": -0.5, "odds": 1.4},
            {"num": 2, "name": "毒島 誠", "rank": "A1", "branch": "群馬", "motor_rate": 39.5, "ex_time": 6.72, "tilt": -0.5, "odds": 4.8},
            {"num": 3, "name": "茅原 悠紀", "rank": "A1", "branch": "岡山", "motor_rate": 52.1, "ex_time": 6.64, "tilt": -0.5, "odds": 6.2},
            {"num": 4, "name": "白井 英治", "rank": "A1", "branch": "山口", "motor_rate": 33.0, "ex_time": 6.75, "tilt": 0.0, "odds": 12.5},
            {"num": 5, "name": "馬場 貴也", "rank": "A1", "branch": "滋賀", "motor_rate": 37.8, "ex_time": 6.70, "tilt": -0.5, "odds": 18.0},
            {"num": 6, "name": "池田 浩二", "rank": "A1", "branch": "愛知", "motor_rate": 29.4, "ex_time": 6.78, "tilt": 0.0, "odds": 34.0}
        ]
    }

    w = race_info["weather"]
    st.info(f"🌊 **水面気象**: 天候: {w['weather']} | 風: {w['wind']} | 波高: {w['wave']}")

    st.markdown("##### 📋 出走表・展示気配・オッズ")
    boat_cols = st.columns(6)
    colors = ["#ffffff", "#212121", "#d32f2f", "#1976d2", "#fbc02d", "#388e3c"]
    text_colors = ["#000", "#fff", "#fff", "#fff", "#000", "#fff"]

    for i, b in enumerate(race_info["boats"]):
        with boat_cols[i]:
            st.markdown(f"""
            <div style="background:{colors[i]}; color:{text_colors[i]}; border:1px solid #999; border-radius:6px; padding:6px; text-align:center; font-size:12px;">
                <div style="font-size:16px; font-weight:bold;">{b['num']}号艇</div>
                <div style="font-weight:bold; font-size:14px; margin:2px 0;">{b['name']}</div>
                <div>{b['rank']} / {b['branch']}</div>
                <hr style="margin:4px 0; border-color:#888;">
                <div>展示: <b>{b['ex_time']}</b></div>
                <div>2連率: {b['motor_rate']}%</div>
                <div style="color:#d32f2f; font-weight:bold; background:#fff; border-radius:3px; margin-top:2px;">単勝 {b['odds']}倍</div>
            </div>
            """, unsafe_allow_html=True)

    st.write("")
    
    if st.button(f"🚀 {cur_stadium} {cur_race}R フォーメーション予想を算出", use_container_width=True):
        if not GEMINI_API_KEY:
            st.error("Gemini API Key が設定されていません。")
        else:
            with st.spinner("AIが展開分析＆厳密なフォーメーション計算中..."):
                try:
                    res, used_model = analyze_with_ai(cur_stadium, cur_race, race_info, GEMINI_API_KEY)
                    
                    st.success(f"✅ 解析完了（Engine: {used_model}）")
                    st.markdown("### 📊 展開予測")
                    st.markdown(f"> **主要決まり手**: `{res['flow']}`  | **AI 自信度**: **{res['confidence']}%**")
                    st.info(f"**展開分析**: {res['summary']}")
                    st.caption(f"**根拠**: {res.get('reason', '')}")
                    
                    st.markdown("### 🎯 3連単フォーメーション（点数自動計算済）")
                    
                    f_hon, list_hon, count_hon = parse_and_expand_formation(res.get("honmei_raw", ""))
                    f_osa, list_osa, count_osa = parse_and_expand_formation(res.get("osae_raw", ""))
                    f_ana, list_ana, count_ana = parse_and_expand_formation(res.get("ana_raw", ""))
                    
                    c1, c2, c3 = st.columns(3)
                    
                    with c1:
                        st.markdown(f"""
                        <div class="form-box" style="border-left: 5px solid #2e7d32;">
                            <span class="point-badge" style="background-color:#2e7d32;">計 {count_hon} 点</span>
                            <div style="color:#2e7d32; font-weight:bold; font-size:16px;">🎯 本命</div>
                            <div style="font-size:22px; font-weight:bold; margin: 8px 0; color:#111;">{f_hon}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_hon}点)"):
                            st.write(", ".join(list_hon))
                            
                    with c2:
                        st.markdown(f"""
                        <div class="form-box" style="border-left: 5px solid #ef6c00;">
                            <span class="point-badge" style="background-color:#ef6c00;">計 {count_osa} 点</span>
                            <div style="color:#ef6c00; font-weight:bold; font-size:16px;">🛡️ 抑え</div>
                            <div style="font-size:22px; font-weight:bold; margin: 8px 0; color:#111;">{f_osa}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_osa}点)"):
                            st.write(", ".join(list_osa))
                            
                    with c3:
                        st.markdown(f"""
                        <div class="form-box" style="border-left: 5px solid #c62828;">
                            <span class="point-badge" style="background-color:#c62828;">計 {count_ana} 点</span>
                            <div style="color:#c62828; font-weight:bold; font-size:16px;">⚡ 穴・高配当</div>
                            <div style="font-size:22px; font-weight:bold; margin: 8px 0; color:#111;">{f_ana}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_ana}点)"):
                            st.write(", ".join(list_ana))

                except Exception as e:
                    st.error(f"エラー: {e}")
