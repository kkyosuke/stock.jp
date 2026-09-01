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
