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
    _note(f"[{label}] URL {C.safe_url(page.url)}")
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
# 文字が "CSV" だけのボタンもある（損益PL集計の画面）。完全一致で探すので
# 「CSV一括設定」のような書き込み系には当たらない。具体的な名前から順に試す。
CSV_BUTTONS = ("CSVダウンロード", "CSV出力", "ダウンロード", "出力", "CSV")


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


import calendar as _calendar


def _month_range_label(ym: str) -> tuple[str, str]:
    """'2026-08' → ('2026年08月01日', '2026年08月31日')。画面の表記に合わせる。

    画面に出ていたのは `2026年09月20日-2026年09月20日`（＝今日だけ）。
    CSVは**画面の期間ぶん1行**なので、ここを月初〜月末にしないと当月しか取れない。"""
    year, month = (int(x) for x in ym.split("-"))
    last = _calendar.monthrange(year, month)[1]
    return f"{year}年{month:02d}月01日", f"{year}年{month:02d}月{last:02d}日"


_DATE_FORMS = (
    ("%Y-%m-%d", r"^\d{4}-\d{2}-\d{2}$"),
    ("%Y/%m/%d", r"^\d{4}/\d{2}/\d{2}$"),
    ("%Y%m%d",   r"^\d{8}$"),
    ("%Y年%m月%d日", r"^\d{4}年\d{2}月\d{2}日$"),
    ("%Y-%m",    r"^\d{4}-\d{2}$"),
    ("%Y%m",     r"^\d{6}$"),
)


def _date_form(value: str) -> str | None:
    """クエリの値が日付なら、その書式を返す。違えば None。"""
    import re
    for fmt, pat in _DATE_FORMS:
        if re.match(pat, value or ""):
            return fmt
    return None


def _try_pl_period_by_url(page, ym: str) -> bool:
    """期間がURLのクエリに乗っていれば、そこを書き換えて開き直す。

    ピッカーを操作するより確実で、壊れ方も分かりやすい。
    **ただし必ず開いた後に画面を読み直して確かめる。**

    分析サイトはSSOで入っているので、同じパスのクエリだけを差し替える
    （別パスへ飛ぶとセッションを落とす恐れがある）。ログイン画面に
    戻されたら、その旨を出して諦める。"""
    import datetime
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(page.url or "")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    dated = [(i, k, v, _date_form(v)) for i, (k, v) in enumerate(pairs) if _date_form(v)]
    if not dated:
        _note("[PL期間] URLのクエリに日付らしい値は無い")
        return False
    _note(f"[PL期間] URLのクエリ: {C.url_query_shape(page.url)}")
    _note(f"[PL期間] 日付らしい鍵: {[k for _, k, _, _ in dated]}")

    year, month = (int(x) for x in ym.split("-"))
    last = _calendar.monthrange(year, month)[1]
    first_d = datetime.date(year, month, 1)
    last_d = datetime.date(year, month, last)
    # 2つあれば「開始・終了」とみなす。1つなら月そのものの指定とみなす。
    new_pairs = list(pairs)
    for n, (i, k, _v, fmt) in enumerate(dated):
        use = first_d if (len(dated) == 1 or n == 0) else last_d
        new_pairs[i] = (k, use.strftime(fmt))
    target = urlunsplit((parts.scheme, parts.netloc, parts.path,
                         urlencode(new_pairs), parts.fragment))
    _note(f"[PL期間] URLを書き換えて開き直す: {C.url_query_shape(target)}")
    try:
        page.goto(target, wait_until="networkidle", timeout=40000)
    except Exception as e:
        _note(f"[PL期間] 開き直せなかった: {type(e).__name__}: {e}")
        return False
    page.wait_for_timeout(4000)
    if _on_login_page(page):
        _note("[PL期間] URL直打ちでログイン画面に戻された。この経路は使えない")
        return False
    return _period_matches(page, ym)


