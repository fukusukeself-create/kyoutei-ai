"""Gemini にレース (出馬表・馬柱・馬場・モデルの勝率) を読ませ、馬別評価と展開の見解を JSON で返させる。

オッズは渡さない。渡すと評価が人気に引きずられ、市場との差 (妙味) が消えるため。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from scraper import Race

# gemini-3.7-flash 以降は無料枠が狭いため既定にしない。従量課金なら GEMINI_MODEL で切替。
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash"]

SYSTEM_PROMPT = """あなたは中央競馬 (JRA) の予想を専門とするアナリストです。
与えられる出馬表・近5走の馬柱・馬場状態・脚質・統計モデルの勝率を読み、
各馬を 1〜5 の5段階で評価し、展開と勝負のポイントを日本語で簡潔に述べてください。

評価の基準:
- 5: 勝ち負け必至の中心馬 (レースに0〜2頭)
- 4: 上位争い確実級
- 3: 展開次第で馬券圏内
- 2: 好走には条件が要る
- 1: 消し
統計モデルの勝率は参考値です。馬柱から読める根拠 (距離・馬場・クラス替わり・
斤量・休み明け・脚質と展開) があれば、モデルと違う評価を恐れないでください。
コメントには馬名でなく必ず馬番 (例:「7番」) を使い、各馬 40字以内で。
出力は指定の JSON のみ。"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pace": {"type": "string", "description": "予想ペース (ハイ/平均/スロー) と逃げ馬の想定"},
        "scenario": {"type": "string", "description": "展開シナリオ 120字以内"},
        "key_point": {"type": "string", "description": "勝負のポイント 100字以内"},
        "danger": {"type": "string", "description": "人気でも危ない馬とその理由 80字以内"},
        "horses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "umaban": {"type": "integer"},
                    "rating": {"type": "integer", "minimum": 1, "maximum": 5},
                    "comment": {"type": "string"},
                },
                "required": ["umaban", "rating", "comment"],
            },
        },
    },
    "required": ["pace", "scenario", "key_point", "danger", "horses"],
}


class PredictionError(Exception):
    pass


def _race_payload(race: Race, model_probs: dict[int, float]) -> dict:
    horses = []
    for h in race.horses:
        past = []
        for p in h.past[:5]:
            past.append({
                "日付": p["date"], "場": p["venue"], "レース": (p["race_name"] + " " + p["grade"]).strip(),
                "コース": f"{p['surface']}{p['distance']}", "馬場": p["condition"], "着順": p["finish"],
                "頭数": p["heads"], "人気": p["ninki"], "着差": p["margin"], "通過": p["passing"],
                "上り": p["agari"], "騎手": p["jockey"], "斤量": p["weight"],
            })
        horses.append({
            "馬番": h.umaban, "枠": h.waku, "馬名": h.name, "性齢": h.sex_age, "斤量": h.weight,
            "騎手": h.jockey, "厩舎": h.trainer, "馬体重": h.body_weight or "未発表", "父": h.sire,
            "脚質": h.style, "間隔": h.interval, "備考": h.rest_note,
            "モデル勝率": round(model_probs.get(h.umaban, 0) * 100, 1), "近5走": past,
        })
    return {
        "レース": {
            "日付": race.date, "場": race.venue, "R": race.rno, "名称": race.name, "クラス": race.cls,
            "コース": race.course, "回り": race.turn, "天候": race.weather, "馬場": race.condition,
            "頭数": race.heads,
        },
        "出走馬": horses,
    }


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise PredictionError("AIの応答が JSON ではありませんでした。")
    return json.loads(text[start:end + 1])


def analyze(api_key: str, race: Race, model_probs: dict[int, float],
            model: Optional[str] = None) -> dict:
    """戻り: {pace, scenario, key_point, danger, ratings:{馬番:1-5}, comments:{馬番:str}, model}"""
    if not api_key:
        raise PredictionError("GEMINI_API_KEY が設定されていません。")
    client = genai.Client(api_key=api_key)
    model = model or DEFAULT_MODEL
    user = ("次のレースを分析し、JSON で出力してください。\n\n```json\n"
            + json.dumps(_race_payload(race, model_probs), ensure_ascii=False) + "\n```")
    use_schema = True

    def request(name: str):
        cfg = genai_types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, max_output_tokens=8192)
        if use_schema:
            cfg.response_mime_type = "application/json"
            cfg.response_json_schema = OUTPUT_SCHEMA
        return client.models.generate_content(model=name, contents=user, config=cfg)

    candidates = [model] + [m for m in FALLBACK_MODELS if m != model]
    last: Optional[Exception] = None
    used = model
    response = None
    for name in candidates:
        delay = 2.0
        for attempt in range(3):
            try:
                response = request(name)
                used = name
                break
            except genai_errors.ServerError as e:      # 503 混雑など
                last = e
            except genai_errors.ClientError as e:
                code = getattr(e, "code", None)
                msg = str(getattr(e, "message", "") or e)
                if code in (429, 404):                  # 利用枠超過 / モデル無し → 次のモデルへ
                    last = e
                    break
                if use_schema and re.search(r"response_json_schema|response_mime_type|json.?schema", msg, re.I):
                    use_schema = False                  # 構造化出力に非対応なら外して再試行
                    continue
                if code in (401, 403):
                    raise PredictionError("Gemini API キーが無効か、権限がありません。") from e
                raise PredictionError(f"Gemini API エラー: {msg}") from e
            if attempt < 2:
                time.sleep(delay)
                delay *= 2
        if response is not None:
            break
    if response is None:
        raise PredictionError(
            f"Gemini が応答しませんでした ({type(last).__name__}: {getattr(last, 'code', '')})。"
            "利用枠を使い切ったか混雑中です。少し待つか GEMINI_MODEL を変えてください。")
    try:
        data = _parse_json(response.text or "")
    except (json.JSONDecodeError, PredictionError) as e:
        raise PredictionError(f"AIの応答を読み取れませんでした: {e}") from e
    ratings, comments = {}, {}
    valid = {h.umaban for h in race.horses}
    for item in data.get("horses", []):
        try:
            u = int(item.get("umaban"))
            r = int(item.get("rating", 3))
        except (TypeError, ValueError):
            continue
        if u in valid:
            ratings[u] = max(1, min(5, r))
            comments[u] = str(item.get("comment", "")).strip()
    return dict(pace=data.get("pace", ""), scenario=data.get("scenario", ""),
                key_point=data.get("key_point", ""), danger=data.get("danger", ""),
                ratings=ratings, comments=comments, model=used)
