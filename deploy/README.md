# VPS への設置手順（systemd タイマーで自動投稿）

Ubuntu / Debian 系を前提にしています。コマンドは VPS 上で実行します。

> ⚠️ **Python 3.12 以上が必要です。** Ubuntu 24.04 は 3.12 が標準です。Debian 12 や Ubuntu 22.04 は標準が 3.11 以下なので、別途 3.12 以上を用意してください。
> ⚠️ **PC（Windows）からの投稿は止めてください。** 重複防止の履歴はマシンごとのファイルなので、PC と VPS の両方から投稿すると同じ献立が出たり、二重投稿になったりします。

## 1. 必要なものを入れる

```bash
sudo apt update
sudo apt install -y python3 python3-venv git
python3 --version   # 3.12 以上であること
```

## 2. Bot 専用のユーザーを作る

ログインできない、Bot を動かすためだけのユーザーです。

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin saizebot
```

## 3. プログラムを置く

```bash
sudo git clone https://github.com/Hydran63a/saize-health-bot.git /opt/saize-health-bot
sudo chown -R saizebot:saizebot /opt/saize-health-bot
```

リポジトリが Private の場合は、`git clone` のときに GitHub のログイン（個人アクセストークン）を求められます。

## 4. 仮想環境とライブラリ

```bash
cd /opt/saize-health-bot
sudo -u saizebot python3 -m venv .venv
sudo -u saizebot .venv/bin/pip install -r requirements.txt
```

## 5. `.env` を置く（トークンを含むので取り扱いに注意）

PC の `.env` を VPS にコピーします（PC 側で実行。`ユーザー名@VPSのアドレス` は自分のものに置き換え）。

```bash
scp .env ユーザー名@VPSのアドレス:/tmp/saize.env
```

VPS 側で所定の場所に移し、**Bot のユーザーだけが読めるように**します。

```bash
sudo mv /tmp/saize.env /opt/saize-health-bot/.env
sudo chown saizebot:saizebot /opt/saize-health-bot/.env
sudo chmod 600 /opt/saize-health-bot/.env
```

重複防止の履歴も引き継ぐ場合は、PC の `database/saize.sqlite3` を同じ方法で `/opt/saize-health-bot/database/` に置きます（同じく `chown saizebot:saizebot`）。

## 6. 動作確認（投稿しない）

```bash
cd /opt/saize-health-bot
sudo -u saizebot .venv/bin/python main.py check --account
sudo -u saizebot .venv/bin/python main.py post --slot 11:00 --dry-run
```

## 7. systemd に登録する

```bash
sudo cp deploy/saize-bot-post.service deploy/saize-bot-post.timer /etc/systemd/system/
sudo systemctl daemon-reload
systemd-analyze calendar "*-*-* 11:00:00 Asia/Tokyo"   # 次の実行時刻が表示されれば OK
sudo systemctl enable --now saize-bot-post.timer
systemctl list-timers saize-bot-post.timer              # NEXT に次の 11:00 / 15:00 が出ていれば OK
```

`systemd-analyze calendar` でエラーになる古い systemd の場合は、VPS のタイムゾーンを日本時間にして（`sudo timedatectl set-timezone Asia/Tokyo`）、タイマーの `Asia/Tokyo` を消してください。

## 8. 運用

| やりたいこと | コマンド |
|---|---|
| 次の実行時刻を見る | `systemctl list-timers saize-bot-post.timer` |
| 実行結果のログを見る | `journalctl -u saize-bot-post.service -n 50` |
| Bot のログを見る | `tail -n 50 /opt/saize-health-bot/logs/bot.log` |
| 今すぐ1回実行する（枠の時刻外なら何もしない） | `sudo systemctl start saize-bot-post.service` |
| 自動投稿を止める | `sudo systemctl disable --now saize-bot-post.timer` |

### プログラムを更新する

```bash
cd /opt/saize-health-bot
sudo -u saizebot git pull
sudo -u saizebot .venv/bin/pip install -r requirements.txt
```

1回ずつ起動して終わる方式なので、更新後の再起動は不要です（次の実行から新しいプログラムが使われます）。
`deploy/` の設定ファイルが変わったときだけ、手順 7 の `cp` と `daemon-reload` をやり直してください。

### 投稿時刻を変える

`.env` の `POST_TIMES` と、`/etc/systemd/system/saize-bot-post.timer` の `OnCalendar` を**両方**同じ時刻にして、`sudo systemctl daemon-reload` と `sudo systemctl restart saize-bot-post.timer` を実行します。