def _period_matches(page, ym: str) -> bool:
    """画面がほんとうに対象月になったかを読み直して確かめる。

    **ここを省くと「合わせたつもりで当月のまま」になり、どの月を指定しても
    同じ数字が入る**という、いちばん気づきにくい壊れ方をする。"""
    start, end = _month_range_label(ym)
    try:
        shown = page.evaluate(
            r"""() => [...new Set((document.body.innerText || '').split('\n')
                .map(t => t.trim())
                .filter(t => /\d{4}年\d{2}月\d{2}日/.test(t)))].slice(0, 8)""")
    except Exception as e:
        _note(f"[PL期間] 画面を読み直せなかった: {type(e).__name__}: {e}")
        return False
    if any(start in v for v in shown) and any(end in v for v in shown):
        _note(f"[PL期間] {ym} に合った（画面: {' / '.join(shown)}）")
        return True
    _note(f"[PL期間] 合っていない。期待 {start}-{end} / 画面 {' / '.join(shown)}")
    return False


def _select_pl_period(page, ym: str) -> bool:
    """損益PL集計の期間を対象月（月初〜月末）に合わせる。合ったかを返す。

    CSVは**画面に出ている期間ぶん1行**しか落ちず、既定は「今日-今日」。
    ここを合わせられないと当月しか取れない。

    確実な順に3つ試す。

    1. **URLのクエリを書き換える。** 期間が乗っているならこれがいちばん確実で、
       壊れたときも読んで分かる
    2. **値の形（YYYY年MM月DD日）で入力欄を探して入れる。** ただし実測では
       画面の `2026年09月20日-2026年09月20日` は `<input>` ではなく
       Vuetify の `v-field` の中のテキストで、唯一の input は空だった
    3. どちらも駄目なら**ピッカーを開いて中身を出す**（次の実装のため）

    id は `input-64` のような**自動採番で毎回変わる**ので、idでは掴まない。

    **どの経路でも、最後に画面を読み直して確かめる（`_period_matches`）。**
    ここを省くと「合わせたつもりで当月のまま」になり、どの月を指定しても
    同じ数字が入るという、いちばん気づきにくい壊れ方をする。"""
    # ① URLのクエリに期間が乗っていれば、そこを書き換えるのがいちばん確実
    if _try_pl_period_by_url(page, ym):
        return True

    # ② 値の形（YYYY年MM月DD日）で入力欄を探して入れる
    start, end = _month_range_label(ym)
    want = f"{start}-{end}"
    try:
        found = page.evaluate(
            r"""(want) => {
                const re = /\d{4}年\d{2}月\d{2}日/;
                document.querySelectorAll('[data-claude-period]')
                    .forEach(e => e.removeAttribute('data-claude-period'));
                const el = [...document.querySelectorAll('input')]
                    .find(i => re.test(i.value || ''));
                if (!el) return { ok: false, why: '年月日の形の入力欄が無い' };
                el.setAttribute('data-claude-period', '1');
                const ro = el.readOnly || el.getAttribute('readonly') !== null;
                // Vue は value を直接書いても気づかない。setter を呼んでから
                // input/change を投げる（これをしないと画面だけ変わって中身が古いまま）
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, want);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return { ok: true, readonly: ro };
            }""", want)
    except Exception as e:
        _note(f"[PL期間] 期間の入力欄を操作できなかった: {type(e).__name__}: {e}")
        return False
    if not found.get("ok"):
        _note(f"[PL期間] {found.get('why')}")
        # 期間は input ではなく div 側に出ている（実測: 唯一の input は空で
        # readonly でもなく、画面の 2026年09月20日-2026年09月20日 は別の要素）。
        # ピッカーを開いて中身を出す。ここで諦めると次も同じところで止まる。
        _open_period_picker(page)
        return False
    if found.get("readonly"):
        # 読み取り専用ならピッカーを開かないと変えられない。ここで黙って
        # 先に進むと当月のCSVを「対象月のもの」として取り込んでしまう。
        _note("[PL期間] 入力欄が読み取り専用。値の直接入力では変えられない")
    try:
        page.locator("[data-claude-period='1']").first.press("Enter", timeout=4000)
    except Exception:
        pass
    page.wait_for_timeout(4000)

    # ---- 確認: 画面がほんとうに対象月になったか ----
    if _period_matches(page, ym):
        return True
    _note("[PL期間] このまま落とすと当月のCSVを対象月として取り込むので、中止する")
    # ③ どちらも駄目ならピッカーを開いて中身を出す（次の実装のため）
    _open_period_picker(page)
    return False


