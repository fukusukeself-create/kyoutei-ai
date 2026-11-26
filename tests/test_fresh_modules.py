"""更新後の読み込み直し（fresh_modules）のテスト。"""
import importlib
import os
import sys

import fresh_modules


def write(path, text, mtime):
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_stale_package_is_reloaded_after_files_change(tmp_path, monkeypatch):
    pkg = tmp_path / "demo_pkg"
    pkg.mkdir()
    write(pkg / "__init__.py", "", 1000)
    write(pkg / "calc.py", "def analyze(a, b, c):\n    return 'old'\n", 1000)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(fresh_modules, "_seen", {})
    importlib.invalidate_caches()
    try:
        # 初回: まだ何も読み込んでいないので、捨てるものはない
        assert fresh_modules.ensure_fresh("demo_pkg", str(pkg)) is False
        from demo_pkg import calc
        assert calc.analyze(1, 2, 3) == "old"
        # ファイルが変わっていなければ、そのまま使い続ける
        assert fresh_modules.ensure_fresh("demo_pkg", str(pkg)) is False
        assert "demo_pkg.calc" in sys.modules

        # 新しい版が届いた（引数が4つに増えた）
        write(pkg / "calc.py", "def analyze(a, b, c, d):\n    return 'new'\n", 2000)
        importlib.invalidate_caches()
        assert fresh_modules.ensure_fresh("demo_pkg", str(pkg)) is True
        assert "demo_pkg.calc" not in sys.modules and "demo_pkg" not in sys.modules
        from demo_pkg import calc as calc2
        assert calc2.analyze(1, 2, 3, 4) == "new"
        assert fresh_modules.ensure_fresh("demo_pkg", str(pkg)) is False
    finally:
        for name in [n for n in sys.modules if n.startswith("demo_pkg")]:
            del sys.modules[name]


def test_package_loaded_before_first_check_is_treated_as_stale(tmp_path, monkeypatch):
    # プロセス起動時から残っている古いパッケージ（今回の不具合の状況）: 初回の確認で必ず読み直す
    pkg = tmp_path / "demo_old"
    pkg.mkdir()
    write(pkg / "__init__.py", "VALUE = 1\n", 1000)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(fresh_modules, "_seen", {})
    importlib.invalidate_caches()
    try:
        import demo_old
        assert demo_old.VALUE == 1
        write(pkg / "__init__.py", "VALUE = 2\n", 2000)
        importlib.invalidate_caches()
        assert fresh_modules.ensure_fresh("demo_old", str(pkg)) is True
        import demo_old as again
        assert again.VALUE == 2
    finally:
        sys.modules.pop("demo_old", None)


def test_newest_mtime_ignores_cache_folders(tmp_path):
    pkg = tmp_path / "p"
    (pkg / "__pycache__").mkdir(parents=True)
    write(pkg / "a.py", "", 1000)
    write(pkg / "weights.json", "{}", 1500)
    write(pkg / "__pycache__" / "a.cpython.py", "", 9999)
    write(pkg / "note.txt", "", 8888)
    assert fresh_modules.newest_mtime(str(pkg)) == 1500
