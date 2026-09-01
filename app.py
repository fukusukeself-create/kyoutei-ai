import os
import json
import time
from datetime import datetime, timezone, timedelta
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

# 日本標準時 (JST)
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)

# 1. APIキー取得（Secrets または 環境変数優先）
GEMINI_API_KEY = ""
if "GEMINI_API_KEY" in st.secrets:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
elif os.environ.get("GEMINI_API_KEY"):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
else:
    GEMINI_API_KEY = st.sidebar.text_input("🔑 Gemini API Key を入力", type="password")

# 2. フォーメーション厳密計算エンジン（重複排除・昇順ソート・点数計算）
def parse_and_expand_formation(formation_str):
    try:
        parts = str(formation_str).strip().replace(" ", "").split("-")
        if len(parts) != 3:
            return str(formation_str), [], 0

        first_ranks = sorted(list(set([int(c) for c in parts[0] if c.isdigit()])))
        second_ranks = sorted(list(set([int(c) for c in parts[1] if c.isdigit()])))
        third_ranks = sorted(list(set([int(c) for c in parts[2] if c.isdigit()])))

        clean_first = "".join(map(str, first_ranks))
        clean_second = "".join(map(str, second_ranks))
        clean_third = "".join(map(str, third_ranks))
        formatted_str = f"{clean_first}-{clean_second}-{clean_third}"

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

# 3. リアルタイム動的スケジュール設定
STADIUM_DEFINITIONS = [
    {"id": "01", "name": "桐生", "type": "night", "active": False},
    {"id": "02", "name": "戸田", "type": "day", "active": False},
    {"id": "03", "name": "江戸川", "type": "day", "active": False},
    {"id": "04", "name": "平和島", "type": "day", "active": False},
    {"id": "05", "name": "多摩川", "type": "day", "grade": "G3", "active": True, "start_h": 10, "start_m": 45},
    {"id": "06", "name": "浜名湖", "type": "day", "grade": "G3", "active": True, "start_h": 10, "start_m": 30},
    {"id": "07", "name": "蒲郡", "type": "night", "active": True, "start_h": 15, "start_m": 15},
    {"id": "08", "name": "常滑", "type": "day", "active": True, "start_h": 10, "start_m": 40},
    {"id": "09", "name": "津", "type": "day", "active": False},
    {"id": "10", "name": "三国", "type": "day", "active": False},
    {"id": "11", "name": "びわこ", "type": "day", "active": False},
    {"id": "12", "name": "住之江", "type": "night", "active": True, "start_h": 15, "start_m": 0},
    {"id": "13", "name": "尼崎", "type": "day", "active": True, "start_h": 10, "start_m": 30},
    {"id": "14", "name": "鳴門", "type": "day", "active": False},
    {"id": "15", "name": "丸亀", "type": "night", "active": False},
    {"id": "16", "name": "児島", "type": "day", "active": True, "start_h": 10, "start_m": 35},
    {"id": "17", "name": "宮島", "type": "day", "active": False},
    {"id": "18", "name": "徳山", "type": "day", "active": True, "start_h": 8, "start_m": 45},
    {"id": "19", "name": "下関", "type": "night", "active": True, "start_h": 15, "start_m": 10},
    {"id": "20", "name": "若松", "type": "night", "grade": "G3", "active": True, "start_h": 15, "start_m": 20},
    {"id": "21", "name": "芦屋", "type": "day", "active": True, "start_h": 8, "start_m": 30},
    {"id": "22", "name": "福岡", "type": "day", "active": False},
    {"id": "23", "name": "唐津", "type": "day", "active": False},
    {"id": "24", "name": "大村", "type": "night", "active": True, "start_h": 17, "start_m": 10},
]

