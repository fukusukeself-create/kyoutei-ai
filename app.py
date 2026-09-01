import os
import json
import time
import re
from datetime import datetime, timezone, timedelta
import streamlit as st
from google import genai
from google.genai import types

# ページ基本設定
st.set_page_config(
    page_title="BOAT RACE AI 最強予想ナビ",
    page_icon="🚤",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# モバイル対応・4列グリッド維持用カスタムCSS
st.markdown("""
<style>
/* スマホ画面でも横4列を維持する設定 */
div[data-testid="column"] {
    min-width: 23% !important;
    flex: 1 1 23% !important;
    padding: 2px !important;
}
div[data-testid="stHorizontalBlock"] {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: wrap !important;
    gap: 4px !important;
}
/* タブとボタンの調整 */
.stButton>button {
    padding: 2px 4px !important;
    font-size: 12px !important;
}
</style>
""", unsafe_allow_html=True)

# 日本標準時 (JST)
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)
today_display = now_jst.strftime("%m/%d")
today_full = now_jst.strftime("%Y年%m月%d日")

# 1. APIキー取得
GEMINI_API_KEY = ""
if "GEMINI_API_KEY" in st.secrets:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
elif os.environ.get("GEMINI_API_KEY"):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
else:
    GEMINI_API_KEY = st.sidebar.text_input("🔑 Gemini API Key を入力", type="password")

# 2. 全国24場 定義（公式アプリの配置順）
ALL_STADIUMS = [
    {"id": "01", "name": "桐生", "night": True},
    {"id": "02", "name": "戸田", "night": False},
    {"id": "03", "name": "江戸川", "night": False},
    {"id": "04", "name": "平和島", "night": False},
    {"id": "05", "name": "多摩川", "night": False},
    {"id": "06", "name": "浜名湖", "night": False},
    {"id": "07", "name": "蒲郡", "night": True},
    {"id": "08", "name": "常滑", "night": False},
    {"id": "09", "name": "津", "night": False},
    {"id": "10", "name": "三国", "night": False},
    {"id": "11", "name": "びわこ", "night": False},
    {"id": "12", "name": "住之江", "night": True},
    {"id": "13", "name": "尼崎", "night": False},
    {"id": "14", "name": "鳴門", "night": False},
    {"id": "15", "name": "丸亀", "night": True},
    {"id": "16", "name": "児島", "night": False},
    {"id": "17", "name": "宮島", "night": False},
    {"id": "18", "name": "徳山", "night": False},
    {"id": "19", "name": "下関", "night": True},
    {"id": "20", "name": "若松", "night": True},
    {"id": "21", "name": "芦屋", "night": False},
    {"id": "22", "name": "福岡", "night": False},
    {"id": "23", "name": "唐津", "night": False},
    {"id": "24", "name": "大村", "night": True},
]

# 3. フォーメーション計算エンジン
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
                if s == f: continue
                for t in third_ranks:
                    if t == f or t == s: continue
                    combinations.append(f"{f}-{s}-{t}")

        return formatted_str, combinations, len(combinations)
    except Exception:
        return str(formation_str), [], 0

# 4. Gemini リアルタイム検索による当日の全国24場データ取得（IP制限回避）
@st.cache_data(ttl=60)
def fetch_realtime_stadiums_data(api_key, date_text, current_hm):
    client = genai.Client(api_key=api_key)
    prompt = f"""
本日（{date_text} 現在時刻 {current_hm} JST）のボートレース（競艇）全国24場の最新開催状況を正確にWeb検索して抽出してください。

調査対象（24場）:
桐生, 戸田, 江戸川, 平和島, 多摩川, 浜名湖, 蒲郡, 常滑, 津, 三国, びわこ, 住之江, 尼崎, 鳴門, 丸亀, 児島, 宮島, 徳山, 下関, 若松, 芦屋, 福岡, 唐津, 大村

各場について以下の項目を特定してください:
1. is_active: 本日レース開催があるか（true/false）
2. grade: グレード（SG, G1, G2, G3, 一般）
3. day_text: 日程（初日, 2日目, 3日目, 4日目, 5日目, 最終日）
4. is_closed: 本日の全レースが終了しているか（true/false）
5. current_round: 現在発売中・直近のレース番号（1〜12）
6. race_title: そのレースの種別（予選, 選抜戦, 予選特賞, 一般特賞, 特選, 優勝戦 など）
7. deadline: 公式締切予定時刻（HH:MM形式）

必ず以下のJSON配列形式のみを出力してください:
[
  {{
    "name": "蒲郡",
    "is_active": true,
    "grade": "一般",
    "day_text": "最終日",
    "is_closed": false,
    "current_round": 10,
    "race_title": "選抜戦",
    "deadline": "19:36"
  }},
  {{
    "name": "桐生",
    "is_active": false,
    "grade": "",
    "day_text": "",
    "is_closed": false,
    "current_round": 1,
    "race_title": "",
    "deadline": "--:--"
  }}
]
"""
    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        text = res.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except Exception:
        return []

