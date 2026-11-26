"""予想エンジン（モデル・フォーメーション・AI返答の解析）のテスト。通信はしない。"""
import datetime
import itertools

import numpy as np
import pytest

from kyotei import ai, formation, model, openapi, service


def make_race(preview=True):
    boats = []
    for n in range(1, 7):
        boats.append({
            "boat": n, "name": f"選手{n}", "toban": 4000 + n, "cls": ["A1", "A2", "B1", "B1", "B1", "B2"][n - 1],
            "age": 30 + n, "weight": 52.0 + n * 0.3, "f": 0, "l": 0, "avg_st": 0.14 + n * 0.005,
            "nat_win": 7.5 - n * 0.6, "nat_2": 50 - n * 5, "nat_3": 65 - n * 5,
            "loc_win": 7.0 - n * 0.5, "loc_2": 48 - n * 5, "loc_3": 60 - n * 5,
            "motor_no": 10 + n, "motor_2": 35.0, "motor_3": 50.0, "boat_no": 20 + n, "boat_2": 33.0, "boat_3": 50.0,
            "series": [{"place": 2}, {"place": 3}],
        })
    race = {"date": "20261125", "jcd": 12, "rno": 5, "title": "テスト杯", "subtitle": "予選", "grade": "一般",
            "distance": 1800, "deadline": "12:30", "boats": boats, "preview": None}
    if preview:
        race["preview"] = {
            "wind": 3, "wind_dir": 5, "wave": 2, "weather": "晴", "temp": 15, "water_temp": 16,
            "boats": {n: {"course": n, "st": 0.10 + n * 0.01, "ex_time": 6.70 + n * 0.01, "tilt": -0.5, "weight_adj": 0}
                      for n in range(1, 7)},
        }
    return race


# ---------------------------------------------------------------- モデル
def test_predict_gives_valid_probabilities():
    for pv in (True, False):
        pred = model.predict(make_race(pv))
        assert pred["mode"] == ("live" if pv else "pre")
        assert pred["P"].sum() == pytest.approx(1.0, abs=1e-9)
        assert sum(pred["win"]) == pytest.approx(1.0, abs=1e-9)
        assert sum(pred["top3"]) == pytest.approx(3.0, abs=1e-9)
        assert len(pred["trifecta"]) == 120
        # 同じ艇が2回出る組合せの確率は0
        for i in range(6):
            assert pred["P"][i, i, :].sum() == 0 and pred["P"][i, :, i].sum() == 0 and pred["P"][:, i, i].sum() == 0
        # A1で1コースの1号艇が本命になる
        assert int(np.argmax(pred["win"])) == 0


def test_faster_exhibition_time_raises_win_probability():
    base = make_race()
    fast = make_race()
    fast["preview"]["boats"][3]["ex_time"] = 6.55
    assert model.predict(fast)["win"][2] > model.predict(base)["win"][2]


def test_start_exhibition_course_is_used_when_valid():
    race = make_race()
    # 4号艇が前づけで2コースに入った並び 1-4-2-3-5-6
    for boat, course in {1: 1, 4: 2, 2: 3, 3: 4, 5: 5, 6: 6}.items():
        race["preview"]["boats"][boat]["course"] = course
    assert model.resolve_courses(race) == [1, 3, 4, 2, 5, 6]
    race["preview"]["boats"][6]["course"] = None  # 展示の進入が欠けていたら枠なりに戻す
    assert model.resolve_courses(race) == [1, 2, 3, 4, 5, 6]


def test_preview_without_exhibition_times_falls_back_to_pre_model():
    race = make_race()
    for b in race["preview"]["boats"].values():
        b["ex_time"] = None
    assert model.predict(race)["mode"] == "pre"


