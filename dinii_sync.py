"""
dinii_sync.py — ダイニー管理画面から売上/原価CSVを取得してシートへ
使い方:  python dinii_sync.py [YYYY-MM]
Secrets: DINII_USER(=ログイン用メールアドレス) / DINII_PASS  （+ 出力先 TARGET_SHEET_ID か ローカルCSV）
ログインは「フォームがあれば入力、無ければ既ログインとみなして続行」の寛容方式。
CSV出力は「データ出力・連携 → CSVダウンロード」＝ /aggregatedData/daily/export。
期間指定とダウンロードボタンの実セレクタは POS_DIAG=1 の診断出力（ジョブログ）で確定する。
"""
import sys, os
from playwright.sync_api import sync_playwright
import pos_common as C

DINII_LOGIN_URL = os.environ.get("DINII_LOGIN_URL") or "https://dashboard.self.dinii.jp/"
# 確定済み: 売上/原価の集計CSV。/onlinePaymentCsv/export はモバイル決済取引なので使わない。
EXPORT_PATH = "/aggregatedData/daily/export"

# ダウンロードボタンの候補。効いたものは診断ログに出るので、確定後は先頭に寄せる。
DL_SELECTORS = [
    "button:has-text('CSVダウンロード')",
    "button:has-text('ダウンロード')",
    "button:has-text('CSV')",
    "button:has-text('出力')",
    "a:has-text('CSVダウンロード')",
    "a:has-text('ダウンロード')",
    ":text('CSVダウンロード')",
]


def _base() -> str:
    return DINII_LOGIN_URL.rstrip("/")


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
    else:
        print("[dinii] ログインフォーム無し＝既ログインとみなして続行")


def _goto_export(page):
    """データ出力・連携（集計CSV）画面へ。URL直打ちが効かなければメニュー経由で。"""
    page.goto(_base() + EXPORT_PATH, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if "export" not in page.url:
        print(f"[dinii] URL直打ちで遷移できず（現在 {page.url}）。メニューから辿ります")
        page.get_by_text("データ出力・連携", exact=False).first.click()
        page.wait_for_timeout(2500)


def _set_period(page, ym: str):
    """対象月を期間欄に入れる。欄の実セレクタが未確定なので、当たったものだけ使う。
    入らなかった場合は画面の既定期間のままCSVを落とし、normalize側の年月で辻褄を合わせる。"""
    y, m = ym.split("-")
    first = f"{y}-{m}-01"
    import calendar
    last = f"{y}-{m}-{calendar.monthrange(int(y), int(m))[1]:02d}"
    filled = []
    for sel, val in [("input[type='date']", first), ("input[type='month']", ym)]:
        loc = page.locator(sel)
        n = loc.count()
        if n == 0:
            continue
        try:
            loc.nth(0).fill(val)
            filled.append(f"{sel}[0]={val}")
            if sel == "input[type='date']" and n > 1:
                loc.nth(1).fill(last)
                filled.append(f"{sel}[1]={last}")
        except Exception as e:
            print(f"[dinii] 期間入力失敗 {sel}: {e}")
    if filled:
        print(f"[dinii] 期間を指定: {', '.join(filled)}")
        page.wait_for_timeout(1500)
    else:
        print("[dinii] 期間欄が見つからず。画面の既定期間のまま取得します（要確認）")


def fetch_csv(ym: str) -> bytes:
    user = C.env("DINII_USER"); pw = C.env("DINII_PASS")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        _open(page, user, pw)

        # 診断モード：ダッシュボードとエクスポート画面の構造を出し、DLも試して終了
        if C.is_diag():
            page.wait_for_timeout(1500)
            C.diag_dump(page, "dinii_00_dashboard")
            try:
                _goto_export(page)
                C.diag_dump(page, "dinii_01_export")
            except Exception as e:
                print(f"[dinii][diag] export画面への遷移失敗: {e}")
            _set_period(page, ym)
            data, sel = C.try_download(page, DL_SELECTORS)
            if data:
                print(f"[dinii][diag] ダウンロード成功（効いたセレクタ: {sel}）")
                C.describe_csv(data, "dinii_export_sample")
            else:
                print("[dinii][diag] 自動ダウンロード不成立。上の『クリック候補』からボタン名を確定します")
            browser.close()
            return b""

        # ---- 実取得 ----
        _goto_export(page)
        _set_period(page, ym)
        data, sel = C.try_download(page, DL_SELECTORS)
        if not data:
            C.diag_dump(page, "dinii_dl_fail")
            browser.close()
            raise RuntimeError(
                "[dinii] CSVダウンロードボタンを特定できませんでした。"
                "POS_DIAG=1 で実行してログの『クリック候補』から DL_SELECTORS を確定してください"
            )
        print(f"[dinii] CSV取得（セレクタ: {sel}）")
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
