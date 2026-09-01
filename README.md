# stock.jp

## Live operations

- [日本株テンバガー実運用手順書 v0.1](docs/live-operation-playbook-v0.1.md) — 注文、監視、情勢指数、購入・継続・追加・縮小・売却の手順
- [日本株運用ガバナンス v0.1](docs/operation-governance-v0.1.md) — PAPER/LIVE、適用ルール版、昇格条件、定期的なルール見直し
- [日本株テンバガー判定・運用ルール v0.4](docs/tenbagger-rule-v0.4.md) — 運用資産100%を対象にする現行PAPER資金配分
- [2025–2026年ルール履歴再生](docs/historical-replay-2025-2026.md) — 年別・銘柄別損益の受入仕様、データ充足状況、PAPER判定
- [履歴日足アーカイブ仕様 v0.1](docs/historical-data-import-v0.1.md) — PR #14互換の日付別CSV、検証、ハッシュ、非公開データの扱い
- [日本株運用状態・台帳仕様 v0.2](docs/operation-state-v0.2.md) — 利確フラグ、決算連続条件、再購入禁止、回収原資、約定履歴を翌日へ引き継ぐ
- [夜間運用の完了条件・重複防止 v0.1](docs/run-integrity-v0.1.md) — 空レポート、取得漏れ、同時実行、重複注文、誤ったカットオフ前進を拒否
- [公式情報源スキャン v0.1](docs/official-source-scan-v0.1.md) — EDINET取得、TDnet・会社IR・JPX調査キュー、未確認時の売買停止
- [日次自動実行手順書 v0.1](docs/daily-automation-runbook-v0.1.md) — 平日1回の定時実行、注文候補、失敗時の再開、次回への引き継ぎ
- [公開・非公開リポジトリの使い分け](docs/repository-roles-and-private-backup-v0.1.md) — 公開コードと暗号化バックアップの保存・復元手順
- [日本株運用レビュースキル](.agents/skills/japan-stock-operator/SKILL.md) — 同じ判定とログ作成をCodexで再現
- [運用ログ用テンプレート](operations/templates/decision-log-template.md) — 個人情報を含む実ログは `operations/private/` に保存

## Research rules

- [全銘柄日次株価のGitHub Actions運用 v0.1](docs/daily-stock-price-actions-v0.1.md) — JPX銘柄一覧とYahoo Finance chart endpointによるOHLCV収集・PR作成

- [日本株テンバガー判定・運用ルール v0.3](docs/tenbagger-rule-v0.3.md) — 5倍時の原資回収と候補がある場合だけの再配分を含む凍結済みチャレンジャー
- [日本株テンバガー判定・運用ルール v0.2](docs/tenbagger-rule-v0.2.md) — v0.4の銘柄選定・出口の基準版
- [v0.4資金配分の12か月過去データ実験](docs/tenbagger-v0.4-allocation-replay-2025.md) — 2025年公式日足によるv0.2対v0.4の配分・最大DD診断
- [v0.2価格ルール適用時の参考損益（2016–2026年）](docs/tenbagger-v0.2-price-only-pnl-2016-2026.md) — 現存テンバガー83銘柄の銘柄別・合計診断
- [日本株テンバガー判定ルール v0.1](docs/tenbagger-rule-v0.1.md) — 凍結済み旧版
- [日本株テンバガー仮説の予備検証（2016–2026年）](docs/tenbagger-validation-2016-2026.md)
- [検証用データと再生成方法](data/README.md)