def test_wind_direction_labels():
    assert model.wind_label(4, 5) == "追い風"
    assert model.wind_label(4, 13) == "向かい風"
    assert model.wind_label(4, 1) == "横風"
    assert model.wind_label(0, 5) == "無風"
    assert model.wind_label(3, 17) == "無風"
    tail, head, cross = model.wind_components(4, 13)
    assert head == pytest.approx(4.0) and tail == 0 and cross == pytest.approx(0.0, abs=1e-9)


def test_series_score_good_form_is_negative():
    assert model.series_score([{"place": 1}, {"place": 1}, {"place": 2}]) < 0
    assert model.series_score([{"place": 6}, {"place": 5}]) > 0
    assert model.series_score([]) == 0
    assert model.series_score([{"place": None}, {"place_text": "F"}]) == 0


def test_course_stats_shrink_toward_average():
    few = model.course_stat_values({"1": [2, 2, 2, 2, 0.2, 2]}, 1)[0]
    many = model.course_stat_values({"1": [60, 60, 60, 60, 6.0, 60]}, 1)[0]
    assert 0 < few < many
    assert model.course_stat_values({}, 1) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------- フォーメーション
def test_expand_removes_duplicate_boats():
    f = {"first": [1], "second": [2, 3], "third": [2, 3, 4]}
    assert formation.expand(f) == [(1, 2, 3), (1, 2, 4), (1, 3, 2), (1, 3, 4)]
    assert formation.to_text(f) == "1 - 2,3 - 2,3,4"
    assert formation.to_text({"first": [1], "second": [2], "third": [1, 2, 3, 4, 5, 6]}) == "1 - 2 - 全"


def test_normalize_cleans_ai_output():
    assert formation.normalize({"first": ["1", 1, 9], "second": [3, 2], "third": [4]}) == {"first": [1], "second": [2, 3], "third": [4]}
    assert formation.normalize({"first": [1], "second": [1], "third": [1]}) is None
    assert formation.normalize("1-2-3") is None


def test_best_formation_respects_point_limit_and_is_optimal_for_single_point():
    pred = model.predict(make_race())
    P = pred["P"]
    one = formation.best_formation(P, 1)
    assert formation.expand(one) == [pred["trifecta"][0][1]]
    for limit in (2, 4, 8, 12):
        f = formation.best_formation(P, limit)
        assert 1 <= len(formation.expand(f)) <= limit


def test_best_formation_matches_brute_force():
    rng = np.random.default_rng(0)
    P = np.zeros((6, 6, 6))
    for i, j, k in itertools.permutations(range(6), 3):
        P[i, j, k] = rng.random() ** 3
    P /= P.sum()
    f = formation.best_formation(P, 6, min_gain=0.0)
    got = formation.coverage(P, formation.expand(f))
    subsets = [s for n in range(1, 7) for s in itertools.combinations(range(1, 7), n)]
    best = 0.0
    for a in (s for s in subsets if len(s) <= 2):
        for b in subsets:
            for c in subsets:
                t = formation.expand({"first": a, "second": b, "third": c})
                if 1 <= len(t) <= 6:
                    best = max(best, formation.coverage(P, t))
    assert got == pytest.approx(best)


def test_suggest_sub_does_not_overlap_main():
    P = model.predict(make_race())["P"]
    for style, (main_pts, sub_pts) in formation.STYLES.items():
        s = formation.suggest(P, style)
        main, sub = formation.expand(s["main"]), formation.expand(s["sub"])
        assert len(main) <= main_pts and len(sub) <= sub_pts
        assert not set(main) & set(sub)


def test_describe_excludes_duplicates_and_reports_probability():
    P = model.predict(make_race())["P"]
    main = {"first": [1], "second": [2, 3], "third": [2, 3, 4]}
    sub = {"first": [1, 2], "second": [1, 2], "third": [3]}
    card = formation.describe(sub, P, {"2-1-3": 45.6}, "抑え", exclude=formation.expand(main))
    assert card["points"] == 1 and card["dups"] == 1
    assert card["tickets"][0]["ticket"] == "2-1-3" and card["tickets"][0]["odds"] == 45.6
    assert card["prob"] == pytest.approx(P[1, 0, 2])


