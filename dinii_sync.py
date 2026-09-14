"""
dinii_sync.py — ダイニー管理画面から売上/原価CSVを取得してシートへ
使い方:  python dinii_sync.py [YYYY-MM]
Secrets: DINII_USER / DINII_PASS  （+ 出力先 TARGET_SHEET_ID か ローカルCSV）
※ TODO は管理画面を共有してもらってから確定（ログインURL・セレクタ・エクスポート導線・CSV列名）
"""
import sys, os
from playwright.sync_api import sync_playwright
import pos_common as C

DINII_LOGIN_URL = os.environ.get("DINII_LOGIN_URL", "")  # TODO: 管理画面のログインURL

def fetch_csv(ym: str) -> bytes:
    user = C.env("DINII_USER"); pw = C.env("DINII_PASS")
    if not (user and pw and DINII_LOGIN_URL):
        raise RuntimeError("DINII_USER / DINII_PASS / DINII_LOGIN_URL を設定してください")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        # 1) ログイン ---- TODO: セレクタを実画面に合わせる
        page.goto(DINII_LOGIN_URL, wait_until="networkidle")
        page.fill("input[type='email'], input[name='email']", user)      # TODO
        page.fill("input[type='password'], input[name='password']", pw)  # TODO
        page.click("button[type='submit']")                              # TODO
        page.wait_for_load_state("networkidle")
        # 2) 売上レポート画面へ ---- TODO: メニュー導線
        # page.click("text=売上"); page.click("text=レポート")
        # 3) 対象月を指定 ---- TODO: 期間セレクタ
        # page.fill("input[name='month']", ym)
        # 4) CSVエクスポートをクリックし、ダウンロードを捕捉（数字はスクレイプしない）
        with page.expect_download() as dl_info:
            page.click("text=CSV")   # TODO: エクスポートボタンの実セレクタ
        download = dl_info.value
        path = download.path()
        data = open(path, "rb").read()
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
    rows = normalize(C.parse_csv_bytes(data), ym)
    print(f"[dinii] {ym}: {len(rows)}店取得")
    sid = C.env("TARGET_SHEET_ID")
    if sid:
        C.write_to_sheet(rows, sid, worksheet="POS売上")
    else:
        C.write_local_csv(rows, f"dinii_{ym}.csv")

if __name__ == "__main__":
    main()
