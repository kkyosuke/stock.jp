# 全銘柄日次株価のGitHub Actions運用 v0.1

## 結論

株探のHTMLスクレイピングは行わない。株探利用規約はコンテンツの蓄積・複製・加工・
再配信を禁止しており、約3,700ページへの日次アクセスは負荷と保守性の面でも不適切である。

代わりに、JPXの月次「東証上場銘柄一覧」で現行の内国株式を確定し、Yahoo Financeの
chart endpointを銘柄ごとに呼び出す。Python標準ライブラリと既存依存`xlrd`だけを使い、
次の値を日付別CSVへ保存する。

- 始値、高値、安値、終値
- 直前取引日終値から計算した前日比と前日比％
- 売買高
- 銘柄ごとの取得状態

## 初回設定

1. GitHubの`Settings > Actions > General > Workflow permissions`で`Read and write
   permissions`と`Allow GitHub Actions to create and approve pull requests`を有効にする。
2. 今回の初回データは`lookback_days=21`で取得する。将来Actionsから再構築する場合も、
   `daily-stock-prices`を`Run workflow`から同じ値で1回実行する。
3. 作成されたPRで`data/daily-prices/latest.json`の`fetch.error_count`と最新日の
   `quote_count`を確認してマージする。

通常実行に株価APIキーは不要である。リポジトリ設定で標準`GITHUB_TOKEN`からのPR作成を
許可できない場合だけ、Contents/Pull requestsの書込権限を持つfine-grained PATまたは
GitHub App tokenをActions secret `PR_TOKEN`へ登録する。

## 日次動作

`.github/workflows/daily-stock-prices.yml`は平日18:30（Asia/Tokyo）に実行する。
直近7暦日とその前日比計算用の重複期間を取得するため、休日、実行漏れ、Yahoo側の
後日訂正を吸収できる。同じ内容のファイルは書き換えず、差分がある場合だけ
`automation/daily-stock-prices`ブランチのPRを作成または更新する。

全リクエストの98%未満しか成功しなければ、広域障害やアクセス制限とみなして追跡データを
変更せず失敗する。98%以上なら個別エラーを`FETCH_ERROR`として残し、PRで確認できる。

## 境界

Yahoo Financeのchart endpointは非公式であり、利用可能性も値の正確性も保証されない。
このCSVは候補探索と欠損監視に限定し、`nightly_operation.py`の注文判断へ直接接続しない。
売買判断へ使う値は、取引所、適時開示、法定開示などの一次情報で再照合する。

このリポジトリは公開されているため、実データの継続公開を開始する前にYahooと元データ
提供者の最新の利用条件を確認する。公開が許容されない場合は、リポジトリをprivateにするか、
CSVをGit追跡せず利用者だけが読めるprivate storageへ保存先を変更する。
