"""
uleji_sync.py — 新Uレジ(USENレジ)管理画面から売上/原価CSVを取得してシートへ
使い方:  python uleji_sync.py [YYYY-MM]
Secrets: ULEJI_COMPANY(=企業コード) / ULEJI_USER(=担当者コード) / ULEJI_PASS(=パスワード)
ログインは「フォームがあれば入力、無ければ既ログインとみなして続行」の寛容方式。
売上/原価CSVの導線・列は診断出力(POS_DIAG=1)で確定する。
"""
import sys, os
from playwright.sync_api import sync_playwright
import pos_common as C

ULEJI_LOGIN_URL = os.environ.get("ULEJI_LOGIN_URL") or "https://pos.usen-regi.com/cms/login/init"


def _submit_otp(page) -> bool:
    """メールで届く認証コードを取得して入力し、認証まで進める。
    コードはログインを実行しているこのプロセス自身が取りに行く（人手を挟まない）。"""
    import datetime
    if not C.otp_imap_configured():
        raise RuntimeError(
            "[uleji] 認証コードが必要ですが、メール取得の設定がありません。"
            "OTP_IMAP_HOST / OTP_IMAP_USER / OTP_IMAP_PASS を Secrets に設定してください"
        )
    # 「認証コード再送」を押してから取りに行く。こうすると受信時刻が
    # 確実にこの時刻より後になり、前回の古いコードを拾う事故を防げる。
    sent_at = datetime.datetime.now(datetime.timezone.utc)
    try:
        resend = page.get_by_role("button", name="認証コード再送")
        if resend.count() > 0 and resend.first.is_visible():
            resend.first.click()
            print("[uleji] 認証コードを再送させました")
            page.wait_for_timeout(2000)
    except Exception as e:
        print(f"[uleji] 再送ボタンの操作に失敗（届いている前提で続行）: {e}")

    # 実物のメールで確定した特徴。Secretsで上書きもできる。
    #   差出人  no-reply@pos.usen-regi.com
    #   件名    【USENレジ】認証コードのお知らせ
    #   本文    認証コード：123456   （有効期限15分）
    # 差出人で絞るのが要。個人のメールボックスには他サービスの
    # 「認証コード」メールも届くので、件名の語だけでは別のコードを拾う。
    code = C.fetch_otp_via_imap(
        sent_at,
        subject_hint="認証コード",
        from_hint="usen-regi.com",
        code_regex=r"認証コード[：:]\s*(\d{6})",
    )
    page.locator("#authenticationCode").fill(code)
    page.get_by_role("button", name="認証", exact=True).click()
    try:
        page.wait_for_selector("#authenticationCode", state="hidden", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    still = detect_otp(page)
    if still:
        C.diag_dump(page, "uleji_otp_rejected")
        raise RuntimeError("[uleji] 認証コードを入力しましたが、まだ認証画面のままです")
    print("[uleji] 認証コードによるログインに成功しました")
    return True


def _open(page, company: str, user: str, pw: str):
    page.goto(ULEJI_LOGIN_URL, wait_until="domcontentloaded")
    try:
        page.wait_for_selector("input[placeholder*='企業コード'], input[type='password']", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    comp = page.get_by_placeholder("企業コードを入力")
    if comp.count() > 0:  # ログインが必要
        if not (company and user and pw):
            raise RuntimeError("ULEJI_COMPANY(企業コード) / ULEJI_USER(担当者コード) / ULEJI_PASS を設定してください")
        comp.fill(company)
        page.get_by_placeholder("担当者コードを入力").fill(user)
        try:
            page.locator("input[type='password']").first.fill(pw)
        except Exception:
            page.get_by_placeholder("パスワードを入力").fill(pw)
        page.get_by_role("button", name="ログイン").click()
        # dinii と同じ理由で networkidle は使わない。OTP欄が出るか、
        # 画面が切り替わるまでを上限つきで待つ。
        try:
            page.wait_for_selector("#authenticationCode", state="visible", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(2000)


# OTP画面の特徴語。ログイン直後にこれらが出ていたら自動化はそこで止まる。
OTP_HINTS = ["認証コード", "確認コード", "ワンタイム", "メールに送信", "6桁"]


def detect_otp(page) -> str | None:
    """メール認証コード画面かを判定して手掛かりを返す。
    実画面ではログインフォームと同じページに #authenticationCode があり、
    OTP要求時だけ表示される。DOMの有無ではなく『見えているか』で判定する。"""
    try:
        code = page.locator("#authenticationCode")
        if code.count() > 0 and code.first.is_visible():
            return "#authenticationCode が表示されている"
    except Exception:
        pass
    try:
        body = page.inner_text("body")[:4000]
    except Exception:
        return None
    for h in OTP_HINTS:
        if h in body:
            return h
    return None


def fetch_csv(ym: str) -> bytes:
    company = C.env("ULEJI_COMPANY"); user = C.env("ULEJI_USER"); pw = C.env("ULEJI_PASS")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        _open(page, company, user, pw)

        otp = detect_otp(page)
        if otp:
            print(f"[uleji] ★メール認証コード(OTP)画面を検出: {otp}")

        if C.is_diag():
            page.wait_for_timeout(2000)
            C.diag_dump(page, "uleji_00_afterlogin")
            print(f"[uleji][diag] OTP画面か: {'YES' if otp else 'NO'} / "
                  f"メール取得の設定: {'あり' if C.otp_imap_configured() else 'なし'}")
            if otp and C.otp_imap_configured():
                try:
                    _submit_otp(page)
                    C.diag_dump(page, "uleji_01_after_otp")
                    C.probe_elements(page, "CSV")
                    C.probe_elements(page, "ダウンロード")
                except Exception as e:
                    print(f"[uleji][diag] OTP突破に失敗: {e}")
            browser.close()
            return b""

        if otp:
            _submit_otp(page)

        # ---- 実取得: 売上/原価CSVのエクスポート（TODO: 診断出力で確定）----
        with page.expect_download() as dl_info:
            page.click("text=CSV")  # TODO: エクスポートボタンの実セレクタ
        data = open(dl_info.value.path(), "rb").read()
        browser.close()
        return data


def normalize(rows: list[dict], ym: str) -> list[dict]:
    """新UレジCSVの列名 -> 共通スキーマ。TODO: 実CSVのヘッダに合わせてキーを対応付け。"""
    idx = C.build_store_index(C.load_stores())
    out = []
    unresolved = []
    for r in rows:
        name = r.get("店舗名") or r.get("店舗") or r.get("shop_name")          # TODO
        uri  = r.get("売上") or r.get("純売上") or r.get("sales")               # TODO
        f    = r.get("原価_フード") or r.get("food_cost") or ""                 # TODO(無ければ空)
        d    = r.get("原価_ドリンク") or r.get("drink_cost") or ""              # TODO
        kyaku= r.get("客数") or r.get("来店客数") or r.get("guests") or ""       # TODO
        if not name:
            continue
        code = C.resolve_store_code(name, idx)
        if not code and name not in unresolved:
            unresolved.append(name)
        out.append({
            "年月": ym, "店舗名": name, "売上": uri, "フード原価": f,
            "ドリンク原価": d, "客数": kyaku, "備考": "", "取込日時": C.now_str(), "POS": "uleji",
            "店舗コード": code,
        })
    if unresolved:
        print(f"[uleji] 店舗マスタに無い表記 {len(unresolved)}件（店舗コードは空のまま取り込む）:")
        for n in unresolved:
            print(f"    | {n}")
    return out


def main():
    ym = C.last_month(sys.argv[1] if len(sys.argv) > 1 else None)
    data = fetch_csv(ym)
    if not data:  # 診断モードは空で返る
        return
    rows = normalize(C.parse_csv_bytes(data), ym)
    print(f"[uleji] {ym}: {len(rows)}店取得")
    sid = C.env("TARGET_SHEET_ID")
    if sid:
        C.write_to_sheet(rows, sid, worksheet="POS売上")
    else:
        C.write_local_csv(rows, f"uleji_{ym}.csv")


if __name__ == "__main__":
    main()