# 5. Gemini リアルタイム検索による指定レースの出走表・直前気配・気象の完全取得
@st.cache_data(ttl=30)
def fetch_realtime_race_detail(api_key, stadium, race_no, date_text):
    client = genai.Client(api_key=api_key)
    prompt = f"""
本日（{date_text}）のボートレース「{stadium} {race_no}R」の公式出走表・直前情報・水面気象データをWeb検索して完全取得してください。

取得項目:
1. レース名（例: 予選, 選抜戦, 優勝戦）
2. 締切予定時刻（例: 19:36）
3. 天候, 気温, 水温, 風向, 風速, 波高
4. 1号艇〜6号艇の全6艇データ:
   - 艇番 (1〜6)
   - 選手名 (フルネーム)
   - 登番 (4桁)
   - 級別 (A1, A2, B1, B2)
   - 支部 (例: 愛知, 福岡)
   - 全国勝率 (例: 6.24)
   - 全国2連率 (例: 45.2)
   - 当地勝率 (例: 6.80)
   - 当地2連率 (例: 50.0)
   - モーター番号 & モーター2連率 (例: No.12 / 38.5%)
   - ボート2連率 (例: 35.0%)
   - 平均スタートタイミング (例: 0.14)
   - 直前展示タイム (例: 6.72)
   - チルト角度 (例: -0.5)

必ず以下のJSON形式のみを出力してください:
{{
  "title": "選抜戦",
  "deadline": "19:36",
  "weather": {{
    "weather": "晴", "temp": "26℃", "water_temp": "24℃", "wind_dir": "北西", "wind_speed": "3m", "wave": "2cm"
  }},
  "boats": [
    {{
      "num": 1, "name": "西野 雄貴", "toban": "4812", "rank": "A1", "branch": "徳島",
      "nat_win": "6.45", "nat_2ren": "48.2", "loc_win": "6.10", "loc_2ren": "40.0",
      "motor_no": "15", "motor_rate": "36.5", "boat_rate": "34.0", "avg_st": "0.14",
      "ex_time": "6.71", "tilt": "-0.5"
    }}
  ]
}}
"""
    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        text = res.text.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}

# 6. AI 予想エンジン
def analyze_with_ai(stadium, race_no, race_data, api_key):
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたは回収率と的中率を極限まで追求するプロ競艇データサイエンティスト兼展開アナリストです。
以下の【公式出走表】【当地成績 vs 全国成績】【直前展示気配・チルト】【水面気象条件】を統合分析し、勝てる3連単フォーメーションを導き出してください。

【対象レース】: {stadium} 競艇場 {race_no}R
【入力データ】:
{json.dumps(race_data, ensure_ascii=False, indent=2)}

【分析方針】
1. **スリット隊形と進入攻防**: 各艇の平均STと直前展示タイム、チルトから1マークの進入隊形と仕掛ける艇を特定。
2. **舟足判定（出足・伸び足・回り足）**: 展示タイム最速艇やモーター実戦足を評価。
3. **フォーメーション厳格ルール**:
   - 各枠内は必ず「数字昇順（小さい順）」で記述すること（例: `1-23-2345`）。
   - 本命: 的中と回収のバランスが良い主軸（4〜8点目安）
   - 抑え: 展開もつれ時のバックアップ（2〜6点目安）
   - 穴: カド捲りや外枠強襲による高配当狙い（6〜12点目安）