_OVERLAY_SEL = ('.v-overlay, .v-menu, .v-date-picker, .v-picker, '
                '[role=dialog], [role=listbox], [role=menu]')


def _overlay_sig(page) -> list:
    """いま見えているオーバーレイの見分け（クラス＋先頭の文字）。

    この画面は**元から** v-list / v-overlay を持っている（左メニューや
    全店舗/グループ/1店舗 の切替）。開いたものを数えるだけだと、
    押す前から在るものを「開いた」と誤報する。前後で差を取るために使う。"""
    try:
        return page.evaluate(
            r"""(sel) => [...document.querySelectorAll(sel)]
                .filter(el => el.offsetParent !== null
                              || getComputedStyle(el).position === 'fixed')
                .map(el => String(el.className || '').slice(0, 40) + '#'
                           + (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40))""",
            _OVERLAY_SEL)
    except Exception:
        return []


def _open_period_picker(page) -> bool:
    """期間のピッカーを開いて中身を棚卸しする。

    前回は「年月日を含むいちばん内側の要素」から祖先を辿ったが、5段では
    `v-field` に届かず `div.v-row`（行全体）を押していた。押しても何も開かず、
    元から在った左メニューを「開いた」と誤報した。

    この画面の `v-field` は1つだけなので、**それを直に押す**。Vuetify は
    追加アイコン（`v-field__append-inner`）側が入口のこともあるので、
    そちらから順に試し、**押す前後でオーバーレイの差を取って**本当に開いたか
    を判定する。"""
    try:
        info = page.evaluate(
            r"""() => {
                const cut = s => (s || '').toString().trim().replace(/\s+/g, ' ').slice(0, 80);
                document.querySelectorAll('[data-claude-period]')
                    .forEach(e => e.removeAttribute('data-claude-period'));
                const fields = [...document.querySelectorAll('.v-field')];
                if (!fields.length) return { ok: false, why: 'v-field が無い' };
                const f = fields[0];
                const targets = [];
                const app = f.querySelector('.v-field__append-inner, .v-field__append, [class*=append]');
                if (app) targets.push(app);
                targets.push(f);
                targets.forEach((el, i) => el.setAttribute('data-claude-period', String(i + 1)));
                return { ok: true, n: targets.length, text: cut(f.innerText),
                         cls: cut(f.className) };
            }""")
    except Exception as e:
        _note(f"[PL期間] v-field を探せなかった: {type(e).__name__}: {e}")
        return False
    if not info.get("ok"):
        _note(f"[PL期間] {info.get('why')}")
        return False
    _note(f"[PL期間] v-field: {info['cls']}")
    _note(f"[PL期間] v-field の中身: {info['text']}")

    before = _overlay_sig(page)
    _note(f"[PL期間] 押す前から見えているオーバーレイ {len(before)}件")
    for i in range(1, info["n"] + 1):
        what = "追加アイコン" if (i == 1 and info["n"] > 1) else "v-field 本体"
        try:
            page.locator(f"[data-claude-period='{i}']").first.click(timeout=5000)
        except Exception as e:
            _note(f"[PL期間] {what}を押せなかった: {type(e).__name__}")
            continue
        page.wait_for_timeout(2500)
        after = _overlay_sig(page)
        fresh = [a for a in after if a not in before]
        if not fresh:
            _note(f"[PL期間] {what}を押したが、新しく開いたものは無い")
            continue
        _note(f"[PL期間] {what}で開いた: {len(fresh)}件")
        for a in fresh[:6]:
            _note(f"      - {a}")
        _dump_open_picker(page)
        return True
    _note("[PL期間] どれを押しても何も開かなかった。別の入口があるはず")
    return False


