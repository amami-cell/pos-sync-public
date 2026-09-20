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


def _main():
    """pytest でもスクリプトでも同じものを走らせる。

    以前はここに呼び出しを並べていたため、**あとから足したテストが
    スクリプト実行では走らず**、それでも「全部通りました」と出ていた。
    数えて出すようにする。"""
    import sys as _sys
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"OK {name}")
        except AssertionError as e:
            bad += 1
            print(f"NG {name}: {e}")
    print("---", f"{len(fns)}件 全部通りました" if not bad else f"{bad}件 失敗")
    _sys.exit(1 if bad else 0)


def test_日付らしい値の書式を見分ける():
    from uleji_sync import _date_form
    assert _date_form("2026-08-01") == "%Y-%m-%d"
    assert _date_form("2026/08/01") == "%Y/%m/%d"
    assert _date_form("20260801") == "%Y%m%d"
    assert _date_form("2026年08月01日") == "%Y年%m月%d日"
    assert _date_form("2026-08") == "%Y-%m"
    assert _date_form("202608") == "%Y%m"


def test_日付でない値は書式なし():
    from uleji_sync import _date_form
    # 店舗コードやトークンを日付と誤認すると、URLを壊して開き直してしまう
    for v in ["0009", "", "abc", "2026", "$2a$04$xxxx", "2026-8-1"]:
        assert _date_form(v) is None, v


def test_クエリの鍵は出すが怪しい値は伏せる():
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import pos_common as C
    got = C.url_query_shape("https://x/v2/pl/sheet?from=2026-09-01&to=2026-09-30&shopCode=0009")
    assert got == "from=2026-09-01 & to=2026-09-30 & shopCode=0009"
    # 長い値・記号を含む値はトークンの疑いがあるので伏せる（公開ログに出るため）
    tok = C.url_query_shape("https://x/sso-auth?authorization=$2a$04$abcdefghijklmnopqrstuvwxyz")
    assert "authorization=…(伏せた)" == tok
    assert "abcdefghij" not in tok


if __name__ == "__main__":
    _main()
