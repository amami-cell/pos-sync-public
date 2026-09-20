"""損益PL集計の期間ラベル。   python tests/test_period.py

CSVは**画面の期間ぶん1行**しか落ちないので、ここを間違えると
「別の月を指定したつもりで当月のまま」になる。うるう年と月末日だけ押さえる。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ULEJI_COMPANY", "x")
from uleji_sync import _month_range_label


def test_月末日が月ごとに変わる():
    assert _month_range_label("2026-08") == ("2026年08月01日", "2026年08月31日")
    assert _month_range_label("2026-09") == ("2026年09月01日", "2026年09月30日")


def test_うるう年の2月():
    assert _month_range_label("2026-02") == ("2026年02月01日", "2026年02月28日")
    assert _month_range_label("2028-02") == ("2028年02月01日", "2028年02月29日")


def test_月はゼロ埋めする():
    # 画面の表記は 2026年01月01日。ゼロを落とすと一致判定が通らず、
    # 「合わせられなかった」と誤って中止する
    assert _month_range_label("2026-01") == ("2026年01月01日", "2026年01月31日")


if __name__ == "__main__":
    test_月末日が月ごとに変わる()
    test_うるう年の2月()
    test_月はゼロ埋めする()
    print("--- 全部通りました")