def _dump_open_picker(page):
    """開いたピッカーの中身（押せるもの・入力欄）を出す。

    カレンダーなのか年月の一覧なのかで操作の書き方が変わるので、
    **形が分かるまで操作を書かない。**"""
    try:
        info = page.evaluate(
            r"""(sel) => {
                const cut = s => (s || '').toString().trim().replace(/\s+/g, ' ').slice(0, 40);
                const vis = el => el.offsetParent !== null
                                  || getComputedStyle(el).position === 'fixed';
                const ov = [...document.querySelectorAll(sel)].filter(vis);
                const btns = ov.flatMap(o => [...o.querySelectorAll(
                    'button, [role=button], [role=option], [role=gridcell], td, th')])
                    .map(b => cut(b.innerText || b.getAttribute('aria-label')))
                    .filter(Boolean);
                const inputs = [...document.querySelectorAll('input')].map(i =>
                    [i.getAttribute('id') || '', i.getAttribute('type') || '',
                     /\d{4}年\d{2}月\d{2}日/.test(i.value || '') ? 'date値あり' : '',
                     (i.readOnly || i.getAttribute('readonly') !== null) ? 'readonly' : ''
                    ].join('\t'));
                // カレンダーかどうかの決め手
                const cal = !!document.querySelector(
                    '.v-date-picker, .v-picker, [class*=calendar], [role=grid]');
                return { btns: [...new Set(btns)].slice(0, 50), inputs, cal };
            }""", _OVERLAY_SEL)
    except Exception as e:
        _note(f"[PL期間] 開いた中身を読めなかった: {type(e).__name__}: {e}")
        return
    _note(f"[PL期間] カレンダーらしき器: {'あり' if info['cal'] else 'なし'}")
    _note(f"[PL期間] 中の押せるもの {len(info['btns'])}件: {' | '.join(info['btns'])}")
    _note(f"[PL期間] 入力欄 {len(info['inputs'])}件:")
    for line in info["inputs"][:12]:
        _note(f"      - {line}")


