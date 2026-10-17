"""その日の全レース (芝・ダート) を予想し、単勝で買うべき馬の一覧を出す。

    python picks.py              # 今日 (日本時間)
    python picks.py 20261011     # 日付指定
    python picks.py 20261011 --all   # 買い目なしのレースも、勝率1位と期待値最大の馬を表示
    python picks.py 20261018 --plan  # レースごとのおすすめの賭け方を1行で (単勝が基本)
    python picks.py 20261018 --plan --why   # おすすめの馬の根拠 (勝率の内訳・調教タイム・騎手の前日と今日) も
    python picks.py --soon 30        # 今日、発走30分以内のレースだけ (馬単・ワイドは直前のオッズで買う)
    python picks.py --soon 30 --min 5 --once shown.json --plan --why   # 発走5〜30分前で、まだ出していないレースだけ
    python picks.py 20261011 --hit   # 的中重視: オッズを見ずに、本命の勝率が高いレースの本命を複勝で (モンテカルロで当たる数も)

買う条件は models/profit.json の検証済みの方針 (単勝・期待値しきい値・勝率下限・新馬/未勝利は除外)。
オッズは発走前に動くので、買う直前にもう一度実行する。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("KEIBA_PREFER_CURL", "1")

import evidence  # noqa: E402
import features as F  # noqa: E402
import montecarlo as mc  # noqa: E402
import workouts  # noqa: E402
import scraper  # noqa: E402
import statmodel  # noqa: E402
import strategy  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
EVERY_PMIN = strategy.EVERY_PMIN   # 毎レース1頭買うときの候補 (strategy.recommend)
HIT_BUY, HIT_CAND = strategy.HIT_BUY, 0.40   # 的中重視: 本命の勝率がこれ以上なら複勝で買う / 候補 (backtest/hit_first.py)


def now_str() -> str:
    return dt.datetime.now(JST).strftime("%Y%m%d")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=dt.datetime.now(JST).strftime("%Y%m%d"))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--soon", type=int, help="発走まで この分数以内のレースだけ (例 --soon 30。買う直前の出し直し用)")
    ap.add_argument("--plan", action="store_true",
                    help="レースごとのおすすめの賭け方を1行で (単勝が基本: 単勝の期待値買い → 本命の複勝 → 参考のワイド・馬単 → 見送り)")
    ap.add_argument("--min", type=int, default=0, help="--soon と一緒に: 発走まで この分数未満のレースは除く")
    ap.add_argument("--once", help="--soon と一緒に: 出したレースをこのファイルに記録し、2回目からは出さない")
    ap.add_argument("--why", action="store_true", help="--plan で、おすすめの馬の根拠も出す")
    ap.add_argument("--hit", action="store_true", help="的中重視: オッズを見ずに、本命の勝率が高いレースの本命を複勝で買う")
    a = ap.parse_args()
    if not statmodel.available():
        sys.exit("統計モデルを読み込めません (pip install -r requirements.txt)")
    pol = (statmodel.load() or {}).get("value_policy")
    rule = ((pol or strategy.DEFAULT_VALUE_POLICY)["policy"].get("単勝") or {})
    if a.plan:
        print(f"{a.date} 全レースの単勝 (◎勝負: 期待値{rule.get('threshold', 1.1)}以上・新馬未勝利除く=学習外2026年 回収105% 的中約10% / "
              f"○いつもの1頭: 勝率{EVERY_PMIN*100:.0f}%以上で期待値1位=学習外 回収約91% 的中約23%)")
    elif a.hit:
        print(f"{a.date} 的中重視: 本命の勝率 {HIT_BUY*100:.0f}%以上 → 複勝で買い / {HIT_CAND*100:.0f}%以上 → 候補 (オッズは見ない)")
    else:
        print(f"{a.date} 買い条件 (新馬・未勝利は除く): " + " / ".join(
            f"{k} 期待値{v['threshold']}以上" for k, v in ((pol or strategy.DEFAULT_VALUE_POLICY)["policy"]).items()))
    # 前日の結果を騎手・調教師の直近60日に足す (学習と同じ扱い)。今日の結果は表示だけ
    prev = evidence.prev_kaisai(a.date)
    rides_prev = evidence.day_rides(prev) if prev else []
    rides_today = (evidence.day_rides(a.date, dt.datetime.now(JST).strftime("%H:%M")) if a.date == now_str()
                   else evidence.day_rides(a.date) if a.date < now_str() else [])
    xform = evidence.extra_form(rides_prev + rides_today)
    if prev:
        print(f"騎手・調教師の調子: {prev[4:6]}/{prev[6:]} の結果 {len(rides_prev)}騎乗を反映")
    import json
    shown = set(json.load(open(a.once))) if a.once and os.path.exists(a.once) else set()
    n_races = n_buy = n_work = 0
    day_bets: list[dict] = []
    hit_ps: list[float] = []
    now = dt.datetime.now(JST)
    for m in scraper.fetch_meetings(a.date):
        for r in m.races:
            if a.soon is not None:
                st = getattr(r, "start", "") or ""
                if not st or a.date != now.strftime("%Y%m%d"):
                    continue
                mins = (int(st[:2]) * 60 + int(st[3:5])) - (now.hour * 60 + now.minute)
                if not a.min <= mins <= a.soon:
                    continue
                if a.once and r.race_id in shown:
                    continue
            try:
                race = scraper.fetch_shutuba(r.race_id, a.date)
                try:
                    scraper.attach_past(race, scraper.fetch_past(r.race_id))
                except scraper.ScrapeError:
                    pass
                odds = scraper.fetch_all_odds(r.race_id)
                sm = statmodel.predict(race, odds.get("win"), extra_form=xform)
                if sm:
                    adj = workouts.adjust(sm["probs"], {h.umaban: h.horse_id for h in race.horses}, race.date or a.date)
                    if adj:
                        sm["model_probs_raw"] = dict(sm["probs"])
                        sm["probs"] = adj["probs"]
                        n_work += 1
            except Exception as e:  # 1レースの失敗で全体を止めない
                print(f"{m.venue}{r.rno}R 取得失敗: {e}")
                continue
            if a.plan and sm:
                bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
                skip = strategy.skip_reason(race, bands)
                names = {h.umaban: h.name for h in race.horses}
                u = max(sm["probs"], key=sm["probs"].get)
                pw = sm["probs"][u]
                pl = mc.probs(sm["probs"], 20000)["place"].get(u, 0)
                ow = (odds.get("win") or {}).get(f"{u:02d}")
                head = f"{m.venue}{r.rno:>2}R {getattr(r, 'start', '')} {race.surface}{race.distance}m"
                fav = f"本命 {u}番{names.get(u, '')} 勝率{pw*100:.0f}% 3着内{pl*100:.0f}%" + (f" 単勝{ow}倍" if ow else "")
                n_races += 1
                rc = strategy.recommend(sm["probs"], odds, pol, bands, skip)
                x = rc["umaban"]
                rec = f"{rc['mark']}単勝 {x}番{names.get(x, '')} (勝率{rc['prob']*100:.0f}%" + (
                    f" {rc['odds']}倍 期待値{rc['ev']:.2f})" if rc["odds"] else " オッズ未発売)")
                n_buy += 1
                extra = []
                if rc["fuku"]:
                    extra.append(f"複勝なら {rc['fuku']}番 (3着内{pl*100:.0f}%)")
                if rc["exotic"]:
                    extra.append("参考 " + "、".join(f"{t.kind}{('→' if t.kind == '馬単' else '-').join(map(str, t.combo))}" for t in rc["exotic"][:2]))
                if skip:
                    extra.append("注意: " + (skip[:-3] if skip.endswith('のため') else skip.removesuffix('ため')))
                print(f"{head} | {rec} | {fav}" + (f" | {' / '.join(extra)}" if extra else ""))
                if a.once:
                    shown.add(r.race_id)
                    json.dump(sorted(shown), open(a.once, "w"))
                if a.why and sm.get("features"):
                    ev = evidence.reasons(x, race, sm, statmodel.load()["stats"], race_wk=evidence.race_workouts(race),
                                          rides_today=rides_today, rides_prev=rides_prev)
                    if sm.get("model_probs_raw"):
                        ev["model"] = sm["model_probs_raw"].get(x, ev["model"])
                    for line in evidence.text(ev, final=sm["probs"].get(x)):
                        print("      " + line)
                continue
            if a.hit and sm:
                bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
                skip = strategy.skip_reason(race, bands)
                u = max(sm["probs"], key=sm["probs"].get)
                pw = sm["probs"][u]
                head = f"{m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m"
                name_u = next((h.name for h in race.horses if h.umaban == u), "")
                if skip:
                    pl = mc.probs(sm["probs"], 20000)["place"].get(u, 0)
                    print(f"  見送り推奨 {head}  (参考の本命 {u}番 {name_u} 勝率{pw*100:.0f}% 3着内{pl*100:.0f}%): {skip}")
                    continue
                n_races += 1
                pl = mc.probs(sm["probs"], 20000)["place"].get(u, 0)
                mark = "◎複勝で買い" if pw >= HIT_BUY else "○候補" if pw >= HIT_CAND else "  見送り"
                if pw >= HIT_CAND or a.all:
                    print(f"{mark} {head}  {u}番 {{names}}  勝率{pw*100:.0f}% 3着内{pl*100:.0f}%".replace("{names}", next((h.name for h in race.horses if h.umaban == u), "")))
                if pw >= HIT_BUY:
                    n_buy += 1
                    hit_ps.append(pl)
                continue
            if not sm or not odds.get("win"):
                print(f"{m.venue}{r.rno}R オッズ未発売 (買い目は発売後に)")
                continue
            bands = strategy.race_bands(race.surface, race.heads, F.class_rank(" ".join([race.cls, race.grade, race.name])))
            skip = strategy.skip_reason(race, bands)
            if skip:
                print(f"  見送り推奨 {m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m {race.cls or race.grade}: {skip}")
                continue
            n_races += 1
            names = {h.umaban: h.name for h in race.horses}
            vb = strategy.value_bets(sm["probs"], odds, pol, bands)
            buys = vb.get("単勝", [])
            exotic = [t for k in ("ワイド", "馬単") for t in vb.get(k, [])]
            head = f"{m.venue}{r.rno}R {r.start if hasattr(r, 'start') else ''} {race.surface}{race.distance}m {race.cls or race.grade}"
            sim = mc.probs(sm["probs"], 20000) if buys else None
            for t in buys:
                n_buy += 1
                u = t.combo[0]
                print(f"★買い {head}  {u}番 {names.get(u, '')}  勝率{t.prob*100:.1f}% 3着内{sim['place'].get(u, 0)*100:.0f}% "
                      f"オッズ{t.odds:.1f} 期待値{t.ev:.2f}")
                day_bets.append(dict(race_id=r.race_id, win=sm["probs"], kind="単勝", umaban=u, odds=t.odds, stake=100))
            for t in exotic:
                n_buy += 1
                sep = "→" if t.kind == "馬単" else "-"
                nm = sep.join(f"{u}番{names.get(u, '')}" for u in t.combo)
                print(f"★{t.kind} {head}  {nm}  確率{t.prob*100:.1f}% オッズ{t.odds:.1f} 期待値{t.ev:.2f}")
                day_bets.append(dict(race_id=r.race_id, win=sm["probs"], kind=t.kind, umaban=t.combo[0], second=t.combo[1],
                                     odds=t.odds, stake=100))
            if not buys and not exotic and a.all:
                u = max(sm["probs"], key=sm["probs"].get)
                print(f"  見送り {head}  本命 {u}番 {names.get(u, '')} 勝率{sm['probs'][u]*100:.0f}%")
    print(f"対象 {n_races}レース / 買い {n_buy}点 (1点100円なら {n_buy*100}円)")
    wk = workouts.load()
    print(f"調教タイム補正 (JRA-VAN): {n_work}レースに適用" if n_work else
          ("調教タイム補正: この日の調教データが無いので未適用 (jravan/load_weekly.py で取り込む)" if wk else
           "調教タイム補正: 調教データ未取り込み (data/jv_private) のため未適用"))
    if a.plan:
        print("※ オッズは発走前に動くので、発走10〜30分前に `picks.py --soon 30 --plan` で出し直す。"
              "○は毎レース買う前提の1頭で、続けると平均では少し負ける (控除率のため)。ワイド・馬単は的中率が低く参考扱い。")
        return
    if a.hit:
        import numpy as np
        rng = np.random.default_rng(0)
        hits = (rng.random((50000, len(hit_ps))) < np.array(hit_ps)).sum(axis=1) if hit_ps else np.zeros(1)
        print(f"\nモンテカルロ (5万回): 当たる数の平均 {hits.mean():.1f}点 / 1点も当たらない確率 {(hits == 0).mean()*100:.1f}% / "
              f"全部当たる確率 {(hits == len(hit_ps)).mean()*100:.0f}%")
        print("検証 (学習外 2025〜2026年・新馬未勝利を除く・調教補正あり): 本命の勝率50%以上の複勝は 250レースで "
              "的中88.8% 回収97.4%、40%以上は 725レースで 的中79.2% 回収89.3%。当たりやすいが、平均では少し負ける。")
        return
    if day_bets:
        s = mc.day_pnl(day_bets, 50000)
        print(f"\nモンテカルロ (この日の買い目を5万回模擬): 1点も当たらない確率 {s['p_zero_hit']*100:.0f}%")
        print("※ 収支の見込みは出さない (モデルの確率を正しいと仮定すると楽観的になりすぎるため)。"
              "検証 (2026年・学習外・JRA-VAN の確定オッズ): 単勝 期待値1.1以上 292点 回収105%、ワイド 1.5以上 591点 104%、"
              "馬単 1.5以上 1,370点 109% (馬単は的中1%台)。どれも点数が少なく偶然の幅が大きい。")
        print("※ 馬単・ワイドは前日や朝のオッズだと期待値が高く出すぎる (売れている量が少ないため)。"
              "検証は発走時点の確定オッズで行ったので、買うのは発走10〜30分前に出し直して残った組だけにする。")

if __name__ == "__main__":
    main()
