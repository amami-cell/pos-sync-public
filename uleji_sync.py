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

FINDINGS: list[str] = []


def _note(line: str):
    """あとでまとめて出す調査メモ。ログのtailが後続ステップで埋まるので、
    結論はステップの最後にまとめて出す。"""
    FINDINGS.append(line)


def _base() -> str:
    from urllib.parse import urlsplit
    u = urlsplit(ULEJI_LOGIN_URL)
    return f"{u.scheme}://{u.netloc}"


# OTP突破後に見えたメニュー（run #12）。売上CSVの出口を探す候補。
#   売上管理 → 伝票明細 / 収入印紙 / 過去売上実績
#   分析  … ダッシュボード。売上・客数はここにある
#
# run #13 で分かったこと: **URLを直接叩いてはいけない**。
#   /cms/side-menu/slip-list      → 「予期せぬエラーが発生しました。再ログインを」
#   /cms/side-menu/past-sales-regist → ログイン画面に戻された（セッションが切れた）
# side-menu配下は画面遷移の途中状態を前提にしているらしく、goto だと壊れる。
# 人と同じようにメニューを「クリック」して開く。
# run #14: 伝票明細は開けた（/cms/slip-list/init?requestTransType=1）。
# 過去売上実績は「押せなかった」＝親メニューをもう一度押して畳んでしまい、
# 子が隠れたため。既に見えている子は親を押さない。
# 分析はクリックしても画面が変わらなかった。別タブで開いている疑いがあるので
# 最後に回し、新しいタブが開いたらそちらを見る。
MENU = [
    ("売上管理", "伝票明細", "uleji_11_slip"),
    ("売上管理", "過去売上実績", "uleji_12_past_sales"),
    (None, "分析", "uleji_10_analysis"),
]

KEYWORDS = ("原価", "売上", "客数", "来客", "食材", "粗利", "仕入",
            "CSV", "ダウンロード", "出力", "エクスポート", "期間", "月次")


def _on_login_page(page) -> bool:
    """ログイン画面に戻されていないか。戻されていたら探索は続けられない。"""
    try:
        return page.locator("#companyCode").count() > 0 and \
            page.locator("#companyCode").first.is_visible()
    except Exception:
        return False


def _frame_texts(page) -> list[tuple[str, str]]:
    """本体と全フレームの本文。画面がiframeの中にあると本体だけ見ても空振りする。"""
    out = []
    for fr in page.frames:
        try:
            out.append((fr.url or "(main)", fr.inner_text("body")))
        except Exception:
            continue
    return out


def _screen_report(page, label: str):
    """画面の中身をまとめメモに残す。数字は伏せる（公開リポジトリのため）。"""
    _note(f"[{label}] URL {page.url}")
    texts = _frame_texts(page)
    if len(texts) > 1:
        _note(f"[{label}] フレーム {len(texts)}個（中身は別フレームにある）")
    for url, body in texts:
        hits = [w for w in KEYWORDS if w in body]
        if not body.strip():
            continue
        where = "" if len(texts) == 1 else f" ＠{url[:60]}"
        _note(f"[{label}] 出てくる語{where}: {hits if hits else 'なし'}")
        lines = [l.strip() for l in body.split("\n") if l.strip()]
        for l in lines[:14]:
            _note(f"      - {C.mask_numbers(l)[:90]}")
    # 入力欄（期間指定の場所を特定するため）。値は出さない。
    try:
        fields = page.evaluate(
            r"""() => [...document.querySelectorAll('input, select')]
                .filter(el => el.type !== 'hidden')
                .map(el => [el.tagName.toLowerCase(), el.type || '', el.name || '',
                            el.id || '', el.placeholder || ''].join('/'))""")
        uniq = []
        for f in fields:
            if f not in uniq:
                uniq.append(f)
        if uniq:
            _note(f"[{label}] 入力欄 {len(uniq)}件（tag/type/name/id/placeholder）:")
            for f in uniq[:20]:
                _note(f"      - {f}")
    except Exception as e:
        _note(f"[{label}] 入力欄の列挙に失敗: {e}")

    # 押せるもの（CSV出力はアイコンだけのこともある）
    try:
        btns = page.evaluate(
            r"""() => [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
                .filter(el => el.offsetParent !== null)
                .map(el => ((el.innerText || el.value || el.getAttribute('aria-label')
                             || el.getAttribute('title') || '').trim()
                            .replace(/\s+/g, ' ')).slice(0, 40))
                .filter(Boolean)""")
        uniq = []
        for b in btns:
            if b not in uniq:
                uniq.append(b)
        _note(f"[{label}] 押せるもの {len(uniq)}件: {C.mask_numbers(' / '.join(uniq[:25]))}")
    except Exception as e:
        _note(f"[{label}] ボタンの列挙に失敗: {e}")


