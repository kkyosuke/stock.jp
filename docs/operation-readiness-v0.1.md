# 日本株運用・継続稼働準備 v0.1

- 目的: 夜1回の依頼を長期間、安全に繰り返せるかを機械的に確認する
- 対象: [夜間ワンショット運用 v0.1](nightly-operation-v0.1.md)

## 1. 初回チェック

~~~bash
.venv/bin/python scripts/operation_bootstrap.py check
~~~

`paper_go: true`になるまでPAPERを始めず、`live_go: true`になるまでLIVEへ昇格しない。PAPERは状態スキーマ、運用ポリシー、マージ済み株価archive、31日以内の検証済みバックアップを必須とする。active対象0件は許可し、`GLOBAL / NO-ACTION`と初回全市場候補レビューだけを行う。EDINET APIキーがないPAPERは手動一次資料fallbackを許すが、確認できない判断は`WAIT`または`fail`にする。APIキーはファイルやチャットへ保存せず、実行環境の`EDINET_API_KEY`へ設定する。暗号化バックアップを夜間運用内で実行するには、公開受信者文字列を`OPERATION_BACKUP_AGE_RECIPIENT`へ設定する。

株価archiveは`data/daily-prices/latest.json`と対応CSVを使い、SHA-256、全市場98%以上、active対象100%、未来日でないこと、7日以内の鮮度を検証する。Yahoo Financeは非公式の二次データなので、注文判断には公式価格・コーポレートアクションと会社一次資料を別途確認する。

このチェックでは外部の定時タスクが実際に起動できるかまでは判定できない。ChatGPTデスクトップ、対象ローカルプロジェクト、平日18:30のスケジュールが利用可能であることを別途確認する。

## 2. 20営業日の状態遷移試験

~~~bash
.venv/bin/python scripts/operation_smoke.py \
  --days 20 --start-date 2026-09-01
~~~

この試験は一時フォルダだけを使い、ネットワーク接続、実データ、実注文を使わない。20回の開始・成果物検証・完了・次回引き継ぎを連続実行し、終了後に一時状態を削除する。`completed_runs: 20`、`consecutive_successful_runs: 20`、`broker_orders_submitted: 0`を確認する。これは実データ20営業日LIVEゲートの代替ではない。

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

macOSでは一度だけ`age`を準備し、秘密鍵をリポジトリ外へ作る。秘密鍵をGit、チャット、運用ログへ貼らない。

~~~bash
brew install age
age-keygen -o /利用者が管理する安全な場所/stock-jp-backup-key.txt
age-keygen -y /利用者が管理する安全な場所/stock-jp-backup-key.txt
~~~

最後のコマンドが表示する`age1...`公開鍵を次の`--age-recipient`へ渡す。

~~~bash
.venv/bin/python scripts/operation_backup.py create \
  --at 2026-09-01T20:00:00+09:00 \
  --age-recipient 'age1...'
~~~

成功時に`status: CREATED`、`encrypted: true`、`verified_before_encryption: true`、`sha256`が表示され、`state.json`へ同じ証拠が記録される。続けて出力されたarchive pathを秘密鍵で検証する。

~~~bash
.venv/bin/python scripts/operation_backup.py verify \
  --archive operations/private/backups/operation-YYYYMMDDTHHMMSS+0900.zip.age \
  --age-identity /利用者が管理する安全な場所/stock-jp-backup-key.txt
~~~

`valid: true`を確認した後に`operation_bootstrap.py check`を再実行する。現在の環境には`age`がないため、インストールと鍵の保管場所は利用者側の準備が必要である。

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
| 平日18:30 | `$japan-stock-operator`で夜間運用を1回実行 |
| 翌朝8:45〜8:55 | 昇格済みLIVEの`PROPOSED`注文だけ人間が確認 |
| 毎週 | `run-history.csv`の失敗・漏れ・未照合注文を確認 |
| 毎月 | 暗号化バックアップを作成・検証 |
| 四半期 | 別フォルダへの復元訓練と戦略成績レビュー |
| ルール変更前 | バックアップ、20日試験、シャドー評価 |

重大データ不足、実行漏れ、復元不能、未照合注文が残る場合は、新しい注文票を増やさず問題を先に解消する。

## 8. LIVE昇格の時間要件

履歴再生は前向き運用の不具合を検出できないため、point-in-time全母集団の履歴再生と最低12か月PAPERを別々の必須ゲートとする。さらに実データ20営業日連続で重大な取得漏れ・重複注文0件、公式情報源coverage、暗号化backup/restore、個人の損失許容・税・証券会社仕様、注文可能時間、利用者の明示承認をすべて必要とする。コード実行だけで最低12か月の経過や本人確認を代替しない。
