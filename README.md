# SwitchBot BotをBluetoothで操作する検証用CLI

PythonとBleakを使い、PCからSwitchBot BotをBluetooth Low Energy（BLE）で直接操作します。
SwitchBot Hub、クラウドアカウント、APIトークンは不要です。

対応する機能は、Botの検出、機器情報の取得、ボタンの押下、スイッチのON/OFFです。
**パスワードが設定されたBotには対応していません。**

## 1. 準備

- Python 3.10以上と、BLEに対応したBluetoothアダプターが必要です。
- WindowsのBluetoothを有効にし、BotをPCの近くに置いてください。
- 以下のコマンドは、プロジェクトのフォルダーでPowerShellから実行します。

最初に、このプロジェクトの仮想環境 `.venv` を有効化してください。

```powershell
.\.venv\Scripts\Activate.ps1
```

以降のコマンドは、仮想環境を有効化した同じターミナルで実行します。
有効化後は `python` が `.venv` 内のPythonを参照します。新しいターミナルを開いた場合は、再度有効化してください。

Bleakがまだこの仮想環境に入っていない場合だけ、次を実行してください。導入済みなら不要です。

```powershell
python -m pip install -r requirements.txt
```

## 2. Botを検出する

```powershell
python .\switchbot_bot.py scan
```

近くのBotのアドレス、名前、電波強度などを表示します。この操作ではBotに接続せず、アームも動きません。

表示内容の例（値は機器や環境によって異なります）:

```text
AA:BB:CC:DD:EE:FF  (unnamed)  RSSI=-50 battery=100% mode=press encrypted=False
```

| 表示                         | 意味                                                        |
| ---------------------------- | ----------------------------------------------------------- |
| `AA:BB:CC:DD:EE:FF`          | 操作対象を指定するためのBluetoothアドレス                   |
| `(unnamed)`                  | 名前が取得できなかった機器。名前がなくても操作できます      |
| `RSSI`                       | 電波の受信強度。距離そのものではありません                  |
| `battery`                    | 電池残量                                                    |
| `mode=press` / `mode=switch` | 押すモード / スイッチモード                                 |
| `encrypted`                  | パスワード設定の有無。`True` のBotはこのCLIの操作対象外です |
| `state=on` / `state=off`     | スイッチモード時にBotが通知する状態                         |

機器によっては一部の情報が表示されません。
複数のBotがある場合は、公式アプリの機器情報と照合し、操作対象を確認してください。

## 3. 機器情報を確認する

以降の例の `AA:BB:CC:DD:EE:FF` は、検出した対象Botのアドレスに置き換えてください。

```powershell
python .\switchbot_bot.py info --address AA:BB:CC:DD:EE:FF
```

Botに接続して、電池残量、ファームウェアバージョン、タイマー数、動作モード、反転設定、長押し時間を取得します。
設定の変更やアームの操作は行いません。

## 4. Botを操作する

**アームが実際に動きます。取り付け先の機器を操作しても安全であることを確認してください。**

### ボタンを押す

公式アプリでBotを「押すモード」に設定してから実行します。

```powershell
python .\switchbot_bot.py press --address AA:BB:CC:DD:EE:FF
```

### スイッチをON/OFFする

公式アプリで「スイッチモード」に設定し、引き上げ用のアタッチメントなどを適切に取り付けてから実行します。

ONにする:

```powershell
python .\switchbot_bot.py on --address AA:BB:CC:DD:EE:FF
```

OFFにする:

```powershell
python .\switchbot_bot.py off --address AA:BB:CC:DD:EE:FF
```

このCLIでは、動作モード、反転設定、長押し時間、スケジュールの変更は行いません。設定には公式アプリを使ってください。

### 実行結果の見方

押下が成功した場合の応答例:

```text
TX: 57 01 00
RX: 01 ff 00
OK: press
```

`TX` は送信データ、`RX` は受信データを16進数で表したものです。
受信データの先頭が `01`（成功ステータス）の場合に `OK` を表示します。後続のデータは機器によって異なる場合があります。

**タイムアウトしても、Botがすでに動作している可能性があります。再実行する前に実機を確認してください。**
二重押下を避けるため、自動再送は行いません。

## うまく動かないとき

### Botが見つからない・接続できない

- WindowsのBluetoothが有効か確認してください。通常、Windows側での事前ペアリングは不要です。
- BotをPCに近づけ、電池残量を確認してください。
- 公式アプリやHubなど、Botに接続している可能性がある機器との接続を解除してから再試行してください。
- 検出したアドレスが操作対象のBotのものか確認してください。

検出時間を延ばし、Bot以外も含めてすべてのBLE機器を表示するには、次を実行します。
`--all` では他の機器も表示されるので、一覧の機器をすべてBotとみなさないでください。

```powershell
python .\switchbot_bot.py scan --all --timeout 15
```

### 応答待ちでタイムアウトする

長押し時間が長い場合などは、待ち時間を延ばしてください。各オプションの単位は秒です。

| オプション           | 内容                                             | 既定値 |
| -------------------- | ------------------------------------------------ | ------ |
| `scan --timeout`     | 機器一覧を取得するための検出時間                 | 10秒   |
| `--scan-timeout`     | 操作対象のBotを探す待ち時間                      | 10秒   |
| `--connect-timeout`  | 接続時にBleakへ渡すタイムアウト                  | 20秒   |
| `--response-timeout` | コマンドの書き込みと応答受信、それぞれの待ち時間 | 各15秒 |

以下は**検出や設定ではなく、実際に押下するコマンド**です。Botの状態を確認してから実行してください。

```powershell
python .\switchbot_bot.py press --address AA:BB:CC:DD:EE:FF --scan-timeout 20 --connect-timeout 30 --response-timeout 30
```

### エラーステータスが返る

| ステータス | 意味と対処                                                          |
| ---------- | ------------------------------------------------------------------- |
| `0x03`     | Botが処理中です。動作が終わってから再試行してください               |
| `0x05`     | コマンドが非対応です。押すモード / スイッチモードを確認してください |
| `0x06`     | 電池残量が少なくなっています。電池を確認してください                |
| `0x07`     | パスワードが必要です。このCLIはパスワード付きBotに未対応です        |

## テスト

```powershell
python -m unittest test_switchbot_bot -v
```

モック（通信相手を模擬する仕組み）でコマンドや応答処理を検証します。
このテストでは実機に接続せず、アームも動きません。実際のBLE接続と動作は、実機で別途確認してください。

## 参考資料

- [SwitchBot公式 Bot BLE仕様](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE/blob/latest/devicetypes/bot.md)
- [Bleak公式ドキュメント](https://bleak.readthedocs.io/)
