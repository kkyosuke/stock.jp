# 実運用開始 readiness review（2026-09-01、修正後）

## 結論

- 実資金`LIVE`: **NO-GO**。12か月PAPER、point-in-time全母集団、実データ20営業日、公式情報源、個人条件、明示承認が未完了である。
- 継続`PAPER`: **コードは開始可能な状態へ修正済み、現在のprivate設定はNO-GO**。`operation_bootstrap.py check`の`paper_blockers`を0件にすれば開始できる。
- 証券会社への通信: 常に`HUMAN_ONLY`。PAPER注文は`PAPER_PROPOSED`で、実注文しない。

現時点のprivate状態では、保有・関心中0件と検証済みバックアップ不足がPAPER blockerである。月次全市場scanは成功済みなので、AIがscreenから一次資料確認対象を選び、`watchlist.csv`へ昇格させた後に日次snapshotとバックアップを作ればよい。

## 目標との適合

| 目標 | 修正後 | 内容 |
|---|---|---|
| 毎日の株価を自動収集 | **PASS（実装・実API）** | 非AI collectorを分離。保有＋関心中を平日18:00に100%取得するlaunchd定義を生成できる。 |
| 全体監視 | **PASS（実API）** | 月次だけJPX内国株式全体を取得し、機械可読screenを作る。 |
| 人がAIへ一回指示 | **PASS** | 保存済みsnapshotを確認後、夜間処理、明日のアクション、今日のログを一巡する。 |
| 明日の動きと今日のログ | **PASS** | `next-day-actions.csv`、`report.md`、`research-results.md`、`handoff.json`を必須検証する。 |
| ニュース・世界情勢 | **PASS（必須タスク化）** | `global-risk.md`へ為替・金利・資源・政策・地政学を事実→KPI伝播→判断で保存する。未完成ならfinalizeできない。 |
| 長期・非デイトレ | **PASS（現行戦略範囲）** | 日次は例外、全体抽出と価格判断は月次、企業進捗は四半期。v0.2の3年期限は維持する。 |
| ピンポイント注文不可 | **PAPERは問題なし／LIVEはNO-GO** | 現行v0.2は翌営業日寄り前確認を要求する。成行へ勝手に変更せず、別執行版の検証まではLIVEにしない。 |

## 実装した修正

### 株価collector

- `scripts/yahoo_price_collector.py`
  - `daily`: portfolio＋active watchlistを1銘柄chart APIでOHLCV取得。100%成功必須。
  - `monthly`: JPX月次一覧から内国株式全体を取得し、Yahooの10銘柄batchで終値系列を取得。99%以上かつactive対象100%を必須。
  - query1/query2切替、3回再試行、TLS CA bundle、future/stale/zero-volume、欠損、checksumを検証。
  - `(price_date, code)`で履歴を重複排除し、保有の`last_close`、倍率、`MA20`、`highest_ma20`、`DD20`を更新。
- 失敗時は`BLOCKED`とし、成功watermarkを進めない。
- 日次snapshotへ対象銘柄集合を固定し、現在の保有＋active watchlistと完全一致しなければ夜間runを開始しない。同日再実行は別attemptへ保存し、成功証跡を上書きしない。

### 判断フロー

- J-Quants日足をYahoo日次snapshotへ置換。J-Quants/EDINETキーがないPAPERは、AIが会社IR、TDnet/JPX、EDINET一次資料をWeb確認し、証拠を残すまで判断を閉じない。
- 日次の世界情勢タスクと`global-risk.md`を追加。テンプレート状態のままではfinalizeを拒否する。
- 初回開示lookbackを1日から7日に変更。
- `paper_go`と`live_go`を別判定し、blockerを機械的に列挙する。
- `nightly_operation.py start`もreadinessを再検証し、空母集団、snapshot不一致、31日超・消失・checksum不一致のバックアップがあればrun作成前に停止する。
- 旧`source-config.json`はバックアップ後にYahoo構成へ移行する。

### 自動実行

