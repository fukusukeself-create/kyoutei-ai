"""AI（Claude / Gemini）による展開予想と最終的な買い目の決定。

集めたデータ一式と、データ予想モデルの確率を文章にまとめてAIに渡し、
決まった形のJSON（展開・各艇の評価・本線/抑えのフォーメーション）で返してもらう。

既定は Claude Opus 5.5（ANTHROPIC_API_KEY があればこちら）。
Anthropic のキーが無く GEMINI_API_KEY だけある時は Gemini を使う。
"""
from __future__ import annotations

import json
import os
import re
import time

from . import formation, model
from .sources import VENUES

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
# 考える深さ。low / medium / high / xhigh / max。深いほど時間と料金がかかる。
CLAUDE_EFFORT = os.environ.get("CLAUDE_EFFORT", "high")
# 料金の目安を出すための単価（100万トークンあたりのドル: 入力, 出力）。
CLAUDE_PRICES = {"claude-opus-5-5": (4.0, 20.0)}

# gemini-3.7-flash は無料枠が 20リクエスト/日 と狭いため既定にしない。
# 従量課金を有効にした場合は環境変数 GEMINI_MODEL で切り替えられる。
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

PROVIDER_LABELS = {"claude": "Claude Opus 5.5", "gemini": "Gemini"}


class AIError(Exception):
    """AIの呼び出しに失敗した時の、利用者向けの説明つきエラー。"""


def pick_provider(anthropic_key: str = "", gemini_key: str = ""):
    """使うAIを決める。Anthropic のキーがあれば Claude、無ければ Gemini。どちらも無ければ None。"""
    if anthropic_key:
        return "claude", anthropic_key
    if gemini_key:
        return "gemini", gemini_key
    return None, ""


def _f(v, fmt="{:.2f}", none="-"):
    try:
        if v is None or v == "":
            return none
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return none


def _series_text(series) -> str:
    parts = []
    for s in series or []:
        place = s.get("place_text") or (str(s["place"]) if s.get("place") else "")
        if not place:
            continue
        c = s.get("course")
        st = s.get("st")
        parts.append(f"{c if c else '?'}コース{place}着" + (f"(ST{st:.2f})" if isinstance(st, (int, float)) else ""))
    return " / ".join(parts) if parts else "なし"


def _course_stat_text(stats, course) -> str:
    row = (stats or {}).get(str(course))
    if not row or not row[0]:
        return "データなし"
    n, w, t2, t3, st_sum, st_n = (list(row) + [0] * 6)[:6]
    st = f" 平均ST{st_sum / st_n:.2f}" if st_n else ""
    return f"{n}走 1着率{w / n * 100:.0f}% 2連対率{t2 / n * 100:.0f}% 3連対率{t3 / n * 100:.0f}%{st}"


