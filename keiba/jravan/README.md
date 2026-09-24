# JRA-VAN データの取り出し (Windows)

1. python.org から **Windows installer (32-bit)** の Python をインストールする
   (JV-Link が 32bit 専用のため)。「Add python.exe to PATH」にチェック。
2. コマンドプロンプトで `py -3-32 -m pip install pywin32`
3. `export_jvdata.py` を好きなフォルダに保存し、そのフォルダで `py -3-32 export_jvdata.py`
4. できた `jvdata.zip` を Google ドライブに置く

取り出すもの (2021年1月以降): レース詳細 (RA)、馬毎レース情報 (SE)、払戻 (HR)、
単勝・複勝オッズ (O1)、三連複オッズ (O5)、坂路調教 (HC)、ウッドチップ調教 (WC)。