def test_other_bets_probabilities_are_consistent():
    pred = model.predict(make_race())
    o = formation.other_bets(pred["P"])
    assert o["単勝"][0]["ticket"] == "1"
    assert o["単勝"][0]["prob"] == pytest.approx(pred["win"][0])
    a, b = map(int, o["2連複"][0]["ticket"].split("="))
    assert o["2連複"][0]["prob"] == pytest.approx(pred["P"][a - 1, b - 1].sum() + pred["P"][b - 1, a - 1].sum())


# ---------------------------------------------------------------- AI返答
GOOD = """```json
{"summary": "1が逃げる", "in_trust": "A", "slit": "123/456", "scenario": "展開", "key_point": "ST",
 "boats": [{"boat": 2, "mark": "○", "comment": "差し"}, {"boat": 1, "mark": "◎", "comment": "逃げ"}, {"boat": 9, "mark": "x"}],
 "main": {"first": [1], "second": [2, 3], "third": [2, 3, 4], "reason": "本線"},
 "sub": {"first": [2], "second": [1], "third": [3, 4], "reason": "差し"}}
```"""


def test_parse_response_reads_fenced_json():
    r = ai.parse_response(GOOD)
    assert r["in_trust"] == "A"
    assert [b["boat"] for b in r["boats"]] == [1, 2]
    assert r["main"] == {"first": [1], "second": [2, 3], "third": [2, 3, 4], "reason": "本線"}
    assert formation.expand(r["sub"]) == [(2, 1, 3), (2, 1, 4)]


def test_parse_response_rejects_missing_or_broken_bets():
    with pytest.raises(ValueError):
        ai.parse_response("")
    with pytest.raises(ValueError):
        ai.parse_response('{"summary": "x", "main": {"first": [1], "second": [1], "third": [1]}}')
    with pytest.raises(ValueError):
        ai.parse_response("買い目は 1-2-3 です")


def test_parse_response_drops_sub_fully_covered_by_main():
    r = ai.parse_response('{"main": {"first":[1],"second":[2,3],"third":[2,3,4]}, "sub": {"first":[1],"second":[2],"third":[3]}}')
    assert r["sub"] is None


def test_prompt_contains_race_data_and_limits():
    race = make_race()
    model.attach_racer_stats(race, {"racers": {"4001": {"1": [40, 30, 34, 36, 5.6, 40]}}})
    analysis = service.analyze(race, "バランス")
    trend = service.trend([{"rno": 1, "top3": (1, 2, 3), "payout": 900, "technique": "逃げ"}], 5)
    text = ai.build_race_text(race, analysis, original={"columns": ["一周"], "boats": {1: {"一周": 36.5}}},
                              stadium=None, trend=trend, odds={"1-2-3": 8.5})
    assert "住之江 5R" in text and "追い風" in text and "1着率75%" in text and "1-2-3:8.5倍" in text and "一周36.50" in text
    prompt = ai.build_prompt(text, "バランス", "3号艇が気配良し")
    assert "8 点以内" in prompt and "4 点以内" in prompt and "3号艇が気配良し" in prompt


# ---------------------------------------------------------------- 段取り
def test_next_race_and_upcoming():
    jst = service.JST
    now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=jst)
    dl = {1: "10:33", 2: "11:59", 3: "12:00", 4: "12:26", 5: "9:05"}
    assert service.next_race(dl, "20261125", now) == (4, "12:26")
    assert service.next_race(dl, "20261124", now) == (None, None)
    assert service.next_race(dl, "20261126", now) == (1, "10:33")
    assert service.next_race({}, "20261125", now) == (None, None)
    venues = [{"jcd": 5, "name": "多摩川", "deadlines": dl}, {"jcd": 12, "name": "住之江", "deadlines": {1: "12:10"}}]
    ups = service.upcoming(venues, "20261125", now)
    assert [(u["name"], u["rno"]) for u in ups] == [("住之江", 1), ("多摩川", 4)]


