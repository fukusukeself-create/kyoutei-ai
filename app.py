import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup
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

# 日本標準時 (JST)
JST = timezone(timedelta(hours=9))
now_jst = datetime.now(JST)
today_str = now_jst.strftime("%Y%m%d")

# 1. APIキー取得
GEMINI_API_KEY = ""
if "GEMINI_API_KEY" in st.secrets:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
elif os.environ.get("GEMINI_API_KEY"):
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
else:
    GEMINI_API_KEY = st.sidebar.text_input("🔑 Gemini API Key を入力", type="password")

# 2. 全国24場 公式場コード辞書
STADIUM_CODES = {
    "桐生": "01", "戸田": "02", "江戸川": "03", "平和島": "04", "多摩川": "05",
    "浜名湖": "06", "蒲郡": "07", "常滑": "08", "津": "09", "三国": "10",
    "びわこ": "11", "住之江": "12", "尼崎": "13", "鳴門": "14", "丸亀": "15",
    "児島": "16", "宮島": "17", "徳山": "18", "下関": "19", "若松": "20",
    "芦屋": "21", "福岡": "22", "唐津": "23", "大村": "24"
}
CODE_TO_STADIUM = {v: k for k, v in STADIUM_CODES.items()}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

# 3. フォーメーション厳密計算エンジン
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

