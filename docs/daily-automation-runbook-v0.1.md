# 日本株運用・日次自動実行手順書 v0.1

- 状態: `PAPER`運用用。`LIVE`昇格は[運用ガバナンス](operation-governance-v0.1.md)に従う
- 作成日: 2026年8月31日
- 基準ルール: [運用ガバナンス v0.1](operation-governance-v0.1.md)、[実運用手順書 v0.1](live-operation-playbook-v0.1.md)。初期状態はv0.2をベースライン、v0.3をシャドー比較とする
- 実行単位: 平日18:30（Asia/Tokyo）の1回

## 1. 結論

ChatGPTデスクトップの同じチャットに、ローカルプロジェクトを対象とする定時タスクを1つだけ作る。平日18:30に `$japan-stock-operator` を明示して実行すれば、前回からの差分確認、期限が来た週次・月次・四半期レビュー、全対象の翌営業日アクション、注文候補、ログ保存、次回への引き継ぎを1回にまとめられる。具体的な正規フローは[夜間ワンショット運用 v0.1](nightly-operation-v0.1.md)に従う。

日次タスクが行うのは情報収集、ルール判定、注文案の作成までである。証券会社への注文送信は行わない。`PAPER`では注文票を `PAPER_PROPOSED` とし、実際には入力しない。`LIVE`昇格後だけ、利用者が翌営業日8:45〜8:55に約10分の注文前チェックを行い、承認した注文を手入力する。18:30以後に重要開示、売買停止、取引所・証券会社障害が発生し得るため、この確認は省略しない。

## 2. 最も楽にするための設計

### 2.1 1つの日次タスクへ集約する

- 毎日: 前回カットオフ後の公式開示と即時撤退条件を差分確認
- 週次: 期限到来時だけ、見落とし、流動性、集中、決算予定を確認
- 月次: 月末値が確定した時だけ、候補抽出、価格・時間条件、正式な`MRS-v0.1`を更新
- 四半期: 新しい四半期開示が出た銘柄だけ、5営業日以内の再採点をキューへ追加
- 本決算: 新しい本決算が出た銘柄だけ、10営業日以内の全面更新をキューへ追加

毎日すべての銘柄を100点で再採点しない。売買判断が発生した銘柄、またはレビュー期限が来た銘柄だけ個別判断ログを作る。それ以外は日次レポートに `NO-ACTION` の件数と例外だけを残す。

### 2.2 引き継ぎをファイルで固定する

チャット履歴だけを状態管理に使わない。Git管理外の `operations/private/` を正本とし、次回実行は必ず `state.json` と前回の `handoff.json` から再開する。

| ファイル | 役割 | 更新者 |
|---|---|---|
| `state.json` | 最終成功日時、開示カットオフ、未完了レビュー、未処理注文 | 日次タスク |
| `run-history.csv` | 1実行1行の成功・失敗履歴 | 日次タスク |
| `watchlist.csv` | 監視対象と次回の全面レビュー日 | 日次タスク・利用者 |
| `portfolio-register.csv` | 保有状態と次回レビュー日 | 利用者が約定反映、日次タスクがレビュー情報更新 |
| `runs/YYYY-MM-DD/report.md` | その日の例外、判断、必要な人間作業 | 日次タスク |
| `runs/YYYY-MM-DD/orders.csv` | 翌営業日の注文候補と実際の約定 | 日次タスク・利用者 |
| `runs/YYYY-MM-DD/sources.csv` | 公開・取得日時付きの根拠URL | 日次タスク |
| `runs/YYYY-MM-DD/coverage.json` | 開始時の対象と、銘柄・情報源ごとの確認完了 | 日次タスク |
| `runs/YYYY-MM-DD/lease.json` | 同時実行を防ぐ6時間のrun token | 日次タスク |
| `runs/YYYY-MM-DD/provider-health.json` | 公式APIごとの成功・不足・取得件数 | 日次タスク |
| `runs/YYYY-MM-DD/research-queue.json` | 当夜に読む一次資料と手動確認のキュー | 日次タスク |
| `runs/YYYY-MM-DD/work-plan.json` | 当夜の期限到来タスクと完了状態 | 日次タスク |
| `runs/YYYY-MM-DD/research-results.md` | 当夜の調査結果 | 日次タスク |
| `runs/YYYY-MM-DD/next-day-actions.csv` | 全対象の翌営業日アクション | 日次タスク |
| `runs/YYYY-MM-DD/raw-sources/` | APIの生レスポンス。private限定 | 日次タスク |
| `runs/YYYY-MM-DD/handoff.json` | 実行状態と次回キュー | 日次タスク |
| `runs/YYYY-MM-DD/pretrade-check.md` | 寄り前の取消・承認チェック | 利用者 |
| `decisions/*.md` | 売買判断・期限到来レビューの詳細 | 日次タスク |

