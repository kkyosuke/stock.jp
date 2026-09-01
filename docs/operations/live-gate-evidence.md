# LIVE gate 証跡の機械判定

実資金への昇格条件は、文章上のチェックだけでなく `scripts/live_gate_evidence.py` で検証する。検証に失敗した条件や証跡が存在しない条件は、必ず未達として扱う。

## point-in-time 全母集団

`point_in_time_full_universe_validation` は、次のコマンドが成功した場合だけ証跡を作成できる。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp point-in-time \
  --write-evidence stock.jp/operations/private/evidence/point-in-time.json
~~~

既定入力は `data/historical-replay/point-in-time-validation.json` である。入力 manifest は以下をすべて満たす必要がある。

- status が `COMPLETED` で、生成日時に UTC offset がある
- 当時点の security master を使い、上場廃止、合併、corporate action を含む
- 必須母集団数と評価済み数が一致する
- hard-gate 入力欠損と look-ahead 違反がともに0件
- `source_snapshot`、`trade_log`、`metrics` の3成果物が存在し、SHA-256 が一致する

manifest や成果物を後から変更すると検証は失敗する。現在は必要な公式データが揃っていないため、証跡を作らず gate を `false` のまま維持する。

証跡の書き込み先は private の `operations/private/evidence/` 配下だけに制限される。公開 Actions では検証ロジックと fixture の回帰テストだけを行い、実口座・判断情報を artifact にしない。

## 2025〜2026年の履歴再生受入

`historical_replay_2025_2026_accepted` は、固定期間 `2025-01-01`〜`2026-08-31` の v0.4 再生結果と、利用者の private review の両方を検証する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp historical-replay \
  --write-evidence stock.jp/operations/private/evidence/historical-replay.json
~~~

公開側の `data/historical-replay/replay-result-2025-2026.json` は、point-in-time manifest のハッシュ、欠損・look-ahead 違反0件、取引数、return、maximum drawdown、benchmark と、取引・月次・metrics 成果物のハッシュを持つ。private 側の `historical-replay-review.json` には、現在の再生結果のハッシュ、`ACCEPT`、承認者・承認日時、drawdown・集中損失・データ制約を確認した事実を記録する。

再生結果を変更すると過去の review は無効になる。成績の数値だけでは自動受入せず、利用者の review がない状態では gate を `false` のままにする。

## v0.4 PAPER を最低12か月

`minimum_12_month_paper_trade` は private の `run-history.csv` と各成功 report から判定する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp paper-duration \
  --write-evidence stock.jp/operations/private/evidence/paper-12-months.json
~~~

最新 attempt が `COMPLETED / PAPER / v0.4` の run だけを数え、最初と最後の完了日の間が365日以上、12以上の暦月に成功記録があり、期間中に成功が1件もない暦月がないことを要求する。対応する private report がない run は数えない。昇格前に `LIVE` run が記録されていた場合も失敗する。

一時 simulation の20日や履歴再生の日数を PAPER 期間へ算入しない。実時間が365日経過する前に、この gate をコードや手動編集で短縮しない。

## 実データ20営業日の shadow run

`twenty_day_shadow_run` は公開株価 archive の最新20営業日と、private の実 run を1対1で照合する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp shadow-run \
  --write-evidence stock.jp/operations/private/evidence/shadow-20-days.json
~~~

各 run は `COMPLETED / PAPER / v0.4`、run ID と株価基準日が同一、alert と data gap が0件でなければならない。さらに通常の run integrity を再検証し、注文件数と `orders.csv` を照合し、20日間をまたぐ ticket ID および銘柄・side・trade date の重複を拒否する。証跡は run ディレクトリ全体と各価格 session の SHA-256 に拘束される。

`operation_smoke.py --days 20` は状態遷移の回帰テストであり、この実データ gate には算入しない。失敗日や取得漏れがあれば、修正後に新しい20営業日を連続して完了する。

## 公式情報源 coverage

`official_source_coverage` は、合格した20日 shadow window の全 run について確認する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp official-coverage \
  --write-evidence stock.jp/operations/private/evidence/official-coverage.json
~~~

EDINET は各日 `LIVE_NETWORK` で1 request 以上成功していなければならず、fixture 実行は算入しない。TDnet、EDINET、JPXと、対象銘柄がある日の会社IRが `CHECKED` で、対応する一次資料行が存在することを要求する。判断に使用した公式カテゴリの行が二次資料なら失敗する。解消していない data gap と、shadow window より古い source watermark も拒否する。

公開株価 archive は再現用の二次データであり、この判定だけで注文価格の公式確認を代替しない。対象銘柄の価格、corporate action、会社IRは各 run の一次資料証跡へ残す。