# 4. 公式サイトから当日の開催場とレース締切時間をリアルタイム取得
@st.cache_data(ttl=60)
def fetch_today_stadium_schedules(date_str):
    url = f"https://www.boatrace.jp/owpc/pc/race/index?hd={date_str}"
    active_stadiums = []
    upcoming_races = []
    current_minutes = now_jst.hour * 60 + now_jst.minute

    try:
        res = requests.get(url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            # 開催場のリンクを抽出
            links = soup.find_all("a", href=re.compile(r"/owpc/pc/race/raceindex\?jcd=(\d{2})"))
            jcds_found = []
            for a in links:
                m = re.search(r"jcd=(\d{2})", a.get("href", ""))
                if m:
                    jcd = m.group(1)
                    if jcd not in jcds_found and jcd in CODE_TO_STADIUM:
                        jcds_found.append(jcd)

            for jcd in jcds_found:
                st_name = CODE_TO_STADIUM[jcd]
                # 各場のレース一覧ページから正確な締切時刻を取得
                r_url = f"https://www.boatrace.jp/owpc/pc/race/raceindex?jcd={jcd}&hd={date_str}"
                r_res = requests.get(r_url, headers=HEADERS, timeout=6)
                if r_res.status_code != 200:
                    continue
                
                r_soup = BeautifulSoup(r_res.text, "html.parser")
                r_tables = r_soup.find_all("table")
                
                race_schedule = {}
                for t in r_tables:
                    rows = t.find_all("tr")
                    for row in rows:
                        txt = row.get_text(separator=" ", strip=True)
                        m_r = re.search(r"(\d{1,2})R", txt)
                        m_time = re.search(r"(\d{2}:\d{2})", txt)
                        if m_r and m_time:
                            r_num = int(m_r.group(1))
                            r_time = m_time.group(1)
                            if 1 <= r_num <= 12:
                                race_schedule[r_num] = r_time

                # 進行状況の判定
                current_round = 1
                is_racing = False
                display_time = "--:--"
                for r_num in sorted(race_schedule.keys()):
                    t_str = race_schedule[r_num]
                    try:
                        th, tm = map(int, t_str.split(":"))
                        t_min = th * 60 + tm
                        if t_min > current_minutes:
                            if not is_racing:
                                current_round = r_num
                                display_time = t_str
                                is_racing = True
                            
                            upcoming_races.append({
                                "stadium": st_name,
                                "round": r_num,
                                "name": "一般・予選" if r_num < 10 else ("特選・選抜" if r_num < 12 else "優勝戦/特選"),
                                "time": t_str,
                                "time_min": t_min
                            })
                    except Exception:
                        continue

                active_stadiums.append({
                    "id": jcd,
                    "name": st_name,
                    "is_racing": is_racing,
                    "current_round": current_round if is_racing else 12,
                    "display_status": "開催中" if is_racing else "発売終了",
                    "r_text": f"{current_round}R {display_time}" if is_racing else "全R終了",
                    "schedule": race_schedule
                })
    except Exception:
        pass

    upcoming_races = sorted(upcoming_races, key=lambda x: x["time_min"])
    return active_stadiums, upcoming_races

# 5. 公式サイトからの出走表・直前情報スクレイピング
@st.cache_data(ttl=30)
def fetch_complete_race_data(stadium_name, race_no, date_str):
    jcd = STADIUM_CODES.get(stadium_name, "20")
    boats = []
    weather_info = {
        "weather": "晴", "wind_dir": "北西", "wind_speed": "3m", "wind_type": "追風",
        "wave": "2cm", "temp": "26.0℃", "water_temp": "24.0℃"
    }

    try:
        # A. 出走表データ取得
        race_url = f"https://www.boatrace.jp/owpc/pc/race/racelist?jcd={jcd}&hd={date_str}&rno={race_no}"
        res = requests.get(race_url, headers=HEADERS, timeout=8)
        
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            tbodies = soup.find_all("tbody")
            
            for idx, tb in enumerate(tbodies):
                text_all = tb.get_text(separator=" ", strip=True)
                # 選手名リンクまたはdivを探索
                name_tag = tb.find("a", href=re.compile(r"toban=\d+")) or tb.find("div", class_=re.compile(r"is-fs18"))
                if not name_tag:
                    continue
                
                racer_name = name_tag.get_text(strip=True).replace(" ", "").replace("\u3000", " ")
                
                # 登番・級別
                rank_m = re.search(r"(\d{4})\s*/\s*(A1|A2|B1|B2)", text_all)
                toban = rank_m.group(1) if rank_m else ""
                rank = rank_m.group(2) if rank_m else "B1"
                
                # 支部・体重
                branch_m = re.search(r"([^\s/]+)\s*/\s*([^\s/]+)\s*(\d{2}\.\dkg)", text_all)
                branch = branch_m.group(1) if branch_m else "支部"
                weight = branch_m.group(3) if branch_m else "52.0kg"

                # 各種パーセンテージ・数値の抽出
                rates = re.findall(r"\d+\.\d{2}", text_all)
                nat_win = rates[0] if len(rates) > 0 else "5.00"
                nat_2ren = rates[1] if len(rates) > 1 else "30.00"
                loc_win = rates[2] if len(rates) > 2 else nat_win
                loc_2ren = rates[3] if len(rates) > 3 else nat_2ren
                motor_rate = rates[4] if len(rates) > 4 else "30.00"
                boat_rate = rates[5] if len(rates) > 5 else "30.00"

                # モーター番号
                motor_m = re.search(r"No\.?\s*(\d+)", text_all)
                motor_no = motor_m.group(1) if motor_m else str(idx + 1)

                # 平均ST
                st_m = re.search(r"F\d*\s*L\d*\s*(0\.\d{2})", text_all)
                avg_st = st_m.group(1) if st_m else "0.15"

                boats.append({
                    "num": len(boats) + 1,
                    "toban": toban,
                    "name": racer_name,
                    "rank": rank,
                    "branch": branch,
                    "weight": weight,
                    "avg_st": avg_st,
                    "nat_win": nat_win,
                    "nat_2ren": nat_2ren,
                    "loc_win": loc_win,
                    "loc_2ren": loc_2ren,
                    "motor_no": motor_no,
                    "motor_rate": motor_rate,
                    "boat_rate": boat_rate,
                    "ex_time": "6.70",
                    "tilt": "-0.5",
                    "parts": "なし"
                })
                if len(boats) == 6:
                    break

        # B. 直前情報取得（気象・展示タイム・チルト）
        before_url = f"https://www.boatrace.jp/owpc/pc/race/beforeinfo?jcd={jcd}&hd={date_str}&rno={race_no}"
        res_b = requests.get(before_url, headers=HEADERS, timeout=8)
        
        if res_b.status_code == 200:
            soup_b = BeautifulSoup(res_b.text, "html.parser")
            
            # 水面気象
            w_box = soup_b.find("div", class_="weather1") or soup_b
            w_text = w_box.get_text(separator=" ", strip=True)
            
            w_match = re.search(r"天候\s*([^\s]+)", w_text)
            if w_match: weather_info["weather"] = w_match.group(1)
            
            wind_match = re.search(r"風速\s*(\d+m)", w_text)
            if wind_match: weather_info["wind_speed"] = wind_match.group(1)
            
            wave_match = re.search(r"波高\s*(\d+cm)", w_text)
            if wave_match: weather_info["wave"] = wave_match.group(1)
            
            temp_match = re.search(r"気温\s*([\d\.]+℃)", w_text)
            if temp_match: weather_info["temp"] = temp_match.group(1)

            # 展示タイム & チルト
            tables = soup_b.find_all("table")
            for t in tables:
                rows = t.find_all("tr")
                for r in rows:
                    r_text = r.get_text(separator=" ", strip=True)
                    ex_m = re.findall(r"6\.\d{2}", r_text)
                    tilt_m = re.findall(r"[-+]?[0-3]\.[05]", r_text)
                    for i, b in enumerate(boats):
                        if i < len(ex_m): b["ex_time"] = ex_m[i]
                        if i < len(tilt_m): b["tilt"] = tilt_m[i]

    except Exception as e:
        st.warning(f"データ取得中に軽微なエラーが発生しました: {e}")

    return {"boats": boats, "weather": weather_info}

# 6. AI 予想エンジン
def analyze_with_ai(stadium, race_no, race_data, api_key):
    client = genai.Client(api_key=api_key)
    prompt = f"""
あなたは回収率と的中率を極限まで追求するプロ競艇データサイエンティスト兼展開アナリストです。
以下の【会場水面特性】【公式出走表】【当地成績 vs 全国成績】【直前展示気配・チルト】【水面気象条件】を統合分析し、勝てる3連単フォーメーションを導き出してください。

【対象レース】: {stadium} 競艇場 {race_no}R
【入力データ】:
{json.dumps(race_data, ensure_ascii=False, indent=2)}

【分析方針】
1. **スリット隊形と進入攻防**: 各艇の平均STと直前展示タイム、チルト、部品交換から1マークの進入隊形と仕掛ける艇（捲り・差し・カド受け）を特定。
2. **舟足判定（出足・伸び足・回り足）**: 展示タイム最速艇や当地2連率の高いモーターの実戦足を評価。
3. **水面・気象の利**: 風速・波高によるイン逃げ率の上下（強追風＝差し・捲り差し、強向風＝カド捲り等）を加味。
4. **フォーメーション厳格ルール**:
   - 必ず各枠内は「数字昇順（小さい順）」で記述すること（例: `1-23-2345`）。
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
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    last_err = None

    for m in models:
        try:
            res = client.models.generate_content(
                model=m,
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
            return json.loads(text), m
        except Exception as e:
            last_err = e
            time.sleep(1)
            continue

    raise Exception(f"AI解析エラー: {last_err}")

# データ取得
dynamic_stadiums, dynamic_timeline = fetch_today_stadium_schedules(today_str)

# セッション状態初期化
if "selected_stadium" not in st.session_state:
    st.session_state["selected_stadium"] = dynamic_stadiums[0]["name"] if dynamic_stadiums else "蒲郡"
if "selected_race" not in st.session_state:
    st.session_state["selected_race"] = 1

# --- アプリヘッダー ---
st.markdown(f"""
<div style="background-color:#004b91; color:#ffffff; padding:12px 16px; border-radius:8px; margin-bottom:12px; display:flex; justify-content:space-between; align-items:center;">
    <span style="font-size:18px; font-weight:bold; color:#ffffff;">🚤 BOAT RACE AI 最強予想ナビ</span>
    <span style="font-size:13px; font-weight:bold; background-color:#ffffff; color:#004b91; padding:3px 8px; border-radius:4px;">{now_jst.strftime('%H:%M')} JST (本日 {now_jst.strftime('%m/%d')})</span>
</div>
""", unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["🚩 開催一覧", "⏰ 締切順（リアルタイム）", "🎯 レース詳細・最強AI分析"])

# ----------------- TAB 1: 開催一覧 -----------------
with tab1:
    st.markdown("##### 🚩 本日の開催場（タップしてレースを選択）")
    if not dynamic_stadiums:
        st.info("本日の開催情報を取得中、または本日のレースは終了しました。")
    else:
        cols = st.columns(4)
        for idx, item in enumerate(dynamic_stadiums):
            with cols[idx % 4]:
                if item.get("is_racing"):
                    btn_label = f"{item['name']}\n{item.get('display_status')}\n{item.get('r_text')}"
                    if st.button(btn_label, key=f"std_btn_{item['id']}", use_container_width=True, type="primary"):
                        st.session_state["selected_stadium"] = item["name"]
                        st.session_state["selected_race"] = item.get("current_round", 1)
                        st.toast(f"{item['name']} を選択しました！「🎯 レース詳細・最強AI分析」タブを開いてください。")
                else:
                    st.markdown(f"""
                    <div style="background-color:#f1f5f9; border:1px solid #cbd5e1; border-radius:6px; padding:8px; text-align:center; margin-bottom:8px;">
                        <div style="font-weight:bold; color:#64748b; font-size:14px;">{item['name']}</div>
                        <div style="color:#94a3b8; font-size:11px;">{item.get('display_status')}</div>
                        <div style="color:#ef4444; font-size:12px; font-weight:bold;">{item.get('r_text')}</div>
                    </div>
                    """, unsafe_allow_html=True)

# ----------------- TAB 2: 締切順 -----------------
with tab2:
    st.markdown("##### ⏰ まもなく締切のレース（締切順）")
    if not dynamic_timeline:
        st.info("本日の全レース発売が終了したか、直近のレースがありません。")
    else:
        for r in dynamic_timeline[:10]:
            st.markdown(f"""
            <div style="background-color:#ffffff; border:1.5px solid #cbd5e1; border-radius:8px; padding:12px; margin-bottom:10px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span style="font-size:18px; font-weight:bold; color:#0f172a;">{r['stadium']}</span>
                        <span style="font-size:14px; font-weight:bold; color:#005bac; margin-left:8px;">{r['round']}R {r['name']}</span>
                    </div>
                    <div>
                        <span style="font-size:12px; color:#64748b;">公式締切予定:</span>
                        <span style="font-size:20px; font-weight:bold; color:#dc2626; margin-left:4px;">{r['time']}</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"👉 {r['stadium']} {r['round']}R のAI予想を見る", key=f"time_btn_{r['stadium']}_{r['round']}", use_container_width=True):
                st.session_state["selected_stadium"] = r["stadium"]
                st.session_state["selected_race"] = r["round"]
                st.toast(f"{r['stadium']} {r['round']}R を選択しました！「🎯 レース詳細・最強AI分析」タブを開いてください。")

