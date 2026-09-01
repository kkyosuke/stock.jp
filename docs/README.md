# ドキュメント索引

## 最初に読むもの

1. [リポジトリ境界](architecture/repository-boundaries.md)
2. [運用ガバナンス](operations/operation-governance-v0.1.md)
3. [運用状態・台帳](operations/operation-state-v0.2.md)
4. [PAPER/LIVE 準備判定](operations/operation-readiness-v0.1.md)
5. [LIVE 運用手順](operations/live-operation-playbook-v0.1.md)

## 分類

- `architecture/`: public/private の責務、データ境界、復旧方針
- `operations/`: 日次実行、公式情報源、状態遷移、障害時の扱い
- `rules/`: 変更管理された売買ルール
- `research/`: 過去データ検証、制約、再生成手順

実資金投入の可否は研究成績だけでは決めません。`operation_bootstrap.py check` の全ゲート、最低12か月の PAPER、実データ20営業日の連続運用、private リポジトリ復旧訓練、利用者の明示承認をすべて満たした場合だけ GO とします。
