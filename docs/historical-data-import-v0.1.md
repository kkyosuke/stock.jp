# 履歴日足アーカイブ仕様 v0.1

- 発効日: 2026年8月31日
- 保存先: Git管理外の`operations/private/historical-replay/daily-prices/`
- CSV契約: PR #14の`data/daily-prices/`と同一
- 用途: 2025–2026年ルール履歴再生、欠損監査
- 禁止: 株探スクレイピング、推測補完、認証情報や契約データの公開Git追加

## 1. 保存形式

1営業日を`YYYY/YYYY-MM-DD.csv`へ保存する。列名、列順、日付形式は次で固定する。

```csv
日付,銘柄コード,銘柄名,市場・商品区分,33業種区分,始値,高値,安値,終値,前日比,前日比％,売買高(株),取得状態
2026-08-31,1234,会社名,グロース（内国株式）,情報・通信業,4740,4875,4530,4650,-160,-3.33,1738100,OK
```

ユーザー向け表示を`26/08/31`にする場合も、正本CSVではPR #14に合わせて`2026-08-31`とする。各ファイルは当日に上場していた内国株を1コード1行で持ち、現在の銘柄一覧を過去へ遡及適用しない。

## 2. 状態と計算

- `OK`: OHLCと出来高を取得済み。高値・安値の大小関係と非負の整数出来高を満たす
- `NO_QUOTE`: 当日の対象銘柄だが有効な日足がない。価格列は空欄
- `FETCH_ERROR`: 取得処理が失敗し、銘柄単位の結果を確定できない。価格列は空欄
- `前日比`: 同一銘柄の直前取引日終値との差
- `前日比％`: `前日比 / 直前取引日終値 × 100`

期間の先頭、上場初日、直前終値を確定できない日は、`OK`でも前日比2列を空欄にできる。株式分割・併合をまたぐ損益再生では、表示用の未調整OHLCだけを使わず、privateに保存した調整係数とコーポレートアクション証跡を併用する。

バックテスト用の補助データは`operations/private/historical-replay/supplemental/`へ分離する。

- `adjusted-daily-prices.csv.gz`: 調整後OHLC、調整後出来高、調整係数
- `market-cap-daily.csv.gz`: 日次時価総額。単位は百万円
- `corporate-actions.csv.gz`: 権利落種類と調整係数

## 3. 検証

取得元固有の一時コードを残さず、次の共通検証器でアーカイブを確認する。

```bash
.venv/bin/python scripts/validate_daily_price_archive.py \
  --archive operations/private/historical-replay/daily-prices \
  --manifest operations/private/historical-replay/daily-price-manifest.json \
  --source-label 'licensed first-party historical source'
```

検証器は全CSVについて、列順、ファイル名と日付、銘柄コード重複、取得状態、数値、OHLC、出来高、SHA-256を確認する。公開可能な件数・期間・ハッシュだけを`data/historical-replay/price-coverage-2025-2026.json`へ複製し、契約データの行自体はPRへ含めない。

`operations/private/`はGit管理外なので、セッションworktreeを削除する前に利用者管理の暗号化ストレージへバックアップする。PRのマージだけではprivateアーカイブは別worktreeや別端末へ複製されない。

## 4. PR #14データとの境界

`data/daily-prices/`はPR #14が作る現行銘柄中心の候補探索データであり、履歴再生のpoint-in-time母集団とは混ぜない。列契約は共通でも、取得元、対象母集団、公式性、調整方法が異なるため、マニフェストと保存ルートを分ける。

不足期間を別の取得元で補う場合は、日付ごとの取得元と公式性をマニフェストへ明記し、重複日は優先順位を事前固定する。株探による補完は行わない。