def test_trend_and_check_hit():
    results = [
        {"rno": 1, "top3": (1, 2, 3), "payout": 900, "technique": "逃げ"},
        {"rno": 2, "top3": (3, 1, 4), "payout": 5000, "technique": "まくり"},
        {"rno": 3, "top3": (1, 4, 2), "payout": 1500, "technique": "逃げ"},
    ]
    t = service.trend(results, 3)
    assert t["races"] == 2 and t["lane1_wins"] == 1 and t["kimarite"] == {"逃げ": 1, "まくり": 1} and t["avg_payout"] == 2950
    assert service.trend(results, 1) is None
    a = service.analyze(make_race(), "的中重視")
    top = a["pred"]["trifecta"][0][1]
    assert service.check_hit(a["cards"], top) == "本線"
    assert service.check_hit(a["cards"], (6, 5, 4)) == ""
    assert service.check_hit(a["cards"], None) is None


def test_openapi_conversion_roundtrip():
    rec = {"race_date": "2026-11-25", "race_stadium_number": 5, "race_number": 1, "race_closed_at": "2026-11-25 10:33:00",
           "race_grade_number": 5, "race_title": "杯", "race_subtitle": "一般", "race_distance": 1800,
           "boats": [{"racer_boat_number": n, "racer_name": "山田　太郎", "racer_number": 4000 + n, "racer_class_number": 1,
                      "racer_national_top_1_percent": 6.5} for n in range(1, 7)]}
    race = openapi.program_to_race(rec)
    assert race["deadline"] == "10:33" and race["boats"][0]["cls"] == "A1" and race["boats"][0]["name"] == "山田 太郎"
    assert openapi.race_key(rec) == ("20261125", 5, 1)
    res = openapi.result_to_dict({"race_technique_number": 4, "boats": [
        {"racer_boat_number": 3, "racer_place_number": 1}, {"racer_boat_number": 4, "racer_place_number": 2},
        {"racer_boat_number": 1, "racer_place_number": 3}, {"racer_boat_number": 2, "racer_place_number": None}],
        "payouts": {"trifecta": [{"combination": "3-4-1", "payout": 2450}]}})
    assert res["top3"] == (3, 4, 1) and res["technique"] == "まくり差し" and res["payouts"]["trifecta"] == [("3-4-1", 2450)]
    assert openapi.preview_to_dict({"boats": [{"racer_boat_number": 1, "racer_exhibition_time": None}]}) is None


# ---------------------------------------------------------------- まとめて取得
def test_get_bundle_fetches_everything_and_caches(monkeypatch):
    calls = {"program": 0, "preview": 0, "odds": 0}
    preview_box = {"value": None}

    def program(jcd, rno, hd):
        calls["program"] += 1
        return make_race(preview=False)

    def preview(jcd, rno, hd):
        calls["preview"] += 1
        return preview_box["value"]

    def odds(jcd, rno, hd):
        calls["odds"] += 1
        return {"1-2-3": 7.7}

    monkeypatch.setattr(service, "_cache", {})
    monkeypatch.setattr(service, "get_program", program)
    monkeypatch.setattr(service, "get_preview", preview)
    monkeypatch.setattr(service, "get_odds", odds)
    monkeypatch.setattr(service, "get_original_exhibition", lambda *a: None)
    monkeypatch.setattr(service, "get_results", lambda *a: [{"rno": 1, "top3": (1, 2, 3), "payout": 900, "technique": "逃げ"}])
    monkeypatch.setattr(service, "get_stadium", lambda *a: None)

    b1 = service.get_bundle(12, 5, "20261125")
    assert b1["race"]["preview"] is None and b1["odds"] == {"1-2-3": 7.7} and len(b1["results"]) == 1
    assert not model.has_preview(b1["race"])
    b1["race"]["boats"][0]["name"] = "書き換え"  # 呼び出し側が書き換えてもキャッシュは汚れない

    b2 = service.get_bundle(12, 5, "20261125")
    assert calls == {"program": 1, "preview": 1, "odds": 1}
    assert b2["race"]["boats"][0]["name"] == "選手1"

    # 展示が出たあと「最新に更新」すると、直前情報とオッズだけ取り直す（出走表は取り直さない）
    preview_box["value"] = make_race()["preview"]
    service.clear_live(12, 5, "20261125")
    b3 = service.get_bundle(12, 5, "20261125")
    assert calls == {"program": 1, "preview": 2, "odds": 2}
    assert model.has_preview(b3["race"]) and service.analyze(b3["race"])["pred"]["mode"] == "live"


