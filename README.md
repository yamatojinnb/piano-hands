# 指づかいピアノ（piano-hands）

楽譜が読めなくても、**どの鍵盤を・どの指で・いつ押すか**を、降ってくる鍵盤と両手の絵で見ながら練習するためのページです。

- 公開ページ：https://yamatojinnb.github.io/piano-hands/
- 1つのアプリ（`index.html`）とピアノの音（`assets/piano/`）を全曲で共通に使い、曲ごとに増えるのは `songs/<曲ID>.json`（0.1MB前後）だけです。

## できること

- 降ってくる鍵盤＋指番号（1＝親指 … 5＝小指）＋真上から見た両手の動き
- 「流す」（10%〜の低速再生）と「1音ずつ」（ボタンやBluetoothペダルを押すまで待つ）
- 右手だけ／左手だけ（その手に寄って拡大）、小節を選んだ自動くり返し
- 強弱・ペダルなどの指示を日本語で表示（例：だんだん強く cresc.）
- 指番号をその場で直して、GitHubに保存

## フォルダ構成

| 場所 | 中身 |
|---|---|
| `index.html` | アプリ本体（曲の一覧と練習画面） |
| `assets/piano/` | ピアノの音（Salamander Grand Piano、25音、約0.7MB） |
| `songs/index.json` | 曲の一覧（自動生成） |
| `songs/<曲ID>.json` | 曲データ：音符・指番号・手の位置・日本語の指示（自動生成） |
| `songs/fix/<曲ID>.json` | 自分で直した指番号（画面の「GitHubに保存」から自動で追記） |
| `tools/catalog.json` | 登録する曲のリスト（楽譜データの場所・曲名など） |
| `tools/build_song.py` | 楽譜データ → 曲データ を作るツール |
| `tools/report.json` | 指番号の自動チェック結果 |

## 指番号の決め方

上にあるものほど優先します。

1. **自分で直した指**（`songs/fix/`）
2. **楽譜に印刷された指**（初版楽譜などのデータに入っている場合。画面では金色）
3. **自動計算**：運指計算ツール PianoPlayer で求めたあと、次のルールで整える
   - くり返し部分は1回目と同じ指にする
   - 同じ形の小節が後で出てきたら同じ指にする
   - 和音で同じ指を2回使わない

## 指番号を直す（画面から）

1. 練習画面で一時停止するか「1音ずつ」にする
2. 「指番号を直す」で正しい指を選ぶ（緑の数字になる）
3. 「GitHubに保存」→ 開いたGitHubの画面で「Create」を押す
4. 数分で GitHub Actions が修正を取り込み、直した指を前提に手の動きを計算し直して公開し直す

## 曲を追加する（Claude Code に頼む場合）

`tools/catalog.json` に1曲分を足して、次を実行します。

```
pip install -r tools/requirements.txt
python tools/build_song.py <曲ID>
```

楽譜データは、著作権の切れたクラシック曲の電子楽譜（Humdrum **kern 形式）を GitHub の公開リポジトリから取得します。

## 出典

- ショパン：Fryderyk Chopin Institute「Chopin First Editions」（CC BY 4.0） https://github.com/pl-wnifc/humdrum-chopin-first-editions
- ベートーヴェン：Craig Stuart Sapp「Beethoven Piano Sonatas」 https://github.com/craigsapp/beethoven-piano-sonatas
- ピアノの音：Salamander Grand Piano（Alexander Holm、CC BY 3.0）、npm パッケージ @audio-samples/piano-mp3-velocity8 を短く切って使用
- 自動運指：PianoPlayer（Marco Musy） https://github.com/marcomusy/pianoplayer
