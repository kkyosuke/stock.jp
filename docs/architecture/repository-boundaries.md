# 公開・非公開リポジトリ境界

## 結論

2つのリポジトリは責務を分け、private 側を作業環境のルートにします。

~~~text
stock.jp.private/                   # private: 実運用の正本
├── docs/                           # 現在のGO/NO-GO、個人向け手順、レビュー
├── operations/private/             # 状態、台帳、日次成果物、判断証跡
├── scripts/setup-workspace.sh       # submoduleとprivateデータ接続の準備
└── stock.jp/                        # public repoを固定するsubmodule
    ├── .github/workflows/           # 公開CIと定期データ収集
    ├── data/                        # 公開可能なデータ
    ├── docs/                        # 汎用仕様、ルール、研究
    ├── operations/templates/        # 空テンプレートだけ
    ├── scripts/
    └── tests/
~~~

セットアップ後の `stock.jp/operations/private` は `../../operations/private` への symlink です。public の既存プログラムは同じパスを使えますが、実データを追跡するのは親の private リポジトリです。public 単独 checkout ではこの symlink も実データも存在しません。

## public `stock.jp` に置くもの

- 一般化した売買ルール、運用仕様、テンプレート
- 取得元と生成手順を公開できる市場データと研究結果
- データ収集、検証、状態遷移、注文候補作成のコード
- 合成 fixture と回帰テスト
- GitHub Actions で繰り返せる公開情報の取得と検証

public Actions は計算資源として活用しますが、private の値を Secrets で渡したり、実運用成果物を artifact/cache/ログへ出したりしません。public の処理結果は、不特定多数へ公開されても問題ないものだけに限定します。

## private `stock.jp.private` に置くもの

- `state.json`、各台帳、run/handoff/order/decision log
- 保有数量、取得原価、資金、税、証券会社注文ID
- 利用者固有の損失許容、口座制約、GO/NO-GO 承認
- 実運用の対象銘柄、障害記録、復旧訓練証跡
- public submodule の採用 commit

private であっても API キー、パスワード、MFA 回復コード、cookie、秘密鍵は Git に保存しません。実行時の環境変数または認証情報管理機能を使用します。

## 暗号化アーカイブを使わない方針

運用データは private Git に平文で commit し、private リポジトリ自体のアクセス制御、ブランチ保護、アカウントの多要素認証を境界にします。`age`、`.zip.age`、GitHub Release バックアップ、復号鍵は運用から除外します。

復旧可能性は暗号化 archive の存在ではなく、次の訓練で確認します。

1. private リポジトリを空の別ディレクトリへ clone する
2. submodule を初期化し、setup script で symlink を再作成する
3. 状態 schema と台帳を validate する
4. 最新成功 run、handoff、未照合注文、台帳残高を照合する
5. 復旧日時、private commit、public submodule commit、結果を private の証跡へ記録する

private Git は利便性のための履歴であり、ホスティング障害やアカウント喪失に対する完全なバックアップではありません。LIVE 前に、アクセス制御された別媒体への private repository mirror と、その clone 復旧も確認します。これは application-level の暗号化 archive を復活させる要件ではありません。

## 移動判定

次のいずれかに該当するファイルは private へ移します。

- 個人または口座を特定できる
- 実保有、実資金、実注文、実約定を推測できる
- 現在の監視対象や個別の売買意思を示す
- 利用者固有の GO/NO-GO、損失許容、税務判断を含む
- 認証情報や非公開ライセンスデータを含む

一般化・匿名化しても現在のポジションを推測できる場合は public に要約を置きません。迷う場合は private とし、後から再現可能な汎用部分だけを public へ切り出します。

## 禁止事項

- private repository やその checkout を public repository の履歴へ含める
- public Issue、PR、Actions artifact/cache/log に実運用データを出す
- private データを public Actions の入力にする
- 秘密情報を「private repo だから安全」とみなして commit する
- 状態ファイルだけを手動コピーして台帳との整合性を失う