def build_race_text(race, analysis, original=None, stadium=None, trend=None, odds=None) -> str:
    """Geminiに読ませるレース資料（人が読んでも分かる文章形式）。"""
    pred = analysis["pred"]
    courses = pred["courses"]
    pv = race.get("preview")
    L = []
    L.append(f"■レース: ボートレース{VENUES.get(race['jcd'], race['jcd'])} {race['rno']}R "
             f"{race.get('title', '')} {race.get('subtitle', '')} [{race.get('grade', '')}] "
             f"{race.get('distance') or ''}m 締切{race.get('deadline') or '?'}"
             + (" ※進入固定レース" if race.get("fixed_course") else ""))

    L.append("\n■出走表")
    for i, b in enumerate(race["boats"]):
        c = courses[i]
        L.append(
            f"{b['boat']}号艇 {b['name']} {b.get('cls', '')} {b.get('age') or '?'}歳 {_f(b.get('weight'), '{:.1f}')}kg"
            f" | 全国 勝率{_f(b.get('nat_win'))} 2連率{_f(b.get('nat_2'), '{:.1f}')}% 3連率{_f(b.get('nat_3'), '{:.1f}')}%"
            + (f" | 当地 勝率{_f(b.get('loc_win'))} 2連率{_f(b.get('loc_2'), '{:.1f}')}%" if b.get("loc_win") else " | 当地 実績なし") +
            f" | モーター{b.get('motor_no') or '?'}号 2連率{_f(b.get('motor_2'), '{:.1f}')}% 3連率{_f(b.get('motor_3'), '{:.1f}')}%"
            f" | ボート2連率{_f(b.get('boat_2'), '{:.1f}')}%"
            f" | 平均ST{_f(b.get('avg_st'))} F{b.get('f') or 0} L{b.get('l') or 0}"
        )
        L.append(f"   今節成績: {_series_text(b.get('series'))}")
        L.append(f"   {c}コースでの過去1年成績: {_course_stat_text(b.get('course_stats'), c)}")

    if pv and model.has_preview(race):
        L.append("\n■直前情報（展示）")
        L.append(f"気象: {pv.get('weather') or '?'} 気温{_f(pv.get('temp'), '{:.0f}')}℃ 水温{_f(pv.get('water_temp'), '{:.0f}')}℃ "
                 f"風 {model.wind_label(pv.get('wind'), pv.get('wind_dir'))} {_f(pv.get('wind'), '{:.0f}')}m "
                 f"波高{_f(pv.get('wave'), '{:.0f}')}cm")
        ex = {b: v.get("ex_time") for b, v in pv["boats"].items() if v.get("ex_time")}
        rank = {b: r + 1 for r, (b, _) in enumerate(sorted(ex.items(), key=lambda kv: kv[1]))}
        slit = sorted(race["boats"], key=lambda b: courses[b["boat"] - 1])
        L.append("スタート展示の進入: " + "".join(str(b["boat"]) for b in slit) + "（内から順）")
        for b in race["boats"]:
            p = pv["boats"].get(b["boat"], {})
            st = p.get("st")
            st_txt = "-" if st is None else (f"F{abs(st):.2f}" if st < 0 else f"{st:.2f}")
            extra = ""
            if p.get("parts"):
                extra += f" 部品交換:{p['parts']}"
            if p.get("propeller"):
                extra += f" プロペラ:{p['propeller']}"
            if p.get("weight_adj"):
                extra += f" 調整重量{p['weight_adj']}kg"
            L.append(f"{b['boat']}号艇: 進入{p.get('course') or '?'}コース 展示ST{st_txt} "
                     f"展示タイム{_f(p.get('ex_time'))}({rank.get(b['boat'], '-')}位) チルト{_f(p.get('tilt'), '{:.1f}')}{extra}")
        if original:
            cols = original["columns"]
            L.append("オリジナル展示（秒。小さいほど良い）: " + " / ".join(cols))
            for b in sorted(original["boats"]):
                vals = original["boats"][b]
                L.append(f"{b}号艇: " + " ".join(f"{c}{_f(vals.get(c))}" for c in cols))
    else:
        L.append("\n■直前情報: まだ公開されていません（展示前）。出走表のデータだけで予想すること。")

    if stadium:
        L.append("\n■この場のコース別成績（最近3か月）")
        for c in range(1, 7):
            s = stadium["courses"].get(c)
            if not s:
                continue
            kim = " ".join(f"{k}{v:.0f}%" for k, v in s["kimarite"].items() if v)
            L.append(f"{c}コース: 1着{s['place'][0]:.1f}% 2着{s['place'][1]:.1f}% 3着{s['place'][2]:.1f}% 決まり手[{kim}]")

    if trend:
        kim = " ".join(f"{k}{v}回" for k, v in trend["kimarite"].items())
        L.append(f"\n■本日ここまでの傾向（{trend['races']}レース消化）")
        L.append(f"1号艇の1着 {trend['lane1_wins']}回 / 決まり手 {kim} / 3連単平均配当 {trend.get('avg_payout') or '-'}円")
        L.append("結果: " + " ".join(f"{r['rno']}R {'-'.join(map(str, r['top3']))}({r.get('technique', '')})" for r in trend["list"]))

    if odds:
        top = sorted(odds.items(), key=lambda kv: kv[1])[:12]
        L.append("\n■3連単オッズ 人気順（参考。売れている順）")
        L.append(" ".join(f"{k}:{v:.1f}倍" for k, v in top))

    L.append(f"\n■データ予想モデル（過去約6万レースから学習。{'展示込み' if pred['mode'] == 'live' else '出走表のみ'}）")
    for i, b in enumerate(race["boats"]):
        L.append(f"{b['boat']}号艇: 1着{pred['win'][i] * 100:.1f}% 2連対{pred['top2'][i] * 100:.1f}% 3連対{pred['top3'][i] * 100:.1f}%")
    L.append("3連単の確率上位: " + " ".join(f"{a}-{b}-{c}:{p * 100:.1f}%" for p, (a, b, c) in pred["trifecta"][:15]))
    for card in analysis["cards"]:
        L.append(f"モデルの{card['label']}: {card['text']}（{card['points']}点）")
    return "\n".join(L)


OUTPUT_SPEC = """{
  "summary": "このレースの結論を1〜2文で",
  "in_trust": "S か A か B か C（1コースが逃げ切る信頼度。Sが最も堅い）",
  "slit": "予想する進入隊形。例 123/456",
  "scenario": "スタートから1マークの展開予想を3〜5文で。誰が攻めて誰が残るか",
  "boats": [
    {"boat": 1, "mark": "◎ ○ ▲ △ のどれか、または空文字", "comment": "その艇の評価を1文で（機力・スタート・コース相性など根拠つき）"}
  ],
  "main": {"first": [1], "second": [2, 3], "third": [2, 3, 4], "reason": "この買い目にした理由を1〜2文で"},
  "sub": {"first": [2], "second": [1], "third": [3, 4], "reason": "本線が外れるならどういう展開か、を1〜2文で"},
  "key_point": "勝負の決め手・注意点を1文で"
}"""