def _click_text(page, text: str) -> bool:
    """画面に見えている文字をクリックする。メニューはリンクにもボタンにも見えるので、
    役割を決め打ちせず、見えている最初の一致を押す。"""
    for make in (lambda: page.get_by_role("link", name=text, exact=True),
                 lambda: page.get_by_role("button", name=text, exact=True),
                 lambda: page.get_by_text(text, exact=True)):
        try:
            loc = make()
            for i in range(min(loc.count(), 3)):
                if loc.nth(i).is_visible():
                    loc.nth(i).click()
                    return True
        except Exception:
            continue
    return False


def _visible(page, text: str) -> bool:
    try:
        loc = page.get_by_text(text, exact=True)
        return any(loc.nth(i).is_visible() for i in range(min(loc.count(), 3)))
    except Exception:
        return False


def _click_maybe_popup(page, text: str, wait_ms: int = 5000):
    """押したあと、新しいタブが開いたらそのタブを返す。開かなければ元のページ。
    押せなければ None。別タブで開く画面を「何も起きなかった」と誤判定しないため。"""
    ctx = page.context
    before = list(ctx.pages)
    if not _click_text(page, text):
        return None
    page.wait_for_timeout(wait_ms)
    fresh = [p for p in ctx.pages if p not in before]
    if fresh:
        try:
            fresh[0].wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        return fresh[0]
    return page


# 押してはいけないもの。過去売上実績は「売上を登録する」画面で、
# 登録 と CSV一括設定（一括取込）は**書き込み**の操作。読み取りしかしない。
FORBIDDEN = ("登録", "CSV一括設定", "削除", "更新", "取込", "アップロード")

# 落とすだけのボタン。押すとファイルが降ってくる想定。
CSV_BUTTONS = ("CSVダウンロード", "CSV出力", "ダウンロード", "出力")


def _try_csv(page, label: str, tag: str):
    """CSVを落とすボタンがあれば押して、列名だけを記録する。
    書き込み系のボタンには絶対に触らない。"""
    names = [b for b in CSV_BUTTONS if _visible(page, b)]
    if not names:
        return
    _note(f"[{label}] 落とせそうなボタン: {names}")
    for name in names:
        if any(ng in name for ng in FORBIDDEN):
            _note(f"[{label}] 『{name}』は書き込みの恐れがあるので押さない")
            continue
        try:
            with page.expect_download(timeout=120000) as dl:
                page.get_by_text(name, exact=True).first.click(timeout=5000)
            data = open(dl.value.path(), "rb").read()
            _note(f"[{label}] 『{name}』でファイルを取得: {C.sniff_bytes(data)}")
            try:
                rows = C.parse_csv_bytes(data)
                header = list(rows[0].keys()) if rows else []
                _note(f"[{label}] CSV {len(rows)}行 / {len(header)}列")
                _note(f"[{label}] 列名: {' | '.join(header)}")
                cost = [h for h in header if "原価" in h or "仕入" in h or "粗利" in h]
                _note(f"[{label}] 原価らしき列: {cost if cost else 'なし'}")
            except Exception as e:
                _note(f"[{label}] CSVの解析に失敗: {e}")
            C.describe_csv(data, tag + "_dl")
            return
        except Exception as e:
            _note(f"[{label}] 『{name}』では落ちてこなかった: {type(e).__name__}")


def _cost_lines(page, label: str):
    """原価まわりの表示を、数字を伏せて拾う。どんな粒度で持っているかを見るため。"""
    try:
        body = page.inner_text("body")
    except Exception:
        return
    hits = [l.strip() for l in body.split("\n")
            if any(w in l for w in ("原価", "仕入", "粗利", "FL"))]
    if not hits:
        _note(f"[{label}] 原価の行は見つからなかった")
        return
    _note(f"[{label}] 原価まわりの表示 {len(hits)}行（数字は伏せています）:")
    seen = []
    for l in hits:
        m = C.mask_numbers(l)[:90]
        if m not in seen:
            seen.append(m)
            _note(f"      - {m}")


