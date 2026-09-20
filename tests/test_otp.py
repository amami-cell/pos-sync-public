"""認証コードメールの取り出しを確かめる。   python tests/test_otp.py

IMAP接続は差し替えて、実サーバーに繋がずに判定だけを見る。
メールの形は実物どおり（差出人・件名・本文）。コードの数字はダミー。
"""
import sys, os, datetime, imaplib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(OTP_IMAP_HOST="imap.example", OTP_IMAP_USER="u", OTP_IMAP_PASS="p")
import pos_common as C

def mail(frm, subj, body, when):
    from email.message import EmailMessage
    from email.utils import format_datetime
    m = EmailMessage(); m["From"] = frm; m["Subject"] = subj
    m["Date"] = format_datetime(when); m.set_content(body)
    return m.as_bytes()

USEN = ("USENレジ <no-reply@pos.usen-regi.com>", "【USENレジ】認証コードのお知らせ",
        "USENレジより認証コードをお知らせいたします。\n"
        "▼以下の番号をログイン画面の認証コード欄に入力してください。▼\n"
        "認証コード：123456\n\n"
        "※認証コードの有効期限は15分です。\n")
GOOGLE = ("Google <no-reply@accounts.google.com>", "Google 認証コード",
          "認証コードは 654321 です。\n")

class FakeIMAP:
    msgs = []
    def __init__(self, host, port): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, u, p): pass
    def select(self, folder, readonly=False): assert readonly, "readonlyで開いていない"
    def search(self, charset, q):
        return "OK", [b" ".join(str(i+1).encode() for i in range(len(self.msgs)))]
    def fetch(self, num, spec):
        assert spec == "(BODY.PEEK[])", f"既読にしてしまう取得: {spec}"
        return "OK", [(b"", self.msgs[int(num)-1])]

imaplib.IMAP4_SSL = FakeIMAP
now = datetime.datetime.now(datetime.timezone.utc)
KW = dict(subject_hint="認証コード", from_hint="usen-regi.com",
          code_regex=r"認証コード[：:]\s*(\d{6})")

def run(name, msgs, not_before, expect):
    FakeIMAP.msgs = msgs
    try:
        got = C.fetch_otp_via_imap(not_before, timeout_sec=0, poll_sec=0, **KW)
    except RuntimeError:
        got = None
    ok = "OK " if got == expect else "NG "
    print(f"{ok}{name}: 期待={expect} 実際={got}")
    return got == expect

old = now - datetime.timedelta(hours=3)
ok = True
ok &= run("USENのメールだけ", [mail(*USEN, now)], now, "123456")
ok &= run("他サービスの認証コードは拾わない", [mail(*GOOGLE, now)], now, None)
ok &= run("両方あってもUSENを選ぶ", [mail(*GOOGLE, now), mail(*USEN, now)], now, "123456")
ok &= run("古いメールは無視", [mail(*USEN, old)], now, None)
FORWARDED = ("ガルーン <amami@example.co.jp>", "Fwd: 【USENレジ】認証コードのお知らせ",
             "---------- 転送メッセージ ----------\n"
             "From: USENレジ <no-reply@pos.usen-regi.com>\n\n"
             "認証コード：123456\n")
ok &= run("転送で差出人が書き換わっても拾う", [mail(*FORWARDED, now)], now, "123456")
ok &= run("差出人が違えば件名が同じでも無視",
          [mail("偽 <a@example.com>", USEN[1], USEN[2], now)], now, None)
print("---", "全部通りました" if ok else "失敗あり")
sys.exit(0 if ok else 1)
