"""画面を触るJS（page.evaluate に渡す文字列）の構文検査。   python tests/test_js_syntax.py

**Pythonからは壊れて見えない種類のバグを止める。** JSは文字列として
持っているだけなので、壊れていても import は通りテストも緑になる。
壊れたと分かるのは**本番で画面を触った瞬間**で、そこまで十数分かかるうえ、
短時間に何度も流すとOTPが弾かれる。

実際にこれを踏んだ（2026-09-21）。編集の都合で `'\\n'` が**本物の改行**として
ファイルに書かれ、JSの文字列リテラルが途中で切れていた。

    [PL期間] カレンダーの見出しを読めなかった: SyntaxError: Invalid or unexpected token
    [PL期間] 中の文字を読めなかった:           SyntaxError: Invalid or unexpected token

**いちばん知りたい情報を出すはずの2か所が、まるごと動いていなかった。**
しかも例外を握って先へ進む作りなので、ジョブは緑のまま終わる。

node があるときだけ走る（無い環境では黙って飛ばす）。
"""
import os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TARGETS = ("uleji_sync.py", "dinii_sync.py", "pos_common.py")


def _node_missing() -> bool:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        return False
    except Exception:
        return True


def _blocks(path: str):
    """r\"\"\" ... \"\"\" のうち、アロー関数になっているものをJSとみなす。"""
    src = open(path, encoding="utf-8").read()
    return [b for b in re.findall(r'r"""(.*?)"""', src, re.S) if "=>" in b]


def _check(js: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write("const __f = " + js.strip().rstrip(";") + ";\n")
        path = f.name
    try:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        return "" if r.returncode == 0 else r.stderr.strip()
    finally:
        os.unlink(path)


def test_画面を触るJSが構文として通る():
    if _node_missing():
        print("   （node が無いので飛ばしました）")
        return
    bad = []
    total = 0
    for name in TARGETS:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        for i, js in enumerate(_blocks(path)):
            total += 1
            err = _check(js)
            if err:
                head = js.strip().splitlines()[0][:60]
                bad.append(f"{name} の{i + 1}番目（{head}）: "
                           f"{err.splitlines()[3] if len(err.splitlines()) > 3 else err}")
    assert total, "JSブロックが1つも見つかりません（検査そのものが効いていない）"
    assert not bad, "構文が通らないJSがあります:\n  " + "\n  ".join(bad)
    print(f"   （{total}ブロック検査）")


def test_文字列リテラルの中に生の改行が無い():
    """node が無い環境でも、いちばん踏みやすい形だけは止める。

    `'` で開いた文字列が同じ行で閉じずに終わっていたら、まず改行の混入。"""
    bad = []
    for name in TARGETS:
        path = os.path.join(ROOT, name)
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        for js in _blocks(path):
            for line in js.splitlines():
                # 行末が ' で終わらず、' の数が奇数なら閉じていない
                if line.count("'") % 2 and not line.rstrip().endswith("\\"):
                    bad.append(f"{name}: {line.strip()[:70]}")
    assert not bad, "JSの文字列が行内で閉じていません（生の改行の混入）:\n  " + \
        "\n  ".join(bad)


if __name__ == "__main__":
    from _runner import main
    main(globals(), __file__)