def _boat_list():
    return {"type": "array", "items": {"type": "integer", "enum": [1, 2, 3, 4, 5, 6]}}


def _bet_schema():
    return {
        "type": "object",
        "properties": {"first": _boat_list(), "second": _boat_list(), "third": _boat_list(), "reason": {"type": "string"}},
        "required": ["first", "second", "third", "reason"],
        "additionalProperties": False,
    }


# Claude の構造化出力に渡すJSONスキーマ（この形のJSONが必ず返る）。中身の意味は OUTPUT_SPEC と同じ。
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "in_trust": {"type": "string", "enum": ["S", "A", "B", "C"]},
        "slit": {"type": "string"},
        "scenario": {"type": "string"},
        "boats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "boat": {"type": "integer", "enum": [1, 2, 3, 4, 5, 6]},
                    "mark": {"type": "string", "enum": ["◎", "○", "▲", "△", ""]},
                    "comment": {"type": "string"},
                },
                "required": ["boat", "mark", "comment"],
                "additionalProperties": False,
            },
        },
        "main": _bet_schema(),
        "sub": _bet_schema(),
        "key_point": {"type": "string"},
    },
    "required": ["summary", "in_trust", "slit", "scenario", "boats", "main", "sub", "key_point"],
    "additionalProperties": False,
}


def build_instructions(style: str) -> str:
    """AIへの指示（レースによらず同じ部分）。"""
    main_pts, sub_pts = formation.STYLES.get(style, formation.STYLES[formation.DEFAULT_STYLE])
    return f"""あなたはボートレース（競艇）の予想を専門とするAIです。
利用者が渡すレース資料を全部読み、そのレースの3連単を「当てる」ことを最優先に予想してください。
配当の高さや回収率は考えなくてよく、実際に来る可能性が高い買い目を選ぶこと。

# 予想の進め方
1. 進入隊形を決める（スタート展示の進入があればそれを重視。進入固定なら枠なり）。
2. スタート（展示ST・平均ST・F持ち）と機力（展示タイム・オリジナル展示・モーター・今節成績）から、1マークの展開を組み立てる。
3. 風・波・この場のコース傾向・本日の決まり手の傾向で補正する。
4. データ予想モデルの確率は過去約6万レースの統計から出した土台。基本はこれを尊重し、資料から読み取れる明確な理由がある時だけ動かす。
5. 最後に買い目をフォーメーションで決める。

# 買い目のルール
- 券種は3連単。first=1着候補、second=2着候補、third=3着候補（艇番 1〜6 の数字の配列）。
- main（本線）は展開した時に {main_pts} 点以内。sub（抑え）は {sub_pts} 点以内で、本線と重ならない買い目にする。
- 点数は「同じ艇が重複する組合せを除いた数」で数える。例: first=[1], second=[2,3], third=[2,3,4] は 1-2-3, 1-2-4, 1-3-2, 1-3-4 の4点。
- 当てることが目的なので、点数の上限までしっかり使ってよい。

# 出力
次のJSONだけを出力すること（説明文やコードブロック記号は不要）。boats は1〜6号艇の6件すべて。
読むのは競艇ファンの利用者本人。文章は日本語で、専門用語は短く分かりやすく。
{OUTPUT_SPEC}"""


def build_user_message(race_text: str, memo: str = "") -> str:
    """AIに渡す資料（レースごとに変わる部分）。"""
    memo_block = f"\n\n■利用者のメモ（参考情報として考慮する）\n{memo.strip()}" if memo and memo.strip() else ""
    return f"# 資料\n{race_text}{memo_block}"


def build_prompt(race_text: str, style: str, memo: str = "") -> str:
    """指示と資料を1本にまとめた文章（Gemini用）。"""
    return build_instructions(style) + "\n\n" + build_user_message(race_text, memo)