def test_get_bundle_raises_when_program_unavailable(monkeypatch):
    def fail(*a):
        raise service.SourceError("出走表なし")
    monkeypatch.setattr(service, "_cache", {})
    monkeypatch.setattr(service, "get_program", fail)
    for name in ("get_preview", "get_odds", "get_original_exhibition", "get_results", "get_stadium"):
        monkeypatch.setattr(service, name, lambda *a: None)
    with pytest.raises(service.SourceError):
        service.get_bundle(1, 1, "20261125")


def test_empty_preview_is_rechecked_sooner(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(service.time, "time", lambda: clock["t"])
    monkeypatch.setattr(service, "_cache", {})
    n = {"calls": 0}

    def preview(*a):
        n["calls"] += 1
        return None
    monkeypatch.setattr(service, "get_preview", preview)
    service.preview_cached(1, 1, "20261125")
    clock["t"] += service.TTL_PREVIEW_EMPTY - 1
    service.preview_cached(1, 1, "20261125")
    assert n["calls"] == 1
    clock["t"] += 2
    service.preview_cached(1, 1, "20261125")
    assert n["calls"] == 2


def test_minutes_to_deadline():
    now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=service.JST)
    assert service.minutes_to_deadline("12:26", "20261125", now) == 26
    assert service.minutes_to_deadline("11:50", "20261125", now) == -10
    assert service.minutes_to_deadline("12:26", "20261126", now) is None
    assert service.minutes_to_deadline("", "20261125", now) is None


# ---------------------------------------------------------------- AIの選択と呼び出し
def test_pick_provider_prefers_claude():
    assert ai.pick_provider("sk-ant-x", "gem") == ("claude", "sk-ant-x")
    assert ai.pick_provider("", "gem") == ("gemini", "gem")
    assert ai.pick_provider("", "") == (None, "")


def test_instructions_are_stable_and_race_data_goes_in_user_message():
    # 指示はレースによらず同じ（買い方ごとに1種類）。レースの資料とメモは利用者メッセージ側に入る
    assert ai.build_instructions("バランス") == ai.build_instructions("バランス")
    assert "8 点以内" in ai.build_instructions("バランス") and "12 点以内" in ai.build_instructions("的中重視")
    user = ai.build_user_message("住之江 5R の資料", "3号艇が気配良し")
    assert "住之江 5R の資料" in user and "3号艇が気配良し" in user
    assert "住之江" not in ai.build_instructions("バランス")