def calculate_dynamic_schedule():
    current_time_minutes = now_jst.hour * 60 + now_jst.minute
    stadium_status_list = []
    upcoming_races = []

    for item in STADIUM_DEFINITIONS:
        st_data = item.copy()
        if not item.get("active"):
            st_data["display_status"] = "開催なし"
            st_data["r_text"] = "--:--"
            st_data["is_racing"] = False
            st_data["current_round"] = 1
            stadium_status_list.append(st_data)
            continue

        start_min = item["start_h"] * 60 + item["start_m"]
        current_r = None
        current_r_time_str = ""
        is_finished = True

        for r in range(1, 13):
            r_close_min = start_min + (r - 1) * 30
            close_h = r_close_min // 60
            close_m = r_close_min % 60
            time_str = f"{close_h:02d}:{close_m:02d}"

            if r_close_min > current_time_minutes:
                current_r = r
                current_r_time_str = time_str
                is_finished = False
                
                upcoming_races.append({
                    "stadium": item["name"],
                    "round": r,
                    "name": "予選 / 特賞" if r >= 7 else "予選",
                    "time": time_str,
                    "time_min": r_close_min,
                    "night": item["type"] == "night",
                    "grade": item.get("grade", "")
                })
                break

        if is_finished:
            st_data["display_status"] = "発売終了"
            st_data["r_text"] = "12R 終了"
            st_data["is_racing"] = False
            st_data["current_round"] = 12
        else:
            st_data["display_status"] = "一般戦" if not item.get("grade") else item.get("grade")
            st_data["r_text"] = f"{current_r}R {current_r_time_str}"
            st_data["is_racing"] = True
            st_data["current_round"] = current_r

        stadium_status_list.append(st_data)

    upcoming_races = sorted(upcoming_races, key=lambda x: x["time_min"])
    return stadium_status_list, upcoming_races

dynamic_stadiums, dynamic_timeline = calculate_dynamic_schedule()

# AI予想関数（安全な多段フォールバック）
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
    # 安定稼働するFlashモデル一覧
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    last_err = None
    for m in models:
        try:
            res = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2)
            )
            return json.loads(res.text), m
        except Exception as e:
            last_err = e
            time.sleep(0.5)
            continue
    raise Exception(f"AI解析エラー: {last_err}")

# セッション状態初期化
if "selected_stadium" not in st.session_state:
    st.session_state["selected_stadium"] = "下関"
if "selected_race" not in st.session_state:
    st.session_state["selected_race"] = 8

# --- アプリヘッダー ---
st.markdown(f"""
<div style="background-color:#004b91; color:#ffffff; padding:12px 16px; border-radius:8px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
    <span style="font-size:18px; font-weight:bold; color:#ffffff;">🚤 BOAT RACE AI 予想ナビ</span>
    <span style="font-size:13px; font-weight:bold; background-color:#ffffff; color:#004b91; padding:3px 8px; border-radius:4px;">{now_jst.strftime('%H:%M')} JST</span>
</div>
""", unsafe_allow_html=True)

# 確実・安定な標準タブナビゲーション
tab1, tab2, tab3 = st.tabs(["🚩 開催一覧", "⏰ 締切順（リアルタイム）", "🎯 レース詳細・AI分析"])

# ----------------- TAB 1: 開催一覧 -----------------
with tab1:
    st.markdown("##### 🚩 本日の開催場（タップするとレース詳細へ移動します）")
    cols = st.columns(4)
    for idx, item in enumerate(dynamic_stadiums):
        with cols[idx % 4]:
            is_night = "🌙 " if item.get("type") == "night" else ""
            grade_b = f"[{item['grade']}] " if item.get("grade") else ""
            
            # 開催中の場
            if item.get("is_racing"):
                btn_label = f"{is_night}{item['name']}\n{grade_b}{item.get('display_status')}\n{item.get('r_text')}"
                if st.button(btn_label, key=f"std_btn_{item['id']}", use_container_width=True, type="primary"):
                    st.session_state["selected_stadium"] = item["name"]
                    st.session_state["selected_race"] = item.get("current_round", 1)
                    st.toast(f"{item['name']} を選択しました！「🎯 レース詳細・AI分析」タブを開いてください。")
            else:
                # 終了 / 開催なし（薄グレー表示で文字は濃いグレー）
                st.markdown(f"""
                <div style="background-color:#f1f5f9; border:1px solid #cbd5e1; border-radius:6px; padding:8px; text-align:center; margin-bottom:8px;">
                    <div style="font-weight:bold; color:#64748b; font-size:14px;">{is_night}{item['name']}</div>
                    <div style="color:#94a3b8; font-size:11px;">{item.get('display_status')}</div>
                    <div style="color:#ef4444; font-size:12px; font-weight:bold;">{item.get('r_text')}</div>
                </div>
                """, unsafe_allow_html=True)

