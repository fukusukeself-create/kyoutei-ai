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

# モバイル対応・4列グリッド強制維持CSS
st.markdown("""
<style>
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
.stButton>button {
    padding: 2px 4px !important;
    font-size: 11px !important;
    height: auto !important;
    min-height: 32px !important;
}
</style>
""", unsafe_allow_html=True)

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

# 2. 公式24場 定義（公式アプリと同じ24場グリッド順）
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
STADIUM_CODES = {s["name"]: s["id"] for s in ALL_STADIUMS}
CODE_TO_STADIUM = {s["id"]: s["name"] for s in ALL_STADIUMS}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8"
}

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

# 4. 公式サイトから全24場の当日開催ステータス＆締切情報を一括スクレイピング
@st.cache_data(ttl=30)
def fetch_all_stadiums_status(date_str):
    current_minutes = now_jst.hour * 60 + now_jst.minute
    
    stadium_dict = {}
    for item in ALL_STADIUMS:
        stadium_dict[item["id"]] = {
            "id": item["id"],
            "name": item["name"],
            "night": item["night"],
            "is_active": False,
            "grade": "",
            "day_text": "",
            "status_text": "--",
            "current_round": 1,
            "deadline_time": "--:--",
            "is_closed": False,
            "race_title": "予選",
            "races": {}
        }

    try:
        url = f"https://www.boatrace.jp/owpc/pc/race/index?hd={date_str}"
        res = requests.get(url, headers=HEADERS, timeout=8)
        
        active_jcds = set()
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            links = soup.find_all("a", href=re.compile(r"jcd=(\d{2})"))
            for a in links:
                m = re.search(r"jcd=(\d{2})", a["href"])
                if m and m.group(1) in stadium_dict:
                    active_jcds.add(m.group(1))

        # 開催中の場からスケジュール取得
        for jcd in active_jcds:
            sdata = stadium_dict[jcd]
            sdata["is_active"] = True
            
            r_url = f"https://www.boatrace.jp/owpc/pc/race/raceindex?jcd={jcd}&hd={date_str}"
            r_res = requests.get(r_url, headers=HEADERS, timeout=6)
            if r_res.status_code != 200:
                continue

            r_soup = BeautifulSoup(r_res.text, "html.parser")
            text_all = r_soup.get_text(separator=" ", strip=True)

            # グレード
            if "SG" in text_all: sdata["grade"] = "SG"
            elif "G1" in text_all or "GI" in text_all: sdata["grade"] = "G1"
            elif "G2" in text_all or "GII" in text_all: sdata["grade"] = "G2"
            elif "G3" in text_all or "GIII" in text_all: sdata["grade"] = "G3"
            else: sdata["grade"] = "一般"

            # 日程
            day_m = re.search(r"(初日|２日目|2日目|３日目|3日目|４日目|4日目|５日目|5日目|６日目|6日目|最終日)", text_all)
            sdata["day_text"] = day_m.group(1) if day_m else "開催中"

            # 各レースの締切時刻とレース名
            r_info_dict = {}
            for t in r_soup.find_all("table"):
                for row in t.find_all("tr"):
                    row_txt = row.get_text(separator=" ", strip=True)
                    rm = re.search(r"(\d{1,2})R", row_txt)
                    tm = re.search(r"(\d{2}:\d{2})", row_txt)
                    if rm and tm:
                        r_no = int(rm.group(1))
                        if 1 <= r_no <= 12:
                            r_title = "予選"
                            if "選抜" in row_txt: r_title = "選抜戦"
                            elif "特賞" in row_txt: r_title = "予選特賞"
                            elif "特選" in row_txt: r_title = "一般特賞"
                            elif "特別" in row_txt: r_title = "予選特別"
                            elif "優勝" in row_txt: r_title = "優勝戦"
                            
                            r_info_dict[r_no] = {"time": tm.group(1), "title": r_title}
            sdata["races"] = r_info_dict

            # 進行中レースの特定
            found_next = False
            for r_no in sorted(r_info_dict.keys()):
                t_str = r_info_dict[r_no]["time"]
                try:
                    th, tm_val = map(int, t_str.split(":"))
                    t_min = th * 60 + tm_val
                    if t_min > current_minutes:
                        sdata["current_round"] = r_no
                        sdata["deadline_time"] = t_str
                        sdata["race_title"] = r_info_dict[r_no]["title"]
                        sdata["status_text"] = f"{r_no}R {t_str}"
                        sdata["is_closed"] = False
                        found_next = True
                        break
                except Exception:
                    continue

            if not found_next and r_info_dict:
                sdata["is_closed"] = True
                sdata["status_text"] = "発売終了"
                sdata["current_round"] = 12

    except Exception:
        pass

    # 締切順リスト生成
    upcoming_list = []
    for jcd, sdata in stadium_dict.items():
        if sdata["is_active"] and not sdata["is_closed"]:
            r_no = sdata["current_round"]
            if r_no in sdata["races"]:
                r_info = sdata["races"][r_no]
                t_str = r_info["time"]
                try:
                    th, tm_val = map(int, t_str.split(":"))
                    t_min = th * 60 + tm_val
                    upcoming_list.append({
                        "id": jcd,
                        "stadium": sdata["name"],
                        "round": r_no,
                        "race_title": r_info["title"],
                        "time": t_str,
                        "time_min": t_min,
                        "grade": sdata["grade"],
                        "day_text": sdata["day_text"],
                        "night": sdata["night"]
                    })
                except Exception:
                    continue

    upcoming_list = sorted(upcoming_list, key=lambda x: x["time_min"])
    return list(stadium_dict.values()), upcoming_list

