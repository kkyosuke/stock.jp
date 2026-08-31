# 日本株運用・継続稼働準備 v0.1

- 目的: 夜1回の依頼を長期間、安全に繰り返せるかを機械的に確認する
- 対象: [夜間ワンショット運用 v0.1](nightly-operation-v0.1.md)

## 1. 初回チェック

~~~bash
.venv/bin/python scripts/operation_bootstrap.py check
~~~

`paper_go: true`になるまで継続PAPERを始めず、`live_go: true`になるまで実資金を使わない。PAPERは有効なポリシー、`HUMAN_ONLY`、保有または関心中の対象、checksum検証済みの日次Yahoo snapshot、存在・SHA-256・作成時検証を確認できる31日以内のバックアップを必須とする。EDINET/J-QuantsのキーがないPAPERは、AI実行内で会社IR、TDnet/JPX、EDINETの一次資料をWeb確認し、未完了ならその日の判断を閉じない。

`automatic_source_blockers`は公式APIによる自動化不足を別に表示する。LIVEではこの不足もすべてblockerになる。APIキーはファイルやチャットへ保存せず、必要な場合だけ環境変数へ設定する。暗号化バックアップには公開受信者文字列を`OPERATION_BACKUP_AGE_RECIPIENT`へ設定する。

このチェックではlaunchdや外部の定時タスクが実際に起動できるかまでは判定できない。平日18:00のcollectorログと、必要に応じてChatGPTデスクトップ、対象ローカルプロジェクト、平日18:30のリマインダーを別途確認する。

## 2. 20営業日の状態遷移試験

~~~bash
.venv/bin/python scripts/operation_smoke.py \
  --days 20 --start-date 2026-09-01
~~~

この試験は一時フォルダだけを使い、ネットワーク接続、実データ、実注文を使わない。20回の状態遷移を確認するだけで、LIVEゲートの「実データ20営業日連続」を満たさない。LIVE根拠には前向きに保存された20日分のYahoo snapshot、夜間run、欠損・重複監査を使う。

## 3. 実行漏れと古いロック

~~~bash
.venv/bin/python scripts/operation_watchdog.py check \
  --at 2026-09-01T21:00:00+09:00
~~~

- `OK`: 次回日時より前
- `DUE`: 次回日時を過ぎたが猶予内
- `RUNNING`: 正規の夜間実行が進行中
- `MISSED`: 次回日時から既定120分を超えて成功実行がない
- `STALE_RUN`: 未完了実行のleaseが切れている、またはleaseがない
- `NEEDS_FIRST_RUN`: まだ成功実行がない

`nightly_operation.py start`は開始前のwatchdog結果も返す。`MISSED`でも前回の成功カットオフから差分を再取得するため、確認範囲を飛ばさない。`STALE_RUN`は既存フォルダをrun tokenで再開し、新しい注文票を別に作らない。

## 4. バックアップ

非公開状態には数量、価格、資金ログが含まれる。バックアップも機密情報として扱う。推奨は`age`暗号化である。

~~~bash
.venv/bin/python scripts/operation_backup.py create \
  --at 2026-09-01T20:00:00+09:00 \
  --age-recipient 'age1...'
~~~

`PAPER`で暗号化環境を準備する前だけ、危険を理解した上で`--allow-plaintext`を明示できる。`LIVE`はこの例外を認めず、`age`と受信者指定が必須である。バックアップは`operations/private/backups/`に置かれGit追跡されない。端末故障に備える複製先は、利用者が管理する暗号化ストレージとする。

`OPERATION_BACKUP_AGE_RECIPIENT`が設定済みなら`--age-recipient`は省略できる。夜間作業計画には、最終バックアップから31日を超えた時点でバックアップタスクが自動追加される。受信者が未設定なら平文へ自動フォールバックせず、タスクを延期して利用者へ報告する。

作成時に全ファイルのSHA-256とサイズを検証する。月1回、およびルール・状態スキーマ変更前に作成する。

## 5. 復元訓練

まず検証する。

~~~bash
.venv/bin/python scripts/operation_backup.py verify \
  --archive operations/private/backups/operation-YYYYMMDDTHHMMSS+0900.zip
~~~

次に別フォルダへ展開する。

~~~bash
.venv/bin/python scripts/operation_backup.py restore \
  --archive operations/private/backups/operation-YYYYMMDDTHHMMSS+0900.zip \
  --destination operations/private/restores/drill-YYYYMMDD
~~~

復元コマンドは現行`operations/private/`へ上書きしない。展開した`state.json`、最新`handoff.json`、台帳件数、未照合注文を比較する。四半期ごと、およびLIVE昇格前に復元訓練を行い、確認結果をLIVEゲートの根拠へ記録する。

暗号化バックアップの`verify`と`restore`には秘密鍵ファイルを`--age-identity`で渡す。秘密鍵はリポジトリへ置かない。

## 6. CI

`.github/workflows/operation-tests.yml`はPRとmain更新時に、全単体テスト、Pythonコンパイル、スキル構成、20営業日シミュレーションを実行する。CIは公開fixtureだけを使い、APIキーや`operations/private/`をアップロードしない。

CI成功は実情報源の疎通や投資成績を保証しない。初回チェック、夜間レポートのデータ欠損、定期的な復元訓練を併用する。

## 7. 継続運用の最小周期

| 時期 | 実施内容 |
|---|---|
| 平日18:00 | Yahooで保有＋関心中の株価を100%取得 |
| 平日18:30 | `$japan-stock-operator`で夜間運用を1回実行 |
| 翌朝8:45〜8:55 | 昇格済みLIVEの`PROPOSED`注文だけ人間が確認 |
| 毎週 | `run-history.csv`の失敗・漏れ・未照合注文を確認 |
| 毎月 | JPX全銘柄のYahoo scan、候補集合見直し、暗号化バックアップ |
| 四半期 | 別フォルダへの復元訓練と戦略成績レビュー |
| ルール変更前 | バックアップ、20日試験、シャドー評価 |

重大データ不足、実行漏れ、復元不能、未照合注文が残る場合は、新しい注文票を増やさず問題を先に解消する。