def _explore_analytics(page, tag: str):
    """分析サイト（別ドメイン）を見る。ここに原価がある。
    予算登録など書き込みの画面には入らない。"""
    _note("[分析] ここは別サイト（analytics-pc.usen-regi.com）。原価はここにある")
    _cost_lines(page, "分析")
    # このSPAはリンクが<a>ではないので、DOM中のhrefを総ざらいして画面を探す
    try:
        hrefs = page.evaluate(
            r"""() => [...new Set([...document.querySelectorAll('[href]')]
                .map(e => e.getAttribute('href')).filter(Boolean))]""")
        _note(f"[分析] 画面へのリンク {len(hrefs)}件: {' / '.join(hrefs[:30])}")
    except Exception as e:
        _note(f"[分析] リンクの列挙に失敗: {e}")
    # 隠れているものも含めて、押せる候補の名前を全部出す（原価の画面を探すため）
    try:
        allnames = page.evaluate(
            r"""() => [...new Set([...document.querySelectorAll(
                'button, [role=button], [role=tab], [role=menuitem], li, [class*=nav], [class*=menu]')]
                .map(e => (e.getAttribute('aria-label') || e.getAttribute('title')
                           || (e.childElementCount === 0 ? e.textContent : '') || '')
                          .trim().replace(/\s+/g, ' '))
                .filter(t => t && t.length < 24))]""")
        _note(f"[分析] 画面にある名前 {len(allnames)}件: "
              f"{C.mask_numbers(' / '.join(allnames[:40]))}")
    except Exception as e:
        _note(f"[分析] 名前の列挙に失敗: {e}")

    for name in ("詳細を表示", "View as data table, Chart"):
        if any(ng in name for ng in FORBIDDEN):
            continue
        if _click_text(page, name):
            page.wait_for_timeout(5000)
            _note(f"[分析] 『{name}』を押した後:")
            _screen_report(page, f"分析({name})")
            _cost_lines(page, f"分析({name})")
            C.diag_dump(page, f"{tag}_{'detail' if '詳細' in name else 'table'}")
            _try_csv(page, f"分析({name})", f"{tag}_{'detail' if '詳細' in name else 'table'}")


def _explore_menu(page, parent: str | None, child: str, tag: str):
    """メニューをクリックして画面を開き、中身をまとめメモに残す。"""
    if _on_login_page(page):
        _note(f"[{child}] ログイン画面に戻されているため調査できず")
        return
    try:
        # 既に子が見えているなら親は押さない。押すと畳まれて子が隠れる
        # （ダイニーの「全選択」で同じ失敗をした）。
        if parent and not _visible(page, child):
            if not _click_text(page, parent):
                _note(f"[{child}] 親メニュー『{parent}』が押せなかった")
                return
            page.wait_for_timeout(1200)
        target = _click_maybe_popup(page, child)
        if target is None:
            _note(f"[{child}] メニューが押せなかった")
            return
        if target is not page:
            _note(f"[{child}] 別タブで開いた")
        C.diag_dump(target, tag)
        _screen_report(target, child)
        for word in ("CSV", "ダウンロード", "出力"):
            C.probe_elements(target, word, limit=3)
        C.probe_icon_buttons(target)
        if _on_login_page(target):
            _note(f"[{child}] この画面を開いた直後にログイン画面へ戻された")
            return
        # 検索して初めてCSV出力が現れる画面があるので、あれば押して見直す。
        # 読むだけの操作なので副作用は無い。
        if _click_text(target, "検索") or _click_text(target, "検 索"):
            target.wait_for_timeout(6000)
            _note(f"[{child}] 検索を押した後:")
            _screen_report(target, child + "(検索後)")
            C.diag_dump(target, tag + "_searched")
        _try_csv(target, child, tag)
        if "analytics" in (target.url or ""):
            _explore_analytics(target, tag)
        if target is not page:
            target.close()
    except Exception as e:
        _note(f"[{child}] 調査に失敗: {e}")


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
                    _note("認証コードの自動入力に成功。管理画面に入れている")
                    C.probe_elements(page, "CSV")
                    C.probe_elements(page, "ダウンロード")
                    for parent, child, tag in MENU:
                        _explore_menu(page, parent, child, tag)
                except Exception as e:
                    print(f"[uleji][diag] OTP突破に失敗: {e}")
                    _note(f"OTP突破に失敗: {e}")
            print("==== ulejiまとめ（ここが結論） ====")
            for line in FINDINGS:
                print(f"  {line}")
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
