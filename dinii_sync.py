"""
dinii_sync.py — ダイニー管理画面から売上/原価CSVを取得してシートへ
使い方:  python dinii_sync.py [YYYY-MM]
Secrets: DINII_USER(=ログイン用メールアドレス) / DINII_PASS  （+ 出力先 TARGET_SHEET_ID か ローカルCSV）
ログインは「フォームがあれば入力、無ければ既ログインとみなして続行」の寛容方式。
CSV出力は「データ出力・連携 → CSVダウンロード」＝ /aggregatedData/daily/export。
期間指定とダウンロードボタンの実セレクタは POS_DIAG=1 の診断出力（ジョブログ）で確定する。
"""
import sys, os, datetime
from playwright.sync_api import sync_playwright
import pos_common as C

DINII_LOGIN_URL = os.environ.get("DINII_LOGIN_URL") or "https://dashboard.self.dinii.jp/"
# 確定済み: 売上/原価の集計CSV。/onlinePaymentCsv/export はモバイル決済取引なので使わない。
EXPORT_PATH = "/aggregatedData/daily/export"
# 画面に「集計完了まで数分かかる場合があります」と明記されているため長めに取る
DL_TIMEOUT_MS = 300000

# ダウンロードボタンの候補。効いたものは診断ログに出るので、確定後は先頭に寄せる。
DL_SELECTORS = [
    "button:has-text('ダウンロード')",
    "[role=button]:has-text('ダウンロード')",
    "a:has-text('ダウンロード')",
    ":text-is('ダウンロード')",
    "button:has-text('CSVダウンロード')",
    "button:has-text('CSV')",
    "button:has-text('出力')",
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
        # networkidle は使わない。SPAは背景通信が途切れず30秒待っても発火せずに
        # タイムアウトする（run #5 の失敗原因）。ログイン後の画面に出る要素で待つ。
        try:
            page.wait_for_selector(":text(\"データ出力・連携\"), :text(\"ダッシュボード\")", timeout=30000)
        except Exception:
            print("[dinii] ログイン後の目印が見つからないまま続行（診断出力で確認）")
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


def _fill_picker(page, placeholder: str, value: str) -> bool:
    """Ant Design の DatePicker に日付を入れる。クリック→入力→Enter が定石。
    入力後に value を読み直して、本当に入ったかを確認する。"""
    loc = page.get_by_placeholder(placeholder)
    if loc.count() == 0:
        return False
    el = loc.first
    try:
        el.click()
        page.wait_for_timeout(300)
        el.fill(value)
        page.wait_for_timeout(300)
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        got = (el.input_value() or "").strip()
        if got:
            print(f"[dinii] 期間 {placeholder} = {got}")
            return True
        print(f"[dinii] 期間 {placeholder} に {value} を入れたが空のまま")
    except Exception as e:
        print(f"[dinii] 期間 {placeholder} 入力失敗: {e}")
    return False


def _set_period(page, ym: str):
    """対象月を期間欄に入れる。実画面には日付欄が2系統ある:
      - 日付を選択 (#aggregatedDataByShopsForm_targetDate) … 店舗横断集計が使う
      - 開始日付 / 終了日付                                  … 店舗別集計が使う
    どちらの帳票を落とすか決め打ちできないので、両方に入れる。
    Ant Design の DatePicker なので クリック→入力→Enter で入れる。"""
    import calendar
    y, m = int(ym.split("-")[0]), int(ym.split("-")[1])
    last_day = calendar.monthrange(y, m)[1]
    ok = False
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        first = datetime.date(y, m, 1).strftime(fmt)
        last = datetime.date(y, m, last_day).strftime(fmt)
        got_s = _fill_picker(page, "開始日付", first)
        got_e = _fill_picker(page, "終了日付", last)
        # 店舗横断集計側。範囲ではなく単日/月の可能性があるため月初を入れる
        got_t = _fill_picker(page, "日付を選択", first)
        if got_s and got_e:
            ok = True
        if ok or got_t:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
            return True
        print(f"[dinii] 書式 {fmt} では入らず。次の書式を試します")
    print("[dinii] 期間欄に入力できませんでした（画面の既定期間のまま進みます）")
    return False


def _dl_state(page, when: str):
    """ダウンロードボタンの活性状態をログへ。どの操作で押せるようになるかを突き止める。"""
    try:
        btns = page.locator("button:has-text('ダウンロード')")
        n = btns.count()
        states = []
        for i in range(min(n, 4)):
            states.append(f"[{i}]{'disabled' if btns.nth(i).is_disabled() else '★ENABLED'}")
        print(f"[dinii] DLボタン（{when}）: {' '.join(states) if states else '見つからず'}")
    except Exception as e:
        print(f"[dinii] ボタン状態の取得に失敗（{when}）: {e}")


def _ensure_checked(page, label: str) -> bool:
    """ラベルに対応するチェックボックスを確実にONにする。
    既にONなら押さない。Ant Design のチェックボックスはトグルなので、
    ONの状態で押すと外れる（run #1 で「全選択」を押して全解除していた）。"""
    for sel in (f".ant-checkbox-wrapper:has-text('{label}')",
                f"label:has-text('{label}')"):
        loc = page.locator(sel)
        if loc.count() == 0:
            continue
        el = loc.first
        box = el.locator("input[type=checkbox]")
        try:
            if box.count() > 0 and box.first.is_checked():
                print(f"[dinii] 「{label}」は既にON（押さない）")
                return True
            el.click()
            page.wait_for_timeout(1200)
            on = box.count() > 0 and box.first.is_checked()
            print(f"[dinii] 「{label}」をクリック → {'ON' if on else 'OFFのまま'}")
            return on
        except Exception as e:
            print(f"[dinii] 「{label}」の操作に失敗: {e}")
    print(f"[dinii] 「{label}」が見つかりません")
    return False


def _select_all_shops(page) -> bool:
    """店舗選択の「全選択」をONにする。未選択だとDLボタンが無効のまま。"""
    return _ensure_checked(page, "全選択")


def _select_output_file(page) -> bool:
    """出力ファイル選択。売上・客数は日計(summaryByShops.csv)から取れている。
    原価がどの帳票に入るかが未確定なので、候補も一緒に選んで1回のZIPで確かめる。
    出数集計は商品別の出数で、原価が入っているならここが最有力。"""
    ok = False
    for label in ("summaryByShops.csv", "日計(日別・店舗統一)", "店舗統一"):
        if _ensure_checked(page, label):
            ok = True
            break
    # 原価の在り処を1回で突き止めるため、商品まわりの帳票も選ぶ
    for label in ("出数集計", "orderSummary.csv"):
        if _ensure_checked(page, label):
            _note("[出力ファイル選択] 出数集計も一緒に選んだ（原価列の有無を確認するため）")
            break
    return ok


def _list_shops(page):
    """店舗名の一覧をログへ。店舗マスタとの照合確認に使う。"""
    try:
        names = page.evaluate(
            r"""() => [...document.querySelectorAll('label,li,.ant-checkbox-wrapper')]
                .map(el => (el.innerText || '').trim().replace(/\s+/g, ' '))
                .filter(t => t && t.length <= 40 && t !== '全選択')""")
        uniq = []
        for n in names:
            if n not in uniq:
                uniq.append(n)
        print(f"[dinii] 店舗候補 {len(uniq)}件")
        for n in uniq[:120]:
            print(f"    | {n}")
    except Exception as e:
        print(f"[dinii] 店舗一覧の取得に失敗: {e}")


# 原価の在り処を探す候補。診断で見つかったリンクより。
#   /bi/flDashboard … 経営管理。FL=Food&Labor cost、原価はここにある可能性が高い
# 日計CSV(90列)には原価の列が1つも無かったため、別画面を当たる必要がある。
FINDINGS: list[str] = []


def _note(line: str):
    """あとでまとめて出す調査メモ。"""
    FINDINGS.append(line)


_mask_numbers = C.mask_numbers   # 共通側へ移した（ulejiでも使うため）


def _collect_checkbox_labels(page):
    """出力ファイル選択に並ぶ帳票の名前を全部拾う。"""
    try:
        labels = page.evaluate(
            r"""() => [...document.querySelectorAll('input[type=checkbox]')].map(el => {
                const w = el.closest('label') || el.closest('.ant-checkbox-wrapper') || el.parentElement;
                return ((w && w.innerText) || '').trim().replace(/\s+/g, ' ').slice(0, 80);
            }).filter(Boolean)""")
        uniq = []
        for t in labels:
            if t not in uniq:
                uniq.append(t)
        _note(f"[出力ファイル選択] 選べる帳票 {len(uniq)}件:")
        for t in uniq:
            _note(f"      - {t}")
        return uniq
    except Exception as e:
        _note(f"[出力ファイル選択] 取得に失敗: {e}")
        return []


def _collect_cost_context(page, path: str):
    """原価まわりの画面テキストを、数字を伏せた形で拾う。
    どんな項目名で原価が並んでいるかを知るため。"""
    try:
        lines = [l.strip() for l in page.inner_text("body").split("\n") if l.strip()]
        hit = [l for l in lines
               if any(w in l for w in ("原価", "フード", "ドリンク", "粗利", "FL"))]
        _note(f"[{path}] 原価まわりの表示 {len(hit)}行（数字は伏せています）:")
        for l in hit[:25]:
            _note(f"      - {_mask_numbers(l)[:90]}")
    except Exception as e:
        _note(f"[{path}] 画面テキストの取得に失敗: {e}")


def _cost_coverage(page):
    """店舗ごとに原価が反映されているかを判定する。
    経営管理画面のカードは「原価率：」がラベルだけの要素で、値は隣の要素にある。
    ラベルと同じ行に値が無いときは次の行を値として読む（前回はここを取り違えて
    全店を「未反映」と誤判定した）。
    率そのものは出さず、店舗名と 反映あり/未反映 だけをログに出す。"""
    js = r"""() => {
        const zen = v => v.replace(/[０-９．％－]/g, c =>
            '0123456789.%-'['０１２３４５６７８９．％－'.indexOf(c)]);
        const isEmpty = v => {
            const h = zen(String(v)).trim();
            return h === '' || h === '-' || h === '--' || h === '—' || h === '未設定'
                || h === 'N/A' || /^0(\.0+)?\s*%?$/.test(h);
        };
        const isRank = s => /^[\d\s\-–—.、]+$/.test(s);
        const lines = el => (el.innerText || '').split('\n')
            .map(x => x.trim()).filter(Boolean);
        const seen = new Set();
        const out = [];
        const leaves = [...document.querySelectorAll('*')].filter(el =>
            el.children.length === 0 && /原価率/.test(el.textContent || ''));
        for (const n of leaves) {
            // カードは「行が3本以上ある最初の祖先」。文字数で決めると
            // 店名が短い店だけ判定を外して body まで昇ってしまう。
            let p = n.parentElement, card = null;
            for (let i = 0; i < 8 && p; i++, p = p.parentElement) {
                if (p === document.body || p === document.documentElement) break;
                if (lines(p).length >= 3) { card = p; break; }
            }
            if (!card) continue;   // カードを特定できないものは判定しない
            if (seen.has(card)) continue;
            seen.add(card);
            const segs = lines(card);
            const i = segs.findIndex(s => /原価率/.test(s));
            let val = '';
            if (i >= 0) {
                // 「原価率：28.5%」のように同じ行に値があればそれを、
                // 「原価率：」だけの行なら次の行を値とみなす。ただし次の行が
                // 別の項目名（例「客数」）なら値ではない＝空として扱う。
                const looksValue = v => /^[-–—]+$/.test(v.trim())
                    || /^[0-9.,]+\s*%?$/.test(zen(v).trim());
                const tail = zen(segs[i]).replace(/^[\s\S]*原価率[：:]?/, '')
                                          .replace(/^[\s\/]+/, '').trim();
                const next = segs[i + 1] || '';
                val = tail || (looksValue(next) ? next : '');
            }
            const before = i > 0 ? segs.slice(0, i) : segs;
            const name = (before.find(s => !isRank(s) && !/原価率/.test(s))
                          || '(店舗名不明)').slice(0, 40);
            out.push({
                name: name,
                ok: !isEmpty(val),
                segs: segs.slice(0, 5),
            });
        }
        return out;
    }"""
    try:
        rows = page.evaluate(js)
    except Exception as e:
        _note(f"[原価の反映状況] 判定に失敗: {e}")
        return
    if not rows:
        _note("[原価の反映状況] 「原価率」を含むカードが見つからず、判定できなかった")
        return
    uniq, keys = [], set()
    for r in rows:
        k = (r["name"], r["ok"])
        if k in keys:
            continue
        keys.add(k)
        uniq.append(r)
    ng = [r for r in uniq if not r["ok"]]
    _note(f"[原価の反映状況] 店舗 {len(uniq)}件中 未反映 {len(ng)}件")
    for r in uniq:
        _note(f"      - {r['name']}\t{'反映あり' if r['ok'] else '未反映'}")
    # 前回は読み取り位置がずれて全件が「未反映」になった。同じ壊れ方を
    # 見逃さないよう、全件同じ判定のときはカードの構造も（数字を伏せて）出す。
    if uniq and (len(ng) == len(uniq) or len(ng) == 0):
        _note("      ※ 全件が同じ判定。読み取り位置がずれている可能性があるので"
              "カードの構造を確認する（数字は伏せる）:")
        for r in uniq[:3]:
            _note(f"        {_mask_numbers(' | '.join(r['segs']))}")


# ダイニーのサポート回答（2026-09）で、原価の出口が判明した。
#   ダッシュボード ＞ [経営管理タブ] ＞ [CSVダウンロード]
#     - 原価集計（日別） … ※実原価は「ダイニー経営管理契約店舗」のみ
#     - 出数集計        … 販売数。メニュー原価と掛けて商品別総原価を出せる
# こちらが今まで見ていた「データ出力・連携」(/aggregatedData/daily/export) とは
# **別の画面**。あちらの15帳票に原価が無かったのは当然で、場所が違っていた。
# 参考: https://dinii.wraptas.site/5d140f1f09354f109133e04a246594df
EXPLORE_PATHS = [
    ("/bi/flDashboard", "dinii_10_fl"),
    ("/aggregatedData/menu/export", "dinii_11_menu_export"),
]


def _find_cost_export(page):
    """経営管理の画面から「CSVダウンロード」へ辿る。
    URLを当てずに、画面にあるリンク・ボタンから探す（当て推量のURLはSPAを
    壊すことがある。新Uレジでセッションごと落とした）。"""
    try:
        hrefs = page.evaluate(
            r"""() => [...new Set([...document.querySelectorAll('a[href]')]
                .map(e => e.getAttribute('href'))
                .filter(h => h && !/\.(css|js|woff2?|ttf|eot|svg|png|jpg)/.test(h)))]""")
        _note(f"[経営管理] 画面内のリンク {len(hrefs)}件: {' / '.join(hrefs[:25])}")
    except Exception as e:
        _note(f"[経営管理] リンクの列挙に失敗: {e}")

    for name in ("CSVダウンロード", "CSV出力", "ダウンロード", "CSV"):
        try:
            loc = page.get_by_text(name, exact=True)
            hit = None
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible():
                    hit = loc.nth(i)
                    break
            if hit is None:
                continue
            before = page.url
            hit.click(timeout=5000)
            page.wait_for_timeout(5000)
            _note(f"[経営管理] 『{name}』を押した → {page.url}"
                  f"{'（画面は変わらず）' if page.url == before else ''}")
            C.diag_dump(page, "dinii_12_fl_export")
            _collect_checkbox_labels(page)
            _collect_cost_context(page, "経営管理のCSVダウンロード")
            return True
        except Exception as e:
            _note(f"[経営管理] 『{name}』の操作に失敗: {type(e).__name__}")
    _note("[経営管理] CSVダウンロードへの導線が画面から見つからなかった")
    return False


def _explore(page, path: str, tag: str):
    """指定パスを開いて構造を吐く。原価が取れる画面と導線を特定するため。"""
    base = _base()
    try:
        page.goto(base + path, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        print(f"[dinii][探索] {path} → 実際のURL {page.url}")
        C.diag_dump(page, tag)
        for word in ("ダウンロード", "CSV", "エクスポート", "出力"):
            C.probe_elements(page, word, limit=3)
        # ダイニーのDLボタンは aria-label="download" のアイコンのみ。
        # 文字で探しても引っかからないので、アイコン側からも探す
        C.probe_icon_buttons(page)
        _collect_cost_context(page, path)
        if "flDashboard" in path:
            _cost_coverage(page)
            # サポート回答（2026-09）より、原価の出口は
            # ダッシュボード ＞ 経営管理タブ ＞ CSVダウンロード。
            #   原価集計（日別）… ※実原価はダイニー経営管理契約店舗のみ
            #   出数集計       … 販売数×メニュー原価で商品別総原価
            # 今まで見ていた「データ出力・連携」とは別の画面だった。
            _find_cost_export(page)
        # 画面に原価らしき語があるかを確認（数字は出さない）
        try:
            body = page.inner_text("body")
            hits = [w for w in ("原価", "FL", "粗利", "F/L", "フード", "ドリンク", "理論原価")
                    if w in body]
            print(f"[dinii][探索] {path} に出てくる語: {hits if hits else 'なし'}")
        except Exception:
            pass
    except Exception as e:
        print(f"[dinii][探索] {path} の調査に失敗: {e}")


def fetch_csv(ym: str) -> bytes:
    user = C.env("DINII_USER"); pw = C.env("DINII_PASS")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        try:
            _open(page, user, pw)
        except Exception as e:
            # ログイン段階で落ちても診断だけは残す（run #5 では何も残らなかった）
            print(f"[dinii] ログイン処理で例外: {e}")
            C.diag_dump(page, "dinii_login_error")
            if not C.is_diag():
                browser.close()
                raise
            browser.close()
            return b""

        # 診断モード：ダッシュボードとエクスポート画面の構造を出し、DLも試して終了
        if C.is_diag():
            page.wait_for_timeout(1500)
            C.diag_dump(page, "dinii_00_dashboard")
            try:
                _goto_export(page)
                C.diag_dump(page, "dinii_01_export")
            except Exception as e:
                print(f"[dinii][diag] export画面への遷移失敗: {e}")
            # 「ダウンロード」ボタンが有効1・無効1の2つあり、どちらが本命か不明。
            # 正体（所属フォーム・近傍見出し・HTML）と、未入力の欄を洗い出す。
            C.probe_elements(page, "ダウンロード")
            C.probe_form_state(page)
            _list_shops(page)
            # どの操作でDLボタンが押せるようになるかを1段ずつ確かめる
            _dl_state(page, "初期")
            _select_all_shops(page)
            _dl_state(page, "全選択後")
            _set_period(page, ym)
            page.keyboard.press("Escape")  # 日付パネルが開いたままだとボタンを覆う
            page.wait_for_timeout(800)
            _dl_state(page, "期間指定後")
            # 「出力ファイル選択」の選択肢を文言つきで確定する。
            # 店舗横断集計は日付1日ぶんしか出ない（落ちたファイル名が ...-20260801.csv）。
            # 月次で取るには 開始日付/終了日付 を持つ「店舗別集計」側を使う必要があり、
            # そのカードのDLボタン[1]を有効にする条件を突き止める。
            C.probe_checkboxes(page)
            _collect_checkbox_labels(page)
            _select_output_file(page)
            _dl_state(page, "出力ファイル選択後")
            C.probe_checkboxes(page)
            C.probe_form_state(page)
            C.diag_dump(page, "dinii_02_after_period")
            print("---- 期間指定後の状態 ----")
            C.probe_elements(page, "ダウンロード")
            C.probe_cards(page, "ダウンロード")
            # どのボタンが本命か不明なので、押せるものを順に試して結果を記録する
            data = None
            for i in range(2):  # 1回最大5分待つため回数を絞る
                data, note = C.click_and_watch(page, i, timeout_ms=DL_TIMEOUT_MS)
                print(f"[dinii][diag] {note}")
                if data:
                    break
            if data:
                # 解析に失敗しても正体だけは掴めるよう、ここで止めない
                print(f"[dinii][diag] 取得: {C.sniff_bytes(data)}")
                try:
                    C.describe_csv(data, "dinii_export_sample")
                except Exception as e:
                    print(f"[dinii][diag] CSV解析に失敗: {type(e).__name__}: {e}")
            else:
                print("[dinii][diag] 自動ダウンロード不成立。上の『クリック候補』からボタン名を確定します")
            # 日計CSVに原価が無かったので、原価が取れる画面を探す
            print("==== 原価の在り処を探索 ====")
            for path, tag in EXPLORE_PATHS:
                _explore(page, path, tag)
            # ログのtailは後続ステップで埋まりやすいので、要点をここで再掲する
            print("==== diniiまとめ（ここが結論） ====")
            print(f"  日計CSV: {'取得できた（原価の列は無し。売上と客数のみ）' if data else '取得できず'}")
            for line in FINDINGS:
                print("  " + line)
            browser.close()
            return b""

        # ---- 実取得 ----
        _goto_export(page)
        _select_all_shops(page)
        _set_period(page, ym)
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
        _select_output_file(page)
        _dl_state(page, "実取得の直前")
        data, sel = C.try_download(page, DL_SELECTORS, timeout_ms=DL_TIMEOUT_MS)
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
            "ドリンク原価": d, "客数": kyaku, "備考": "", "取込日時": C.now_str(), "POS": "dinii",
            "店舗コード": code,
        })
    if unresolved:
        print(f"[dinii] 店舗マスタに無い表記 {len(unresolved)}件（店舗コードは空のまま取り込む）:")
        for n in unresolved:
            print(f"    | {n}")
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
