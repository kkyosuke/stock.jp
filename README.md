# stock.jp

日本株戦略の再現可能なコード、公開データ、ルール、運用仕様を管理する公開リポジトリです。GitHub Actions を使う定期収集・検証・回帰テストはここで実行します。個人の保有数量、資金、注文、判断記録は含めません。

## リポジトリの責務

| ディレクトリ | 内容 |
|---|---|
| `.github/workflows/` | 公開データ収集、CI、再現可能な自動化 |
| `.agents/skills/` | 個人情報を含まない運用手順 |
| `data/` | 公開可能で出典・生成方法を追跡できるデータ |
| `docs/architecture/` | public/private の境界と全体設計 |
| `docs/operations/` | PAPER/LIVE の運用仕様 |
| `docs/rules/` | 凍結した戦略ルール |
| `docs/research/` | 検証結果と再生成手順 |
| `operations/templates/` | 秘密情報を含まない台帳テンプレート |
| `scripts/`, `tests/` | 実装と回帰テスト |

実運用では private リポジトリ `stock.jp.private` がこのリポジトリを submodule として固定し、`stock.jp/operations/private` を private 側の `operations/private` へ接続します。暗号化アーカイブは使わず、private Git の履歴と現行構成の clean-clone 確認を運用証跡にします。詳細は[リポジトリ境界](docs/architecture/repository-boundaries.md)を参照してください。

## はじめに

~~~bash
python3 -m venv .venv
.venv/bin/pip install 'xlrd==2.0.2' 'openpyxl==3.1.5' 'certifi==2026.7.22' PyYAML
.venv/bin/python -m unittest discover -s tests -v
~~~

初回状態を作る場合は private リポジトリから実行してください。単独で PAPER 用の一時環境を試す場合だけ、`operations/private/` をローカルに作成して次を実行します。

~~~bash
.venv/bin/python scripts/operation_state.py migrate
.venv/bin/python scripts/operation_state.py validate
.venv/bin/python scripts/operation_bootstrap.py check
~~~

`paper_go: true` になるまで PAPER を開始せず、`live_go: true` になるまで実注文を行いません。コードは注文候補を作りますが、証券会社へ自動送信しません。

## ドキュメント

- [ドキュメント索引](docs/README.md)
- [公開・非公開リポジトリ境界](docs/architecture/repository-boundaries.md)
- [運用ガバナンス](docs/operations/operation-governance-v0.1.md)
- [LIVE 運用手順](docs/operations/live-operation-playbook-v0.1.md)
- [PAPER/LIVE 準備判定](docs/operations/operation-readiness-v0.1.md)
- [現行 PAPER ルール v0.4](docs/rules/tenbagger-rule-v0.4.md)
- [検証用データと再生成方法](data/README.md)

## セキュリティ境界

公開 Issue、PR、Actions artifact、ログへ、口座情報、API キー、保有数量、取得原価、資金額、注文 ID、実運用の判断記録を出力しないでください。秘密情報を検知した場合は履歴からの除去と認証情報の失効を優先します。
