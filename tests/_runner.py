"""スクリプト実行（`python tests/xxx.py`）で全テストを走らせる共通部品。

**なぜ要るか。** 各ファイルの末尾に `if __name__ == "__main__": _main()` を
置く書き方だと、**あとからその行より下にテストを足したときに走らない**。
pytest では走るのでテストは緑に見え、スクリプト実行だけが静かに素通りする。

このセッションで3回踏んだ。だから「何件見つけたか」ではなく
**「ソースにある `def test_` の数と一致するか」**を突き合わせる。
足したのに走っていなければ、そこで止まる。
"""
import re
import sys


def run_tests(namespace: dict, source_path: str) -> int:
    found = [(n, f) for n, f in sorted(namespace.items())
             if n.startswith("test_") and callable(f)]
    with open(source_path, encoding="utf-8") as fh:
        declared = len(re.findall(r"^def (test_\w+)", fh.read(), re.M))
    if len(found) != declared:
        print(f"!! ソースに def test_ が {declared}件 あるのに {len(found)}件しか"
              f"見えていません。`if __name__` ブロックより下に書いていませんか")
        return 1
    bad = 0
    for name, fn in found:
        try:
            fn()
            print(f"OK {name}")
        except AssertionError as e:
            bad += 1
            print(f"NG {name}: {e}")
    print("---", f"{len(found)}件 全部通りました" if not bad else f"{bad}件 失敗")
    return 1 if bad else 0


def main(namespace: dict, source_path: str):
    sys.exit(run_tests(namespace, source_path))