def test_output_schema_matches_what_parse_response_reads():
    props = ai.OUTPUT_SCHEMA["properties"]
    assert set(ai.OUTPUT_SCHEMA["required"]) == set(props) == {"summary", "in_trust", "slit", "scenario", "boats", "main", "sub", "upset", "hole", "key_point"}
    assert props["main"]["required"] == ["first", "second", "third", "reason"]
    sample = {"summary": "s", "in_trust": "A", "slit": "123/456", "scenario": "x", "key_point": "k",
              "boats": [{"boat": n, "mark": "", "comment": "c"} for n in range(1, 7)],
              "main": {"first": [1], "second": [2, 3], "third": [2, 3, 4], "reason": "r"},
              "sub": {"first": [2], "second": [1], "third": [3], "reason": "r"},
              "upset": "4号艇のまくりで荒れる余地", "hole": {"tickets": ["4-5-1", "4 - 5 - 6", "4-4-1", "7-1-2", "4-5-1"], "reason": "まくり"}}
    r = ai.parse_response(__import__("json").dumps(sample, ensure_ascii=False))
    assert len(r["boats"]) == 6 and formation.expand(r["sub"]) == [(2, 1, 3)]
    assert r["upset"] == "4号艇のまくりで荒れる余地" and r["hole"] == {"tickets": [(4, 5, 1), (4, 5, 6)], "reason": "まくり"}
    # 穴なし（空の配列）は None になり、エラーにはしない
    sample["hole"] = {"tickets": [], "reason": ""}
    assert ai.parse_response(__import__("json").dumps(sample, ensure_ascii=False))["hole"] is None


def test_predict_with_ai_uses_claude_and_retries_once_on_invalid_bets(monkeypatch):
    race = make_race()
    analysis = service.analyze(race, "バランス")
    calls = []
    answers = ['{"main": {"first": [1], "second": [1], "third": [1]}}', GOOD]

    def fake_claude(api_key, system, user, model_name=None):
        calls.append((api_key, system, user))
        return answers[len(calls) - 1], {"model": "claude-opus-5-5", "input_tokens": 4000, "output_tokens": 3000, "cost_usd": 0.076}

    monkeypatch.setattr(ai, "call_claude", fake_claude)
    monkeypatch.setattr(ai, "call_gemini", lambda *a, **k: pytest.fail("Geminiを呼んではいけない"))
    r = ai.predict_with_ai("claude", "sk-ant-x", race, analysis, "バランス", memo="メモです")
    assert len(calls) == 2 and r["provider"] == "claude" and r["usage"]["cost_usd"] == 0.076
    assert r["main"]["first"] == [1] and "住之江 5R" in r["race_text"]
    key, system, user = calls[0]
    assert key == "sk-ant-x" and "8 点以内" in system and "メモです" in user and "住之江 5R" in user

    monkeypatch.setattr(ai, "call_claude", lambda *a, **k: ("買い目なし", None))
    with pytest.raises(ai.AIError):
        ai.predict_with_ai("claude", "sk-ant-x", race, analysis, "バランス")


def test_predict_with_ai_falls_back_to_gemini_provider(monkeypatch):
    race = make_race()
    analysis = service.analyze(race, "少点数")
    monkeypatch.setattr(ai, "call_claude", lambda *a, **k: pytest.fail("Claudeを呼んではいけない"))
    monkeypatch.setattr(ai, "call_gemini", lambda key, prompt, model_name=None: GOOD)
    r = ai.predict_with_ai("gemini", "gem", race, analysis, "少点数")
    assert r["provider"] == "gemini" and r["usage"] is None and r["main"]["second"] == [2, 3]


# ---------------------------------------------------------------- 荒れ度と穴
def test_upset_level_separates_solid_and_open_races():
    solid = np.zeros((6, 6, 6))
    solid[0, 1, 2], solid[0, 2, 1], solid[0, 1, 3] = 0.5, 0.3, 0.2
    flat = np.zeros((6, 6, 6))
    for i, j, k in itertools.permutations(range(6), 3):
        flat[i, j, k] = 1 / 120
    s, f = model.upset_level(solid), model.upset_level(flat)
    assert s["label"] == "堅そう" and f["label"] == "荒れそう"
    assert s["manshu_prob"] < 0.10 < 0.16 < f["manshu_prob"] <= 0.25
    mid = model.upset_level(model.predict(make_race())["P"])
    assert mid["label"] in ("堅そう", "ふつう", "荒れそう") and 0.05 < mid["manshu_prob"] < 0.26