def _period_probe(page, label: str):
    """期間（年月）を指定する部品の正体を暴く。損益PL集計のCSVは
    **画面の期間ぶん1行しか落ちない**ので、月を指定できないと当月しか取れない。

    過去売上実績の側は `#designatedYear` / `#designatedMonth` という素の select
    だったが、分析サイトは別実装（Vue）で、前回の棚卸しでは input も select も
    0件だった。日付ピッカーが div で出来ている可能性が高い。
    セレクタを当てずっぽうで書くと「押せたつもりで当月のまま」になるので、
    ここで正体を出してから実装する。

    ※年月そのものは秘密ではないので伏せない（伏せると何も分からなくなる）。
    値の入っている input の value だけは出さない（認証情報が入りうる）。"""
    try:
        info = page.evaluate(
            r"""() => {
                const cut = s => (s || '').toString().trim().replace(/\s+/g, ' ').slice(0, 60);
                const fields = [...document.querySelectorAll('input,select,textarea')].map(el => {
                    const opts = el.tagName === 'SELECT'
                        ? [...el.options].slice(0, 16).map(o => cut(o.text)).join(',') : '';
                    // 値そのものは出さない（認証情報が入りうる）。ただし
                    // 「年月日が入っているか」と readonly かは、期間を入れられるか
                    // どうかの決め手なので出す。
                    const dateish = /\d{4}年\d{2}月\d{2}日|\d{4}[/-]\d{2}[/-]\d{2}/
                        .test(el.value || '') ? 'date値あり' : '';
                    const ro = (el.readOnly || el.getAttribute('readonly') !== null)
                        ? 'readonly' : '';
                    return [el.tagName.toLowerCase(), el.getAttribute('type') || '',
                            el.getAttribute('name') || '', el.getAttribute('id') || '',
                            cut(el.getAttribute('placeholder')), cut(el.className),
                            dateish, ro, opts].join('\t');
                });
                // 日付ピッカーが div で出来ている場合に備えて、それらしい器も拾う
                const picky = [...document.querySelectorAll(
                    '[class*=picker],[class*=date],[class*=calendar],[class*=month],[class*=period],[role=combobox]')]
                    .map(el => el.tagName.toLowerCase() + '\t' + cut(el.className)
                               + '\t' + cut(el.childElementCount === 0 ? el.textContent : ''));
                // 画面に出ている「2026/09」「2026年9月」風の表示（＝いま何月を見ているか）
                const shown = [...new Set((document.body.innerText || '').split('\n')
                    .map(cut)
                    .filter(t => /\d{4}\s*[/年-]\s*\d{1,2}/.test(t)))].slice(0, 12);
                return { fields: [...new Set(fields)], picky: [...new Set(picky)].slice(0, 25), shown };
            }""")
    except Exception as e:
        _note(f"[{label}] 期間の部品を調べられなかった: {type(e).__name__}: {e}")
        return
    _note(f"[{label}] 期間の部品しらべ（{C.safe_url(page.url)}）")
    _note(f"      入力欄 {len(info['fields'])}件:")
    for f in info["fields"][:20]:
        _note(f"      - {f}")
    _note(f"      ピッカーらしき器 {len(info['picky'])}件:")
    for f in info["picky"][:20]:
        _note(f"      - {f}")
    _note(f"      画面に出ている年月 {len(info['shown'])}件: {' / '.join(info['shown'])}")
    if not info["fields"] and not info["picky"]:
        _note(f"      → 期間の部品が1つも無い。この画面は月を選べない可能性がある")


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


def _open_card(page, label: str, tag: str) -> bool:
    """『label』のカードの中にある「詳細を表示」を押す。
    ダッシュボードには同じ文言のボタンがカードの数だけあるので、
    見えている先頭を押すと別のカード（売上）に行ってしまう。
    目印をDOMに付けてから、その目印を押す。"""
    try:
        found = page.evaluate(
            r"""(label) => {
                document.querySelectorAll('[data-claude-target]')
                    .forEach(e => e.removeAttribute('data-claude-target'));
                const leaf = [...document.querySelectorAll('*')].find(
                    el => el.children.length === 0 &&
                          (el.textContent || '').trim() === label);
                if (!leaf) return 'ラベルが見つからない';
                let p = leaf.parentElement;
                for (let i = 0; i < 10 && p; i++, p = p.parentElement) {
                    const btn = [...p.querySelectorAll('button, a, [role=button]')]
                        .find(b => /詳細/.test(b.textContent || ''));
                    if (btn) { btn.setAttribute('data-claude-target', '1'); return 'ok'; }
                }
                return 'カード内に詳細ボタンが無い';
            }""", label)
        if found != "ok":
            _note(f"[分析] 『{label}』のカードを開けない: {found}")
            return False
        page.locator("[data-claude-target='1']").first.click(timeout=5000)
        page.wait_for_timeout(6000)
        _note(f"[分析] 『{label}』の詳細を開いた → {C.safe_url(page.url)}")
        C.diag_dump(page, f"{tag}_{label}")
        _screen_report(page, f"分析({label})")
        _cost_lines(page, f"分析({label})")
        _period_probe(page, f"分析({label})")
        # CSVは**画面の期間ぶん1行**。既定は今日だけなので、月初〜月末に
        # 合わせてから落とす。合わせられなければ落とさない（当月のCSVを
        # 対象月のものとして取り込むほうが、取れないことより悪い）。
        ym = globals().get("TARGET_YM")
        if ym and _select_pl_period(page, ym):
            _try_csv(page, f"分析({label} {ym})", f"{tag}_{label}_{ym}")
        elif ym:
            _note(f"[分析({label})] 期間を {ym} に合わせられないのでCSVは落とさない")
        else:
            _try_csv(page, f"分析({label})", f"{tag}_{label}")
        return True
    except Exception as e:
        _note(f"[分析] 『{label}』のカード操作に失敗: {type(e).__name__}: {e}")
        return False


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

    # ダッシュボードはカードが縦に並び、カードごとに「詳細を表示」がある。
    # 素直に押すと先頭（売上）のカードに行ってしまい、FLコストに辿り着けない。
    # FLコストのカードの中にある「詳細を表示」を名指しで押す。
    _open_card(page, "FLコスト", tag)

    for name in ("詳細を表示", "View as data table, Chart"):
        if any(ng in name for ng in FORBIDDEN):
            continue
        if _click_text(page, name):
            page.wait_for_timeout(5000)
            _note(f"[分析] 『{name}』を押した後:")
            _screen_report(page, f"分析({name})")
            _cost_lines(page, f"分析({name})")
            _period_probe(page, f"分析({name})")
            C.diag_dump(page, f"{tag}_{'detail' if '詳細' in name else 'table'}")
            _try_csv(page, f"分析({name})", f"{tag}_{'detail' if '詳細' in name else 'table'}")


