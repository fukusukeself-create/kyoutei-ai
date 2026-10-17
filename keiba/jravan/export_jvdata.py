"""JRA-VAN データラボのデータを JV-Link から取り出して、テキストファイルに書き出す (Windows 専用)。

使い方 (Windows のコマンドプロンプトで):
    py -3-32 -m pip install pywin32
    py -3-32 export_jvdata.py                  # 2015年1月以降の、予想に使える全種類
    py -3-32 export_jvdata.py RACE MING        # 種類を絞って取り直す
    py -3-32 export_jvdata.py --from 2010      # 開始年を変える

・JV-Link は 32bit のため、32bit 版の Python が必要 (python.org の "Windows installer (32-bit)")。
・JV-Link 設定に データラボの利用キー が入っていること (無いと -301 で失敗する)。
・取り出すもの (種類 → レコード):
    RACE: RA レース詳細 (ラップ含む) / SE 馬毎レース情報 (通過順・上がり・タイム差) / HR 払戻 /
          O1 単勝・複勝・枠連オッズ / O2 馬連 / O3 ワイド / O4 馬単 / O5 三連複 / WF WIN5 / JG 競走除外
    SLOP: HC 坂路調教            WOOD: WC ウッドチップ調教
    MING: DM タイム型マイニング予想 / TM 対戦型マイニング予想 (JRA-VAN の予想値)
    BLOD: HN 繁殖馬 / SK 産駒 / BT 系統          DIFN: UM 競走馬 / KS 騎手 / CH 調教師 / BR 生産者 / BN 馬主 / RC レコード
    三連単オッズ (O6) と票数 (H1・H6) は大きすぎるので取らない。
・書き出し先: このファイルと同じフォルダの jvdata_out フォルダに、種類×年ごとの zip
  (例: jvdata_RACE_2024.zip)。フォルダごと Google ドライブに置いてもらえれば、アプリ側で取り込む。
・1行 = 1レコード (JV-Data の固定長レコードをそのまま UTF-8 で保存)。項目の切り出しはアプリ側で行う。
・JV-Link は一度ダウンロードしたファイルをパソコンに保存するので、2回目からは速い。
"""

import os
import shutil
import sys
import time
import zipfile

FROM_YEAR = "2015"
# (種類, 取るレコード, 代わりの種類名)。新しい JV-Link では DIFF→DIFN・BLOD→BLDN の名前になっていることがある
SPECS = [
    ("RACE", {"RA", "SE", "HR", "O1", "O2", "O3", "O4", "O5", "WF", "JG"}, []),
    ("SLOP", {"HC"}, []),
    ("WOOD", {"WC"}, []),
    ("MING", {"DM", "TM"}, []),
    ("BLOD", {"HN", "SK", "BT"}, ["BLDN"]),
    ("DIFN", {"UM", "KS", "CH", "BR", "BN", "RC"}, ["DIFF"]),
]
# 年の位置 (レコード先頭からの文字位置)。無いもの (マスタ類) は年で分けない
YEAR_AT = {k: 11 for k in ("RA", "SE", "HR", "O1", "O2", "O3", "O4", "O5", "WF", "JG", "DM", "TM")}
YEAR_AT.update({"HC": 12, "WC": 12})
OPTION_SETUP = 4          # セットアップ (ダイアログ無し)。過去分を FROM から全部取る
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "jvdata_work")
OUT = os.path.join(HERE, "jvdata_out")


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


def year_of(kind: str, rec: str) -> str:
    i = YEAR_AT.get(kind)
    y = rec[i:i + 4] if i is not None else ""
    return y if y.isdigit() else "all"


def open_spec(jv, names: list[str], fromtime: str):
    """種類名を順に試して、開けたものを返す。"""
    for name in names:
        res = jv.JVOpen(name, fromtime, OPTION_SETUP, 0, 0, "")
        ret, readcount, downloadcount, _ = (res + (0, 0, ""))[:4] if isinstance(res, tuple) else (res, 0, 0, "")
        if ret >= 0:
            return name, readcount, downloadcount
        print(f"JVOpen({name}) に失敗しました (コード {ret}): {explain(ret)}")
        jv.JVClose()
        if ret not in (-111, -1):
            return None, 0, 0
    return None, 0, 0


def export_spec(jv, spec: str, keep: set, alts: list[str], fromtime: str) -> dict:
    print(f"\n=== {spec} ({', '.join(sorted(keep))}) {fromtime[:4]}年以降 ===", flush=True)
    name, readcount, downloadcount = open_spec(jv, [spec] + alts, fromtime)
    if not name:
        return {}
    print(f"読み込むファイル {readcount} 件、ダウンロード {downloadcount} 件")
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

    files: dict = {}
    n: dict = {}
    t0 = time.time()
    total = 0
    nfile = 0
    last = time.time()
    print("読み込み中 (しばらく表示が変わらなくても動いています)…", flush=True)
    while True:
        r = jv.JVRead("", 110000, "")
        code, buff = (r[0], r[1]) if isinstance(r, tuple) else (r, "")
        if time.time() - last >= 15:   # 15秒ごとに進み具合を出す
            last = time.time()
            print(f"  …ファイル {nfile}/{readcount} 件目・{total:,} 件 ({time.time()-t0:.0f}秒)", flush=True)
        if code > 0:
            rec = (buff or "").rstrip("\r\n")
            kind = rec[:2]
            if kind in keep:
                key = (kind, year_of(kind, rec))
                if key not in files:
                    files[key] = open(os.path.join(WORK, f"{spec}_{kind}_{key[1]}.txt"), "w", encoding="utf-8", newline="\n")
                files[key].write(rec + "\n")
                n[kind] = n.get(kind, 0) + 1
                total += 1
                if total % 50000 == 0:
                    print(f"  {total:,} 件 ({time.time()-t0:.0f}秒) {n}", flush=True)
        elif code == -1:        # 次のファイルへ
            nfile += 1
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
    print(f"{spec} 完了: {n}", flush=True)

    # 年ごとに zip にまとめる
    years = sorted({y for _, y in files})
    for y in years:
        zpath = os.path.join(OUT, f"jvdata_{spec}_{y}.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for kind, yy in files:
                if yy == y:
                    p = os.path.join(WORK, f"{spec}_{kind}_{y}.txt")
                    z.write(p, arcname=os.path.basename(p))
                    os.remove(p)
        print(f"  → {os.path.basename(zpath)} ({os.path.getsize(zpath)/1e6:.1f} MB)")
    return n


def main():
    args = sys.argv[1:]
    from_year = FROM_YEAR
    if "--from" in args:
        i = args.index("--from")
        from_year = args[i + 1]
        del args[i:i + 2]
    only = {a.upper() for a in args}
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
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    summary = {}
    for spec, keep, alts in SPECS:
        if only and spec not in only:
            continue
        summary[spec] = export_spec(jv, spec, keep, alts, f"{from_year}0101000000")
    shutil.rmtree(WORK, ignore_errors=True)

    print("\n=== まとめ ===")
    for spec, n in summary.items():
        print(f"  {spec}: {n if n else '取れませんでした'}")
    size = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT)) / 1e6
    print(f"\n完了。{OUT} フォルダ ({size:.0f} MB) を、フォルダごと Google ドライブに置いてください。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
