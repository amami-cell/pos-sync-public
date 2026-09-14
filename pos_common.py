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
COLUMNS = ["年月", "店舗名", "売上", "フード原価", "ドリンク原価", "客数", "備考", "取込日時", "POS"]

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

# ---- 店舗名 → 財務店舗コード（prefix）対応。CSVの店舗表記に合わせて随時追記 ----
# 例: dinii/UレジのCSVに出る店舗名 : 財務prefix
STORE_MAP = {
    # "ひよこ飯店": "0001069",
    # "ちゃーちゃん": "0001111",
    # "料理と酒 たいだい": "0001137",
    # ... 管理画面CSVの実表記を見てここに追記
}

def to_prefix(name: str) -> str | None:
    n = norm_name(name)
    for k, v in STORE_MAP.items():
        if norm_name(k) == n or norm_name(k) in n or n in norm_name(k):
            return v
    return None

def parse_csv_bytes(data: bytes, encodings=("utf-8-sig", "cp932", "utf-8")) -> list[dict]:
    """POSのCSV（UTF-8/Shift_JIS両対応）を辞書リストに。"""
    for enc in encodings:
        try:
            text = data.decode(enc)
            return list(csv.DictReader(io.StringIO(text)))
        except UnicodeDecodeError:
            continue
    raise ValueError("CSVの文字コードを判別できませんでした（utf-8-sig/cp932/utf-8で失敗）")

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
# POS_DIAG=1 のとき、スクショ・HTML・クリック候補・リンク・入力欄一覧を ./diag/ に保存する。
# artifact が取得できない環境でも導線を確定できるよう、要点は標準出力にも echo する
# （このリポジトリは private なのでジョブログは外部に出ない）。
DIAG_ECHO_MAX = 60  # ログに出す最大件数（多すぎるログを防ぐ）


def _uniq(seq):
    seen, out = set(), []
    for t in seq:
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def _echo(tag: str, label: str, items: list[str]):
    """診断結果の要点をジョブログへ。件数が多いときは先頭のみ。"""
    shown = items[:DIAG_ECHO_MAX]
    print(f"[diag:{tag}] {label} ({len(items)}件" + (f"、先頭{len(shown)}件を表示" if len(shown) < len(items) else "") + ")")
    for t in shown:
        print(f"    | {t}")


def diag_dump(page, tag: str, outdir: str = "diag"):
    import os as _os
    _os.makedirs(outdir, exist_ok=True)
    print(f"[diag:{tag}] URL = {page.url}")
    try:
        page.screenshot(path=f"{outdir}/{tag}.png", full_page=True)
    except Exception as e:
        print(f"[diag:{tag}] screenshot失敗: {e}")
    try:
        with open(f"{outdir}/{tag}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        print(f"[diag:{tag}] html失敗: {e}")

    # クリック候補（メニュー/ボタン/リンクのテキスト）
    try:
        items = _uniq(page.evaluate("""() => [...document.querySelectorAll('a,button,[role=button],[role=menuitem]')]
            .map(el => (el.innerText||el.getAttribute('aria-label')||'').trim().replace(/\s+/g,' '))
            .filter(t => t && t.length <= 40)"""))
        with open(f"{outdir}/{tag}_clickables.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(items))
        _echo(tag, "クリック候補", items)
    except Exception as e:
        print(f"[diag:{tag}] clickable列挙失敗: {e}")

    # リンク（text と href）— エクスポート画面のURLを直接特定するため
    try:
        links = _uniq(page.evaluate("""() => [...document.querySelectorAll('a[href]')]
            .map(el => ((el.innerText||'').trim().replace(/\s+/g,' ').slice(0,40)) + '\t' + el.getAttribute('href'))"""))
        with open(f"{outdir}/{tag}_links.tsv", "w", encoding="utf-8") as f:
            f.write("text\thref\n" + "\n".join(links))
        _echo(tag, "リンク(text→href)", links)
    except Exception as e:
        print(f"[diag:{tag}] link列挙失敗: {e}")

    # ボタンの棚卸し（disabled 状態つき）— 期間未指定でDLボタンが押せないケースを見分けるため
    try:
        btns = _uniq(page.evaluate("""() => [...document.querySelectorAll('button,[role=button],a')]
            .map(el => {
                const t = (el.innerText||el.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ').slice(0,30);
                if (!t) return '';
                const off = el.disabled || el.getAttribute('aria-disabled') === 'true'
                    || (el.className||'').toString().includes('disabled');
                const vis = el.offsetParent !== null || el.tagName === 'BODY';
                return t + '\\t' + (off ? 'disabled' : 'enabled') + '\\t' + (vis ? 'visible' : 'hidden');
            }).filter(Boolean)"""))
        with open(f"{outdir}/{tag}_buttons.tsv", "w", encoding="utf-8") as f:
            f.write("text\tstate\tvisible\n" + "\n".join(btns))
        _echo(tag, "ボタン(text/状態/表示)", btns)
    except Exception as e:
        print(f"[diag:{tag}] button列挙失敗: {e}")

    # 入力欄の棚卸し — 期間指定(年月/開始日/終了日)のセレクタを確定するのに必須。
    # value は入力済みの値（＝認証情報が入りうる）なので出さない。
    try:
        fields = _uniq(page.evaluate("""() => [...document.querySelectorAll('input,select,textarea')]
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
    with open(f"{outdir}/{tag}.csv", "wb") as f:
        f.write(data)
    rows = parse_csv_bytes(data)
    header = list(rows[0].keys()) if rows else []
    print(f"[diag:{tag}] CSV {len(rows)}行 / {len(header)}列")
    _echo(tag, "CSV列名", header)
    # 店舗名らしき列の値だけは STORE_MAP を埋めるのに要るので、その列のユニーク値を出す
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
