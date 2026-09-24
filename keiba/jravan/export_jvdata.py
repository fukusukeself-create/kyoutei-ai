"""JRA-VAN データラボのデータを JV-Link から取り出して、テキストファイルに書き出す (Windows 専用)。

使い方 (Windows のコマンドプロンプトで):
    py -3-32 -m pip install pywin32
    py -3-32 export_jvdata.py             # RACE・SLOP・WOOD をすべて
    py -3-32 export_jvdata.py RACE        # レース・払戻・オッズだけ (取り直し用)

・JV-Link は 32bit のため、32bit 版の Python が必要 (python.org の "Windows installer (32-bit)")。
・2021年1月以降の次のデータを書き出す。初回は JRA-VAN からのダウンロードに時間がかかる。
    RACE: RA (レース詳細)  SE (馬毎レース情報)  HR (払戻)  O1 (単勝・複勝・枠連オッズ)  O5 (三連複オッズ)
    SLOP: HC (坂路調教)
    WOOD: WC (ウッドチップ調教)
・書き出し先: このファイルと同じフォルダの jvdata フォルダ。最後に jvdata.zip にまとめる。
  その zip を Google ドライブに置いてもらえれば、アプリ側で取り込む。
・1行 = 1レコード (JV-Data の固定長レコードをそのまま UTF-8 で保存)。項目の切り出しはアプリ側で行う。
"""

import os
import sys
import time
import zipfile

FROM = "20210101000000"
SPECS = [
    ("RACE", {"RA", "SE", "HR", "O1", "O5"}),
    ("SLOP", {"HC"}),
    ("WOOD", {"WC"}),
]
OPTION_SETUP = 4          # セットアップ (ダイアログ無し)。過去分を FROM から全部取る
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "jvdata")


ERRORS = {
    -1: "該当データがありません",
    -2: "セットアップ画面でキャンセルされました。もう一度実行し「スタートキットを持っていない」を選んで OK を押してください",
    -111: "データ種別の指定が正しくありません", -115: "開始日時の指定が正しくありません",
    -201: "JVInit が行われていません", -202: "前回の処理が終わっていません。少し待つか、パソコンを再起動してから実行してください",
    -301: "利用キーの認証に失敗しました (JV-Link 設定の利用キーを確認)", -302: "利用キーの有効期限が切れています",
    -303: "利用キーが設定されていません (JV-Link 設定で入力)", -401: "JV-Link 内部エラー", -411: "サーバーエラー (HTTP 404)",
    -412: "サーバーエラー (HTTP 403)", -413: "サーバーエラー", -421: "サーバーエラー", -431: "サーバーエラー",
    -501: "スタートキットが正しくありません", -502: "ダウンロードに失敗しました (通信を確認して再実行)",
    -503: "ファイルが見つかりません", -504: "サーバーメンテナンス中です。時間をおいて再実行してください",
}


def explain(code: int) -> str:
    return ERRORS.get(code, "不明なエラー")


def main():
    only = {a.upper() for a in sys.argv[1:]}
    if sys.maxsize > 2 ** 32:
        print("64bit 版の Python で動いています。JV-Link は 32bit 専用なので、32bit 版で実行してください:")
        print("    py -3-32 export_jvdata.py")
        return 1
    try:
        import win32com.client
    except ImportError:
        print("pywin32 がありません。先に次を実行してください:  py -3-32 -m pip install pywin32")
        return 1

    jv = win32com.client.Dispatch("JVDTLab.JVLink")
    ret = jv.JVInit("UNKNOWN")
    if ret != 0:
        print(f"JVInit に失敗しました (コード {ret})。JV-Link の設定で利用キーを確認してください。")
        return 1
    os.makedirs(OUT, exist_ok=True)

    for spec, keep in SPECS:
        if only and spec not in only:
            continue
        print(f"\n=== {spec} ({', '.join(sorted(keep))}) {FROM[:8]} 以降 ===")
        res = jv.JVOpen(spec, FROM, OPTION_SETUP, 0, 0, "")
        ret, readcount, downloadcount, lastts = (res + (0, 0, ""))[:4] if isinstance(res, tuple) else (res, 0, 0, "")
        if ret < 0:
            print(f"JVOpen({spec}) に失敗しました (コード {ret}): {explain(ret)}")
            continue
        print(f"読み込むファイル {readcount} 件、ダウンロード {downloadcount} 件")
        # ダウンロードが終わるまで待つ
        while downloadcount > 0:
            st = jv.JVStatus()
            if st < 0:
                print(f"ダウンロード中にエラー (コード {st}): {explain(st)}")
                break
            print(f"\rダウンロード {st}/{downloadcount}", end="", flush=True)
            if st >= downloadcount:
                break
            time.sleep(2)
        print()

        files = {k: open(os.path.join(OUT, f"{spec}_{k}.txt"), "w", encoding="utf-8", newline="\n") for k in keep}
        n = {k: 0 for k in keep}
        t0 = time.time()
        while True:
            r = jv.JVRead("", 110000, "")
            code, buff = (r[0], r[1]) if isinstance(r, tuple) else (r, "")
            if code > 0:
                rec = (buff or "").rstrip("\r\n")
                kind = rec[:2]
                if kind in files:
                    files[kind].write(rec + "\n")
                    n[kind] += 1
                    total = sum(n.values())
                    if total % 20000 == 0:
                        print(f"  {total:,} 件 ({time.time()-t0:.0f}秒) {n}", flush=True)
            elif code == -1:        # 次のファイルへ
                continue
            elif code == 0:         # すべて読み終えた
                break
            elif code == -3:        # まだダウンロード中
                time.sleep(1)
            else:
                print(f"JVRead エラー (コード {code}): {explain(code)}")
                break
        for f in files.values():
            f.close()
        jv.JVClose()
        print(f"{spec} 完了: {n}")

    zpath = os.path.join(HERE, "jvdata.zip" if not only else f"jvdata_{'_'.join(sorted(only))}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(OUT)):
            if only and name.split("_")[0] not in only:
                continue
            z.write(os.path.join(OUT, name), arcname=name)
    print(f"\n完了。{zpath} を Google ドライブに置いてください ({os.path.getsize(zpath)/1e6:.0f} MB)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
