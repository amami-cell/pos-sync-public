"""
pos_common.py  —  ダイニー / 新Uレジ 売上・原価をスプレッドに取り込む共通部品
方針：Playwrightは「ログイン→公式CSVエクスポート」までを自動化し、
      画面の数字はスクレイプせず、ダウンロードした正規CSVを取り込む（B＋A）。

依存: playwright, gspread, google-auth, pandas（無ければcsv標準ライブラリでも可）
認証情報は環境変数（GitHub Actions Secrets）から。コード/チャットに直書きしない。
"""
from __future__ import annotations
import os, csv, io, datetime, json, unicodedata

# ---- 共通スキーマ（既存FW→シートと揃える）----
# 年月, 店舗名, 売上, フード原価(F), ドリンク原価(D), 客数, 備考, 取込日時, POS
COLUMNS = ["年月", "店舗名", "売上", "フード原価", "ドリンク原価", "客数", "備考", "取込日時", "POS",
           "店舗コード"]  # 店舗コードは末尾に追加。既存列の順序は変えていない

def last_month(ym: str | None = None) -> str:
    """対象月 YYYY-MM。未指定なら先月（締め後の当月分を翌月頭に回す運用向け）。"""
    if ym:
        return ym
    t = datetime.date.today().replace(day=1) - datetime.timedelta(days=1)
    return f"{t.year:04d}-{t.month:02d}"

def norm_name(s: str) -> str:
    """全角/半角・空白ゆらぎを吸収して店舗名照合しやすくする。"""
    return unicodedata.normalize("NFKC", str(s)).replace(" ", "").replace("　", "").strip()

def env(*keys: str) -> str | None:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return None

# 店舗名→店舗コードの対応表はここには持たない。
# hansoku の店舗マスタを唯一の正とし、下部の load_stores() で実行時に読む。

ENCODINGS = ("utf-8-sig", "cp932", "utf-8", "euc-jp")


def sniff_bytes(data: bytes) -> str:
    """落ちてきたファイルの正体を、中身を出さずに見分ける。
    公開リポジトリのログに載るので、売上そのものは出さない。"""
    head = data[:8]
    if head[:4] == b"PK\x03\x04":
        kind = "ZIP（複数店舗のCSVがまとまっている可能性）"
    elif head[:2] == b"\x1f\x8b":
        kind = "gzip"
    elif head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        kind = "UTF-16（BOM付き）"
    elif head[:3] == b"\xef\xbb\xbf":
        kind = "UTF-8（BOM付き）"
    elif head[:5] == b"%PDF-":
        kind = "PDF"
    elif head[:2] == b"\xd0\xcf":
        kind = "古いExcel(.xls)"
    else:
        try:
            data[:400].decode("utf-8")
            kind = "テキスト（UTF-8）"
        except UnicodeDecodeError:
            kind = "テキストではない、または別の文字コード"
    return f"{len(data)}バイト / 先頭8バイト={head.hex()} / 推定={kind}"


def _decode_csv(data: bytes, encodings=ENCODINGS) -> list[dict]:
    for enc in encodings:
        try:
            return list(csv.DictReader(io.StringIO(data.decode(enc))))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"CSVの文字コードを判別できませんでした（{'/'.join(encodings)}で失敗）")


def parse_csv_bytes(data: bytes, encodings=ENCODINGS) -> list[dict]:
    """POSのCSVを辞書リストに。ZIPで来る場合は中のCSVをすべて展開して連結する。
    ダイニーは『複数店舗の場合はZIP』と画面に明記されており、実際ZIPで来る。"""
    if data[:4] == b"PK\x03\x04":
        import zipfile
        rows: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            print(f"[csv] ZIPを展開: {len(names)}ファイル")
            for n in names:
                print(f"    | {n}")
            for n in names:
                if not n.lower().endswith(".csv"):
                    continue
                part = _decode_csv(z.read(n), encodings)
                # どのファイル由来かを残す。店舗別ZIPだと店名がファイル名にある
                for r in part:
                    r.setdefault("_source_file", n)
                rows.extend(part)
        if not rows:
            raise ValueError("ZIPの中にCSVが見つかりませんでした")
        return rows
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return _decode_csv(data, ("utf-16",) + encodings)
    return _decode_csv(data, encodings)

