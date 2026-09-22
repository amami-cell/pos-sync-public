"""シートへ書き戻すとき、どの既存行を残すか。   python tests/test_sheet_merge.py

`write_to_sheet` は**シートをいったん空にしてから書き戻す**。つまりここで
落とした行は消える。復旧できない。埋め戻しで数か月ぶんを一度に書くときに
いちばん効くので、判断だけを純粋な関数にして固定する。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pos_common import keep_rows, row_key


def _r(ym, name, pos, uri=""):
    return {"年月": ym, "店舗名": name, "POS": pos, "売上": uri}


# ---- 置き換え ----

def test_同じ月と店とPOSは置き換える():
    old = [_r("2026-07", "ぎふや 福岡天神店", "新Uレジ", "100")]
    new = [_r("2026-07", "ぎふや 福岡天神店", "新Uレジ", "200")]
    assert keep_rows(old, new) == []      # 古いほうは残さない（＝新しい行で置き換わる）


def test_別の月は残す():
    old = [_r("2026-06", "ぎふや 福岡天神店", "新Uレジ")]
    new = [_r("2026-07", "ぎふや 福岡天神店", "新Uレジ")]
    assert keep_rows(old, new) == old


def test_別の店は残す():
    old = [_r("2026-07", "すさび湯 歌舞伎町", "新Uレジ")]
    new = [_r("2026-07", "ぎふや 福岡天神店", "新Uレジ")]
    assert keep_rows(old, new) == old


def test_POSが違えば必ず残す():
    # **いちばん怖い巻き添え。** 同じシートにダイニーと新Uレジが並ぶ。
    # ここを緩めると、片方を取り込むたびにもう片方が消える。
    old = [_r("2026-07", "ひよこ飯店", "ダイニー")]
    new = [_r("2026-07", "ひよこ飯店", "新Uレジ")]
    assert keep_rows(old, new) == old


# ---- 表記ゆれ ----

def test_店舗名の表記ゆれは同じ行とみなす():
    # 全角空白・半角空白のゆれで二重に並ばせない
    old = [_r("2026-07", "ぎふや　福岡天神店", "新Uレジ")]
    new = [_r("2026-07", "ぎふや 福岡天神店", "新Uレジ")]
    assert keep_rows(old, new) == []


def test_年月が数値で返っても同じ行とみなす():
    # シートから読むと数値になることがある。型が違うだけで別行にすると
    # **同じ月が二重に並ぶ**
    old = [{"年月": 202607, "店舗名": "ひよこ飯店", "POS": "新Uレジ"}]
    new = [{"年月": "202607", "店舗名": "ひよこ飯店", "POS": "新Uレジ"}]
    assert keep_rows(old, new) == []


def test_同一性の鍵は年月と店舗名とPOSだけ():
    # 売上が違っても同じ行。金額で見分けると、修正のたびに二重になる
    a = _r("2026-07", "ひよこ飯店", "新Uレジ", "100")
    b = _r("2026-07", "ひよこ飯店", "新Uレジ", "999")
    assert row_key(a) == row_key(b)


# ---- 埋め戻し（複数月をまとめて書く） ----

def test_埋め戻しで5か月ぶんを置き換えても他は残る():
    old = ([_r(f"2026-0{m}", "ぎふや 福岡天神店", "新Uレジ") for m in range(4, 9)]
           + [_r("2026-07", "ひよこ飯店", "ダイニー"),
              _r("2026-03", "ぎふや 福岡天神店", "新Uレジ")])
    new = [_r(f"2026-0{m}", "ぎふや 福岡天神店", "新Uレジ") for m in range(4, 9)]
    kept = keep_rows(old, new)
    assert len(kept) == 2, kept
    assert {row_key(r) for r in kept} == {
        row_key(_r("2026-07", "ひよこ飯店", "ダイニー")),
        row_key(_r("2026-03", "ぎふや 福岡天神店", "新Uレジ")),
    }


def test_書く行が空なら何も落とさない():
    # 取得に失敗した月を「空で上書き」しない。消してから書き戻す作りなので、
    # 落とすと復旧できない
    old = [_r("2026-07", "ひよこ飯店", "ダイニー"),
           _r("2026-07", "ぎふや 福岡天神店", "新Uレジ")]
    assert keep_rows(old, []) == old


def test_既存が空でも落ちない():
    assert keep_rows([], [_r("2026-07", "ひよこ飯店", "新Uレジ")]) == []


if __name__ == "__main__":
    from _runner import main
    main(globals(), __file__)
