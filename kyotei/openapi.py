"""Boatrace Open API (https://boatraceopenapi.github.io) のJSONを、アプリ共通のレース辞書に変換する。

Boatrace Open API は公式サイトの出走表・直前情報・結果をJSONで配布している
オープンデータ。1日ぶんを1リクエストで取れるので、モデル学習と、
公式サイトに繋がらない時の予備データ元として使う。
"""
from __future__ import annotations

BASE = "https://boatraceopenapi.github.io"
CLASS_BY_NUMBER = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}
WEATHER_BY_NUMBER = {1: "晴", 2: "曇り", 3: "雨", 4: "雪", 5: "台風", 6: "霧"}
TECHNIQUE_BY_NUMBER = {1: "逃げ", 2: "差し", 3: "まくり", 4: "まくり差し", 5: "抜き", 6: "恵まれ"}
GRADE_BY_NUMBER = {1: "SG", 2: "G1", 3: "G2", 4: "G3", 5: "一般"}


def day_url(kind: str, hd: str) -> str:
    """kind: programs / previews / results, hd: YYYYMMDD"""
    return f"{BASE}/{kind}/v2/{hd[:4]}/{hd}.json"


def today_url(kind: str) -> str:
    return f"{BASE}/{kind}/v2/today.json"


def race_key(rec) -> tuple:
    return (rec["race_date"].replace("-", ""), int(rec["race_stadium_number"]), int(rec["race_number"]))


def program_to_race(rec) -> dict:
    boats = []
    for b in rec.get("boats") or []:
        if not b.get("racer_boat_number"):
            continue
        boats.append({
            "boat": int(b["racer_boat_number"]),
            "name": (b.get("racer_name") or "").replace("　", " ").strip(),
            "toban": b.get("racer_number"),
            "cls": CLASS_BY_NUMBER.get(b.get("racer_class_number"), ""),
            "age": b.get("racer_age"),
            "weight": b.get("racer_weight"),
            "f": b.get("racer_flying_count") or 0,
            "l": b.get("racer_late_count") or 0,
            "avg_st": b.get("racer_average_start_timing"),
            "nat_win": b.get("racer_national_top_1_percent"),
            "nat_2": b.get("racer_national_top_2_percent"),
            "nat_3": b.get("racer_national_top_3_percent"),
            "loc_win": b.get("racer_local_top_1_percent"),
            "loc_2": b.get("racer_local_top_2_percent"),
            "loc_3": b.get("racer_local_top_3_percent"),
            "motor_no": b.get("racer_assigned_motor_number"),
            "motor_2": b.get("racer_assigned_motor_top_2_percent"),
            "motor_3": b.get("racer_assigned_motor_top_3_percent"),
            "boat_no": b.get("racer_assigned_boat_number"),
            "boat_2": b.get("racer_assigned_boat_top_2_percent"),
            "boat_3": b.get("racer_assigned_boat_top_3_percent"),
            "series": [],
        })
    boats.sort(key=lambda x: x["boat"])
    closed = rec.get("race_closed_at") or ""
    return {
        "date": rec["race_date"].replace("-", ""),
        "jcd": int(rec["race_stadium_number"]),
        "rno": int(rec["race_number"]),
        "title": rec.get("race_title") or "",
        "subtitle": rec.get("race_subtitle") or "",
        "grade": GRADE_BY_NUMBER.get(rec.get("race_grade_number"), ""),
        "distance": rec.get("race_distance"),
        "deadline": closed[11:16] if len(closed) >= 16 else "",
        "boats": boats,
        "preview": None,
        "source": "Boatrace Open API",
    }


def preview_to_dict(rec) -> dict | None:
    boats = {}
    for b in rec.get("boats") or []:
        if not b.get("racer_boat_number"):
            continue
        boats[int(b["racer_boat_number"])] = {
            "course": b.get("racer_course_number"),
            "st": b.get("racer_start_timing"),
            "ex_time": b.get("racer_exhibition_time"),
            "tilt": b.get("racer_tilt_adjustment"),
            "weight": b.get("racer_weight"),
            "weight_adj": b.get("racer_weight_adjustment"),
        }
    if not any(v.get("ex_time") for v in boats.values()):
        return None
    return {
        "wind": rec.get("race_wind"),
        "wind_dir": rec.get("race_wind_direction_number"),
        "wave": rec.get("race_wave"),
        "weather": WEATHER_BY_NUMBER.get(rec.get("race_weather_number"), ""),
        "temp": rec.get("race_temperature"),
        "water_temp": rec.get("race_water_temperature"),
        "boats": boats,
        "source": "Boatrace Open API",
    }


def result_to_dict(rec) -> dict | None:
    boats = []
    for b in rec.get("boats") or []:
        if not b.get("racer_boat_number"):
            continue
        boats.append({
            "boat": int(b["racer_boat_number"]),
            "course": b.get("racer_course_number"),
            "st": b.get("racer_start_timing"),
            "place": b.get("racer_place_number"),
            "toban": b.get("racer_number"),
            "name": (b.get("racer_name") or "").replace("　", " ").strip(),
        })
    order = sorted((b["place"], b["boat"]) for b in boats if b["place"] and 1 <= b["place"] <= 6)
    if [p for p, _ in order[:3]] != [1, 2, 3]:
        top3 = None
    else:
        top3 = tuple(bn for _, bn in order[:3])
    payouts = {}
    for kind, items in (rec.get("payouts") or {}).items():
        payouts[kind] = [(i.get("combination"), i.get("payout")) for i in items or []]
    return {
        "top3": top3,
        "technique": TECHNIQUE_BY_NUMBER.get(rec.get("race_technique_number"), ""),
        "boats": boats,
        "payouts": payouts,
        "wind": rec.get("race_wind"),
        "wind_dir": rec.get("race_wind_direction_number"),
        "wave": rec.get("race_wave"),
    }