def _fake_odds(P):
    """市場のオッズのつもりで、確率に反比例する倍率を作る（払戻率75%）。"""
    return {f"{i + 1}-{j + 1}-{k + 1}": round(0.75 / max(P[i, j, k], 1e-6), 1)
            for i, j, k in itertools.permutations(range(6), 3)}


def test_hole_uses_only_manshu_odds_stays_small_and_separate():
    a = service.analyze(make_race(), "的中重視", None)
    P = a["pred"]["P"]
    odds = _fake_odds(P)
    a = service.analyze(make_race(), "的中重視", odds)
    hole = a["hole"]
    bought = {t["ticket"] for c in a["cards"] for t in c["tickets"]}
    assert hole and hole["label"] == "穴" and hole["basis"] == "odds"
    assert 1 <= hole["points"] <= formation.HOLE_POINTS
    assert all(t["odds"] >= formation.HOLE_MIN_ODDS for t in hole["tickets"])
    assert not bought & {t["ticket"] for t in hole["tickets"]}
    # 100倍以上・未購入の中で、確率が高い順にちょうど上から取っている
    pool = [(p, "-".join(map(str, t))) for p, t in a["pred"]["trifecta"]
            if odds["-".join(map(str, t))] >= 100 and "-".join(map(str, t)) not in bought and p >= formation.HOLE_MIN_PROB]
    assert [t["ticket"] for t in hole["tickets"]] == [t for _, t in pool[:formation.HOLE_POINTS]]
    assert hole["prob"] == pytest.approx(sum(p for p, _ in pool[:formation.HOLE_POINTS]))
    assert service.check_hit(a["cards"] + [hole], tuple(map(int, hole["tickets"][0]["ticket"].split("-")))) == "穴"
    # AIが選んだ穴は、購入済みと重なるものを除き、8点までにする
    main_first = tuple(map(int, a["cards"][0]["tickets"][0]["ticket"].split("-")))
    ai_card = service.hole_card(P, odds, [main_first], [main_first, (6, 5, 4), (6, 5, 3)] + [(5, 6, k) for k in (1, 2, 3, 4)] + [(6, 4, k) for k in (1, 2, 3, 5)], "大外")
    assert ai_card["basis"] == "ai" and ai_card["points"] == 8 and ai_card["reason"] == "大外"
    assert "-".join(map(str, main_first)) not in {t["ticket"] for t in ai_card["tickets"]}


def test_hole_without_odds_avoids_top_ranked_and_bought_tickets():
    a = service.analyze(make_race(preview=False), "バランス", {})
    hole = a["hole"]
    top20 = {"-".join(map(str, t)) for _, t in a["pred"]["trifecta"][:20]}
    bought = {t["ticket"] for c in a["cards"] for t in c["tickets"]}
    got = {t["ticket"] for t in hole["tickets"]}
    assert hole["basis"] == "rank" and 1 <= len(got) <= formation.HOLE_POINTS
    assert not got & top20 and not got & bought


def test_no_hole_when_every_ticket_is_below_manshu():
    P = model.predict(make_race())["P"]
    odds = {k: 50.0 for k in _fake_odds(P)}
    assert formation.suggest_hole(P, odds, ()) == ([], "odds")
    assert service.hole_card(P, odds, []) is None


def test_race_text_lists_hole_candidates_and_upset():
    race = make_race()
    odds = _fake_odds(model.predict(race)["P"])
    analysis = service.analyze(race, "バランス", odds)
    text = ai.build_race_text(race, analysis, odds=odds)
    assert "■荒れ度" in text and "■穴候補" in text and "モデルの穴:" in text
    line = text.split("■穴候補")[1].split("\n")[1]
    assert all(float(x.split(":")[1].split("倍")[0]) >= 100 for x in line.split())
    assert formation.parse_ticket("3-4-1") == (3, 4, 1) and formation.parse_ticket("3-3-1") is None and formation.parse_ticket("1-2") is None
    assert f"{formation.HOLE_POINTS} 点以内" in ai.build_instructions("バランス")
