# 公開・非公開リポジトリの使い分け v0.1

## 結論

2つのリポジトリはサブモジュールにせず、完全に分離する。

| リポジトリ | 公開範囲 | 用途 |
|---|---|---|
| [`kkyosuke/stock.jp`](https://github.com/kkyosuke/stock.jp) | Public | ルール、プログラム、テンプレート、機密性のない株価データ |
| `kkyosuke/stock.jp.private` | Private | `age`で暗号化済みの運用バックアップをGitHub Releasesへ保存 |

非公開リポジトリはcloneやsubmoduleとして公開リポジトリへ追加しない。公開側へ非公開リポジトリの認証情報、秘密鍵、平文バックアップを保存しない。

## 1. 公開リポジトリ `stock.jp`

このリポジトリを通常の作業場所とする。次の内容だけをGit管理する。

- PAPER/LIVEガバナンスと売買ルール
- 日次実行、注文票、検証、復元のプログラム
- 個人情報を含まないテンプレートとテストfixture
- 公開可能な正規化済み株価データ

正確な保有数量、取得原価、資金、税、注文履歴、実行ログは、Git管理外の`operations/private/`を正本とする。`.zip`、`.zip.age`、`age`秘密鍵をこの公開リポジトリへcommitしない。

## 2. 非公開リポジトリ `stock.jp.private`

非公開リポジトリは暗号化バックアップの遠隔コピー専用とする。

- バックアップ本体はGit commitではなくGitHub Releaseの添付ファイルにする
- 保存できる運用データは`operation-*.zip.age`だけとする
- 平文の`.zip`、展開済みファイル、`age`秘密鍵、APIキー、口座情報を保存しない
- リポジトリのVisibilityを`Private`から変更しない
- 最新の検証済みReleaseを、次の復元確認が終わる前に削除しない

秘密鍵を失うとバックアップは復号できない。秘密鍵は両リポジトリ外の安全な場所へ保存し、別媒体にも1部複製する。

## 3. バックアップの作成と保存

次のコマンドは公開リポジトリ`stock.jp`のルートで実行する。

一度だけ`age`を導入し、リポジトリ外へ秘密鍵を作成する。

~~~bash
brew install age
age-keygen -o /安全なリポジトリ外の場所/stock-jp-backup-key.txt
age-keygen -y /安全なリポジトリ外の場所/stock-jp-backup-key.txt
~~~

最後のコマンドで表示された`age1...`公開鍵を使って、暗号化バックアップを作成する。

~~~bash
.venv/bin/python scripts/operation_backup.py create \
  --at '<JSTのISO日時>' \
  --age-recipient 'age1...'
~~~

作成されたファイルをローカルで検証する。

~~~bash
.venv/bin/python scripts/operation_backup.py verify \
  --archive operations/private/backups/operation-YYYYMMDDTHHMMSS+0900.zip.age \
  --age-identity /安全なリポジトリ外の場所/stock-jp-backup-key.txt
~~~

`valid: true`を確認してから、暗号化済みファイルだけを非公開Releaseへ添付する。

~~~bash
BACKUP_ARCHIVE='operations/private/backups/operation-YYYYMMDDTHHMMSS+0900.zip.age'
BACKUP_TAG='backup-YYYYMMDDTHHMMSS+0900'
gh release create "$BACKUP_TAG" "$BACKUP_ARCHIVE" \
  --repo kkyosuke/stock.jp.private \
  --title "$BACKUP_TAG" \
  --notes 'age-encrypted stock.jp operation backup; plaintext and identity are not included'
~~~

同じ日時のReleaseを作り直したり、既存ファイルを上書きしたりしない。作成頻度は月1回、運用状態スキーマの変更前、および重要な状態変更後を基本とする。

## 4. 遠隔コピーの確認

アップロード直後に別パスへ再ダウンロードし、同じ秘密鍵で検証する。

~~~bash
mkdir -p operations/private/backups/remote-verify
gh release download 'backup-YYYYMMDDTHHMMSS+0900' \
  --repo kkyosuke/stock.jp.private \
  --pattern 'operation-YYYYMMDDTHHMMSS+0900.zip.age' \
  --dir operations/private/backups/remote-verify

.venv/bin/python scripts/operation_backup.py verify \
  --archive operations/private/backups/remote-verify/operation-YYYYMMDDTHHMMSS+0900.zip.age \
  --age-identity /安全なリポジトリ外の場所/stock-jp-backup-key.txt
~~~

ローカル作成物と再ダウンロード物のSHA-256も一致させる。

~~~bash
shasum -a 256 \
  operations/private/backups/operation-YYYYMMDDTHHMMSS+0900.zip.age \
  operations/private/backups/remote-verify/operation-YYYYMMDDTHHMMSS+0900.zip.age
~~~

## 5. 復元

端末故障時は、公開リポジトリを取得してから非公開Releaseの暗号化ファイルだけをダウンロードする。

~~~bash
mkdir -p operations/private/backups/recovered
gh release download 'backup-YYYYMMDDTHHMMSS+0900' \
  --repo kkyosuke/stock.jp.private \
  --pattern 'operation-YYYYMMDDTHHMMSS+0900.zip.age' \
  --dir operations/private/backups/recovered

.venv/bin/python scripts/operation_backup.py restore \
  --archive operations/private/backups/recovered/operation-YYYYMMDDTHHMMSS+0900.zip.age \
  --age-identity /安全なリポジトリ外の場所/stock-jp-backup-key.txt \
  --destination operations/private/restores/drill-YYYYMMDD
~~~

復元先は検証用の別ディレクトリであり、稼働中の`operations/private/`を上書きしない。`state.json`、最新`handoff.json`、各台帳、未照合注文を比較した後にだけ復旧へ使用する。

## 6. 禁止事項

- `stock.jp.private`をsubmoduleにしない
- 平文バックアップをGitHubへ送らない
- 秘密鍵をGit、GitHub Actions Secrets、チャット、運用ログへ貼らない
- 公開リポジトリのIssue、PR、Actions artifactへ非公開状態を添付しない
- 復号・復元検証をしていないファイルを「検証済みバックアップ」と扱わない
