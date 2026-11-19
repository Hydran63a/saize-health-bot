# Saize Health Bot（仮称）

サイゼリヤのメニューから、LDLコレステロール・中性脂肪・塩分などを意識した食事選びをサポートする Misskey Bot です。

> ⚠️ 本Botは医療上の診断・治療の代わりにはなりません。栄養情報は公式値と推定値を区別して扱い、不明な値は表示しません。

## 開発状況

- [x] Phase 1：開発環境構築
- [ ] Phase 2：メニューデータ収集
- [x] Phase 3：栄養評価機能（`python -m modules.nutrition 料理名 料理名 ...` で確認）
- [ ] Phase 4：おすすめ生成（バランス献立・重複防止・投稿文は実装済み。`python -m modules.recommendation --slot 11:00` で投稿文を確認、`--simulate 20` で重複防止を確認、`--budget 800` で予算指定。定期投稿は税込1,000円以内が初期値）
- [ ] Phase 5：Misskey連携（投稿は実装済み。コマンド応答は未実装）
- [ ] Phase 6：VPS移行（systemd タイマーの設定と手順書は [deploy/README.md](deploy/README.md)。VPS への設置はこれから）

## セットアップ（Windows 11 / PowerShell）

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env   # 必要に応じて値を編集
python main.py                # 環境チェック
python -m pytest              # テスト
```

## セットアップ（Linux）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## メニューデータ

- `data/raw/allergy_YYYY-MM-DD.tsv`：公式「[アレルゲン・カロリー塩分情報一覧](https://allergy.saizeriya.co.jp/allergy)」の写し（公式値のみ）
- `data/raw/allergy_YYYY-MM-DD.meta.json`：取得元URL・公式更新日・取得日・sha256
- `data/menu.json`：上記から生成（`python -m modules.menu_importer`）

公式に掲載されていない栄養素（飽和脂肪酸・食物繊維など）は `null`（`not_published`）とし、推定値は別ファイルで管理します。

### 推定値（公式値ではありません）

- `data/recipes.json`：料理ごとの材料と分量の**仮定**（公式レシピではありません）
- `data/food_subset.json`：上記で使う食品の成分値（成分表からの抜粋）
- `data/estimates.json`：推定結果。計算エネルギーを公式エネルギーに合わせて調整し、信頼度を付与

再生成の手順（成分表Excelが必要。`data/external/` に置く。Git管理外）：

```powershell
python -m modules.food_composition export   # food_subset.json を更新
python -m modules.estimator                 # estimates.json を更新
```

成分表Excel（[文部科学省](https://www.mext.go.jp/a_menu/syokuhinseibun/mext_00001.html)）：
本表 第2章 → `data/external/mext_honpyo_ch2.xlsx`、脂肪酸成分表編 第2章第1表 → `data/external/mext_fattyacid_ch2_t1.xlsx`

**出典：日本食品標準成分表（八訂）増補2023年から引用（文部科学省）**

### 栄養評価の基準値

`data/reference_values.json`：[日本人の食事摂取基準（2025年版）](https://www.mhlw.go.jp/stf/newpage_44138.html)（厚生労働省）の目標量。
食塩相当量 6.5g未満/日、飽和脂肪酸 7%エネルギー以下、食物繊維 20g以上/日（本Botの採用値）。
1食の目安を「1日の目標量÷3」とするのは本Bot独自の簡易な決まりです。

`data/menu_tags.json`：料理の材料タグ（本Bot独自の分類。公式情報ではありません）。

### 価格

`data/prices.json`：グランドメニュー（デジタルメニューブック 2026年10月版）から手で書き写した**標準価格**（税抜・税込）。
地域価格店舗・価格改定先行店舗では価格が異なります。税込価格は「税抜×1.1（持ち帰り商品は×1.08）の切り捨て」と一致することを確認済みです。

## Misskey への投稿

1. Bot 用の Misskey アカウントを用意し、プロフィール設定で **Bot フラグをオン**にする
2. 設定 > API > アクセストークンの発行 で、権限「アカウントの情報を見る」「ノートを作成・削除する」の**2つだけ**を付けて発行
3. `.env` に `MISSKEY_HOST`（ドメインのみ）と `MISSKEY_TOKEN` を書く（トークンは誰にも見せない）

```powershell
python main.py check --account          # つながるか確認（Bot フラグも表示）
python main.py post --slot 11:00 --dry-run   # 投稿せずに文章を確認
python main.py post --slot 11:00        # ENABLE_POSTING=true のときだけ実際に投稿
```

- 公開範囲の初期値は `public`＋`localOnly`（連合なし＝ローカルタイムラインのみ）。`.env` の `POST_VISIBILITY` / `POST_LOCAL_ONLY` で変更
- 投稿に成功したときだけ重複防止の履歴に記録（失敗した献立は記録しない）
- ログは `logs/bot.log`（トークンは出力しない）

## 動作要件

- Python 3.12 以上
