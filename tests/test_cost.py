"""新Uレジの原価（損益PL集計）の取り出しと載せ方。   python tests/test_cost.py

ここを間違えると「どの店のものでもない原価」が静かに載る。
画面を触らずに確かめられる部分だけを固定する。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ULEJI_COMPANY", "x")
import uleji_sync as U


def _csv(header, *rows):
    body = [",".join(header)] + [",".join(str(c) for c in r) for r in rows]
    return ("﻿" + "\n".join(body) + "\n").encode("utf-8")


def test_材料原価を列名で取る():
    # 列数は月によって変わる（データのある列だけ出る。実測 53列 と 54列）。
    # 位置で引くとずれるので、必ず列名で引く。
    data = _csv(["売上実績", "材料原価", "粗利益"], [1000, 250, 750])
    assert U.cost_of(data) == 250
    # 列の並びが変わっても同じ値が取れること
    data2 = _csv(["材料原価", "売上実績"], [250, 1000])
    assert U.cost_of(data2) == 250


def test_材料原価が無ければ総原価を使う():
    data = _csv(["売上実績", "総原価"], [1000, 400])
    assert U.cost_of(data) == 400


def test_原価の列が無ければNone():
    data = _csv(["売上実績", "来店者数"], [1000, 50])
    assert U.cost_of(data) is None


def test_2行来たら使わない():
    # この帳票は期間ぶんを1行にまとめた集計。2行来たら前提が変わっている。
    # 黙って合計すると、店が増えた月から静かに二重計上になる。
    data = _csv(["材料原価"], [250], [300])
    assert U.cost_of(data) is None


def test_データが無ければNone():
    assert U.cost_of(None) is None
    assert U.cost_of(b"") is None


def test_空欄やハイフンは値なし扱い():
    for v in ["", "-", "—"]:
        assert U.cost_of(_csv(["材料原価"], [v])) is None


def test_店舗が1つなら原価を載せる():
    rows = [{"店舗コード": "0009", "日付": "2026-08-01", "売上": "1000", "客数": "10"}]
    out = U.normalize(rows, "2026-08", {"0009": "大衆酒場 ぎふや"}, cost=250)
    assert len(out) == 1
    assert out[0]["原価"] == 250
    # フード／ドリンクには入れない。分かれていない数字を分かれた列に置くと嘘になる
    assert out[0]["フード原価"] == "" and out[0]["ドリンク原価"] == ""
    assert "内訳なし" in out[0]["備考"]


def test_店舗が複数なら原価は載せない():
    # 原価は店舗の区別なく1つしか来ない。配ると、どの店のものでもない数字が
    # 全店に載る。
    rows = [
        {"店舗コード": "0009", "日付": "2026-08-01", "売上": "1000", "客数": "10"},
        {"店舗コード": "0010", "日付": "2026-08-01", "売上": "2000", "客数": "20"},
    ]
    out = U.normalize(rows, "2026-08", {"0009": "A", "0010": "B"}, cost=250)
    assert len(out) == 2
    assert all(r["原価"] == "" for r in out)
    assert all("店舗が2件のため付けない" in r["備考"] for r in out)


def test_原価が取れなくても売上は載る():
    rows = [{"店舗コード": "0009", "日付": "2026-08-01", "売上": "1000", "客数": "10"}]
    out = U.normalize(rows, "2026-08", {"0009": "大衆酒場 ぎふや"}, cost=None)
    assert out[0]["売上"] == 1000 and out[0]["原価"] == ""
def test_原価0は未登録として扱う():
    # 新Uレジの損益PL集計の材料原価は「原価・販管費登録」画面への手入力が元。
    # 入力されていない店は 0 で落ちてくる。実測（2026-08 ぎふや福岡天神）が
    # まさにそれで、売上1264万に対して材料原価0だった。
    # 0を載せると「原価がタダの店」として資料に出る。
    data = _csv(["売上実績", "材料原価"], [12640890, 0])
    assert U.cost_of(data) is None


def test_原価0なら総原価も見るが両方0ならNone():
    data = _csv(["売上実績", "材料原価", "総原価"], [12640890, 0, 0])
    assert U.cost_of(data) is None


def test_材料原価が0でも総原価があれば使う():
    data = _csv(["売上実績", "材料原価", "総原価"], [12640890, 0, 3000000])
    assert U.cost_of(data) == 3000000


def test_負の原価も採らない():
    assert U.cost_of(_csv(["材料原価"], [-100])) is None


def test_マスタに無い店は書かない():
    # ダイニーのアカウントは90店舗ぶん見えている。素通しすると他社の店が並ぶ
    # （実測で14行中11行がよその店だった）。
    import pos_common as C
    rows = [
        {"店舗名": "大衆居酒屋ちゃ～ちゃん", "店舗コード": "1111"},
        {"店舗名": "喰人梅田東通り店", "店舗コード": ""},
        {"店舗名": "横綱 堂山", "店舗コード": None},
        {"店舗名": "Italian Bar NagaGutsu", "店舗コード": "1151"},
    ]
    got = C.keep_our_stores(rows, "（テスト）")
    assert [r["店舗コード"] for r in got] == ["1111", "1151"]


def test_全部よその店なら空になる():
    import pos_common as C
    assert C.keep_our_stores([{"店舗名": "よそ", "店舗コード": ""}], "（テスト）") == []


if __name__ == "__main__":
    from _runner import main
    main(globals(), __file__)
