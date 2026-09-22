"""埋め戻しの段取り（ブラウザ無しで確かめる）。   python tests/test_backfill_flow.py

実機で確かめられるのは**ログインと画面操作だけ**で、それ以外の段取り——
何か月まわすか／1か月落ちても続けるか／シートへ何回書くか——は
ここで固定できる。実行1回に30分の間隔が要るので、**本番に出す前に
出せる分は出しておく。**

`fetch_many`（＝ブラウザを開く部分）だけ差し替えて `main()` を通す。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ULEJI_COMPANY", "x")
import pos_common as C
import uleji_sync as U


class Spy:
    """main() が外へ出す呼び出しを記録する。"""

    def __init__(self, got, sheet_id="SHEET"):
        self.got, self.sheet_id = got, sheet_id
        self.asked = None          # fetch_many に渡った月の並び
        self.writes = []           # write_to_sheet の呼ばれ方
        self.local = []            # ローカルCSVの書き出し

    def __enter__(self):
        self.orig = {n: getattr(U, n, None) for n in
                     ("fetch_many", "normalize", "cost_of")}
        self.origC = {n: getattr(C, n) for n in
                      ("parse_csv_bytes", "keep_our_stores", "report_filled",
                       "write_to_sheet", "write_local_csv", "env")}

        def fetch_many(yms):
            self.asked = list(yms)
            return {k: v for k, v in self.got.items() if k in yms}

        U.fetch_many = fetch_many
        U.normalize = lambda rows, ym, names, cost=None: [
            {"年月": ym, "店舗名": "ぎふや 福岡天神店", "POS": "新Uレジ"}]
        U.cost_of = lambda data: None
        C.parse_csv_bytes = lambda data: [{}]
        C.keep_our_stores = lambda rows, label="": rows
        C.report_filled = lambda rows, label="": None
        C.write_to_sheet = lambda rows, sid, worksheet="": self.writes.append(
            (len(rows), [r["年月"] for r in rows], sid, worksheet))
        C.write_local_csv = lambda rows, path: self.local.append((len(rows), path))
        C.env = lambda *keys: self.sheet_id if "TARGET_SHEET_ID" in keys else None
        return self

    def __exit__(self, *exc):
        for n, v in self.orig.items():
            setattr(U, n, v)
        for n, v in self.origC.items():
            setattr(C, n, v)


def _run(arg, got, sheet_id="SHEET"):
    argv = sys.argv[:]
    sys.argv = ["uleji_sync.py"] + ([arg] if arg else [])
    try:
        with Spy(got, sheet_id) as spy:
            U.main()
            return spy
    finally:
        sys.argv = argv


def _all(yms):
    return {y: (b"csv", {}, None) for y in yms}


MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]


def test_範囲を渡すと5か月まわる():
    spy = _run("2026-04:2026-08", _all(MONTHS))
    assert spy.asked == MONTHS, spy.asked


def test_ログインは1回_月ごとに開き直さない():
    # fetch_many が**1回だけ**呼ばれ、全月をまとめて受け取ること。
    # 月ごとに呼ぶ作りにすると、12分に3回でOTPが弾かれる。
    spy = _run("2026-04:2026-08", _all(MONTHS))
    assert spy.asked is not None and len(spy.asked) == 5


def test_シートへの書き込みは最後に1回():
    # 月ごとに書くと、そのたびシート全体を消して書き直すことになり、
    # 途中で Sheets の書き込み上限に当たるとシートが空のまま止まる。
    spy = _run("2026-04:2026-08", _all(MONTHS))
    assert len(spy.writes) == 1, spy.writes
    n, months, sid, ws = spy.writes[0]
    assert n == 5 and months == MONTHS
    assert sid == "SHEET" and ws == "POS売上"


def test_1か月落ちても残りは書く():
    # 埋め戻しは十数分かかるうえ、流し直すとOTPが弾かれる。
    # 1か月の失敗で残り4か月を捨てない。
    got = _all([m for m in MONTHS if m != "2026-06"])
    spy = _run("2026-04:2026-08", got)
    assert len(spy.writes) == 1
    n, months, _, _ = spy.writes[0]
    assert n == 4 and "2026-06" not in months, months


def test_全月落ちたら書きにいかない():
    # 空で上書きしない（消してから書き戻す作りなので復旧できない）
    spy = _run("2026-04:2026-08", {})
    assert spy.writes == [] and spy.local == []


def test_単月も同じ道を通る():
    spy = _run("2026-07", _all(["2026-07"]))
    assert spy.asked == ["2026-07"]
    assert len(spy.writes) == 1 and spy.writes[0][0] == 1


def test_シートIDが無ければローカルCSVに範囲つきの名前で出す():
    spy = _run("2026-04:2026-08", _all(MONTHS), sheet_id=None)
    assert spy.writes == []
    assert spy.local == [(5, "uleji_2026-04_2026-08.csv")], spy.local


def test_書式が違えば画面を開く前に止まる():
    # ブラウザを起こす前に弾く（OTPを無駄に消費しない）
    for bad in ["2026-8", "2026-08:2026-04", "先月"]:
        try:
            _run(bad, _all(MONTHS))
            assert False, f"{bad} が通ってしまった"
        except ValueError:
            pass


if __name__ == "__main__":
    from _runner import main
    main(globals(), __file__)
