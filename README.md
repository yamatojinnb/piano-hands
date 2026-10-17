# 指づかいピアノ（piano-hands）

楽譜が読めなくても、**どの鍵盤を・どの指で・いつ押すか**を、降ってくる鍵盤と両手の絵で見ながら練習するためのページです。

- 公開ページ：Netlify で公開（アドレスは Netlify のサイト設定で確認）
- 指番号を GitHub に保存するボタンは、持ち主の端末で一度だけ `公開ページ/?edit=<GitHubユーザー名>/piano-hands` を開くと表示されます（ページ自体にはリポジトリ名を書いていません）。
- 1つのアプリ（`index.html`）とピアノの音（`assets/piano/`）を全曲で共通に使い、曲ごとに増えるのは `songs/<曲ID>.json`（0.1MB前後）だけです。

## できること

- 降ってくる鍵盤＋指番号（1＝親指 … 5＝小指）＋真上から見た両手の動き
- 「流す」（10%〜の低速再生）と「1音ずつ」（ボタンやBluetoothペダルを押すまで待つ）
- 右手だけ／左手だけ（その手に寄って拡大）、小節を選んだ自動くり返し
- 強弱・ペダルなどの指示を日本語で表示（例：だんだん強く cresc.）
- 指番号をその場で直して、GitHubに保存

## ピアノの音

- 強さ4段階の録音を、強弱の指示（だんだん強く など）に合わせて切り替える
- 低い音は最長10秒まで自然に響きが消えていく
- ペダル：楽譜にペダル記号があればその区間は音を伸ばす。記号がない曲は、ベースが変わるところで踏み替える前提で伸ばす
- 部屋の響き（残響）と、鍵盤を離したときの小さな音を加える
- 再生中は、すぐ先の数秒分をあらかじめ1本の音に書き出してから流す（スマホでも途切れない。1小節戻ってもすぐ鳴る）
- 音のファイルは `python tools/make_samples.py` で作り直せる

## フォルダ構成

| 場所 | 中身 |
|---|---|
| `index.html` | アプリ本体（曲の一覧と練習画面） |
| `assets/piano/` | ピアノの音（Salamander Grand Piano。強さ4段階×30音＋鍵盤を離す音88、約10MB。曲で使う音だけを読み込む） |
| `songs/index.json` | 曲の一覧（自動生成） |
| `songs/<曲ID>.json` | 曲データ：音符・指番号・手の位置・日本語の指示（自動生成） |
| `songs/fix/<曲ID>.json` | 自分で直した指番号（画面の「GitHubに保存」から自動で追記） |
| `tools/catalog.json` | 登録する曲のリスト（楽譜データの場所・曲名など） |
| `tools/build_song.py` | 楽譜データ → 曲データ を作るツール |
| `tools/report.json` | 指番号の自動チェック結果 |

## どちらの手で弾くか

楽譜の「上の段・下の段」と「右手・左手」は同じではありません。内側の声部（伴奏など）は、もう一方の手の段に書かれることがよくあります。たとえば悲愴 第2楽章の冒頭の16分音符の伴奏は下の段に書かれていますが、ベートーヴェンの弟子チェルニーは「内声の伴奏は例外なく右手で弾く」と書いています。

そこで曲データを作るときに、次のルールで手を決めています（`separate_hands`）。

- いちばん下の声部（ベース）は左手、いちばん上の声部（旋律）は右手で固定する
- その内側の音は、小節ごとに「書かれている段の手のまま」か「もう一方の手」かを選ぶ。選ぶ基準は次のとおり
  - 片手で同時に9度を超えて押さない
  - 片手で押さえたまま10度を超えて広げない
  - 両手が交差しない
- 同じ声部は、なるべく続けて同じ手で弾く

変えた小節は `tools/report.json` の `hand_moves` に、まだ不自然な箇所は `hand_audit` に記録しています。

## 指番号の決め方

上にあるものほど優先します。

1. **自分で直した指**（`songs/fix/`）
2. **楽譜に印刷された指**（初版楽譜などのデータに入っている場合。画面では金色）
3. **自動計算**：ピアニストの指使いから学習した統計モデル（Nakamura・Saito・Yoshii 2019 の2次HMM）で求めたあと、次のルールで整える
   - くり返し部分は1回目と同じ指にする
   - 同じ形の小節（8音以上）が後で出てきたら同じ指にする
   - 和音で同じ指を2回使わない
   - 隣の鍵盤へ速く動くのに同じ指が続く箇所は、隣の指に直す

自動計算の精度はピアニストの指使いと比べて測っています（`docs/fingering-eval.md`）。ピアニスト同士の一致率 73.6% に対し、以前の PianoPlayer は約61%、今の統計モデルは約70%です。
統計モデルのプログラムは作者が GitHub で公開しているものを、曲データを作るときにダウンロードして使います（このリポジトリには含めていません）。学習に使われたデータの利用条件は「非営利・学術目的」です。

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
- 自動運指：Eita Nakamura, Yasuyuki Saito, Kazuyoshi Yoshii, "Statistical Learning and Estimation of Piano Fingering", Information Sciences 517 (2020)。プログラム https://github.com/statpianofingering/statpianofingering.github.io （予備として PianoPlayer https://github.com/marcomusy/pianoplayer ）
