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

## private repository 復旧

`private_repository_recovery` は、通常の private remote とアクセス制御された mirror の両方から復旧できた記録を検証する。公開テンプレート `operations/templates/live-gate-evidence/recovery-drill-template.json` を private の `operations/private/evidence/recovery-drill.json` へコピーし、実施結果を記入する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp repository-recovery \
  --write-evidence stock.jp/operations/private/evidence/repository-recovery.json
~~~

clean clone、submodule、workspace setup、state と bootstrap、最新成功 run と handoff、全台帳、未照合注文を照合する。復旧した private commit と public submodule commit は元の40桁SHAと一致しなければならない。通常 remote と mirror の両方の復旧を要求する。

復旧訓練は90日で失効する。稼働中 checkout を訓練先にせず、一時ディレクトリを使う。repository URL、credential、口座情報は証跡へ記載しない。

## 利用者のリスク・税・証券会社確認

`personal_risk_and_broker_check` は利用者本人しか確定できない。`operations/templates/live-gate-evidence/personal-risk-and-broker-template.json` を private evidence へコピーし、公式の証券会社規則と本人の状況を確認して記入する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp personal-risk \
  --write-evidence stock.jp/operations/private/evidence/personal-risk.json
~~~

生活・納税・5年以内の予定・借入資金を除外し、全体損失停止、税区分、手数料、売買単位、指値、注文期限、8:45〜8:55の本人確認能力を確認する。個人上限は v0.4 の1銘柄10%、業種20%、初回5%、追加2.5%、参加率10%を緩和できない。発注は `HUMAN_ONLY` で broker API の自動送信を使わない。

金融庁の注意喚起に沿い、正しい証券会社URLの bookmark、MFAまたはpasskey、login・取引通知、phishing・口座lock時の手順も確認する。電話番号やcredentialはJSONに書かず、保管場所だけを記録する。この確認は90日で失効し、証券会社仕様や本人事情が変われば直ちに再実施する。

- [金融庁: フィッシングによる証券口座への不正アクセス等にご注意ください](https://www.fsa.go.jp/ordinary/chuui/chuui_phishing.html)
- [JPX: 内国株の売買制度](https://www.jpx.co.jp/equities/trading/domestic/01.html)

## v0.4 holdout 昇格

`v04_holdout_promotion` は通常の7 gate とは別に必要である。point-in-time の固定 holdout で v0.2 と v0.4 を比較し、12か月 PAPER 証跡が完成した後に、`v04-holdout-review-template.json` を private evidence へコピーして本人が判断する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp v04-promotion \
  --write-evidence stock.jp/operations/private/evidence/v04-promotion.json
~~~

再生結果は holdout の事前宣言、閾値凍結日時、再調整0回を持ち、v0.2/v0.4双方のreturn、maximum drawdown、1銘柄・業種の最大損失寄与を含める。本人は v0.4 の資金配分による損失増幅、戦略待機資金が安全資産でないこと、過去診断が前向きPAPERを代替しないことを確認する。

review は再生結果、履歴再生受入証跡、12か月PAPER証跡のSHA-256へ拘束する。いずれかを更新すると再承認が必要になる。判定は成績が正なら自動昇格するものではなく、本人が `PROMOTE_V0_4_TO_LIVE` を明示した場合だけ合格する。

## 最終 LIVE 昇格

各コマンドの合格結果を上記の既定 evidence path へ `--write-evidence` で保存する。最後に `live-approval-template.json` を `operations/private/evidence/live-approval.json` へコピーし、private/public commit、現在のPAPER policy、8証跡のSHA-256、本人の `LIVE` 判断を記入する。

まず変更を加えない判定を実行する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp promote-live \
  --write-evidence stock.jp/operations/private/evidence/live-readiness.json
~~~

このコマンドは8条件を元データから再計算し、保存証跡の `gate / eligible / blockers / inputs` と一致すること、最終承認が全証跡と現在のPAPER policyのSHA-256に一致することを確認する。`eligible: true` を本人が確認した後だけ、同じ入力のまま明示的に適用する。

~~~bash
stock.jp/.venv/bin/python stock.jp/scripts/live_gate_evidence.py \
  --root stock.jp promote-live --apply
stock.jp/.venv/bin/python stock.jp/scripts/operation_policy.py status
stock.jp/.venv/bin/python stock.jp/scripts/operation_bootstrap.py check
~~~

`--apply` は合格時だけ `operation-policy.json` を原子的に `LIVE` へ変更し、7 gate、v0.4 promotion、証跡path、承認者・日時を同時に設定する。不合格ならpolicyを変更しない。以後のbootstrapも最終承認のハッシュと各型付き証跡を検証し、ファイルを空で作るだけの昇格を拒否する。

LIVE後も注文は自動送信されない。各 `PROPOSED` ticket は8:45〜8:55の pretrade check 後に本人が証券会社へ手入力し、blocking gap、復旧不能、本人状況や証券会社仕様の変更があれば `PAUSED` へ戻す。