# 5. 出走表・直前気配・選手データの取得
@st.cache_data(ttl=20)
def fetch_complete_race_data(stadium_name, race_no, date_str):
    jcd = STADIUM_CODES.get(stadium_name, "07")
    boats = []
    weather_info = {
        "weather": "晴", "wind_dir": "北西", "wind_speed": "3m", "wind_type": "追風",
        "wave": "2cm", "temp": "26.0℃", "water_temp": "24.0℃"
    }
    race_meta = {
        "race_title": f"{stadium_name} {race_no}R",
        "deadline": "--:--",
        "distance": "1800m"
    }

    try:
        # A. 出走表
        race_url = f"https://www.boatrace.jp/owpc/pc/race/racelist?jcd={jcd}&hd={date_str}&rno={race_no}"
        res = requests.get(race_url, headers=HEADERS, timeout=8)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            header_text = soup.get_text(separator=" ", strip=True)
            dm = re.search(r"締切予定\s*(\d{2}:\d{2})", header_text)
            if dm: race_meta["deadline"] = dm.group(1)

            tbodies = soup.find_all("tbody")
            for idx, tb in enumerate(tbodies):
                t_str = tb.get_text(separator=" ", strip=True)
                name_tag = tb.find("div", class_=re.compile(r"is-fs18")) or tb.find("a", href=re.compile(r"toban=\d+"))
                if not name_tag: continue
                racer_name = name_tag.get_text(strip=True).replace("\u3000", " ")

                rank_m = re.search(r"(\d{4})\s*/\s*(A1|A2|B1|B2)", t_str)
                toban = rank_m.group(1) if rank_m else ""
                rank = rank_m.group(2) if rank_m else "B1"

                branch_m = re.search(r"([^\s/]+)\s*/\s*([^\s/]+)\s*(\d{2}\.\dkg)", t_str)
                branch = branch_m.group(1) if branch_m else "支部"
                weight = branch_m.group(3) if branch_m else "52.0kg"

                rates = re.findall(r"\d+\.\d{2}", t_str)
                nat_win = rates[0] if len(rates) > 0 else "5.00"
                nat_2ren = rates[1] if len(rates) > 1 else "30.00"
                loc_win = rates[2] if len(rates) > 2 else nat_win
                loc_2ren = rates[3] if len(rates) > 3 else nat_2ren
                motor_rate = rates[4] if len(rates) > 4 else "30.00"
                boat_rate = rates[5] if len(rates) > 5 else "30.00"

                motor_m = re.search(r"No\.?\s*(\d+)", t_str)
                motor_no = motor_m.group(1) if motor_m else str(idx + 1)

                st_m = re.search(r"F\d*\s*L\d*\s*(0\.\d{2})", t_str)
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
                if len(boats) == 6: break

        # B. 直前情報
        before_url = f"https://www.boatrace.jp/owpc/pc/race/beforeinfo?jcd={jcd}&hd={date_str}&rno={race_no}"
        res_b = requests.get(before_url, headers=HEADERS, timeout=8)
        if res_b.status_code == 200:
            soup_b = BeautifulSoup(res_b.text, "html.parser")
            w_box = soup_b.find("div", class_="weather1") or soup_b
            w_text = w_box.get_text(separator=" ", strip=True)

            wm = re.search(r"天候\s*([^\s]+)", w_text)
            if wm: weather_info["weather"] = wm.group(1)
            wm_w = re.search(r"風速\s*(\d+m)", w_text)
            if wm_w: weather_info["wind_speed"] = wm_w.group(1)
            wm_wv = re.search(r"波高\s*(\d+cm)", w_text)
            if wm_wv: weather_info["wave"] = wm_wv.group(1)
            wm_t = re.search(r"気温\s*([\d\.]+℃)", w_text)
            if wm_t: weather_info["temp"] = wm_t.group(1)

            tables = soup_b.find_all("table")
            for t in tables:
                for r in t.find_all("tr"):
                    r_text = r.get_text(separator=" ", strip=True)
                    ex_m = re.findall(r"6\.\d{2}", r_text)
                    tilt_m = re.findall(r"[-+]?[0-3]\.[05]", r_text)
                    for i, b in enumerate(boats):
                        if i < len(ex_m): b["ex_time"] = ex_m[i]
                        if i < len(tilt_m): b["tilt"] = tilt_m[i]
    except Exception:
        pass

    return {"boats": boats, "weather": weather_info, "meta": race_meta}

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
    models = ["gemini-2.5-flash", "gemini-2.0-flash"]
    last_err = None
    for m in models:
        try:
            res = client.models.generate_content(
                model=m,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.15)
            )
            text = res.text.strip()
            if "```json" in text: text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text: text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text), m
        except Exception as e:
            last_err = e
            time.sleep(1)
            continue
    raise Exception(f"AI解析エラー: {last_err}")

