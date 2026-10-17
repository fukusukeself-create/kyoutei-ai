"""Windows の export_jvdata.py --weekly が作った jv_weekly_YYYYMMDD.zip を取り込む。

    python jravan/load_weekly.py jv_weekly_20261023.zip

data/jv_private/features.csv.gz に足し込む (同じ馬・同じレース日は新しい方・60日より古いものは捨てる)。
JRA-VAN の規約上、このデータは公開リポジトリに入れない (data/jv_private は .gitignore 済み)。
"""
from __future__ import annotations

import datetime as dt
import io
import os
import sys
import zipfile

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import workouts as W  # noqa: E402


def main() -> None:
    with zipfile.ZipFile(sys.argv[1]) as z:
        new = pd.read_csv(io.BytesIO(z.read("features.csv")), dtype={"horse_id": str, "race_date": str})
    os.makedirs(W.PRIVATE, exist_ok=True)
    if os.path.exists(W.FEATS):
        old = pd.read_csv(W.FEATS, dtype={"horse_id": str, "race_date": str})
        new = pd.concat([old, new]).drop_duplicates(["horse_id", "race_date"], keep="last")
    cutoff = (dt.date.today() - dt.timedelta(days=60)).strftime("%Y%m%d")
    new = new[new.race_date >= cutoff].sort_values(["race_date", "horse_id"])
    new.to_csv(W.FEATS, index=False, compression="gzip")
    have = int((new.w_missing == 0).sum())
    print(f"調教の特徴 {len(new):,}頭分 (調教あり {have:,}) / レース日 {sorted(new.race_date.unique())} → {W.FEATS}")


if __name__ == "__main__":
    main()