以下のJSONフォーマットのみを出力してください:
{{
  "summary": "スリット隊形と1マーク攻防の具体的展開予測（誰が仕掛け、誰が展開を突くか）",
  "confidence": 88,
  "flow": "イン逃げ / 2コース差し / 3コースまくり差し / 4カドまくり",
  "motor_eval": "節イチ級モーターや展示気配が抜けている注目艇の解説",
  "honmei_raw": "1-23-2345",
  "osae_raw": "1-24-234",
  "ana_raw": "23-123-12345",
  "reason": "買い目の論理的根拠（当地相性・気象・オッズ妙味）"
}}
"""
    res = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.15
        )
    )
    text = res.text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    return json.loads(text)

# --- アプリヘッダー ---
st.markdown(f"""
<div style="background-color:#004b91; color:#ffffff; padding:10px 14px; border-radius:6px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
    <div style="font-size:18px; font-weight:bold;">🚤 BOAT RACE トップ</div>
    <div style="font-size:12px; background-color:#ffffff; color:#004b91; padding:3px 8px; border-radius:4px; font-weight:bold;">
        {now_jst.strftime('%H:%M')} JST (本日 {today_display})
    </div>
</div>
""", unsafe_allow_html=True)

if not GEMINI_API_KEY:
    st.warning("⚠️ サイドバーまたはSecretsに Gemini API Key を設定してください。")
    st.stop()

# リアルタイムデータの読み込み
with st.spinner("🌐 本日の全国24場 リアルタイム開催情報を更新中..."):
    current_hm = now_jst.strftime("%H:%M")
    raw_stadium_data = fetch_realtime_stadiums_data(GEMINI_API_KEY, today_full, current_hm)

# 辞書化
stadium_map = {item["name"]: item for item in raw_stadium_data if isinstance(item, dict) and "name" in item}

# 24場の状態統合
grid_data = []
timeline_data = []
for s in ALL_STADIUMS:
    info = stadium_map.get(s["name"], {})
    is_active = info.get("is_active", False)
    grade = info.get("grade", "")
    day_text = info.get("day_text", "")
    is_closed = info.get("is_closed", False)
    r_no = info.get("current_round", 1)
    r_title = info.get("race_title", "予選")
    dl_time = info.get("deadline", "--:--")

    entry = {
        "id": s["id"],
        "name": s["name"],
        "night": s["night"],
        "is_active": is_active,
        "grade": grade,
        "day_text": day_text,
        "is_closed": is_closed,
        "current_round": r_no,
        "race_title": r_title,
        "deadline": dl_time
    }
    grid_data.append(entry)

    if is_active and not is_closed and dl_time != "--:--":
        timeline_data.append(entry)

# セッション状態
if "selected_stadium" not in st.session_state:
    st.session_state["selected_stadium"] = timeline_data[0]["name"] if timeline_data else "蒲郡"
if "selected_race" not in st.session_state:
    st.session_state["selected_race"] = timeline_data[0]["current_round"] if timeline_data else 10

# タブ構成
tab1, tab2, tab3 = st.tabs(["🚩 開催一覧", "⏰ 締切順", "🎯 レース詳細・最強AI分析"])

# ==========================================
# TAB 1: 開催一覧（公式24場グリッドUI）
# ==========================================
with tab1:
    cols = st.columns(4)
    for idx, s in enumerate(grid_data):
        with cols[idx % 4]:
            if not s["is_active"]:
                st.markdown(f"""
                <div style="background-color:#f1f5f9; border:1px solid #cbd5e1; border-radius:6px; padding:10px 2px; text-align:center; min-height:86px; margin-bottom:6px;">
                    <div style="font-weight:bold; font-size:15px; color:#64748b;">{s['name']}</div>
                    <div style="font-size:15px; color:#94a3b8; margin-top:8px;">--</div>
                </div>
                """, unsafe_allow_html=True)
            elif s["is_closed"]:
                g_badge = f"<span style='background-color:#0284c7; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; margin-right:2px;'>{s['grade']}</span>" if s['grade'] and s['grade'] != "一般" else ""
                st.markdown(f"""
                <div style="background-color:#e2e8f0; border:1px solid #94a3b8; border-radius:6px; padding:6px 2px; text-align:center; min-height:86px; margin-bottom:6px;">
                    <div style="font-weight:bold; font-size:15px; color:#334155;">{s['name']}</div>
                    <div style="font-size:11px; color:#475569; margin-top:2px;">{g_badge}{s['grade'] if s['grade']=='一般' else ''} {s['day_text']}</div>
                    <div style="font-size:12px; color:#64748b; font-weight:bold; margin-top:4px;">発売終了</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                is_night_icon = "🌙 " if s["night"] else ""
                card_bg = "#e0e7ff" if s["night"] else "#eff6ff"
                border_c = "#6366f1" if s["night"] else "#3b82f6"
                g_badge = f"<span style='background-color:#2563eb; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; margin-right:2px;'>{s['grade']}</span>" if s['grade'] and s['grade'] != "一般" else ""
                
                st.markdown(f"""
                <div style="background-color:{card_bg}; border:1.5px solid {border_c}; border-radius:6px; padding:6px 2px; text-align:center; min-height:86px; margin-bottom:4px;">
                    <div style="font-weight:bold; font-size:15px; color:#0f172a;">{is_night_icon}{s['name']}</div>
                    <div style="font-size:11px; color:#334155; margin-top:2px;">{g_badge}{s['grade'] if s['grade']=='一般' else ''} {s['day_text']}</div>
                    <div style="font-size:13px; color:#dc2626; font-weight:bold; margin-top:4px;">{s['current_round']}R {s['deadline']}</div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("選択", key=f"grid_btn_{s['id']}", use_container_width=True):
                    st.session_state["selected_stadium"] = s["name"]
                    st.session_state["selected_race"] = s["current_round"]
                    st.toast(f"{s['name']} {s['current_round']}R を選択しました！「🎯 レース詳細・最強AI分析」タブを開いてください。")

# ==========================================
# TAB 2: 締切順（公式リストUI）
# ==========================================
with tab_menu2:
    if not timeline_data:
        st.info("本日のレース発売はすべて終了しました。")
    else:
        for r in timeline_data:
            is_night_str = "🌙 " if r["night"] else ""
            grade_badge = f"<span style='background-color:#2563eb; color:#ffffff; font-size:11px; font-weight:bold; padding:2px 6px; border-radius:4px; margin-right:6px;'>{r['grade']}</span>" if r['grade'] and r['grade'] != "一般" else ""
            card_bg = "#f5f3ff" if r["night"] else "#eff6ff"
            border_c = "#ddd6fe" if r["night"] else "#bfdbfe"

            st.markdown(f"""
            <div style="background-color:{card_bg}; border:1.5px solid {border_c}; border-radius:8px; padding:10px 14px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div style="display:flex; align-items:center;">
                        {grade_badge}
                        <span style="font-size:17px; font-weight:bold; color:#0f172a;">{is_night_str}{r['stadium'] if 'stadium' in r else r['name']}</span>
                        <span style="font-size:12px; color:#64748b; margin-left:8px;">{r['day_text']}</span>
                    </div>
                    <div style="font-size:15px; font-weight:bold; color:#0284c7; margin-top:2px;">
                        {r['current_round']}R {r['race_title']}
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b;">締切予定時刻</div>
                    <div style="font-size:22px; font-weight:bold; color:#dc2626;">{r['deadline']}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"👉 {r['name']} {r['current_round']}R の出走表・AI予想", key=f"tl_btn_{r['id']}", use_container_width=True):
                st.session_state["selected_stadium"] = r["name"]
                st.session_state["selected_race"] = r["current_round"]
                st.toast(f"{r['name']} {r['current_round']}R を選択しました！「🎯 レース詳細・最強AI分析」タブを開いてください。")

