# 全銘柄日次株価のGitHub Actions運用 v0.1

## 結論

株探のHTMLスクレイピングは行わない。株探利用規約はコンテンツの蓄積・複製・加工・
再配信を禁止しており、約3,700ページへの日次アクセスは負荷と保守性の面でも不適切である。

代わりに、JPXの月次「東証上場銘柄一覧」で現行の内国株式を確定し、Yahoo Financeの
chart endpointを銘柄ごとに呼び出す。JPXの現行`.xlsx`は`openpyxl`、保存済みの旧
`.xls`は`xlrd`で読み、
次の値を日付別CSVへ保存する。

- 始値、高値、安値、終値
- 直前取引日終値から計算した前日比と前日比％
- 売買高
- 銘柄ごとの取得状態

## 初回設定

1. 自動更新用のGitHub Appを作成し、Repository permissionsの`Contents: Read and write`と
   `Pull requests: Read and write`を付け、このrepositoryへinstallする。必須checkのbypass権限は付けない。
2. `Settings > Secrets and variables > Actions`のVariablesに`AUTOMATION_APP_CLIENT_ID`
   （AppのClient ID）、Secretsに`AUTOMATION_APP_PRIVATE_KEY`（Appで生成した秘密鍵の全文）を登録する。
   秘密鍵はGitやログへ保存しない。未設定なら収集開始前に設定名を表示して失敗する。
3. 今回の初回データは`lookback_days=21`で取得する。将来Actionsから再構築する場合も、
   `daily-stock-prices`を`Run workflow`から同じ値で1回実行する。
4. repository設定でauto-mergeを有効にし、mainの必須`test`を維持する。workflowはデータ検証、
   全test、compile、20日smokeが成功した場合だけ、作成したPRのsquash auto-mergeを有効にする。

株価APIキーや長期PATは不要である。収集・検証はread-onlyの`GITHUB_TOKEN`で行い、PR作成と
auto-mergeの設定にはGitHub Appの短命なinstallation tokenを使う。tokenは当該repositoryの
Contents/Pull requestsだけに限定し、有効期間1時間を収集で消費しないよう検証後に発行する。

## 日次動作

`.github/workflows/daily-stock-prices.yml`は平日18:30（Asia/Tokyo）に実行する。
直近7暦日とその前日比計算用の重複期間を取得するため、休日、実行漏れ、Yahoo側の
後日訂正を吸収できる。同じ内容のファイルは書き換えず、差分がある場合だけ
`automation/daily-stock-prices`ブランチのPRを作成または更新する。

全リクエストの98%未満しか成功しなければ、広域障害やアクセス制限とみなして追跡データを
変更せず失敗する。98%以上なら個別エラーを`FETCH_ERROR`として残し、PRで確認できる。
通常の無人mergeでは取得エラー0件、最新sessionの件数一致、最新価格のカバー率98%以上、変更CSVが指定lookback内である
ことも要求する。収集後にarchive schema、checksum、全回帰test、Python compile、20日
operation smokeを同じjobで実行する。いずれかが失敗した場合はPRを作成・mergeしない。
すべて成功し、差分がある場合だけPRを作成し、`--auto --squash`でmergeする。差分がない日は
何もしない。

GitHub Appで作成・更新したPRでは通常の`pull_request`イベントから`operation-tests`が起動する。
必須`test`が成功してからauto-mergeするため、PR branchへの別途`workflow_dispatch`は行わない。
標準`GITHUB_TOKEN`で作成したPRはチェック実行に承認を要求されるため、代替として使用しない。
これは[GitHub公式のworkflow連鎖の仕様](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)に従う。

価格のカバー率不足ではmainを変更せず、収集元の価格が揃ってから同じworkflowを再実行する。
`NO_QUOTE`はHTTP取得の成功と区別する。たとえば取得成功3,707件でも、株価ありが4件なら不合格。
PRと必須checkの成功、mainの`latest.json`更新まで確認して、初回設定の完了とする。

## 境界

Yahoo Financeのchart endpointは非公式であり、利用可能性も値の正確性も保証されない。
このCSVは候補探索と欠損監視に限定し、`nightly_operation.py`の注文判断へ直接接続しない。
売買判断へ使う値は、取引所、適時開示、法定開示などの一次情報で再照合する。

このリポジトリは公開されているため、実データの継続公開を開始する前にYahooと元データ
提供者の最新の利用条件を確認する。公開が許容されない場合は、リポジトリをprivateにするか、
CSVをGit追跡せず利用者だけが読めるprivate storageへ保存先を変更する。
