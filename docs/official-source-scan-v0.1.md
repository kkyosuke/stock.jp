# 公式情報源スキャン v0.1

- 発効日: 2026年8月31日
- 目的: 夜間エージェントが読むべき一次資料を漏れなくキュー化し、取得不能を売買停止条件へ変換する
- 保存先: Git管理外の`operations/private/runs/YYYY-MM-DD/`

## 1. 取得範囲

機械取得する情報源は次のとおり。

| 情報源 | 取得内容 | 認証 | coverageへの効果 |
|---|---|---|---|
| EDINET API v2 | 日付別の提出書類一覧 | `EDINET_API_KEY` | 全日成功時に`edinet=CHECKED` |
| J-Quants API v2 `/td/list` | TDnet適時開示インデックス | `JQUANTS_API_KEY`と対応プラン | 全日成功時に`tdnet=CHECKED` |
| J-Quants API v2 `/fins/summary` | 決算サマリー | `JQUANTS_API_KEY` | 決算レビュー候補を作成 |
| J-Quants API v2 `/equities/bars/daily` | 日足・売買代金・調整係数 | `JQUANTS_API_KEY` | 価格・流動性更新の入力 |

会社IRとJPXの売買停止・監理/整理・上場廃止等の告知は、対象URLが会社ごとに異なり一つの公開APIで網羅できないため、エージェントが一次サイトを確認する`PENDING`タスクとして必ず作る。J-QuantsのTDnetエンドポイントが使えない場合も、手動TDnet確認タスクと重大データ不足を作る。

公式仕様の確認先:

- [JPXによるJ-Quants API V2開始案内](https://www.jpx.co.jp/corporate/news/news-releases/6020/20260119.html)
- [J-Quants公式Pythonクライアント](https://github.com/J-Quants/jquants-api-client-python)
- [J-Quants公式V2 QuickStart](https://github.com/J-Quants/jquants-api-quick-start)
- [EDINET公式のAPIキー案内](https://disclosure2.edinet-fsa.go.jp/week0020.aspx)
- [JPXのTDnet APIサービス](https://www.jpx.co.jp/markets/paid-info-listing/tdnet/02.html)

## 2. 認証情報

APIキーは環境変数だけで渡す。JSON、チャット、ログ、コマンド引数、Git管理下へ値を書かない。

~~~bash
export JQUANTS_API_KEY='ダッシュボードで発行した値'
export EDINET_API_KEY='EDINETで発行した値'
~~~

変数名、エンドポイント、timeoutは`operations/private/source-config.json`で設定する。値そのものは保存しない。J-Quantsの利用可能APIと保存期間はプランで異なるため、`/td/list`が403等なら成功扱いにしない。

J-Quantsから取得した生データは利用条件に従いprivateの`raw-sources/`だけへ保存し、リポジトリやPRへ追加しない。公開レポートには必要な判断と集計だけを出す。

## 3. 実行

`prepare`が返したrun IDとtokenをそのまま渡す。

~~~bash
.venv/bin/python scripts/official_source_scan.py \
  --run-id 2026-08-31 \
  --run-token '<prepareが返したrun_token>' \
  --cutoff 2026-08-31T18:30:00+09:00 \
  --at 2026-08-31T18:35:00+09:00
~~~

前回成功カットオフの10分前から当日カットオフまで、日付単位で重ねて取得する。初回だけ`initial_lookback_days`を使う。カットオフ後に公開された行はrawへ保存しても当日判断へ入れず、次回の重複期間で処理する。

取得結果は次へ保存する。

- `provider-health.json`: APIごとの成功、件数、認証不足、HTTPエラー
- `research-queue.json`: 新しい対象開示、会社IR、JPX告知、必要な手動フォールバック
- `sources.csv`: query範囲の証跡と対象銘柄の一次資料ID
- `raw-sources/*.json`: privateの生レスポンス
- `coverage.json`: 機械的に確認できたEDINET/TDnetだけ`CHECKED`
- `handoff.json`: 未解決の重大データ不足

検索結果0件も、0件だった検索日・URL・取得日時をquery証跡として残す。0件と取得失敗を同じにしない。

## 4. エージェントが続ける作業

1. `research-queue.json`を優先度順に処理する
2. 対象会社IRとJPX告知を公式サイトで確認し、`sources.csv`へコード付き証跡を追加する
3. 新規開示タスクは`S-A`を先に判定し、必要な判断ログを作る
4. 完了タスクを`COMPLETED`にし、`evidence_source_ids`を付ける
5. 翌日へ送る作業だけ`DEFERRED`にし、同じtask IDを`handoff.pending_reviews`へ入れる
6. 手動フォールバックで取得漏れを解消した場合は、該当gapを`RESOLVED`にして解決日時と証跡IDを残す
7. 全対象を確認後に`coverage.json`を`COMPLETED`へする

APIキー不足やプラン不足を推測で埋めない。未解決の重大gap、`PENDING`タスク、一次資料のない`COMPLETED`タスクが1件でもあれば、日次実行の`complete`は拒否される。