def _wait_sso(page, label: str, timeout_ms: int = 25000):
    """分析サイトはSSOの受け渡しページ(/sso-auth)を経由してから本画面に変わる。
    転送を待たずに読むと中身が空で「原価が無い」と誤判定する。
    ※このURLのクエリには認証トークンが載る。ログにはパスだけ出す。"""
    import time
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if "sso-auth" not in (page.url or ""):
            return True
        page.wait_for_timeout(1000)
    _note(f"[{label}] SSOの転送が {timeout_ms // 1000}秒で終わらなかった")
    return False


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
        _wait_sso(target, child)
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


def _otp_error_text(page) -> str:
    """認証画面が出しているエラー文を拾う。

    コード自体はログにも出さないので、ここでも6桁の並びは伏せる。
    短時間に何度も試すと「回数を超えました」の類が出ることがあり、
    その場合は**間を置く**のが正しい対応。再試行を重ねると悪化する。"""
    try:
        texts = page.evaluate(
            r"""() => {
                const cut = s => (s || '').toString().trim().replace(/\s+/g, ' ').slice(0, 120);
                const sel = '.error, .is-error, .alert, .message, [role=alert],'
                          + '[class*=error], [class*=alert], [class*=message]';
                return [...new Set([...document.querySelectorAll(sel)]
                    .filter(el => el.offsetParent !== null)
                    .map(el => cut(el.innerText))
                    .filter(Boolean))].slice(0, 6);
            }""")
    except Exception:
        return ""
    return " / ".join(C.mask_numbers(t) for t in texts)


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
        # **画面が出しているエラー文を必ず拾う。** これが無いと
        # 「コードが違う」のか「試行回数を超えた／アカウントがロックされた」のか
        # 区別できず、原因の分からないまま再試行して事態を悪くする。
        why = _otp_error_text(page)
        raise RuntimeError(
            "[uleji] 認証コードを入力しましたが、まだ認証画面のままです"
            + (f"（画面のメッセージ: {why}）" if why else "（画面にメッセージは出ていない）")
        )
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


# 過去売上実績の画面（run #17 で確定）
#   select designatedYear  … 年
#   select designatedMonth … 月
#   select storeCode       … 店舗
#   input  pastSalesInfoList[N].sales / .guests … **入力用のグリッド**。触らない
#   ボタン 検索 / CSVダウンロード / 登録 / CSV一括設定
# 落ちるCSV: UTF-8(BOM) / 列は 店舗コード | 日付 | 売上 | 客数 / 日別31行
YEAR_SELECT = "#designatedYear"
MONTH_SELECT = "#designatedMonth"
STORE_SELECT = "#storeCode"


