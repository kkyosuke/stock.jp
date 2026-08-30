# stock.jp

## Live operations

- [日本株テンバガー実運用手順書 v0.1](docs/live-operation-playbook-v0.1.md) — 注文、監視、情勢指数、購入・継続・追加・縮小・売却の手順
- [日本株運用ガバナンス v0.1](docs/operation-governance-v0.1.md) — PAPER/LIVE、適用ルール版、昇格条件、定期的なルール見直し
- [日次自動実行手順書 v0.1](docs/daily-automation-runbook-v0.1.md) — 平日1回の定時実行、注文候補、失敗時の再開、次回への引き継ぎ
- [日本株運用レビュースキル](.agents/skills/japan-stock-operator/SKILL.md) — 同じ判定とログ作成をCodexで再現
- [運用ログ用テンプレート](operations/templates/decision-log-template.md) — 個人情報を含む実ログは `operations/private/` に保存

## Research rules

- [日本株テンバガー判定・運用ルール v0.3](docs/tenbagger-rule-v0.3.md) — 5倍時の原資回収と候補がある場合だけの再配分を含む凍結済みチャレンジャー
- [日本株テンバガー判定・運用ルール v0.2](docs/tenbagger-rule-v0.2.md) — 現行のペーパートレード用ルール
- [v0.2価格ルール適用時の参考損益（2016–2026年）](docs/tenbagger-v0.2-price-only-pnl-2016-2026.md) — 現存テンバガー83銘柄の銘柄別・合計診断
- [日本株テンバガー判定ルール v0.1](docs/tenbagger-rule-v0.1.md) — 凍結済み旧版
- [日本株テンバガー仮説の予備検証（2016–2026年）](docs/tenbagger-validation-2016-2026.md)
- [検証用データと再生成方法](data/README.md)
