# JRA-VAN データの取り出し (Windows)

1. python.org から **Windows installer (32-bit)** の Python をインストールする
   (JV-Link が 32bit 専用のため)。「Add python.exe to PATH」にチェック。
2. コマンドプロンプトで `py -3-32 -m pip install pywin32`
3. `export_jvdata.py` を好きなフォルダに保存し、そのフォルダで `py -3-32 export_jvdata.py`
4. できた `jvdata_out` フォルダを、フォルダごと Google ドライブに置く

取り出すもの (既定は2015年1月以降、`--from 2010` で変更可): レース詳細 (RA)・馬毎レース情報 (SE)・払戻 (HR)・
単勝複勝/馬連/ワイド/馬単/三連複の確定オッズ (O1〜O5)・坂路 (HC)・ウッド (WC) の調教タイム・
JRA-VAN のマイニング予想 (DM・TM)・血統 (HN・SK・BT)・競走馬/騎手/調教師などのマスタ。
種類×年ごとの zip (例: `jvdata_RACE_2024.zip`) に分けて書き出す。

## 毎週の作業 (調教タイムを予想に使う)

1. 金曜の夕方 (出馬表が出た後) に、Windows のコマンドプロンプトで
   `py -3-32 export_jvdata.py --weekly`
2. できた `jvdata_out\jv_weekly_YYYYMMDD.zip` (数十KB) を Google ドライブの `jvdata_out` フォルダに置く
3. Claude に「調教データ置きました」と伝える → `python jravan/load_weekly.py jv_weekly_YYYYMMDD.zip` で取り込み、
   `picks.py` が勝率を調教タイムで補正する (係数は `models/workout_blend.json`、`backtest/fit_workout_blend.py` で作る)

JRA-VAN のデータは規約上 公開リポジトリに置けないので、取り込んだ特徴は `keiba/data/jv_private/` (git に入れない) に置く。