def _store_options(page) -> dict:
    """店舗セレクトの選択肢（コード→表示名）。CSVには店舗コードしか入らないので、
    店舗名に直すためにここで対応表を作る。"""
    try:
        opts = page.evaluate(
            r"""(sel) => {
                const el = document.querySelector(sel);
                if (!el) return [];
                return [...el.options].map(o => [o.value, (o.text || '').trim()]);
            }""", STORE_SELECT)
        return {v: t for v, t in opts if v}
    except Exception:
        return {}


def _select_month(page, ym: str) -> bool:
    """年月セレクトを対象月に合わせる。合わせられたかを返す。"""
    year, month = ym.split("-")
    ok = True
    for sel, want in ((YEAR_SELECT, year), (MONTH_SELECT, month)):
        done = False
        for value in (want, want.lstrip("0"), f"{int(want)}"):
            try:
                page.select_option(sel, value=value, timeout=4000)
                done = True
                break
            except Exception:
                continue
        if not done:   # value で無理ならラベル（「2026年」「8月」等）で試す
            for label in (want, want.lstrip("0"), f"{int(want)}年", f"{int(want)}月"):
                try:
                    page.select_option(sel, label=label, timeout=4000)
                    done = True
                    break
                except Exception:
                    continue
        if not done:
            print(f"[uleji] {sel} を {want} に合わせられませんでした")
            ok = False
    return ok


def _open_past_sales(page):
    """売上管理 → 過去売上実績 を開く。URL直打ちはセッションを壊すのでクリックで。"""
    if not _visible(page, "過去売上実績"):
        if not _click_text(page, "売上管理"):
            raise RuntimeError("[uleji] メニュー『売上管理』を開けませんでした")
        page.wait_for_timeout(1200)
    if not _click_text(page, "過去売上実績"):
        raise RuntimeError("[uleji] メニュー『過去売上実績』を開けませんでした")
    page.wait_for_timeout(5000)
    if _on_login_page(page):
        raise RuntimeError("[uleji] 過去売上実績を開いたらログイン画面に戻されました")


def _download_month(page, ym: str, already_open: bool = False) -> bytes:
    """対象月を指定して検索し、CSVを落とす。書き込み系のボタンには触らない。"""
    if not already_open:
        _open_past_sales(page)
    codes = _store_options(page)
    if codes:
        print(f"[uleji] 店舗セレクトの選択肢 {len(codes)}件:")
        for code, name in list(codes.items())[:20]:
            print(f"    | {code}\t{name}")
    if not _select_month(page, ym):
        raise RuntimeError(f"[uleji] 対象月 {ym} を指定できませんでした")
    if not (_click_text(page, "検索") or _click_text(page, "検 索")):
        raise RuntimeError("[uleji] 検索ボタンを押せませんでした")
    page.wait_for_timeout(6000)
    with page.expect_download(timeout=120000) as dl:
        page.get_by_text("CSVダウンロード", exact=True).first.click(timeout=8000)
    data = open(dl.value.path(), "rb").read()
    print(f"[uleji] {ym} のCSVを取得: {C.sniff_bytes(data)}")
    return data


def fetch_csv(ym: str) -> tuple[bytes, dict]:
    """CSVと、店舗コード→店舗名の対応表を返す。CSVには店舗コードしか
    入っていないので、店舗名は画面のセレクトから取る。"""
    company = C.env("ULEJI_COMPANY"); user = C.env("ULEJI_USER"); pw = C.env("ULEJI_PASS")
    globals()["TARGET_YM"] = ym
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
            C.write_findings("uleji", FINDINGS)
            browser.close()
            return b"", {}

        if otp:
            _submit_otp(page)

        # ---- 実取得: 過去売上実績 → 対象月を指定 → CSVダウンロード ----
        try:
            _open_past_sales(page)
            names = _store_options(page)
            data = _download_month(page, ym, already_open=True)
        finally:
            browser.close()
        return data, names


