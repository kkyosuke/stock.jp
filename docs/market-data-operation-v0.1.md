# Yahoo株価収集・監視範囲 v0.1

- 対象モード: `PAPER`
- 株価源: Yahoo Financeの非公式`/v7/finance/spark` endpoint
- 母集団の正本: JPX「東証上場銘柄一覧」
- 発注: 行わない

## 結論

株価収集はAI判断から分離する。保有・関心中の銘柄は平日18時に毎日取得し、月次レビューではJPXの東証上場銘柄一覧を取得して全体を見直す。

| scope | 対象 | 頻度 | 完了条件 |
|---|---|---|---|
| `daily` | 保有＋`watchlist.active` | 平日18:00 | 対象の100%、重大欠損0 |
| `monthly` | JPX掲載の東証上場銘柄 | 月末レビュー時 | 99%以上、保有・関心中は100% |

Yahooは公式または契約APIではなく、429や仕様変更が発生し得る。取得できない銘柄を前日値で補完せず、日次は`BLOCKED`にする。株価はPAPERの価格計算にだけ用い、会社の事実認定は会社IR、TDnet/JPX、EDINETなどの一次資料で再確認する。

## 日次収集

~~~bash
.venv/bin/python scripts/yahoo_price_collector.py collect --scope daily
~~~

次を保存する。

- `operations/private/market-snapshots/YYYY-MM-DD/daily/HHMMSS*/manifest.json`: 件数、対象銘柄集合、カバレッジ、欠損、checksum、利用制限。同日再実行も別attemptとして残す
- `yahoo-raw.json`: APIレスポンス
- `price-history.csv`: `(price_date, code)`で重複排除したOHLCV・調整終値・売買代金
- `market-data-state.json`: 最終成功snapshotと成功した日次価格基準日
- `portfolio-register.csv`: `last_close`、`current_multiple`、`MA20`、`highest_ma20`、`DD20`

出来高0の行は取引可能日として使わない。調整終値がない場合はフラグを残す。future date、古い価格、checksum不一致、関心銘柄の欠損は判断を止める。

## 月次全体見直し

~~~bash
.venv/bin/python scripts/yahoo_price_collector.py collect --scope monthly
~~~

月次はJPXの当月公開一覧を正本としてYahoo symbolへ変換する。複数銘柄endpointは過去出来高を返さないため、`monthly-price-screen.csv`には全取得銘柄の20日・60日変化率と60日ドローダウンを保存し、流動性は`LIQUIDITY_UNAVAILABLE`と明記する。関心中へ昇格した銘柄は日次chart APIのOHLCVと一次資料で流動性を確認する。月次screenは買い候補の自動決定ではない。

この方式では利用者が最初から約4,000銘柄を手入力する必要はない。全市場を毎日詳細調査することもしない。

## 自動実行

価格collectorはOSのschedulerで動かす。ChatGPT Scheduled taskはPCとアプリが利用可能であること、unattended環境のnetwork許可に依存するため、決定的な価格収集の唯一の起動手段にはしない。設定例は`operations/templates/com.stockjp.price-collector.plist`に置く。

collector完了後、利用者はAIへ一回だけ次のように指示する。

~~~text
$japan-stock-operator を使って、今日のタスクを実行して。
~~~

AIは保存済みsnapshotの状態を確認し、世界情勢・一次資料・期限到来レビュー・明日のアクション・今日のログを保存する。価格snapshotが`BLOCKED`なら売買判断を作らない。

## 制約

- この実装は現在銘柄の前向きPAPER監視用であり、point-in-time全母集団検証を満たさない。
- JPXの月次一覧は現在母集団の正本になるが、過去の上場廃止・当時構成を完全には復元しない。
- LIVE昇格には契約条件を含む公式価格源との照合、12か月PAPER、20営業日連続監査、全LIVEゲートの根拠が別途必要である。
