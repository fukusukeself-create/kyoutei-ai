"""データ予想モデルの学習スクリプト。

Boatrace Open API から過去の出走表・直前情報・結果をダウンロードして学習し、
kyotei/model_weights.json を作り直す。

使い方（リポジトリ直下で）:
    pip install scipy
    python scripts/train_model.py --days 365

オプション:
    --cache DIR   ダウンロードしたJSONの保存先（既定: .cache/history）
    --holdout N   直近N日を検証用に取り分けて成績を表示する（既定: 60）
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime
import itertools
import json
import os
import sys
import time
import urllib.error
import urllib.request

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kyotei import model, openapi  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


# ---------------------------------------------------------------------------
# ダウンロード
# ---------------------------------------------------------------------------
def _get_json(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kyotei-ai-trainer"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def download_day(cache, d):
    hd = d.strftime("%Y%m%d")
    fn = os.path.join(cache, hd + ".json")
    if os.path.exists(fn):
        return fn
    rec = {}
    for kind in ("programs", "previews", "results"):
        j = _get_json(openapi.day_url(kind, hd))
        rec[kind] = (j or {}).get(kind, [])
    if not rec["results"]:
        return None
    with open(fn + ".tmp", "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(fn + ".tmp", fn)
    return fn


# ---------------------------------------------------------------------------
# データセット作成
# ---------------------------------------------------------------------------
def load_dataset(files):
    """日付順に読み、今節成績を過去レースから復元しながら特徴量を作る。"""
    series_hist = {}  # (jcd, 登番) -> [(日付ordinal, 着順)]
    racer_stats = {}  # 登番(str) -> {コース(str): [出走, 1着, 2連対, 3連対, ST合計, ST回数]}
    Xp, Xl, C_pre, C_live, V, Y, D, has_live = [], [], [], [], [], [], [], []
    for fn in sorted(files):
        with open(fn, encoding="utf-8") as f:
            day = json.load(f)
        previews = {openapi.race_key(r): r for r in day["previews"]}
        results = {openapi.race_key(r): r for r in day["results"]}
        progs = sorted(day["programs"], key=lambda r: (r["race_stadium_number"], r["race_number"]))
        for rec in progs:
            key = openapi.race_key(rec)
            res = results.get(key)
            if res is None:
                continue
            race = openapi.program_to_race(rec)
            dord = datetime.date(int(key[0][:4]), int(key[0][4:6]), int(key[0][6:])).toordinal()
            if len(race["boats"]) == 6:
                for b in race["boats"]:
                    h = series_hist.get((race["jcd"], b["toban"]), [])
                    b["series"] = [{"place": p} for (do, p) in h if dord - do <= 6]
                    b["course_stats"] = racer_stats.get(str(b["toban"]), {})
                pv = previews.get(key)
                race["preview"] = openapi.preview_to_dict(pv) if pv else None
                r = openapi.result_to_dict(res)
                if r["top3"]:
                    x_pre, c_pre = model.build_features(race, use_preview=False)
                    live = model.has_preview(race)
                    x_live, c_live = model.build_features(race, use_preview=True)
                    Xp.append(x_pre); Xl.append(x_live)
                    C_pre.append(c_pre); C_live.append(c_live)
                    V.append(race["jcd"] - 1)
                    Y.append([b - 1 for b in r["top3"]])
                    D.append(dord)
                    has_live.append(live)
            # 今節成績の履歴を更新（このレースの結果は次のレース以降で使える）
            for b in res.get("boats") or []:
                p = b.get("racer_place_number")
                if p and 1 <= p <= 6 and b.get("racer_number"):
                    k = (int(rec["race_stadium_number"]), b["racer_number"])
                    series_hist.setdefault(k, []).append((dord, p))
                c = b.get("racer_course_number")
                if p and c and 1 <= c <= 6 and b.get("racer_number"):
                    row = racer_stats.setdefault(str(b["racer_number"]), {}).setdefault(str(c), [0, 0, 0, 0, 0.0, 0])
                    row[0] += 1; row[1] += p == 1; row[2] += p <= 2; row[3] += p <= 3
                    st = b.get("racer_start_timing")
                    if st is not None and p <= 6:
                        row[4] = round(row[4] + st, 2); row[5] += 1
        # 古い履歴を掃除
        if len(series_hist) > 20000:
            for k in list(series_hist):
                series_hist[k] = [(do, p) for (do, p) in series_hist[k] if dord - do <= 6]
                if not series_hist[k]:
                    del series_hist[k]
    return {
        "X_pre": np.asarray(Xp), "X_live": np.asarray(Xl),
        "C_pre": np.asarray(C_pre) - 1, "C_live": np.asarray(C_live) - 1,
        "V": np.asarray(V), "Y": np.asarray(Y), "D": np.asarray(D),
        "has_live": np.asarray(has_live),
        "racer_stats": racer_stats,
    }


# ---------------------------------------------------------------------------
# 学習（段階別の多項ロジット。各段階は凸なので L-BFGS で解ける）
# ---------------------------------------------------------------------------
def fit_stage(X, C, V, Y, stage, l2_w, l2_v, l2_p, feat_mask):
    from scipy.optimize import minimize

    R, _, F = X.shape
    ar = np.arange(R)
    target = Y[:, stage]
    mask = np.ones((R, 6), dtype=bool)
    for s in range(stage):
        mask[ar, Y[:, s]] = False
    n_pair = stage  # 段階2は1着との組合せ、段階3は1着・2着との組合せ
    prev_c = [C[ar, Y[:, s]] for s in range(stage)]
    sizes = [F, 24 * 6] + [36] * n_pair
    offs = np.cumsum([0] + sizes)

    def unpack(theta):
        w = theta[offs[0]:offs[1]] * feat_mask
        v = theta[offs[1]:offs[2]].reshape(24, 6)
        ps = [theta[offs[2 + i]:offs[3 + i]].reshape(6, 6) for i in range(n_pair)]
        return w, v, ps

    def fun(theta):
        w, v, ps = unpack(theta)
        U = X @ w + v[V[:, None], C]
        for i in range(n_pair):
            U = U + ps[i][prev_c[i][:, None], C]
        U = np.where(mask, U, -1e30)
        U = U - U.max(axis=1, keepdims=True)
        E = np.where(mask, np.exp(U), 0.0)
        P = E / E.sum(axis=1, keepdims=True)
        loss = -np.log(P[ar, target] + 1e-300).mean()
        G = P.copy()
        G[ar, target] -= 1.0
        G /= R
        gw = np.einsum("rif,ri->f", X, G) * feat_mask
        gv = np.zeros((24, 6))
        np.add.at(gv, (V[:, None], C), G)
        gps = []
        for i in range(n_pair):
            gp = np.zeros((6, 6))
            np.add.at(gp, (prev_c[i][:, None], C), G)
            gps.append(gp)
        loss += l2_w * (w @ w) + l2_v * (v * v).sum() + sum(l2_p * (p * p).sum() for p in ps)
        grad = np.concatenate(
            [gw + 2 * l2_w * w, (gv + 2 * l2_v * v).ravel()]
            + [(gps[i] + 2 * l2_p * ps[i]).ravel() for i in range(n_pair)]
        )
        return loss, grad

    res = minimize(fun, np.zeros(offs[-1]), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    w, v, ps = unpack(res.x)
    return w, v, ps, res.fun


def fit_model(X, C, V, Y, feat_mask, l2_w=2e-5, l2_v=2e-4, l2_p=2e-5):
    out = {}
    w, v, _, l1 = fit_stage(X, C, V, Y, 0, l2_w, l2_v, l2_p, feat_mask)
    out.update(w1=w, v1=v)
    w, v, ps, l2 = fit_stage(X, C, V, Y, 1, l2_w, l2_v, l2_p, feat_mask)
    out.update(w2=w, v2=v, p12=ps[0])
    w, v, ps, l3 = fit_stage(X, C, V, Y, 2, l2_w, l2_v, l2_p, feat_mask)
    out.update(w3=w, v3=v, p13=ps[0], p23=ps[1])
    return out, (l1, l2, l3)


# ---------------------------------------------------------------------------
# 検証
# ---------------------------------------------------------------------------
def evaluate(w, X, C, V, Y, label):
    perms = list(itertools.permutations(range(6), 3))
    n = len(X)
    win_hit = 0; ll = 0.0
    ranks = []
    for r in range(n):
        P = model.trifecta_probs(X[r], C[r] + 1, V[r] + 1, w)
        win = P.sum(axis=(1, 2))
        win_hit += int(np.argmax(win) == Y[r, 0])
        ll -= np.log(win[Y[r, 0]] + 1e-12)
        probs = np.array([P[p] for p in perms])
        true_p = P[tuple(Y[r])]
        ranks.append(int((probs > true_p).sum()) + 1)
    ranks = np.asarray(ranks)
    stats = {
        "races": n,
        "win_top1": round(win_hit / n, 4),
        "win_logloss": round(ll / n, 4),
    }
    for k in (1, 3, 5, 8, 10, 12, 16, 20, 30):
        stats[f"trifecta_hit@{k}"] = round(float((ranks <= k).mean()), 4)
    print(f"[{label}] " + json.dumps(stats, ensure_ascii=False))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--end", default="")
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(__file__), "..", ".cache", "history"))
    ap.add_argument("--holdout", type=int, default=60)
    ap.add_argument("--out", default=model.WEIGHTS_PATH)
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.cache, exist_ok=True)
    end = (datetime.date.fromisoformat(args.end) if args.end
           else datetime.datetime.now(JST).date() - datetime.timedelta(days=1))
    days = [end - datetime.timedelta(days=i) for i in range(args.days)][::-1]
    if args.no_download:
        files = [os.path.join(args.cache, d.strftime("%Y%m%d") + ".json") for d in days]
        files = [f for f in files if os.path.exists(f)]
    else:
        with cf.ThreadPoolExecutor(3) as ex:
            files = [f for f in ex.map(lambda d: download_day(args.cache, d), days) if f]
    print(f"{len(files)}日ぶんのデータを使用")

    ds = load_dataset(files)
    R = len(ds["Y"])
    print(f"学習対象 {R} レース（うち展示データあり {int(ds['has_live'].sum())}）")

    pre_mask = np.zeros(model.N_FEATURES); pre_mask[:model.N_PRE_FEATURES] = 1.0
    live_mask = np.ones(model.N_FEATURES)
    live_rows = ds["has_live"]
    meta = {"trained_until": end.isoformat(), "days": len(files), "races": int(R)}

    if args.holdout > 0:
        cut = ds["D"].max() - args.holdout
        tr = ds["D"] <= cut; te = ~tr
        w_pre, _ = fit_model(ds["X_pre"][tr], ds["C_pre"][tr], ds["V"][tr], ds["Y"][tr], pre_mask)
        meta["holdout_pre"] = evaluate(w_pre, ds["X_pre"][te], ds["C_pre"][te], ds["V"][te], ds["Y"][te], "検証: 出走表のみ")
        trl = tr & live_rows; tel = te & live_rows
        w_live, _ = fit_model(ds["X_live"][trl], ds["C_live"][trl], ds["V"][trl], ds["Y"][trl], live_mask)
        meta["holdout_live"] = evaluate(w_live, ds["X_live"][tel], ds["C_live"][tel], ds["V"][tel], ds["Y"][tel], "検証: 展示込み")
        meta["holdout_days"] = args.holdout

    w_pre, _ = fit_model(ds["X_pre"], ds["C_pre"], ds["V"], ds["Y"], pre_mask)
    w_live, _ = fit_model(ds["X_live"][live_rows], ds["C_live"][live_rows], ds["V"][live_rows], ds["Y"][live_rows], live_mask)

    def dump(w):
        return {k: np.round(v, 5).tolist() for k, v in w.items()}

    out = {
        "meta": meta,
        "feature_names": list(model.FEATURE_NAMES),
        "models": {"pre": dump(w_pre), "live": dump(w_live)},
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存しました: {args.out}")
    stats_out = os.path.join(os.path.dirname(os.path.abspath(args.out)), "racer_stats.json")
    with open(stats_out, "w", encoding="utf-8") as f:
        json.dump({"meta": {"until": end.isoformat(), "days": len(files)}, "racers": ds["racer_stats"]},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"保存しました: {stats_out}")


if __name__ == "__main__":
    main()
