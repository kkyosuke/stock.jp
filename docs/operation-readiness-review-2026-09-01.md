# 実運用開始 readiness review（2026-09-01、最新main統合後）

## 結論

- 実資金`LIVE`: **NO-GO**。コードだけでは完了しないLIVEゲートを残し、証拠が揃うまで機械的に停止する。
- 継続`PAPER`: **仕組みは実装済み、利用者のprivate設定はNO-GO**。現在のblockerは31日以内の検証済みバックアップが記録されていない1点である。active対象0件は初回候補抽出モードとして許可する。
- 証券会社への通信: 常に`HUMAN_ONLY`。PAPER注文票は`PAPER_PROPOSED`であり、実注文しない。

`operation_bootstrap.py check`が`paper_go: true`を返すまで夜間runは開始できない。`nightly_operation.py start`も同じreadinessを再検証するため、手順の飛ばし越しでは開始できない。

## 目標との適合

| 目標 | 状態 | 実装 |
|---|---|---|
| 毎日の株価を自動収集 | **PASS** | GitHub Actionsが平日18:30 JSTにJPX現行内国株式全体をYahoo Financeから取得し、データPRを作る。 |
| 関心中は毎日、全体は月次見直し | **PASS** | 全体の価格は毎日保存し、毎夜は保有＋active watchlistだけ100%照合する。月次は蓄積済み全体データから候補集合を見直し、再取得しない。 |
| 人がAIへ一回指示 | **PASS** | `$japan-stock-operator`の夜間runがreadiness、調査、翌日アクション、ログ、引き継ぎを一巡する。 |
| 明日の動きと今日のログ | **PASS** | `next-day-actions.csv`、`report.md`、`research-results.md`、`global-risk.md`、`handoff.json`を完了時に検証する。 |
| ニュース・世界情勢 | **PASS（手動調査を含む）** | 為替、金利、資源、主要国政策、地政学を「事実→保有KPIへの伝播→判断」で保存する。一次資料未確認なら閉じない。 |
| 長期・非デイトレ | **PASS** | 日次は例外確認、候補集合は月次、企業進捗は四半期。凍結済みv0.2の長期ルールを維持する。 |
| ピンポイント注文不可 | **PAPERは可／現行LIVEはNO-GO** | 夜間は注文案まで。現行v0.2の翌朝8:45〜8:55確認を行えないならLIVEへ昇格しない。 |

## 株価データの安全境界

正本は`.github/workflows/daily-stock-prices.yml`と`data/daily-prices/`である。関心銘柄だけを取得する別collectorやローカルlaunchdは置かず、二重取得・正本分裂を避けた。

夜間runの前に次をすべて検証する。

1. `latest.json`がYahoo Financeの非公式データであることを明示している
2. 最新CSVのSHA-256がmanifestと一致する
3. 全市場の正常価格coverageが98%以上
4. 保有＋active watchlistの全銘柄が`取得状態=OK`で、正のOHLCと非負整数出来高を持つ
5. 価格日が未来でなく、7暦日を超えて古くない
6. 31日以内のバックアップが存在し、checksumと作成時検証証拠が一致する

Yahooは候補探索とPAPER計算用の二次価格源であり、売買判断の一次資料ではない。毎夜、会社IR、TDnet、EDINET、JPXの公式価格・コーポレートアクション、取引日カレンダーを別に確認する。確認できない項目は`WAIT`または`fail`とし、成功カットオフを進めない。

## 実データ確認

2026年8月31日のtracked archiveは次の証拠を持つ。

- JPX現行内国株式: 3,713銘柄
- Yahoo取得成功: 3,713 / 3,713、取得エラー0
- 正常価格: 3,669 / 3,713（98.81%）
- 最新CSV: `data/daily-prices/2026/2026-08-31.csv`
- SHA-256: `019db4bc27932bafb0040443e9c2bb9d2fc33c5018fb884a5daaac6ec0a96d8a`
- 実データのactive対象検証例: `4477`を100%照合し、OHLCV・鮮度・checksumに合格

これはYahoo実APIで取得された株価データの整合性確認であり、公式価格確認、投資成績、point-in-time全母集団、前向きPAPER期間を証明しない。

回帰確認では単体・統合テスト107件、Python compile、`git diff --check`が成功した。20営業日のオフライン状態遷移も20件完了、broker submission 0、重複注文0で成功した。ただしオフラインsmokeは実データ20営業日LIVEゲートの代替ではない。

修正後、現在のprivate設定へ2026年9月1日18:30 JSTを指定した場合のPAPER blockerは次の1件となる。

- `no verified operation backup has been recorded`

active対象0件は警告とし、PAPERは`GLOBAL / NO-ACTION`と`initial_universe_review`を作成する。EDINET APIキー未設定もPAPER blockerではなく手動一次資料fallbackの警告である。LIVEでは必須情報源coverageの一部としてblockerのままとなる。

## PAPERをGOにする手順

1. 最新データPRを確認・マージし、夜間運用を行う永続checkoutへ最新`main`を反映する。
2. `age`暗号化したPAPER用バックアップを作成して検証する。
3. `.venv/bin/python scripts/operation_bootstrap.py check`で`paper_blockers`が0件、`paper_go: true`であることを確認する。
4. 人が「今日のタスクを実行して」と1回依頼する。対象0件ならAIが全市場から候補を絞り、個別注文は`NO-ACTION`にする。
5. 候補の一次資料を確認できた後だけ、少数を`watchlist.csv`のactiveへ登録する。現在保有があれば数量・原価を`portfolio-register.csv`へ正確に登録する。

EDINET APIキーはPAPER開始の必須条件ではない。ただしキーがない場合は毎回の手動一次資料確認が必須であり、取得不能なら売買判断を作らない。

## LIVEをGOにする追加条件

| 条件 | 実行するだけで可能か | 現状 |
|---|---|---|
| point-in-time全母集団検証 | 履歴データの調達・再生・人の受入が必要 | 未完了 |
| 2025–2026履歴再生の受入 | 不足する当時点データと人の受入が必要 | 未承認 |
| 最低12か月PAPER | 実時間の経過が必要 | 未完了。履歴再生で代替しない |
| 実データ20営業日、重大漏れ・重複0 | 前向きrunと監査が必要 | 未完了。オフラインsmokeでは代替しない |
| 公式情報源coverage | APIまたは毎回の網羅証拠が必要 | 未完了 |
| backup/restore | 実施可能 | `age`、秘密鍵管理、別媒体が必要 |
| 損失許容・税・証券会社仕様 | 利用者本人の確認が必要 | 未確認 |
| 注文可能時間 | 利用者本人の確認または新しい執行版のPAPER検証が必要 | 現行条件と不一致 |
| 明示承認 | 利用者本人が最後に行う | 未承認 |

したがってLIVEは、コードや一括スクリプトを実行するだけではGOにならない。履歴再生と最低12か月PAPERは別々に要求し、個人条件と最終承認も自動で真にしない。

## 参照

- [Yahoo株価収集・監視範囲](market-data-operation-v0.1.md)
- [日次自動実行手順](daily-automation-runbook-v0.1.md)
- [継続稼働準備](operation-readiness-v0.1.md)
- [運用ガバナンス](operation-governance-v0.1.md)
- [OpenAI Scheduled tasks公式手順](https://learn.chatgpt.com/docs/automations)
- [JPX 東証上場銘柄一覧](https://www.jpx.co.jp/markets/statistics-equities/misc/01.html)
