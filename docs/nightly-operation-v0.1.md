# 日本株・夜間ワンショット運用 v0.1

- 状態: `PAPER`運用の正規手順
- 実行時刻: 平日18:30（Asia/Tokyo）に1回
- 注文送信: 常に `HUMAN_ONLY`

## 結論

平日18:00に[Yahoo株価collector](market-data-operation-v0.1.md)が保有＋関心中を自動取得する。その後、利用者は夜に「今日の夜間運用を実行して」と1回依頼する。エージェントはsnapshotを検証し、前回の状態、公式情報源、世界情勢、期限到来レビュー、全対象の翌営業日アクション、必要な注文票、監査ログを保存する。

`PAPER`の注文票は仮想注文であり、証券会社へ送信しない。昇格条件を満たした`LIVE`でも夜間に作るのは`PROPOSED`注文票までである。翌朝8:45〜8:55に利用者が注文前チェックを行い、承認した注文だけを証券会社へ手入力する。

## 1. 1回の処理

1. 日次Yahoo snapshotのstatus、対象100%、鮮度、checksumを確認する。不合格なら価格判断を止める。
2. `nightly_operation.py start`でPAPER/LIVE readinessを再検証し、blockerがあればrun作成前に停止する。合格時だけ同時実行ロック、公式情報源、市場カレンダー、期限到来タスクを準備する。
3. `research-queue.json`と`work-plan.json`を一次資料で処理し、`global-risk.md`へ世界情勢と保有KPIへの伝播を保存する。
4. 保有銘柄と監視銘柄を漏れなく`next-day-actions.csv`へ記録する。
5. `BUY / ADD / REDUCE / SELL`なら、ルールID、一次資料ID、個別判断ログを揃えた後で`order_ticket.py propose`を使う。
6. `research-results.md`と`report.md`を完成させ、カバレッジを閉じる。
7. `nightly_operation.py finalize`を実行する。不足があれば完了は拒否される。
8. 成功なら結果と翌日アクションを返し、次回の平日18:30まで「次回夜まで待機」する。重大な情報欠損なら`fail`として成功カットオフを進めない。

## 2. 成果物

すべて`operations/private/runs/YYYY-MM-DD/`に保存する。

| ファイル | 正本となる内容 |
|---|---|
| `work-plan.json` | 今夜行う日次・週次・月次・四半期・ルールレビューと完了状態 |
| `research-queue.json` | 公式APIから発生した一次資料確認タスク |
| `research-results.md` | 当夜の調査結果。事実・計算・判断を分けた要約 |
| `global-risk.md` | 為替・金利・資源・政策・地政学の事実と保有KPIへの伝播 |
| `next-day-actions.csv` | 全保有・全監視銘柄の翌営業日アクション |
| `orders.csv` | アクションに対応する仮想または人間確認待ちの注文票 |
| `sources.csv` | 公開日時、取得日時、URLを持つ根拠 |
| `coverage.json` | 開始時点の対象と確認済み範囲 |
| `report.md` | 利用者向け結果と例外、人間が行うこと |
| `handoff.json` | 未完了レビュー、未照合注文、次回日時 |

チャット履歴は状態の正本にしない。次回は必ず`state.json`と直前の`handoff.json`から再開する。

## 3. 開始コマンド

~~~bash
.venv/bin/python scripts/nightly_operation.py start \
  --at 2026-08-31T18:35:00+09:00 \
  --cutoff 2026-08-31T18:30:00+09:00
~~~

返された`run_token`は終了まで保持する。`locked`なら別の実行を開始しない。`completed`ならその日は既に完了しているため重複作業をしない。

## 4. 注文票の設定

注文票は次のすべてを満たす場合だけ作成できる。

- 運用ポリシーが有効で、`PAPER`または全昇格条件を満たす`LIVE`
- J-Quants市場カレンダーで翌取引日が確定
- 重大なデータ欠損がない
- 調査キューが全件`COMPLETED`。`DEFERRED`を1件でも含む場合、その夜の売買アクションは`WAIT`へ変更する
- 対応する翌日アクションにルールIDと一次資料IDがある
- 同じ銘柄に未照合注文がない

~~~bash
.venv/bin/python scripts/order_ticket.py propose \
  --run-id 2026-08-31 --run-token '<startのrun_token>' \
  --action-id 2026-08-31-1234-next \
  --code 1234 --company '会社名' \
  --side BUY --action BUY --rule-ids 'E-1;E-2' \
  --trade-date 2026-09-01 --limit-price 1000 --quantity 100 \
  --position-pct 1.0 --participation-cap-pct 5.0 \
  --valid-until 2026-09-01T15:30:00+09:00 \
  --decision-id operations/private/decisions/2026-08-31-1234.md \
  --at 2026-08-31T19:00:00+09:00
~~~

このコマンドは`orders.csv`、`next-day-actions.csv`、`handoff.json`、`trade-event-ledger.csv`を整合的に更新するが、証券会社へ通信しない。同じ引数で再実行しても同じ注文票を重複作成しない。

## 5. 完了と待機

~~~bash
.venv/bin/python scripts/nightly_operation.py finalize \
  --run-id 2026-08-31 --run-token '<startのrun_token>' \
  --completed-at 2026-08-31T19:05:00+09:00 \
  --source-cutoff 2026-08-31T18:30:00+09:00 \
  --price-date 2026-08-31 \
  --summary '調査完了。翌営業日のアクションを設定済み'
~~~

成功すると`next_run_at_jst`が次の平日18:30へ設定される。それまでは自動再調査や注文条件の追い上げをしない。例外は、昇格済み`LIVE`注文について翌朝に利用者が行う注文前確認だけである。

## 6. 定時タスク

[OpenAI Scheduled tasks公式手順](https://learn.chatgpt.com/docs/automations)に従い、平日18:30、`Asia/Tokyo`で同じローカルプロジェクトを対象にする。登録する依頼文は次の1行でよい。

~~~text
$japan-stock-operator を使って、今日の夜間運用を最後まで実行してください。
~~~

ローカルファイルへアクセスする実行では、対象コンピューターとデスクトップアプリが利用可能である必要がある。実行結果が`FAILED`または`locked`の場合は、完了したとみなさずレポートの人間作業を確認する。

## 7. 公式情報源

- [J-Quants API V2](https://www.jpx.co.jp/corporate/news/news-releases/6020/20260119.html)
- [J-Quants公式Pythonクライアント](https://github.com/J-Quants/jquants-api-client-python)
- [EDINET APIキー案内](https://disclosure2.edinet-fsa.go.jp/week0020.aspx)
- [TDnet API](https://www.jpx.co.jp/markets/paid-info-listing/tdnet/02.html)

APIの成功は企業IRとJPX個別確認の代替ではない。売買判断に使う事実は一次資料へ結び付ける。

バックアップ、実行漏れ検知、初回診断、20営業日試験は[継続稼働準備 v0.1](operation-readiness-v0.1.md)に従う。