# ==========================================
# TAB 3: レース詳細・AI分析
# ==========================================
with tab3:
    active_names = [s["name"] for s in grid_data if s["is_active"]]
    if not active_names:
        active_names = [s["name"] for s in ALL_STADIUMS]

    c1, c2 = st.columns(2)
    with c1:
        cur_idx = active_names.index(st.session_state["selected_stadium"]) if st.session_state["selected_stadium"] in active_names else 0
        cur_stadium = st.selectbox("競艇場", active_names, index=cur_idx)
    with c2:
        cur_race = st.slider("レース番号", 1, 12, value=int(st.session_state.get("selected_race", 10)))

    # 詳細出走表取得
    with st.spinner(f"🌐 {cur_stadium} {cur_race}R の最新出走表・直前展示気配を取得中..."):
        race_info = fetch_realtime_race_detail(GEMINI_API_KEY, cur_stadium, cur_race, today_full)

    if not race_info or "boats" not in race_info or not race_info["boats"]:
        st.error(f"{cur_stadium} {cur_race}R の出走表データを取得できませんでした。")
    else:
        w = race_info.get("weather", {})
        
        # バナー
        st.markdown(f"""
        <div style="background-color:#0f172a; color:#ffffff; border-radius:8px; padding:10px 16px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
            <div>
                <span style="font-size:20px; font-weight:bold; color:#38bdf8;">{cur_stadium} {cur_race}R</span>
                <span style="font-size:14px; color:#cbd5e1; margin-left:8px;">{race_info.get('title', '予選')}</span>
            </div>
            <div>
                <span style="font-size:12px; color:#94a3b8;">公式締切予定:</span>
                <span style="font-size:22px; font-weight:bold; color:#f87171; margin-left:6px;">{race_info.get('deadline', '--:--')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
        <div style="background-color:#e0f2fe; border:1px solid #bae6fd; border-radius:6px; padding:8px 12px; margin-bottom:12px; color:#0369a1; font-size:12px; display:flex; justify-content:space-between; flex-wrap:wrap;">
            <div>🌊 <b>天候</b>: {w.get('weather', '晴')} | <b>気温</b>: {w.get('temp', '-')} | <b>水温</b>: {w.get('water_temp', '-')}</div>
            <div>💨 <b>風況</b>: {w.get('wind_dir', '-')} {w.get('wind_speed', '-')} | <b>波高</b>: {w.get('wave', '-')}</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("##### 📋 公式出走表・展示気配・モーターデータ")
        header_styles = [
            {"bg": "#f8fafc", "text": "#0f172a", "border": "#94a3b8"},
            {"bg": "#1e293b", "text": "#ffffff", "border": "#0f172a"},
            {"bg": "#dc2626", "text": "#ffffff", "border": "#b91c1c"},
            {"bg": "#2563eb", "text": "#ffffff", "border": "#1d4ed8"},
            {"bg": "#eab308", "text": "#0f172a", "border": "#ca8a04"},
            {"bg": "#16a34a", "text": "#ffffff", "border": "#15803d"},
        ]

        boat_cols = st.columns(6)
        for i, b in enumerate(race_info["boats"]):
            hs = header_styles[i] if i < len(header_styles) else header_styles[0]
            with boat_cols[i]:
                st.markdown(f"""
                <div style="background-color:#ffffff; border:1.5px solid #cbd5e1; border-radius:6px; overflow:hidden; text-align:center; box-shadow:0 1px 2px rgba(0,0,0,0.05); margin-bottom:8px;">
                    <div style="background-color:{hs['bg']}; color:{hs['text']}; font-weight:bold; font-size:13px; padding:3px; border-bottom:1px solid {hs['border']};">
                        {b.get('num', i+1)}号艇 ({b.get('rank', 'B1')})
                    </div>
                    <div style="padding:5px 3px; color:#0f172a; font-size:11px;">
                        <div style="font-weight:bold; font-size:13px; color:#0f172a;">{b.get('name', '-')}</div>
                        <div style="color:#64748b; font-size:10px;">{b.get('toban', '-')} / {b.get('branch', '')}</div>
                        <hr style="margin:3px 0; border:0; border-top:1px solid #e2e8f0;">
                        <div>全国: <b>{b.get('nat_win', '-')}%</b> ({b.get('nat_2ren', '-')})</div>
                        <div style="color:#005bac; font-weight:bold;">当地: {b.get('loc_win', '-')}% ({b.get('loc_2ren', '-')})</div>
                        <div>平均ST: <b>{b.get('avg_st', '0.15')}</b></div>
                        <hr style="margin:3px 0; border:0; border-top:1px solid #e2e8f0;">
                        <div>モーター No.{b.get('motor_no', '-')}: <b>{b.get('motor_rate', '-')}%</b></div>
                        <div style="background-color:#f0f9ff; border-radius:3px; padding:2px; margin-top:3px; border:1px solid #bae6fd;">
                            <span style="color:#0284c7; font-weight:bold;">展示: {b.get('ex_time', '-')}</span>
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

        st.write("")
        if st.button(f"🔥 {cur_stadium} {cur_race}R 最強AIで展開・買い目を導き出す", use_container_width=True, type="primary"):
            with st.spinner("スリット隊形・展示気配・モーターパワー・気象条件を分析中..."):
                try:
                    res = analyze_with_ai(cur_stadium, cur_race, race_info, GEMINI_API_KEY)
                    st.success("✅ AI展開解析完了")

                    st.markdown(f"""
                    <div style="background-color:#ffffff; border-left:5px solid #005bac; border-radius:8px; padding:14px; box-shadow:0 1px 4px rgba(0,0,0,0.05); margin-bottom:14px;">
                        <h4 style="color:#005bac; margin:0 0 6px 0;">📊 スリット隊形 & 1マーク展開予測</h4>
                        <div style="font-size:14px; color:#334155; margin-bottom:6px;">
                            主要決まり手: <b style="color:#005bac; font-size:16px;">{res.get('flow', 'イン逃げ')}</b> | 自信度: <b style="color:#dc2626; font-size:16px;">{res.get('confidence', 85)}%</b>
                        </div>
                        <p style="color:#0f172a; font-size:14px; line-height:1.6; margin:0 0 8px 0;">{res.get('summary', '')}</p>
                        <div style="background-color:#f8fafc; padding:8px; border-radius:6px; font-size:13px; color:#334155; border:1px solid #e2e8f0;">
                            🚀 <b>舟足・モーター評価</b>: {res.get('motor_eval', '展示タイム・機力優勢艇に注目')}
                        </div>
                        <div style="color:#64748b; font-size:12px; margin-top:6px;">🎯 <b>勝負の根拠</b>: {res.get('reason', '')}</div>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("#### 🎯 厳選 3連単フォーメーション（点数自動計算済）")
                    f_hon, list_hon, count_hon = parse_and_expand_formation(res.get("honmei_raw", ""))
                    f_osa, list_osa, count_osa = parse_and_expand_formation(res.get("osae_raw", ""))
                    f_ana, list_ana, count_ana = parse_and_expand_formation(res.get("ana_raw", ""))

                    c_a, c_b, c_c = st.columns(3)
                    with c_a:
                        st.markdown(f"""
                        <div style="background-color:#f0fdf4; border:1px solid #bbf7d0; border-left:5px solid #16a34a; border-radius:8px; padding:12px;">
                            <span style="background-color:#16a34a; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_hon} 点</span>
                            <div style="color:#16a34a; font-weight:bold; font-size:15px;">🎯 本命（鉄板・主軸）</div>
                            <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_hon}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_hon}点)"):
                            st.write(", ".join(list_hon))

                    with c_b:
                        st.markdown(f"""
                        <div style="background-color:#fff7ed; border:1px solid #fed7aa; border-left:5px solid #ea580c; border-radius:8px; padding:12px;">
                            <span style="background-color:#ea580c; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_osa} 点</span>
                            <div style="color:#ea580c; font-weight:bold; font-size:15px;">🛡️ 抑え（保険・連下）</div>
                            <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_osa}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_osa}点)"):
                            st.write(", ".join(list_osa))

                    with c_c:
                        st.markdown(f"""
                        <div style="background-color:#fef2f2; border:1px solid #fecaca; border-left:5px solid #dc2626; border-radius:8px; padding:12px;">
                            <span style="background-color:#dc2626; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_ana} 点</span>
                            <div style="color:#dc2626; font-weight:bold; font-size:15px;">⚡ 穴・高配当（展開崩れ）</div>
                            <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_ana}</div>
                        </div>
                        """, unsafe_allow_html=True)
                        with st.expander(f"買い目内訳 ({count_ana}点)"):
                            st.write(", ".join(list_ana))

                except Exception as e:
                    st.error(f"解析エラー: {e}")
