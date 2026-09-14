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
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)


# OTP画面の特徴語。ログイン直後にこれらが出ていたら自動化はそこで止まる。
OTP_HINTS = ["認証コード", "確認コード", "ワンタイム", "メールに送信", "6桁"]


def detect_otp(page) -> str | None:
    """メール認証コード画面かどうかを判定し、当たった手掛かりを返す。"""
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
            print(f"[uleji][diag] OTP画面か: {'YES' if otp else 'NO'}")
            browser.close()
            return b""

        if otp:
            C.diag_dump(page, "uleji_otp_block")
            browser.close()
            raise RuntimeError(
                "[uleji] ログイン後にメール認証コード(OTP)を要求されたため自動取得できません。"
                "README『新UレジのOTP対策』の方針決定待ちです"
            )

        # ---- 実取得: 売上/原価CSVのエクスポート（TODO: 診断出力で確定）----
        with page.expect_download() as dl_info:
            page.click("text=CSV")  # TODO: エクスポートボタンの実セレクタ
        data = open(dl_info.value.path(), "rb").read()
        browser.close()
        return data


def normalize(rows: list[dict], ym: str) -> list[dict]:
    """新UレジCSVの列名 -> 共通スキーマ。TODO: 実CSVのヘッダに合わせてキーを対応付け。"""
    out = []
    for r in rows:
        name = r.get("店舗名") or r.get("店舗") or r.get("shop_name")          # TODO
        uri  = r.get("売上") or r.get("純売上") or r.get("sales")               # TODO
        f    = r.get("原価_フード") or r.get("food_cost") or ""                 # TODO(無ければ空)
        d    = r.get("原価_ドリンク") or r.get("drink_cost") or ""              # TODO
        kyaku= r.get("客数") or r.get("来店客数") or r.get("guests") or ""       # TODO
        if not name:
            continue
        out.append({
            "年月": ym, "店舗名": name, "売上": uri, "フード原価": f,
            "ドリンク原価": d, "客数": kyaku, "備考": "", "取込日時": C.now_str(), "POS": "uleji",
        })
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