# ----------------- TAB 2: 締切順 -----------------
with tab2:
    st.markdown("##### ⏰ まもなく締切のレース（締切順）")
    if not dynamic_timeline:
        st.info("本日の全レース発売が終了しました。")
    else:
        for r in dynamic_timeline[:8]:
            st.markdown(f"""
            <div style="background-color:#ffffff; border:1.5px solid #cbd5e1; border-radius:8px; padding:12px; margin-bottom:10px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span style="font-size:18px; font-weight:bold; color:#0f172a;">{'🌙 ' if r.get('night') else ''}{r['stadium']}</span>
                        <span style="font-size:14px; font-weight:bold; color:#005bac; margin-left:8px;">{r['round']}R {r['name']}</span>
                    </div>
                    <div>
                        <span style="font-size:12px; color:#64748b;">締切予定:</span>
                        <span style="font-size:20px; font-weight:bold; color:#dc2626; margin-left:4px;">{r['time']}</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"👉 {r['stadium']} {r['round']}R のAI予想を見る", key=f"time_btn_{r['stadium']}_{r['round']}", use_container_width=True):
                st.session_state["selected_stadium"] = r["stadium"]
                st.session_state["selected_race"] = r["round"]
                st.toast(f"{r['stadium']} {r['round']}R を読み込みました！「🎯 レース詳細・AI分析」タブを開いてください。")

# ----------------- TAB 3: レース詳細・AI分析 -----------------
with tab3:
    active_names = [s["name"] for s in dynamic_stadiums if s.get("is_racing")]
    if not active_names:
        active_names = ["住之江", "下関", "蒲郡", "若松", "大村"]
        
    c_sel1, c_sel2 = st.columns(2)
    with c_sel1:
        cur_idx = active_names.index(st.session_state["selected_stadium"]) if st.session_state["selected_stadium"] in active_names else 0
        cur_stadium = st.selectbox("競艇場", active_names, index=cur_idx)
    with c_sel2:
        cur_race = st.slider("レース番号", 1, 12, value=int(st.session_state.get("selected_race", 8)))

    # 出走表データ
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
    st.markdown(f"""
    <div style="background-color:#e0f2fe; border:1px solid #bae6fd; border-radius:6px; padding:8px 12px; margin-bottom:12px; color:#0369a1; font-size:13px;">
        🌊 <b>水面気象</b>: 天候: {w['weather']} | 風: {w['wind']} | 波高: {w['wave']} | 気温: {w['temp']}
    </div>
    """, unsafe_allow_html=True)

    st.markdown("##### 📋 出走表・展示気配・単勝オッズ")
    
    # 艇番カラー定義（ヘッダーのみ色付け、中身は白背景に黒文字）
    header_styles = [
        {"bg": "#f8fafc", "text": "#0f172a", "border": "#94a3b8"},  # 1号艇
        {"bg": "#1e293b", "text": "#ffffff", "border": "#0f172a"},  # 2号艇
        {"bg": "#dc2626", "text": "#ffffff", "border": "#b91c1c"},  # 3号艇
        {"bg": "#2563eb", "text": "#ffffff", "border": "#1d4ed8"},  # 4号艇
        {"bg": "#eab308", "text": "#0f172a", "border": "#ca8a04"},  # 5号艇
        {"bg": "#16a34a", "text": "#ffffff", "border": "#15803d"},  # 6号艇
    ]
    
    boat_cols = st.columns(6)
    for i, b in enumerate(race_info["boats"]):
        hs = header_styles[i]
        with boat_cols[i]:
            st.markdown(f"""
            <div style="background-color:#ffffff; border:1.5px solid #cbd5e1; border-radius:8px; overflow:hidden; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,0.05); margin-bottom:10px;">
                <div style="background-color:{hs['bg']}; color:{hs['text']}; font-weight:bold; font-size:14px; padding:4px; border-bottom:1px solid {hs['border']};">
                    {b['num']}号艇 ({b['rank']})
                </div>
                <div style="padding:8px 4px; color:#0f172a; font-size:12px;">
                    <div style="font-weight:bold; font-size:15px; color:#0f172a; margin-bottom:2px;">{b['name']}</div>
                    <div style="color:#64748b; font-size:11px;">{b['branch']}支部</div>
                    <hr style="margin:6px 0; border:0; border-top:1px solid #e2e8f0;">
                    <div style="color:#334155;">展示 <b style="color:#0f172a;">{b['ex_time']}</b></div>
                    <div style="color:#334155;">モーター <b style="color:#0f172a;">{b['motor_rate']}%</b></div>
                    <div style="color:#dc2626; font-weight:bold; background-color:#fee2e2; border-radius:4px; padding:2px; margin-top:4px;">
                        単勝 {b['odds']}倍
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

    st.write("")
    
    if st.button(f"🚀 {cur_stadium} {cur_race}R フォーメーション予想を算出", use_container_width=True, type="primary"):
        if not GEMINI_API_KEY:
            st.error("左側メニューまたはSecretsで Gemini API Key を設定してください。")
        else:
            with st.spinner("AIが展示タイム・スリット展開を分析中..."):
                try:
                    res, used_model = analyze_with_ai(cur_stadium, cur_race, race_info, GEMINI_API_KEY)
                    st.success(f"✅ 解析完了（Engine: {used_model}）")
                    
                    # 展開予測
                    st.markdown(f"""
                    <div style="background-color:#ffffff; border-left:5px solid #005bac; border-radius:8px; padding:14px; box-shadow:0 1px 4px rgba(0,0,0,0.05); margin-bottom:14px;">
                        <h4 style="color:#005bac; margin:0 0 6px 0;">📊 展開予測</h4>
                        <div style="font-size:14px; color:#334155; margin-bottom:6px;">
                            主要決まり手: <b style="color:#005bac;">{res['flow']}</b> | AI 自信度: <b style="color:#dc2626;">{res['confidence']}%</b>
                        </div>
                        <p style="color:#0f172a; font-size:14px; line-height:1.5; margin:0;">{res['summary']}</p>
                        <div style="color:#64748b; font-size:12px; margin-top:6px;">根拠: {res.get('reason', '')}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.markdown("#### 🎯 3連単フォーメーション（点数自動計算済）")
                    
                    f_hon, list_hon, count_hon = parse_and_expand_formation(res.get("honmei_raw", ""))
                    f_osa, list_osa, count_osa = parse_and_expand_formation(res.get("osae_raw", ""))
                    f_ana, list_ana, count_ana = parse_and_expand_formation(res.get("ana_raw", ""))
                    
                    c1, c2, c3 = st.columns(3)
                    
                    with c1:
                        st.markdown(f"""
                        <div style="background-color:#f0fdf4; border:1px solid #bbf7d0; border-left:5px solid #16a34a; border-radius:8px; padding:12px;">
                            <span style="background-color:#16a34a; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_hon} 点</span>
                            <div style="color:#16a34a; font-weight:bold; font-size:15px;">🎯 本命</div>
                            <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_hon}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_hon}点)"):
                            st.write(", ".join(list_hon))
                            
                    with c2:
                        st.markdown(f"""
                        <div style="background-color:#fff7ed; border:1px solid #fed7aa; border-left:5px solid #ea580c; border-radius:8px; padding:12px;">
                            <span style="background-color:#ea580c; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_osa} 点</span>
                            <div style="color:#ea580c; font-weight:bold; font-size:15px;">🛡️ 抑え</div>
                            <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_osa}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_osa}点)"):
                            st.write(", ".join(list_osa))
                            
                    with c3:
                        st.markdown(f"""
                        <div style="background-color:#fef2f2; border:1px solid #fecaca; border-left:5px solid #dc2626; border-radius:8px; padding:12px;">
                            <span style="background-color:#dc2626; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_ana} 点</span>
                            <div style="color:#dc2626; font-weight:bold; font-size:15px;">⚡ 穴・高配当</div>
                            <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_ana}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_ana}点)"):
                            st.write(", ".join(list_ana))

                except Exception as e:
                    st.error(f"{e}")