銘柄・取引・資金の詳しい正本と移行方法は[状態・台帳仕様 v0.2](operation-state-v0.2.md)に従う。とくに、部分利確済みフラグ、`S-B`連続数、再購入禁止、回収原資、業種上限、コーポレートアクションはチャットから推測しない。

失敗時の厳密な完了条件と同時実行防止は[完了条件・重複防止 v0.1](run-integrity-v0.1.md)に従う。

公式APIと一次資料キューは[公式情報源スキャン v0.1](official-source-scan-v0.1.md)に従う。

正確な数量、価格、資産、税、証券会社注文IDはこの非公開領域だけに置く。認証情報、パスワード、APIキー、口座番号はここにも保存しない。

### 2.3 失敗時に確認済み範囲を偽らない

重要な対象を取得できなかった実行は `failed` とし、`last_disclosure_cutoff_jst` を進めない。作成途中のレポートとデータ不足は残す。再実行は既存の日付フォルダを読み、二重の注文候補を作らず続きから処理する。

## 3. 初回セットアップ

マージ後、実際に定時タスクを動かすローカルプロジェクトで一度だけ実行する。

~~~bash
.venv/bin/python scripts/daily_operation.py init
.venv/bin/python scripts/operation_state.py validate
.venv/bin/python scripts/operation_bootstrap.py check
.venv/bin/python scripts/operation_smoke.py --days 20 --start-date 2026-09-01
~~~

次に、次の2ファイルへ対象を登録する。

1. `operations/private/portfolio-register.csv`: 現在の保有銘柄。未保有ならヘッダーだけでよい
2. `operations/private/watchlist.csv`: 監視する候補。最初は手動で選んだ少数から始めてよい

その後、下記の定時タスク用プロンプトをチャットで1回手動実行し、対象件数、根拠URL、注文候補、引き継ぎが期待どおりか確認する。最初の数回は必ず結果をレビューする。

## 4. 定時タスクの設定