- `render_price_schedule.py`がworkspace内に安全なlaunchd定義を生成する。
- 平日18:00は株価だけを自動収集し、AIはその後の人間の一回指示で起動する。
- Scheduled taskを使う場合は、PCとデスクトップアプリが利用可能で、local projectへの書込・network許可が必要である。

## 実データ検証

### Yahoo実API

- 日次chart: `4477 BASE`、64行、最新2026-08-31、coverage 100%、API error 0。
- 3銘柄batch: `1301 / 4477 / 7203`、62行、API error 0。
- 月次50銘柄: 50/50、screen 50行、API error 0。
- JPX実ファイル: 内国株式3,713銘柄を認識。
- 月次全市場: 応答3,713/3,713、鮮度合格3,704/3,713（99.76%）、API error 0、screen 3,708行、所要約96秒。
  - 9銘柄は最終価格が7日超前で`stale_codes`へ記録。
  - 5銘柄は20観測未満のためscreen対象外。
  - 月次batchは過去出来高を返さないため、流動性は未確認として明示し、active昇格後の日次OHLCVで確認する。

### 回帰検証

- 単体・統合テスト: **93件すべて成功**。
- `python -m compileall -q scripts tests`: 成功。
- `git diff --check`: 成功。
- 20営業日オフライン状態遷移: 20完了、broker submission 0。

オフライン20日試験はLIVEゲートの実データ20営業日ではない。Yahoo実データを使う前向き20日分のsnapshotと夜間runを別に蓄積する。

## PAPERをGOにする残作業

`operation_bootstrap.py check`の`paper_blockers`を0件にする。

1. 月次screenをAIが一次資料で確認し、少数の関心銘柄をactive watchlistへ登録する。利用者が全銘柄を手入力する必要はない。
2. `.venv/bin/python scripts/yahoo_price_collector.py collect --scope daily`を成功させる。
3. PAPER用バックアップを作成・verifyする。可能なら最初から`age`暗号化を使う。
4. PRマージ後の永続checkoutでlaunchd定義を生成・登録し、翌日に自動実行ログを確認する。
5. 人が「今日のタスクを実行して」と1回依頼し、一次資料、世界情勢、全アクション、ログをfinalizeする。

公式APIキーはPAPERの価格取得には不要になった。キーがない場合、公式開示の確認はAIの一次資料Web調査が毎回必須である。取得できない日は`WAIT`または`fail`にする。

## LIVEをGOにする追加条件

| 条件 | 実施だけで可能か | 現状 |
|---|---|---|
| point-in-time全母集団 | データ契約・履歴調達が必要 | 未着手。現在のJPX月次一覧とYahooだけでは過去上場廃止を完全復元できない。 |
| 最低12か月PAPER | 時間経過が必要 | 短縮不可。前向きに蓄積する。 |
| 実データ20営業日、重大漏れ・重複0 | 自動監査可能 | collectorとrunを20営業日動かした後に判定する。 |
| 公式情報源カバレッジ | API契約または毎回の網羅証拠が必要 | PAPERは手動fallback可、LIVEは自動化・契約を推奨。 |
| backup/restore | 実施可能 | `age`、秘密鍵管理、別媒体が必要。 |
| 損失許容・税・証券会社仕様 | 利用者本人の確認が必要 | AIだけでは確定不可。 |
| 注文可能時間 | 利用者本人の確認または新版検証が必要 | 現行v0.2と利用可能時間が不一致。 |
| 明示承認 | 利用者本人が最後に行う | 未承認。 |

したがってLIVEは「コードを実行するだけ」ではGOにならない。特に12か月、過去時点データ、個人条件、注文可能時間、最終承認は外部条件である。

## 参照

- [Yahoo株価収集・監視範囲](market-data-operation-v0.1.md)
- [日次自動実行手順](daily-automation-runbook-v0.1.md)
- [継続稼働準備](operation-readiness-v0.1.md)
- [運用ガバナンス](operation-governance-v0.1.md)
- [OpenAI Scheduled tasks公式手順](https://learn.chatgpt.com/docs/automations)
- [JPX 東証上場銘柄一覧](https://www.jpx.co.jp/markets/statistics-equities/misc/01.html)