# データ取得
all_status, upcoming_timeline = fetch_all_stadiums_status(today_str)

# セッション状態管理
if "selected_stadium" not in st.session_state:
    st.session_state["selected_stadium"] = "蒲郡"
if "selected_race" not in st.session_state:
    st.session_state["selected_race"] = 10

# --- トップナビゲーションバー ---
st.markdown(f"""
<div style="background-color:#004b91; color:#ffffff; padding:10px 16px; border-radius:6px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
    <div style="font-size:18px; font-weight:bold;">🚤 トップ</div>
    <div style="font-size:12px; background-color:#ffffff; color:#004b91; padding:3px 8px; border-radius:4px; font-weight:bold;">
        {now_jst.strftime('%H:%M')} JST (本日 {now_jst.strftime('%m/%d')})
    </div>
</div>
""", unsafe_allow_html=True)

# 3つのタブ定義（変数名完全統一）
tab1, tab2, tab3 = st.tabs(["🚩 開催一覧", "⏰ 締切順", "🎯 レース詳細・最強AI分析"])

# ==========================================
# TAB 1: 開催一覧（公式24場グリッドUI）
# ==========================================
with tab1:
    grid_cols = st.columns(4)
    for idx, s in enumerate(all_status):
        with grid_cols[idx % 4]:
            if not s["is_active"]:
                st.markdown(f"""
                <div style="background-color:#f1f5f9; border:1px solid #cbd5e1; border-radius:6px; padding:10px 2px; text-align:center; min-height:86px; margin-bottom:4px;">
                    <div style="font-weight:bold; font-size:15px; color:#64748b;">{s['name']}</div>
                    <div style="font-size:15px; color:#94a3b8; margin-top:10px;">--</div>
                </div>
                """, unsafe_allow_html=True)
            elif s["is_closed"]:
                g_badge = f"<span style='background-color:#0284c7; color:#fff; font-size:10px; padding:1px 4px; border-radius:3px; margin-right:2px;'>{s['grade']}</span>" if s['grade'] and s['grade'] != "一般" else ""
                st.markdown(f"""
                <div style="background-color:#e2e8f0; border:1px solid #94a3b8; border-radius:6px; padding:6px 2px; text-align:center; min-height:86px; margin-bottom:4px;">
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
                <div style="background-color:{card_bg}; border:1.5px solid {border_c}; border-radius:6px; padding:6px 2px; text-align:center; min-height:86px; margin-bottom:2px;">
                    <div style="font-weight:bold; font-size:15px; color:#0f172a;">{is_night_icon}{s['name']}</div>
                    <div style="font-size:11px; color:#334155; margin-top:2px;">{g_badge}{s['grade'] if s['grade']=='一般' else ''} {s['day_text']}</div>
                    <div style="font-size:13px; color:#dc2626; font-weight:bold; margin-top:4px;">{s['status_text']}</div>
                </div>
                """, unsafe_allow_html=True)
                if st.button("選択", key=f"btn_st_{s['id']}", use_container_width=True):
                    st.session_state["selected_stadium"] = s["name"]
                    st.session_state["selected_race"] = s["current_round"]
                    st.toast(f"{s['name']} {s['current_round']}R を選択しました！「🎯 レース詳細・最強AI分析」タブを開いてください。")

