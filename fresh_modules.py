"""更新後に、古いままメモリに残っているモジュールを捨てて読み込み直すための小道具。

Streamlit は画面側（app.py）を毎回ファイルから実行し直すが、app.py が import したパッケージは
プロセスが動いている間メモリに残る。GitHub から新しい版が届いた直後は「app.py は新しいのに
パッケージは古い」状態になり、引数の数が合わないなどのエラーになることがある。
app.py の先頭でこれを呼ぶと、パッケージのファイルが読み込み時から変わっていれば捨てて、次の import で読み直させる。
"""
from __future__ import annotations

import os
import sys

_seen: dict[str, float] = {}   # パッケージ名 -> 読み込んだ時点の「いちばん新しいファイルの更新時刻」


def newest_mtime(package_dir: str) -> float:
    newest = 0.0
    for root, _dirs, files in os.walk(package_dir):
        if "__pycache__" in root:
            continue
        for name in files:
            if name.endswith((".py", ".json")):
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
    return newest


def ensure_fresh(package: str, package_dir: str) -> bool:
    """package のファイルが読み込み後に変わっていたら、メモリ上のモジュールを捨てる。捨てたら True。"""
    newest = newest_mtime(package_dir)
    loaded = any(n == package or n.startswith(package + ".") for n in sys.modules)
    stale = loaded and _seen.get(package) != newest
    if stale:
        for name in [n for n in sys.modules if n == package or n.startswith(package + ".")]:
            del sys.modules[name]
    _seen[package] = newest
    return stale
