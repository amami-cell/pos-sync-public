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

# ---- 診断: ログイン後の画面を artifact に吐く（エクスポート導線をスクショ無しで確定するため）----
# 環境変数 POS_DIAG=1 のとき、ログイン直後の画面のスクショ・HTML・リンク一覧を
# ./diag/ に保存する。GitHub Actions で artifact として上げれば導線を確定できる。
def diag_dump(page, tag: str, outdir: str = "diag"):
    import os as _os
    _os.makedirs(outdir, exist_ok=True)
    try:
        page.screenshot(path=f"{outdir}/{tag}.png", full_page=True)
    except Exception as e:
        print(f"[diag] screenshot失敗 {tag}: {e}")
    try:
        html = page.content()
        with open(f"{outdir}/{tag}.html", "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        print(f"[diag] html失敗 {tag}: {e}")
    # 画面上のクリック候補（メニュー/ボタン/リンクのテキスト）を列挙
    try:
        items = page.evaluate("""() => [...document.querySelectorAll('a,button,[role=button],[role=menuitem]')]
            .map(el => (el.innerText||el.getAttribute('aria-label')||'').trim())
            .filter(t => t && t.length <= 30)""")
        seen, uniq = set(), []
        for t in items:
            if t not in seen:
                seen.add(t); uniq.append(t)
        with open(f"{outdir}/{tag}_clickables.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(uniq))
        print(f"[diag] {tag}: クリック候補 {len(uniq)}件を {outdir}/{tag}_clickables.txt へ")
    except Exception as e:
        print(f"[diag] clickable列挙失敗 {tag}: {e}")
    # 画面内リンク（href付き）— エクスポート画面のURLを直接特定するため
    try:
        links = page.evaluate("""() => [...document.querySelectorAll('a[href]')]
            .map(el => ((el.innerText||'').trim().slice(0,30)) + '\\t' + el.getAttribute('href'))""")
        seen, uniq = set(), []
        for t in links:
            if t not in seen:
                seen.add(t); uniq.append(t)
        with open(f"{outdir}/{tag}_links.tsv", "w", encoding="utf-8") as f:
            f.write("text\thref\n" + "\n".join(uniq))
        print(f"[diag] {tag}: リンク {len(uniq)}件を {outdir}/{tag}_links.tsv へ")
    except Exception as e:
        print(f"[diag] link列挙失敗 {tag}: {e}")

def is_diag() -> bool:
    return os.environ.get("POS_DIAG", "") in ("1", "true", "yes")
