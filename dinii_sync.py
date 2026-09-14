"""
dinii_sync.py — ダイニー管理画面から売上/原価CSVを取得してシートへ
使い方:  python dinii_sync.py [YYYY-MM]
Secrets: DINII_USER(=ログイン用メールアドレス) / DINII_PASS  （+ 出力先 TARGET_SHEET_ID か ローカルCSV）
ログインは「フォームがあれば入力、無ければ既ログインとみなして続行」の寛容方式。
売上/原価CSVは左メニュー「データ出力・連携」から。導線とCSV列は診断出力(POS_DIAG=1)で確定する。
"""
import sys, os
from playwright.sync_api import sync_playwright
import pos_common as C

DINII_LOGIN_URL = os.environ.get("DINII_LOGIN_URL") or "https://dashboard.self.dinii.jp/"


def _open(page, user: str, pw: str):
    """ダッシュボードを開く。ログインフォームが出たら入力、出なければ既ログインとして続行。"""
    page.goto(DINII_LOGIN_URL, wait_until="domcontentloaded")
    # ログインフォーム or ダッシュボードのどちらかが出るまで最大15秒待つ
    try:
        page.wait_for_selector("input[placeholder*='メール'], :text(\"データ出力・連携\")", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    email = page.locator("input[placeholder*='メール']")
    if email.count() > 0:  # ログインが必要
        if not (user and pw):
            raise RuntimeError("DINII_USER(メールアドレス) / DINII_PASS を設定してください")
        email.first.fill(user)
        try:
            page.locator("input[type='password']").first.fill(pw)
        except Exception:
            page.get_by_placeholder("パスワード").fill(pw)
        page.get_by_role("button", name="ログイン").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
    # ここまで来たらダッシュボードにいる想定


def fetch_csv(ym: str) -> bytes:
    user = C.env("DINII_USER"); pw = C.env("DINII_PASS")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        _open(page, user, pw)

        # 診断モード：ダッシュボード＋「データ出力・連携」を開いて中身を吐いて終了
        if C.is_diag():
            page.wait_for_timeout(2000)
            C.diag_dump(page, "dinii_00_dashboard")
            try:
                page.get_by_text("データ出力・連携", exact=False).first.click()
                page.wait_for_timeout(2500)
                C.diag_dump(page, "dinii_01_dataexport")
            except Exception as e:
                print(f"[dinii][diag] データ出力・連携クリック失敗: {e}")
            browser.close()
            print("[dinii] POS_DIAG: 画面を diag/ に出力しました")
            return b""

        # ---- 実取得: 売上/原価CSVのエクスポート（TODO: 診断出力で確定）----
        page.get_by_text("データ出力・連携", exact=False).first.click()
        page.wait_for_timeout(1500)
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
        kyaku= r.get("客数") or r.get("来店客数") or r.get("guests") or ""       # TODO
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