def _num(v) -> float | None:
    """CSVの数値。空欄・ハイフンは「値なし」として None を返す。
    0として足すと、入力されていない日が売上0として混ざる。"""
    if v is None:
        return None
    t = str(v).strip().replace(",", "").replace("￥", "").replace("¥", "")
    if t in ("", "-", "--", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def normalize(rows: list[dict], ym: str, store_names: dict | None = None) -> list[dict]:
    """新Uレジの日別CSV（店舗コード/日付/売上/客数）を月次にまとめる。
    店舗コードはUSEN側の体系なので、店舗セレクトの対応表で店舗名に直し、
    店舗名から店舗マスタ（FWの店舗コード）を引く。"""
    stores = C.load_stores()
    idx = C.build_store_index(stores)
    names = store_names or {}
    # 新Uレジ側の表示名は「大衆酒場 ぎふや」のように店舗を特定できない
    # ことがある（マスタには ぎふや天満橋 と ぎふや福岡天神 の2店がある）。
    # 名前で引けないときの逃げ道として、マスタで pos が uleji の店を見ておく。
    uleji_master = [st for st in stores
                    if (st.get("pos") or "fw") == "uleji" and st.get("active", True)]
    agg: dict[str, dict] = {}
    for r in rows:
        pos_code = str(r.get("店舗コード") or "").strip()
        if not pos_code:
            continue
        sales = _num(r.get("売上"))
        guests = _num(r.get("客数"))
        a = agg.setdefault(pos_code, {"売上": 0.0, "客数": 0.0, "日数": 0,
                                      "売上あり": 0, "客数あり": 0})
        if sales is not None:
            a["売上"] += sales
            a["売上あり"] += 1
        if guests is not None:
            a["客数"] += guests
            a["客数あり"] += 1
        a["日数"] += 1

    out, unresolved = [], []
    for pos_code, a in agg.items():
        name = names.get(pos_code) or pos_code
        code = C.resolve_store_code(name, idx)
        extra = ""
        if not code and len(agg) == 1 and len(uleji_master) == 1:
            # CSVに店舗が1つ、マスタで新Uレジの店も1つ。取り違えようがないので結ぶ。
            # どちらかが複数になった時点でこの推定は効かなくなり、空のまま報告される。
            code = uleji_master[0].get("store_code", "")
            name = uleji_master[0].get("store_name", name)
            extra = "／マスタで新Uレジの店が1件のため対応付け"
        if not code and name not in unresolved:
            unresolved.append(name)
        # 何日ぶん足したのかを残す。月の途中までしか入っていないのに
        # 満額の月次として扱うと、前年比や達成率が静かに狂う。
        note = (f"新Uレジ 日別{a['日数']}日分を合算"
                f"（売上入力{a['売上あり']}日）{extra}")
        out.append({
            "年月": ym,
            "店舗名": name,
            "売上": int(a["売上"]) if a["売上あり"] else "",
            "フード原価": "",      # 新Uレジの過去売上実績CSVに原価の列は無い
            "ドリンク原価": "",
            "客数": int(a["客数"]) if a["客数あり"] else "",
            "備考": note,
            "取込日時": C.now_str(),
            "POS": "uleji",
            "店舗コード": code,
        })
    if unresolved:
        print(f"[uleji] 店舗マスタに無い表記 {len(unresolved)}件（店舗コードは空のまま取り込む）:")
        for n in unresolved:
            print(f"    | {n}")
    return out


def main():
    ym = C.last_month(sys.argv[1] if len(sys.argv) > 1 else None)
    # 診断の中からも対象月が要る（PL集計の期間を合わせるため）。
    globals()["TARGET_YM"] = ym
    data, store_names = fetch_csv(ym)
    if not data:  # 診断モードは空で返る
        return
    rows = normalize(C.parse_csv_bytes(data), ym, store_names)
    print(f"[uleji] {ym}: {len(rows)}店取得")
    sid = C.env("TARGET_SHEET_ID")
    if sid:
        C.write_to_sheet(rows, sid, worksheet="POS売上")
    else:
        C.write_local_csv(rows, f"uleji_{ym}.csv")


if __name__ == "__main__":
    main()