# ----------------- TAB 3: レース詳細・AI分析 -----------------
with tab3:
    active_names = [s["name"] for s in dynamic_stadiums] if dynamic_stadiums else list(STADIUM_CODES.keys())
    c_sel1, c_sel2 = st.columns(2)
    with c_sel1:
        cur_idx = active_names.index(st.session_state["selected_stadium"]) if st.session_state["selected_stadium"] in active_names else 0
        cur_stadium = st.selectbox("競艇場", active_names, index=cur_idx)
    with c_sel2:
        cur_race = st.slider("レース番号", 1, 12, value=int(st.session_state.get("selected_race", 1)))

    with st.spinner(f"🌐 公式サイトより {cur_stadium} {cur_race}R の最新出走表・直前情報を取得中..."):
        race_info = fetch_complete_race_data(cur_stadium, cur_race, today_str)

    if not race_info["boats"]:
        st.error(f"{cur_stadium} {cur_race}R の出走表が取得できませんでした（非開催日またはデータ未公開の可能性があります）。")
    else:
        w = race_info["weather"]
        st.markdown(f"""
        <div style="background-color:#e0f2fe; border:1px solid #bae6fd; border-radius:6px; padding:10px 14px; margin-bottom:12px; color:#0369a1; font-size:13px; display:flex; justify-content:space-between; flex-wrap:wrap;">
            <div>🌊 <b>天候</b>: {w.get('weather', '晴')} | <b>気温</b>: {w.get('temp', '-')} | <b>水温</b>: {w.get('water_temp', '-')}</div>
            <div>💨 <b>風況</b>: {w.get('wind_dir', '-')} {w.get('wind_speed', '-')} | <b>波高</b>: {w.get('wave', '-')}</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("##### 📋 公式出走表・当地成績・舟足データ")
        header_styles = [
            {"bg": "#f8fafc", "text": "#0f172a", "border": "#94a3b8"},  # 1
            {"bg": "#1e293b", "text": "#ffffff", "border": "#0f172a"},  # 2
            {"bg": "#dc2626", "text": "#ffffff", "border": "#b91c1c"},  # 3
            {"bg": "#2563eb", "text": "#ffffff", "border": "#1d4ed8"},  # 4
            {"bg": "#eab308", "text": "#0f172a", "border": "#ca8a04"},  # 5
            {"bg": "#16a34a", "text": "#ffffff", "border": "#15803d"},  # 6
        ]
        
        boat_cols = st.columns(6)
        for i, b in enumerate(race_info["boats"]):
            hs = header_styles[i] if i < len(header_styles) else header_styles[0]
            with boat_cols[i]:
                st.markdown(f"""
                <div style="background-color:#ffffff; border:1.5px solid #cbd5e1; border-radius:8px; overflow:hidden; text-align:center; box-shadow:0 1px 3px rgba(0,0,0,0.05); margin-bottom:10px;">
                    <div style="background-color:{hs['bg']}; color:{hs['text']}; font-weight:bold; font-size:14px; padding:4px; border-bottom:1px solid {hs['border']};">
                        {b['num']}号艇 ({b['rank']})
                    </div>
                    <div style="padding:6px 4px; color:#0f172a; font-size:11px;">
                        <div style="font-weight:bold; font-size:14px; color:#0f172a;">{b['name']}</div>
                        <div style="color:#64748b;">登番 {b.get('toban', '-')} / {b.get('branch', '')}</div>
                        <hr style="margin:4px 0; border:0; border-top:1px solid #e2e8f0;">
                        <div>全国勝率: <b>{b.get('nat_win', '-')}%</b> ({b.get('nat_2ren', '-')})</div>
                        <div style="color:#005bac; font-weight:bold;">当地勝率: {b.get('loc_win', '-')}% ({b.get('loc_2ren', '-')})</div>
                        <div>平均ST: <b>{b.get('avg_st', '0.15')}</b></div>
                        <hr style="margin:4px 0; border:0; border-top:1px solid #e2e8f0;">
                        <div>モーター No.{b.get('motor_no', '-')}: <b>{b.get('motor_rate', '-')}%</b></div>
                        <div>ボート 2連: <b>{b.get('boat_rate', '-')}%</b></div>
                        <div style="background-color:#f0f9ff; border-radius:4px; padding:2px; margin-top:4px; border:1px solid #bae6fd;">
                            <span style="color:#0284c7; font-weight:bold;">展示: {b.get('ex_time', '-')}</span> | チルト: {b.get('tilt', '-')}
                        </div>
                    </div>
                </div>
                """, unsafe_allow_html=True)

        st.write("")
        
        # 予想実行
        if st.button(f"🔥 {cur_stadium} {cur_race}R 最強AIで展開・舟足・買い目を導き出す", use_container_width=True, type="primary"):
            if not GEMINI_API_KEY:
                st.error("Gemini API Key を設定してください。")
            else:
                with st.spinner("スリット隊形・当地相性・モーター気配・気象条件を分析中..."):
                    try:
                        res, used_model = analyze_with_ai(cur_stadium, cur_race, race_info, GEMINI_API_KEY)
                        st.success(f"✅ 解析完了（AI Engine: {used_model}）")
                        
                        st.markdown(f"""
                        <div style="background-color:#ffffff; border-left:5px solid #005bac; border-radius:8px; padding:14px; box-shadow:0 1px 4px rgba(0,0,0,0.05); margin-bottom:14px;">
                            <h4 style="color:#005bac; margin:0 0 6px 0;">📊 スリット隊形 & 1マーク展開予測</h4>
                            <div style="font-size:14px; color:#334155; margin-bottom:6px;">
                                主要決まり手予想: <b style="color:#005bac; font-size:16px;">{res.get('flow', 'イン逃げ')}</b> | AI 自信度: <b style="color:#dc2626; font-size:16px;">{res.get('confidence', 85)}%</b>
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
                        
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            st.markdown(f"""
                            <div style="background-color:#f0fdf4; border:1px solid #bbf7d0; border-left:5px solid #16a34a; border-radius:8px; padding:12px;">
                                <span style="background-color:#16a34a; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_hon} 点</span>
                                <div style="color:#16a34a; font-weight:bold; font-size:15px;">🎯 本命（鉄板・主軸）</div>
                                <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_hon}</div>
                            </div>
                            """, unsafe_allow_html=True)
                            with st.expander(f"買い目内訳 ({count_hon}点)"):
                                st.write(", ".join(list_hon))
                                
                        with c2:
                            st.markdown(f"""
                            <div style="background-color:#fff7ed; border:1px solid #fed7aa; border-left:5px solid #ea580c; border-radius:8px; padding:12px;">
                                <span style="background-color:#ea580c; color:#ffffff; font-size:12px; font-weight:bold; padding:2px 8px; border-radius:10px; float:right;">計 {count_osa} 点</span>
                                <div style="color:#ea580c; font-weight:bold; font-size:15px;">🛡️ 抑え（保険・連下）</div>
                                <div style="font-size:22px; font-weight:bold; color:#0f172a; margin:8px 0;">{f_osa}</div>
                            </div>
                            """, unsafe_allow_html=True)
                            with st.expander(f"買い目内訳 ({count_osa}点)"):
                                st.write(", ".join(list_osa))
                                
                        with c3:
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
                        st.error(f"解析中にエラーが発生しました: {e}")