# ---- 出力1: ローカルCSV（既存GASが取り込む運用に合わせる場合）----
def write_local_csv(rows: list[dict], path: str):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    print(f"[write_local_csv] {len(rows)}行 -> {path}")

# ---- 出力2: Googleスプレッドに追記（gspread / サービスアカウント）----
# 注意: infomart-botサービスアカウントはDrive容量枯渇の既知課題あり。
#   対策: 書込先スプレッドを個人アカ所有にして共有編集権を付与 / 別SAを使う 等。
def write_to_sheet(rows: list[dict], spreadsheet_id: str, worksheet: str = "POS売上"):
    import gspread
    from google.oauth2.service_account import Credentials
    sa_json = env("GCP_SA_JSON")  # SecretsにサービスアカウントrawJSONを入れる
    if not sa_json:
        raise RuntimeError("GCP_SA_JSON 未設定。ローカルCSV出力に切替えてください。")
    creds = Credentials.from_service_account_info(
        json.loads(sa_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(worksheet)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet, rows=2000, cols=len(COLUMNS))
        ws.append_row(COLUMNS)
    # 同一(年月×店舗×POS)は重複させない：既存行を消してから追記
    existing = ws.get_all_records()
    keep = [r for r in existing
            if not any(r.get("年月")==x["年月"] and norm_name(r.get("店舗名",""))==norm_name(x["店舗名"]) and r.get("POS")==x["POS"] for x in rows)]
    ws.clear(); ws.append_row(COLUMNS)
    for r in keep + rows:
        ws.append_row([r.get(c, "") for c in COLUMNS])
    print(f"[write_to_sheet] {len(rows)}行 追記 / 既存{len(keep)}行 保持 -> {worksheet}")

def now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---- 診断: 画面の構造を diag/ に保存し、要点はジョブログにも出す ----
# POS_DIAG=1 のとき、画面構造（クリック候補・リンク・ボタン・入力欄）を ./diag/ に保存し、
# 要点は標準出力にも echo する（artifactを開けない環境でも導線を確定できるように）。
# ⚠ このリポジトリは public。実行ログもartifactも誰でも見られる。
#   売上の数字が写るもの（スクショ・HTML・CSVの実体）は want_diag_files() で既定オフ。
#   入力欄の value と認証情報は、どのモードでも出力しない。
DIAG_ECHO_MAX = 60  # ログに出す最大件数（多すぎるログを防ぐ）


def _uniq(seq):
    seen, out = set(), []
    for t in seq:
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def _echo(tag: str, label: str, items: list[str], limit: int | None = None):
    """診断結果の要点をジョブログへ。件数が多いときは先頭のみ。"""
    shown = items[: (limit or DIAG_ECHO_MAX)]
    print(f"[diag:{tag}] {label} ({len(items)}件" + (f"、先頭{len(shown)}件を表示" if len(shown) < len(items) else "") + ")")
    for t in shown:
        print(f"    | {t}")


def want_diag_files() -> bool:
    """スクショとHTMLを残すか。既定は残さない。
    これらは管理画面をそのまま写すため売上の数字が写り込みうる。
    公開リポジトリでは実行ログもartifactも誰でも見られるので、
    既定では作らず、必要なときだけ POS_DIAG_FILES=1 で明示的に有効化する。"""
    return os.environ.get("POS_DIAG_FILES", "") in ("1", "true", "yes")


def diag_dump(page, tag: str, outdir: str = "diag"):
    import os as _os
    _os.makedirs(outdir, exist_ok=True)
    print(f"[diag:{tag}] URL = {page.url}")
    if want_diag_files():
        try:
            page.screenshot(path=f"{outdir}/{tag}.png", full_page=True)
        except Exception as e:
            print(f"[diag:{tag}] screenshot失敗: {e}")
        try:
            with open(f"{outdir}/{tag}.html", "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception as e:
            print(f"[diag:{tag}] html失敗: {e}")
    else:
        print(f"[diag:{tag}] スクショ/HTMLは既定で残しません（必要なら POS_DIAG_FILES=1）")

    # クリック候補（メニュー/ボタン/リンクのテキスト）
    try:
        items = _uniq(page.evaluate(r"""() => [...document.querySelectorAll('a,button,[role=button],[role=menuitem]')]
            .map(el => (el.innerText||el.getAttribute('aria-label')||'').trim().replace(/\s+/g,' '))
            .filter(t => t && t.length <= 40)"""))
        with open(f"{outdir}/{tag}_clickables.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(items))
        _echo(tag, "クリック候補", items)
    except Exception as e:
        print(f"[diag:{tag}] clickable列挙失敗: {e}")

    # リンク（text と href）— エクスポート画面のURLを直接特定するため
    try:
        links = _uniq(page.evaluate(r"""() => [...document.querySelectorAll('a[href]')]
            .map(el => ((el.innerText||'').trim().replace(/\s+/g,' ').slice(0,40)) + '\t' + el.getAttribute('href'))"""))
        with open(f"{outdir}/{tag}_links.tsv", "w", encoding="utf-8") as f:
            f.write("text\thref\n" + "\n".join(links))
        _echo(tag, "リンク(text→href)", links)
    except Exception as e:
        print(f"[diag:{tag}] link列挙失敗: {e}")

    # ボタンの棚卸し（disabled 状態つき）— 期間未指定でDLボタンが押せないケースを見分けるため
    try:
        btns = _uniq(page.evaluate(r"""() => [...document.querySelectorAll('button,[role=button],a')]
            .map(el => {
                const t = (el.innerText||el.getAttribute('aria-label')||'').trim().replace(/\s+/g,' ').slice(0,30);
                if (!t) return '';
                const off = el.disabled || el.getAttribute('aria-disabled') === 'true'
                    || (el.className||'').toString().includes('disabled');
                const vis = el.offsetParent !== null || el.tagName === 'BODY';
                return t + '\t' + (off ? 'disabled' : 'enabled') + '\t' + (vis ? 'visible' : 'hidden');
            }).filter(Boolean)"""))
        with open(f"{outdir}/{tag}_buttons.tsv", "w", encoding="utf-8") as f:
            f.write("text\tstate\tvisible\n" + "\n".join(btns))
        _echo(tag, "ボタン(text/状態/表示)", btns)
    except Exception as e:
        print(f"[diag:{tag}] button列挙失敗: {e}")

    # 入力欄の棚卸し — 期間指定(年月/開始日/終了日)のセレクタを確定するのに必須。
    # value は入力済みの値（＝認証情報が入りうる）なので出さない。
    try:
        fields = _uniq(page.evaluate(r"""() => [...document.querySelectorAll('input,select,textarea')]
            .map(el => [el.tagName.toLowerCase(), el.getAttribute('type')||'', el.getAttribute('name')||'',
                        el.getAttribute('id')||'', el.getAttribute('placeholder')||''].join('\t'))"""))
        with open(f"{outdir}/{tag}_fields.tsv", "w", encoding="utf-8") as f:
            f.write("tag\ttype\tname\tid\tplaceholder\n" + "\n".join(fields))
        _echo(tag, "入力欄(tag/type/name/id/placeholder)", fields)
    except Exception as e:
        print(f"[diag:{tag}] field列挙失敗: {e}")


def try_download(page, selectors: list[str], timeout_ms: int = 10000):
    """候補セレクタを順に押してダウンロードを捕捉。成功したら (bytes, 効いたセレクタ)。"""
    for sel in selectors:
        try:
            with page.expect_download(timeout=timeout_ms) as dl:
                page.locator(sel).first.click(timeout=4000)
            return open(dl.value.path(), "rb").read(), sel
        except Exception:
            continue
    return None, None


def describe_csv(data: bytes, tag: str, outdir: str = "diag") -> list[str]:
    """落ちたCSVを diag/ に保存し、列名（＝スキーマ）だけをログに出す。数値データはログに出さない。"""
    import os as _os
    _os.makedirs(outdir, exist_ok=True)
    if want_diag_files():
        with open(f"{outdir}/{tag}.csv", "wb") as f:
            f.write(data)   # 中身は売上そのもの。既定では保存しない
    print(f"[diag:{tag}] 取得したファイル: {sniff_bytes(data)}")
    rows = parse_csv_bytes(data)
    header = list(rows[0].keys()) if rows else []
    print(f"[diag:{tag}] CSV {len(rows)}行 / {len(header)}列")
    _echo(tag, "CSV列名", header, limit=200)   # 列マッピング確定のため全部出す
    # 店舗名の表記は、店舗マスタとの照合確認に要るのでユニーク値だけ出す（数値は出さない）
    for key in header:
        if any(k in key for k in ("店舗", "店名", "shop", "store")):
            vals = _uniq([str(r.get(key, "")).strip() for r in rows])
            _echo(tag, f"列「{key}」のユニーク値", vals)
            break
    with open(f"{outdir}/{tag}_header.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(header))
    return header


def is_diag() -> bool:
    return os.environ.get("POS_DIAG", "") in ("1", "true", "yes")


def probe_elements(page, text: str, limit: int = 6):
    """指定テキストを含む要素の正体を暴く。
    同じ文言のボタンが複数あって、どれが本命か分からない時に使う。
    outerHTML・disabled・所属フォーム・近傍の見出しをログへ出す。"""
    try:
        info = page.evaluate(
            """(args) => {
                const [txt, lim] = args;
                const els = [...document.querySelectorAll('button,[role=button],a,input[type=submit]')]
                    .filter(el => ((el.innerText||el.value||'')).includes(txt));
                return els.slice(0, lim).map(el => {
                    const form = el.closest('form');
                    let head = '';
                    let p = el.parentElement;
                    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
                        const h = p.querySelector('h1,h2,h3,h4,legend,label');
                        if (h && h.innerText) { head = h.innerText.trim().slice(0, 60); break; }
                    }
                    return {
                        disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                        visible: el.offsetParent !== null,
                        form: form ? (form.id || form.getAttribute('name') || '(無名form)') : '(formなし)',
                        heading: head,
                        html: el.outerHTML.slice(0, 300),
                    };
                });
            }""", [text, limit])
    except Exception as e:
        print(f"[probe] 「{text}」の調査に失敗: {e}")
        return
    print(f"[probe] 「{text}」を含む要素 {len(info)}件")
    for i, d in enumerate(info):
        print(f"  [{i}] disabled={d['disabled']} visible={d['visible']} form={d['form']}")
        print(f"      近傍見出し: {d['heading']}")
        print(f"      html: {d['html']}")


def probe_form_state(page):
    """フォームの未入力箇所を洗い出す。DLボタンが disabled な理由の特定用。
    値そのものは出さず、空かどうかだけを出す（認証情報を出さないため）。"""
    try:
        info = page.evaluate(
            """() => [...document.querySelectorAll('input,select')].map(el => ({
                id: el.id || '', name: el.getAttribute('name') || '',
                ph: el.getAttribute('placeholder') || '',
                type: el.getAttribute('type') || el.tagName.toLowerCase(),
                empty: !(el.value && el.value.length),
                checked: el.type === 'checkbox' ? el.checked : null,
            }))""")
    except Exception as e:
        print(f"[probe] フォーム状態の取得に失敗: {e}")
        return
    print(f"[probe] フォーム入力状態 {len(info)}件（値は出さず空かどうかのみ）")
    for d in info:
        chk = "" if d["checked"] is None else f" checked={d['checked']}"
        print(f"  | {d['type']}\tid={d['id']}\tname={d['name']}\tph={d['ph']}\t空={d['empty']}{chk}")


# ---- OTP(メール認証コード)の自動取得 ----
# 新Uレジはログイン後にメールで届く認証コードを要求する。管理画面側で
# 無効化できないため、実行しているプロセス自身がメールから取りに行く。
# 対応方式は IMAP。ガルーンが直接IMAPで読めるならそれを、読めないなら
# OTPメールだけを転送した専用アドレス（Gmail等）を指す。
#
# 必要な環境変数（すべて GitHub Secrets 経由）:
#   OTP_IMAP_HOST  … 例 imap.gmail.com
#   OTP_IMAP_USER  … メールアドレス
#   OTP_IMAP_PASS  … アプリパスワード等（通常のログインパスワードではないことが多い）
#   OTP_IMAP_PORT   (任意, 既定 993)
#   OTP_IMAP_FOLDER (任意, 既定 INBOX)
#   OTP_SUBJECT_HINT(任意, 既定 認証コード) … 件名/本文の絞り込み語
#   OTP_CODE_REGEX  (任意, 既定 6桁の数字)
#
# コード自体はログに出さない。桁数だけ出す。
OTP_DEFAULT_REGEX = r"(?<!\d)(\d{6})(?!\d)"


def otp_imap_configured() -> bool:
    return all(env(k) for k in ("OTP_IMAP_HOST", "OTP_IMAP_USER", "OTP_IMAP_PASS"))


def _mail_text(msg) -> str:
    """メール本文をテキストで取り出す（HTMLメールならタグを落とす）。"""
    import re as _re
    parts = []
    if msg.is_multipart():
        walk = msg.walk()
    else:
        walk = [msg]
    for part in walk:
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
        except Exception:
            continue
        if ctype == "text/html":
            text = _re.sub(r"<[^>]+>", " ", text)
        parts.append(text)
    return "\n".join(parts)


def fetch_otp_via_imap(not_before, timeout_sec: int = 180, poll_sec: int = 10) -> str:
    """not_before 以降に届いたメールから認証コードを取り出す。
    not_before より前のメールは無視する（前回ログインの古いコードを拾わないため）。
    見つかるまで poll_sec 間隔で最大 timeout_sec 待つ（メールは即着しないため）。"""
    import imaplib, email, re, time
    from email.utils import parsedate_to_datetime

    host = env("OTP_IMAP_HOST"); user = env("OTP_IMAP_USER"); pw = env("OTP_IMAP_PASS")
    port = int(env("OTP_IMAP_PORT") or 993)
    folder = env("OTP_IMAP_FOLDER") or "INBOX"
    hint = env("OTP_SUBJECT_HINT") or "認証コード"
    pattern = re.compile(env("OTP_CODE_REGEX") or OTP_DEFAULT_REGEX)
    if not (host and user and pw):
        raise RuntimeError(
            "OTP_IMAP_HOST / OTP_IMAP_USER / OTP_IMAP_PASS が未設定です。"
            "READMEの『新UレジのOTP自動取得』を参照してください"
        )

    deadline = time.time() + timeout_sec
    attempt = 0
    while True:
        attempt += 1
        try:
            with imaplib.IMAP4_SSL(host, port) as M:
                M.login(user, pw)
                M.select(folder, readonly=True)
                # SINCE は日付単位なので、時刻での絞り込みは取得後に行う
                since = not_before.strftime("%d-%b-%Y")
                typ, data = M.search(None, f'(SINCE "{since}")')
                ids = data[0].split() if typ == "OK" and data and data[0] else []
                for num in reversed(ids):  # 新しいものから
                    typ, raw = M.fetch(num, "(RFC822)")
                    if typ != "OK" or not raw or not raw[0]:
                        continue
                    msg = email.message_from_bytes(raw[0][1])
                    try:
                        sent = parsedate_to_datetime(msg.get("Date"))
                    except Exception:
                        continue
                    if sent is None:
                        continue
                    if sent.timestamp() < not_before.timestamp() - 120:
                        continue  # ログイン試行より前のメールは古いコード
                    subject = str(email.header.make_header(
                        email.header.decode_header(msg.get("Subject") or "")))
                    body = _mail_text(msg)
                    if hint and hint not in subject and hint not in body:
                        continue
                    m = pattern.search(subject) or pattern.search(body)
                    if m:
                        code = m.group(1)
                        print(f"[otp] 認証コードを取得（{len(code)}桁、{attempt}回目の確認、"
                              f"メール受信 {sent.isoformat()}）")
                        return code
        except Exception as e:
            print(f"[otp] IMAP確認でエラー（{attempt}回目、継続します）: {type(e).__name__}: {e}")

        if time.time() >= deadline:
            raise RuntimeError(
                f"[otp] {timeout_sec}秒待っても『{hint}』を含む新着メールが見つかりませんでした。"
                "転送設定・フォルダ名(OTP_IMAP_FOLDER)・絞り込み語(OTP_SUBJECT_HINT)を確認してください"
            )
        print(f"[otp] 未着。{poll_sec}秒後に再確認します")
        time.sleep(poll_sec)


def probe_cards(page, text: str = "ダウンロード", limit: int = 4):
    """指定テキストのボタンが属する「カード」の中身をログへ。
    同じ見た目のボタンが複数あるとき、どのカード（＝どの帳票）のものかを
    見出し・ラベルごと読めるようにする。"""
    try:
        info = page.evaluate(
            r"""(args) => {
                const [txt, lim] = args;
                const els = [...document.querySelectorAll('button')]
                    .filter(el => (el.innerText || '').includes(txt));
                return els.slice(0, lim).map(el => {
                    // 十分な説明文を含む祖先までさかのぼる＝カード単位
                    let p = el.parentElement, card = null, last = null;
                    for (let i = 0; i < 10 && p; i++, p = p.parentElement) {
                        const t = (p.innerText || '').trim();
                        last = p;
                        if (t.length > 60) { card = p; break; }
                    }
                    if (!card) card = last;  // 説明文が短いカードでも最上位の祖先は返す
                    const inputs = card
                        ? [...card.querySelectorAll('input')].map(x =>
                            (x.getAttribute('placeholder') || x.id || x.type || '?')
                            + (x.value ? '=入力済' : '=空')).slice(0, 8)
                        : [];
                    return {
                        disabled: !!el.disabled,
                        cardText: card ? (card.innerText || '').trim().replace(/\n+/g, ' / ').slice(0, 280) : '(カード特定できず)',
                        inputs: inputs.join(', '),
                    };
                });
            }""", [text, limit])
    except Exception as e:
        print(f"[cards] 調査に失敗: {e}")
        return
    print(f"[cards] 「{text}」ボタンを含むカード {len(info)}件")
    for i, d in enumerate(info):
        print(f"  [{i}] disabled={d['disabled']}")
        print(f"      カード内容: {d['cardText']}")
        print(f"      カード内の入力欄: {d['inputs']}")


def click_and_watch(page, index: int, text: str = "ダウンロード", timeout_ms: int = 30000):
    """指定インデックスのボタンを押してダウンロードを待つ。
    落ちてこなかった場合は、画面に出た警告・エラーの文言を拾って返す。"""
    btn = page.locator(f"button:has-text('{text}')").nth(index)
    try:
        if btn.is_disabled():
            return None, f"[{index}] は disabled のため押せません"
    except Exception:
        pass
    try:
        with page.expect_download(timeout=timeout_ms) as dl:
            btn.click(timeout=5000)
        return open(dl.value.path(), "rb").read(), f"[{index}] からダウンロード成功"
    except Exception as e:
        msgs = []
        for sel in (".ant-message", ".ant-notification", "[role=alert]", ".ant-form-item-explain"):
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 3)):
                    t = (loc.nth(i).inner_text() or "").strip()
                    if t:
                        msgs.append(f"{sel}: {t[:120]}")
            except Exception:
                continue
        note = f"[{index}] ダウンロードされず（{type(e).__name__}）"
        if msgs:
            note += " / 画面のメッセージ: " + " | ".join(msgs)
        return None, note


# ---- 店舗マスタ（hansoku の config/stores.yaml を参照する）----
# 店舗名→店舗コードの対応を自前で持つと二重管理になり、店の増減で必ずズレる。
# 販促ダッシュボード(hansoku)が既に持っているマスタを唯一の正とし、
# 公開リポジトリの raw URL から実行時に読む。コピーは置かない。
#   store_code  … FW(Foodist Journal)の店舗コード。実績データの結合キー
#   aliases/yomi/source_name/infomart_name … 表記ゆれの吸収に使う
#   pos         … fw / uleji / dainy（FW未連動の店の実績の出どころ）
STORES_YAML_URL = (
    os.environ.get("STORES_YAML_URL")
    or "https://raw.githubusercontent.com/amami-cell/hansoku/main/config/stores.yaml"
)


def load_stores() -> list[dict]:
    """店舗マスタを取得して辞書のリストで返す。取得できなければ空リスト。"""
    import urllib.request
    try:
        import yaml
    except ImportError:
        print("[stores] PyYAML が無いため店舗マスタを読めません")
        return []
    try:
        with urllib.request.urlopen(STORES_YAML_URL, timeout=30) as r:
            data = yaml.safe_load(r.read().decode("utf-8"))
        stores = data.get("stores", []) if isinstance(data, dict) else []
        print(f"[stores] 店舗マスタを取得: {len(stores)}件")
        return stores
    except Exception as e:
        print(f"[stores] 店舗マスタの取得に失敗（店舗コードは空で続行）: {e}")
        return []


def build_store_index(stores: list[dict]) -> dict[str, str]:
    """照合用の索引を作る。表記ゆれ（別名・読み・コード付き名・インフォマート名）を
    すべて同じ店舗コードに向ける。"""
    idx: dict[str, str] = {}

    def put(key, code):
        k = norm_name(key)
        if k and k not in idx:
            idx[k] = code

    for s in stores:
        code = str(s.get("store_code", "") or "").strip()
        if not code:
            continue
        put(s.get("store_name", ""), code)
        put(s.get("infomart_name", ""), code)
        for a in (s.get("aliases") or []):
            put(a, code)
        for y in (s.get("yomi") or []):
            put(y, code)
        src = str(s.get("source_name", "") or "")
        if "_" in src:                      # "0001069_ひよこ飯店" → "ひよこ飯店"
            put(src.split("_", 1)[1], code)
        put(src, code)
    return idx


def resolve_store_code(name: str, idx: dict[str, str]) -> str:
    """POSのCSVに出る店舗表記から店舗コードを引く。
    完全一致で引けなければ、片方がもう片方を含む形で緩く照合する。"""
    if not name or not idx:
        return ""
    n = norm_name(name)
    if n in idx:
        return idx[n]
    for key, code in idx.items():
        if len(key) >= 3 and (key in n or n in key):
            return code
    return ""


def probe_checkboxes(page, limit: int = 40):
    """チェックボックスをラベル文言つきで列挙する。
    「出力ファイル選択」にどの帳票が並んでいて、どれがONかを確定するため。"""
    try:
        info = page.evaluate(
            r"""(lim) => [...document.querySelectorAll('input[type=checkbox]')]
                .slice(0, lim)
                .map((el, i) => {
                    const w = el.closest('label') || el.closest('.ant-checkbox-wrapper') || el.parentElement;
                    const t = ((w && w.innerText) || '').trim().replace(/\s+/g, ' ').slice(0, 60);
                    return i + '	' + (el.checked ? 'ON ' : 'off') + '	' + (t || '(ラベルなし)');
                })""", limit)
    except Exception as e:
        print(f"[checkbox] 列挙に失敗: {e}")
        return
    print(f"[checkbox] チェックボックス {len(info)}件（番号/状態/ラベル）")
    for t in info:
        print(f"    | {t}")