[OpenAIのScheduled tasks公式手順](https://learn.chatgpt.com/docs/automations)に従い、次の設定で作成する。

- 場所: ChatGPTデスクトップ
- 会話: この運用を続けている同じチャット
- 対象: このリポジトリのローカルプロジェクト
- 実行環境: `Local`。分離worktreeは使わない
- 日時: 月〜金の18:30
- タイムゾーン: `Asia/Tokyo`
- 繰り返し: `RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=18;BYMINUTE=30`

ローカルファイルを扱うため、実行時はコンピューターの電源を入れ、ChatGPTデスクトップを起動しておく。Web上だけの定時タスクではローカルフォルダーを直接更新できない。同じ非公開ログを毎回引き継ぐため、毎回別のworktreeを作る設定にはしない。

### 定時タスクへ登録するプロンプト

~~~text
$japan-stock-operator を使って、今日の夜間運用を最後まで実行してください。
~~~

スキルは`nightly_operation.py start`から開始し、全調査・全対象アクション・必要な注文票を保存して`finalize`する。成功後は次の夜まで待機する。証券会社への注文送信は行わない。

## 5. 1回の実行フロー

1. `nightly_operation.py start` を実行し、返された`run_token`を保持する。同日の実行が`locked`なら別実行を開始しない
2. `operation-policy.json`、`state.json`、保有、取引・資金台帳、再購入禁止、監視、未処理注文、前回引き継ぎを読み、`operation_state.py validate`を通す
3. 今回のJST情報カットオフを宣言する
4. `official_source_scan.py`でTDnet、EDINET、決算サマリー、日足を差分取得し、生成されたキューで会社IRとJPXを確認する
5. `S-A`を最優先し、注文候補に新情報があれば `WAIT` または取消候補にする
6. 期限到来キューだけ週次・月次・四半期・本決算レビューする
7. 判断根拠を `sources.csv` と日次レポートへ記録し、`coverage.json`の確認済み対象を更新する
8. 売買判断または期限到来レビューだけ個別判断ログを作る
9. 全対象の翌営業日アクションを`next-day-actions.csv`へ記録し、売買なら`order_ticket.py propose`で、`PAPER`は`PAPER_PROPOSED`、昇格済み`LIVE`は`PROPOSED`の注文票を設定する
10. `handoff.json` の未完了レビュー、未処理注文、データ不足、次回日時を更新する
11. 全必須対象を確認できた場合だけ `nightly_operation.py finalize` で状態を進め、次の夜まで待機する
12. 重要な取得失敗があれば `fail` とし、次回は前回成功時のカットオフから再確認する

土日・祝日明けも前回成功時からの差分を見るため、休日中の開示を取りこぼさない。平日の非取引日や価格未更新日は無理に売買判断を作らず、開示確認だけを行って価格基準日を明記する。

## 6. 実行コマンド

日次タスクは、現在日時と情報カットオフをUTCオフセット付きで一度だけ渡す。公式情報源スキャンもこの開始コマンドに含まれる。

~~~bash
.venv/bin/python scripts/nightly_operation.py start \
  --at 2026-08-31T18:35:00+09:00 \
  --cutoff 2026-08-31T18:30:00+09:00 \
~~~

成功時:

~~~bash
.venv/bin/python scripts/nightly_operation.py finalize \
  --run-id 2026-08-31 \
  --run-token '<prepareが返したrun_token>' \
  --completed-at 2026-08-31T19:05:00+09:00 \
  --source-cutoff 2026-08-31T18:30:00+09:00 \
  --price-date 2026-08-31 \
  --summary "必須対象の確認完了。翌営業日の注文候補1件"
~~~

重要な取得失敗時:

~~~bash
.venv/bin/python scripts/nightly_operation.py fail \
  --run-id 2026-08-31 \
  --run-token '<prepareが返したrun_token>' \
  --completed-at 2026-08-31T19:05:00+09:00 \
  --summary "TDnet取得不能。カットオフを更新せず次回再確認"
~~~

現在の最終成功状態は次で確認する。

~~~bash
.venv/bin/python scripts/nightly_operation.py status
~~~

## 7. 注文日の最小作業

注文候補が0件なら、利用者の作業は日次レポートの確認だけである。候補がある場合は、翌営業日8:45〜8:55に次だけ行う。

1. `pretrade-check.md` を開く
2. 前回カットオフ後の重要開示、売買停止、分割、気配、障害を確認する
3. 各候補を `SUBMIT / CANCEL / WAIT` のどれかにする
4. `SUBMIT` だけ証券会社へ当日限りの指値で手入力する
5. 約定後に `orders.csv` の約定数量・価格・費用と `portfolio-register.csv` を更新する

成行化、午後の指値引き上げ、未承認注文の追加はしない。予定が変わった場合は「何もしない」を安全側の既定値とする。

## 8. 定期レビュー

最初の4週間は、週末に5分だけ `run-history.csv` を確認する。

- 実行漏れ、失敗、データ不足が続いていないか
- 同じ開示を重複処理していないか
- 注文候補と個別判断ログのルールIDが一致するか
- `PAPER_PROPOSED` が証券会社へ入力されていないか、`PROPOSED` が人間の承認なしに実行済み扱いになっていないか
- ログ量が多すぎる場合、`NO-ACTION`銘柄の個別ログを作っていないか

運用ルール自体の閾値は日次運用の都合で変更しない。手順の不具合と投資仮説の成績を分けてレビューする。

月次バックアップ、四半期の復元訓練、実行漏れ検知、CIについては[継続稼働準備 v0.1](operation-readiness-v0.1.md)に従う。
