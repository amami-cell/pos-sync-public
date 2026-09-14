# POS連携（ダイニー / 新Uレジ）— B案＋A案の良いとこ取り

FWに乗らないPOS（ダイニー・新Uレジ）の売上/原価を、infomart自動化と同じ流れで
Googleスプレッドに取り込む。**Playwrightはログイン→「公式CSVエクスポート」までを自動化し、
画面の数字はスクレイプせず、ダウンロードした正規CSVを取り込む**（＝A案の安定＋B案の自動化）。
API/トークンがあるPOSはPlaywright不要でAPI直取りに差し替え可。

## 構成
- `pos_common.py` … 共通スキーマ・CSVパース・シート書込（重複排除つき）
- `dinii_sync.py` / `uleji_sync.py` … 各POSのログイン→CSVエクスポート→正規化→書込
- `.github/workflows/pos_sync.yml` … 毎月自動実行（締め後）

出力スキーマ（既存FW→シートと統一）：
`年月, 店舗名, 売上, フード原価, ドリンク原価, 客数, 備考, 取込日時, POS`

## セットアップ（credはSecretsに。チャット/コードに直書きしない）
GitHub → リポジトリ → Settings → Secrets and variables → Actions に登録：
- ダイニー（2欄ログイン）
  - `DINII_USER` … ログイン用**メールアドレス**
  - `DINII_PASS` … パスワード
  - `DINII_LOGIN_URL`（任意。既定 `https://dashboard.self.dinii.jp/`）
- 新Uレジ／USENレジ（**3欄ログイン**）
  - `ULEJI_COMPANY` … **企業コード**
  - `ULEJI_USER` … **担当者コード**
  - `ULEJI_PASS` … パスワード
  - `ULEJI_LOGIN_URL`（任意。既定 `https://pos.usen-regi.com/cms/login/init`）
- `TARGET_SHEET_ID`（書込先スプレッドID）と `GCP_SA_JSON`（サービスアカウントのJSON丸ごと）
  ※ infomart-bot SAはDrive容量枯渇の既知課題。書込先は個人アカ所有シートにSAを共有編集者で追加するのが安全。
  ※ SAを使わない場合は `TARGET_SHEET_ID` 未設定でローカルCSV出力に自動フォールバック（既存GAS取込運用向け）。

## 私に共有してほしいもの（selectorを実画面に合わせて確定させるため）
POSごとに以下のいずれか：
1. ログイン画面／売上レポート画面／CSVエクスポートボタンの**スクショ**（要素が見える程度）
2. できれば各画面の**HTML**（DevTools→対象要素を右クリック→Copy→outerHTML、またはページ保存）
3. **エクスポート済みCSVのサンプル1本**（ヘッダ行が分かればベスト。数値はダミーで可）
4. どの店舗がどちらのPOSか（店舗→POS対応）

→ これで `TODO` のログインセレクタ・エクスポート導線・CSV列マッピング（`normalize`）を確定します。

### 診断モード（スクショ無しでエクスポート導線を確定する）
ログイン欄は確定済み。残りの「売上レポート→CSV出力」の導線は、実行環境から実画面を出力させて詰める。
1. Actions → このワークフロー → **Run workflow** で `diag` に `1` を入れて実行。
2. ログイン直後の画面のスクショ・HTML・クリック候補一覧が **artifact `pos-diag`** に上がる。
3. その artifact を私（Claude）に渡せば、エクスポート導線と期間指定を確定してTODOを埋める。
   （＝管理画面のDevToolsを触らずに済む）
※ 診断モードはログインするだけで売上取得・シート書込は行わない（安全に導線だけ確認）。

## ローカル実行（PowerShell運用メモ）
- `&&` は使わない（`;` で連結）。日本語文字列を渡す時はbase64推奨。コマンドは1つずつ。
```
pip install playwright gspread google-auth pandas ; python -m playwright install chromium
$env:DINII_LOGIN_URL="..." ; $env:DINII_USER="..." ; $env:DINII_PASS="..."
python dinii_sync.py 2026-08
```

## 取り込み後
このシート（`POS売上`タブ等）を店長会サマリー生成時に読み、FW欠損店（ダイニー/Uレジ店）の
売上・原価を補完する。理論原価が未取込だった店（ひよこ・ちゃーちゃん・たいだい 等）の
不明ロス見かけ肥大も、これで実態に是正される。