def parse_response(text: str) -> dict:
    """Geminiの返答からJSONを取り出し、買い目を検証して整える。"""
    if not text:
        raise ValueError("AIからの返答が空でした。")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AIの返答を読み取れませんでした。")
    data = json.loads(t[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("AIの返答の形式が違います。")
    out = {
        "summary": str(data.get("summary") or ""),
        "in_trust": str(data.get("in_trust") or "").strip()[:1].upper(),
        "slit": str(data.get("slit") or ""),
        "scenario": str(data.get("scenario") or ""),
        "key_point": str(data.get("key_point") or ""),
        "boats": [],
        "main": None,
        "sub": None,
    }
    for b in data.get("boats") or []:
        try:
            n = int(b.get("boat"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= n <= 6:
            out["boats"].append({"boat": n, "mark": str(b.get("mark") or "").strip()[:1], "comment": str(b.get("comment") or "")})
    out["boats"].sort(key=lambda x: x["boat"])
    for key in ("main", "sub"):
        f = formation.normalize(data.get(key))
        if f:
            f["reason"] = str((data.get(key) or {}).get("reason") or "")
            out[key] = f
    if out["main"] is None:
        raise ValueError("AIが買い目を返しませんでした。")
    if out["sub"]:
        # 抑えが本線に完全に含まれるなら意味がないので捨てる（一部重なりは表示時に除く）
        main_set = set(formation.expand(out["main"]))
        if all(t in main_set for t in formation.expand(out["sub"])):
            out["sub"] = None
    return out


def call_claude(api_key: str, system: str, user: str, model_name: str | None = None) -> tuple[str, dict]:
    """Claude に予想させる。戻り値は (JSON文字列, 利用量)。"""
    import anthropic

    model_id = model_name or CLAUDE_MODEL
    client = anthropic.Anthropic(api_key=api_key, timeout=300.0, max_retries=2)  # 429/5xx はSDKが自動で再試行する
    try:
        response = client.beta.messages.create(
            model=model_id,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": CLAUDE_EFFORT,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            # 安全フィルタの誤判定で止まった時に、別モデルで自動的にやり直してもらう
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Anthropic のAPIキーが正しくありません。Secrets の ANTHROPIC_API_KEY を確認してください。") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError(f"このAPIキーでは {model_id} を使えません: {e.message}") from e
    except anthropic.NotFoundError as e:
        raise AIError(f"モデル {model_id} が見つかりません。CLAUDE_MODEL の指定を確認してください。") from e
    except anthropic.RateLimitError as e:
        raise AIError("Anthropic API の利用上限に達しました。少し待ってからもう一度押してください。") from e
    except anthropic.APIStatusError as e:
        if getattr(e, "type", "") == "billing_error" or "credit balance" in str(e.message).lower():
            raise AIError("Anthropic API のクレジット残高が足りません。Claude Console の Billing で残高を確認してください。") from e
        if e.status_code >= 500:
            raise AIError(f"Anthropic 側が混み合っています（{e.status_code}）。少し待ってからもう一度押してください。") from e
        raise AIError(f"Anthropic API エラー（{e.status_code}）: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Anthropic API に接続できませんでした。少し待ってからもう一度押してください。") from e

    if response.stop_reason == "refusal":
        raise AIError("AIがこの依頼への回答を見送りました。データ予想の買い目を使ってください。")
    if response.stop_reason == "max_tokens":
        raise AIError("AIの回答が長くなりすぎて途中で切れました。もう一度押してください。")
    text = next((b.text for b in response.content if b.type == "text"), "")
    usage = {"model": response.model, "input_tokens": response.usage.input_tokens,
             "output_tokens": response.usage.output_tokens}
    price = CLAUDE_PRICES.get(response.model)
    if price:
        usage["cost_usd"] = (usage["input_tokens"] * price[0] + usage["output_tokens"] * price[1]) / 1_000_000
    return text, usage


def call_gemini(api_key: str, prompt: str, model_name: str | None = None) -> str:
    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client(api_key=api_key)
    # 需要が集中すると 503 UNAVAILABLE が返るが数秒で復帰することが多いため、
    # 指数バックオフで数回リトライしてから諦める。
    last_error = None
    delay = 1.0
    for attempt in range(4):
        try:
            response = client.models.generate_content(
                model=model_name or GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.4},
            )
            return response.text
        except genai_errors.ServerError as e:
            last_error = e
        except genai_errors.ClientError as e:
            if getattr(e, "code", None) != 429:
                raise  # 認証やモデル名の誤りは待っても直らない
            last_error = e
        if attempt < 3:
            time.sleep(delay)
            delay *= 2
    raise last_error


def predict_with_ai(provider: str, api_key: str, race, analysis, style: str, original=None, stadium=None,
                    trend=None, odds=None, memo: str = "") -> dict:
    """AIに展開と最終の買い目を出してもらう。provider は "claude" か "gemini"。"""
    race_text = build_race_text(race, analysis, original, stadium, trend, odds)
    last = None
    for _ in range(2):  # 返ってきた買い目が成立していない時は1回だけやり直す
        usage = None
        try:
            if provider == "claude":
                text, usage = call_claude(api_key, build_instructions(style), build_user_message(race_text, memo))
            else:
                text = call_gemini(api_key, build_prompt(race_text, style, memo))
            result = parse_response(text)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            continue
        result["race_text"] = race_text
        result["provider"] = provider
        result["usage"] = usage
        return result
    raise AIError(f"AIの返答を買い目に変換できませんでした: {last}")