# ==========================================
# TAB 2: 締切順（公式リストUI）
# ==========================================
with tab2:
    if not upcoming_timeline:
        st.info("本日のレース発売はすべて終了しました。")
    else:
        for r in upcoming_timeline:
            is_night_str = "🌙 " if r["night"] else ""
            grade_badge = f"<span style='background-color:#2563eb; color:#ffffff; font-size:11px; font-weight:bold; padding:2px 6px; border-radius:4px; margin-right:6px;'>{r['grade']}</span>" if r['grade'] and r['grade'] != "一般" else ""
            card_bg = "#f5f3ff" if r["night"] else "#eff6ff"
            border_c = "#ddd6fe" if r["night"] else "#bfdbfe"

            st.markdown(f"""
            <div style="background-color:{card_bg}; border:1.5px solid {border_c}; border-radius:8px; padding:10px 14px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div style="display:flex; align-items:center;">
                        {grade_badge}
                        <span style="font-size:17px; font-weight:bold; color:#0f172a;">{is_night_str}{r['stadium']}</span>
                        <span style="font-size:12px; color:#64748b; margin-left:8px;">{r['day_text']}</span>
                    </div>
                    <div style="font-size:15px; font-weight:bold; color:#0284c7; margin-top:2px;">
                        {r['round']}R {r['race_title']}
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:11px; color:#64748b;">締切予定時刻</div>
                    <div style="font-size:22px; font-weight:bold; color:#dc2626;">{r['time']}</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"👉 {r['stadium']} {r['round']}R の出走表・AI予想", key=f"tl_btn_{r['id']}_{r['round']}", use_container_width=True):
                st.session_state["selected_stadium"] = r["stadium"]
                st.session_state["selected_race"] = r["round"]
                st.toast(f"{r['stadium']} {r['round']}R を選択しました！「🎯 レース詳細・最強AI分析」タブを開いてください。")

# ==========================================
# TAB 3: レース詳細・AI分析
# ==========================================
with tab3:
    active_stadium_names = [s["name"] for s in all_status if s["is_active"]]
    if not active_stadium_names:
        active_stadium_names = [s["name"] for s in ALL_STADIUMS]

    c1, c2 = st.columns(2)
    with c1:
        cur_idx = active_stadium_names.index(st.session_state["selected_stadium"]) if st.session_state["selected_stadium"] in active_stadium_names else 0
        cur_stadium = st.selectbox("競艇場", active_stadium_names, index=cur_idx)
    with c2:
        cur_race = st.slider("レース番号", 1, 12, value=int(st.session_state.get("selected_race", 10)))

    with st.spinner(f"🌐 公式サイトより {cur_stadium} {cur_race}R の最新出走表・直前情報を取得中..."):
        race_info = fetch_complete_race_data(cur_stadium, cur_race, today_str)

    meta = race_info.get("meta", {})
    w = race_info["weather"]

    st.markdown(f"""
    <div style="background-color:#0f172a; color:#ffffff; border-radius:8px; padding:10px 16px; margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
        <div>
            <span style="font-size:20px; font-weight:bold; color:#38bdf8;">{cur_stadium} {cur_race}R</span>
            <span style="font-size:13px; color:#94a3b8; margin-left:8px;">{meta.get('distance', '1800m')}</span>
        </div>
        <div>
            <span style="font-size:12px; color:#94a3b8;">公式締切予定:</span>
            <span style="font-size:22px; font-weight:bold; color:#f87171; margin-left:6px;">{meta.get('deadline', '--:--')}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div style="background-color:#e0f2fe; border:1px solid #bae6fd; border-radius:6px; padding:8px 12px; margin-bottom:12px; color:#0369a1; font-size:12px; display:flex; justify-content:space-between; flex-wrap:wrap;">
        <div>🌊 <b>天候</b>: {w.get('weather', '晴')} | <b>気温</b>: {w.get('temp', '-')} | <b>水温</b>: {w.get('water_temp', '-')}</div>
        <div>💨 <b>風況</b>: {w.get('wind_dir', '-')} {w.get('wind_speed', '-')} | <b>波高</b>: {w.get('wave', '-')}</div>
    </div>
    """, unsafe_allow_html=True)

    if not race_info["boats"]:
        st.warning(f"現在、{cur_stadium} {cur_race}R の出走表データが公開されていないか、非開催です。")
    else:
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
                        {b['num']}号艇 ({b['rank']})
                    </div>
                    <div style="padding:5px 3px; color:#0f172a; font-size:11px;">
                        <div style="font-weight:bold; font-size:13px; color:#0f172a;">{b['name']}</div>
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
            if not GEMINI_API_KEY:
                st.error("Gemini API Key を設定してください。")
            else:
                with st.spinner("スリット隊形・展示気配・モーターパワー・気象条件を分析中..."):
                    try:
                        res, used_model = analyze_with_ai(cur_stadium, cur_race, race_info, GEMINI_API_KEY)
                        st.success(f"✅ 解析完了（AI Engine: {used_model}）")

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
                        st.error(f"解析中にエラーが発生しました: {e}")
