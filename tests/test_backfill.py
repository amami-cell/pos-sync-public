"""埋め戻し（期間を指定）の判断。   python tests/test_backfill.py

CSVは**画面の期間ぶん1行**しか落ちない。だから期間を合わせ損ねると
「別の月を指定したつもりで当月のまま」になり、**間違った月の数字が
対象月のものとして本番シートに入る**。画面を触る部分は実機でしか
確かめられないので、**間違いが起きる判断だけを純粋な関数に出して**
ここで固定する。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ULEJI_COMPANY", "x")
import pos_common as C
from uleji_sync import (MAX_MONTH_STEPS, _month_range_iso, month_steps,
                        parse_month_header)


# ---- 対象月の指定 ----


def test_範囲は両端を含む():
    assert C.parse_months("2026-05:2026-08") == [
        "2026-05", "2026-06", "2026-07", "2026-08"]


def test_年をまたぐ範囲():
    assert C.parse_months("2025-11:2026-02") == [
        "2025-11", "2025-12", "2026-01", "2026-02"]


def test_1か月だけでも同じ形で返す():
    # 呼び出し側を1本にするため、単月も並びで返す
    assert C.parse_months("2026-08") == ["2026-08"]


def test_とびとびの指定():
    assert C.parse_months("2026-08,2026-05") == ["2026-05", "2026-08"]


def test_空なら先月ひとつ():
    got = C.parse_months(None)
    assert len(got) == 1 and len(got[0]) == 7


def test_書式が違えば止める():
    # ここを素通しにすると、別の月が対象月として本番シートに入る
    for bad in ["2026-8", "2026/08", "2026-13", "2026-00", "202608", "先月", "2026"]:
        try:
            C.parse_months(bad)
            assert False, f"{bad} が通ってしまった"
        except ValueError:
            pass


def test_範囲が逆なら止める():
    # 黙って0件にすると「取れなかった」と区別がつかない
    try:
        C.parse_months("2026-08:2026-05")
        assert False, "逆順が通ってしまった"
    except ValueError:
        pass


def test_長すぎる範囲は止める():
    # ログインは1回。長すぎると途中でセッションが切れる
    try:
        C.parse_months("2024-01:2026-12")
        assert False, "36か月が通ってしまった"
    except ValueError:
        pass
    assert len(C.parse_months("2025-01:2026-12")) == 24


# ---- カレンダーの見出し ----


def test_年月を読む():
    for t in ["2026年9月", "2026年09月", "2026/09", "2026 - 9", "2026.09"]:
        assert parse_month_header(t) == "2026-09", t


def test_見出しが要素に割れていても読む():
    # 実測（2026-09-22）では `2026` `年` `9` `月` が別々の要素だった。
    # 行ごとに渡すとどれも年月として読めない。つなげてから渡す前提で、
    # 空白や改行が混ざっても読めること。
    for t in ["2026 年 9 月", "2026\n年\n9\n月", "開始日 2026 年 9 月"]:
        assert parse_month_header(t) == "2026-09", repr(t)


def test_英語の月名も読む():
    # Vuetify の既定ロケールが英語のことがある
    assert parse_month_header("September 2026") == "2026-09"
    assert parse_month_header("Sep 2026") == "2026-09"


def test_日まで入っている文字は見出しと読まない():
    # **いちばん怖い取り違え。** 画面には「いま選ばれている期間」
    # 2026年09月20日-2026年09月20日 も出ている。これを見出しと読むと
    # 送る回数が0になり、当月のまま確定して当日のCSVが落ちる。
    assert parse_month_header("2026年09月20日-2026年09月20日") is None
    assert parse_month_header("2026年09月20日") is None
    assert parse_month_header("2026/09/20") is None


def test_読めない文字はNone():
    for t in ["", None, "9月", "期間を指定", "2026", "Ma 2026"]:
        assert parse_month_header(t) is None, t


def test_あいまいな英語の月名は読まない():
    # "Ma" は March とも May とも取れる。当てずっぽうで決めると
    # 2か月ずれたまま気づけない
    assert parse_month_header("Ma 2026") is None
    assert parse_month_header("Ju 2026") is None


# ---- 何回送るか ----


def test_過去へは負の数():
    assert month_steps("2026-09", "2026-05") == -4


def test_未来へは正の数():
    assert month_steps("2026-09", "2027-02") == 5


def test_同じ月なら0():
    # 0 のときに送ってしまうと、合っているのに1か月ずらす
    assert month_steps("2026-09", "2026-09") == 0


def test_年またぎを数え間違えない():
    assert month_steps("2025-12", "2026-01") == 1
    assert month_steps("2026-01", "2025-12") == -1


def test_歯止めは3年():
    # 見出しを読み違えたときに押し続けないための上限
    assert MAX_MONTH_STEPS == 36
    assert abs(month_steps("2026-09", "2020-01")) > MAX_MONTH_STEPS


# ---- 入力欄に入れる書式 ----


def test_type_date_用の書式():
    # <input type=date> は YYYY-MM-DD しか受け取らない。
    # 日本語表記を入れると黙って空のままになる
    assert _month_range_iso("2026-08") == ("2026-08-01", "2026-08-31")


def test_月末日が月ごとに変わる_iso():
    assert _month_range_iso("2026-09") == ("2026-09-01", "2026-09-30")
    assert _month_range_iso("2026-02") == ("2026-02-01", "2026-02-28")
    assert _month_range_iso("2028-02") == ("2028-02-01", "2028-02-29")


if __name__ == "__main__":
    from _runner import main
    main(globals(), __file__)
