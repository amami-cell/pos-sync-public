"""
dinii_sync.py — ダイニー管理画面から売上/原価CSVを取得してシートへ
使い方:  python dinii_sync.py [YYYY-MM]
Secrets: DINII_USER(=ログイン用メールアドレス) / DINII_PASS  （+ 出力先 TARGET_SHEET_ID か ローカルCSV）
ログイン欄は実画面（SPA）に確定済み：メールアドレス / パスワード / 「ログイン」ボタン。
売上レポートへの導線・CSV列マッピングは POS_DIAG=1 の診断出力を見て確定する（下部 TODO）。
"""
import sys, os
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import pos_common as C

DINII_LOGIN_URL = os.environ.get("DINII_LOGIN_URL") or "https://dashboard.self.dinii.jp/"


def _login(page, user: str, pw: str):
    page.goto(DINII_LOGIN_URL, wait_until="networkidle")
    # SPA。placeholderで確実に掴む（大文字小文字/空白は正規化されて一致）。
    email = page.get_by_placeholder("メールアドレス")
    email.wait_for(state="visible", timeout=30000)
    email.fill(user)
    # パスワード欄は type=password を優先、無ければplaceholder
    try:
        page.locator("input[type='password']").first.fill(pw)
    except Exception:
        page.get_by_placeholder("パスワード").fill(pw)
    page.get_by_role("button", name="ログイン").click()
    page.wait_for_load_state("networkidle")


def fetch_csv(ym: str) -> bytes:
    user = C.env("DINII_USER"); pw = C.env("DINII_PASS")
    if not (user and pw):
        raise RuntimeError("DINII_USER(メールアドレス) / DINII_PASS を設定してください")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        try:
            _login(page, user, pw)
        except PWTimeout:
            C.diag_dump(page, "dinii_login_fail")
            raise RuntimeError("[dinii] ログイン画面の要素が見つからない。diag/dinii_login_fail.* を確認")

        # 診断モード：ログイン後の画面を吐いて終了（エクスポート導線の確定用）
        if C.is_diag():
            page.wait_for_timeout(3000)
            C.diag_dump(page, "dinii_after_login")
            browser.close()
            print("[dinii] POS_DIAG: ログイン後の画面を diag/ に出力しました")
            return b""

        # ---- 2) 売上レポート → CSVエクスポート導線（TODO: 診断出力を見て確定）----
        # 例: page.get_by_role("link", name="売上").click()
        #     page.get_by_role("link", name="レポート").click()
        #     page.fill(<期間セレクタ>, ym)
        with page.expect_download() as dl_info:
            page.click("text=CSV")  # TODO: エクスポートボタンの実セレクタ
        data = open(dl_info.value.path(), "rb").read()
        browser.close()
        return data


def normalize(rows: list[dict], ym: str) -> list[dict]:
    """ダイニーCSVの列名 -> 共通スキーマ。TODO: 実CSVのヘッダに合わせてキーを対応付け。"""
    out = []
    for r in rows:
        name = r.get("店舗名") or r.get("店舗") or r.get("shop_name")          # TODO
        uri  = r.get("売上") or r.get("純売上") or r.get("sales")               # TODO
        f    = r.get("原価_フード") or r.get("food_cost") or ""                 # TODO(無ければ空)
        d    = r.get("原価_ドリンク") or r.get("drink_cost") or ""              # TODO
        kyaku= r.get("客数") or r.get("guests") or ""                           # TODO
        if not name:
            continue
        out.append({
            "年月": ym, "店舗名": name, "売上": uri, "フード原価": f,
            "ドリンク原価": d, "客数": kyaku, "備考": "", "取込日時": C.now_str(), "POS": "dinii",
        })
    return out


def main():
    ym = C.last_month(sys.argv[1] if len(sys.argv) > 1 else None)
    data = fetch_csv(ym)
    if not data:  # 診断モードは空で返る
        return
    rows = normalize(C.parse_csv_bytes(data), ym)
    print(f"[dinii] {ym}: {len(rows)}店取得")
    sid = C.env("TARGET_SHEET_ID")
    if sid:
        C.write_to_sheet(rows, sid, worksheet="POS売上")
    else:
        C.write_local_csv(rows, f"dinii_{ym}.csv")


if __name__ == "__main__":
    main()
