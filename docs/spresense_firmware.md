# Spresense ファームウェア連携ガイド

EdgeOps Command Agent は、Spresense（Sony）+ センサーアドオンから Azure Event Hubs に送られる JSON サンプルを受け取り、そのまま既存の Multi-Agent パイプラインに流し込みます。

実機を用意しなくてもデモは回ります（`data/spresense_simulator.py` がローカル JSONL に書き込み、`src/iot_ingest.py` が読み戻します）。

## データフォーマット（1 サンプル = 1 JSON）

```json
{
  "device_id": "spresense-01",
  "equipment_id": "Pump-03",
  "timestamp": 1716700000.123,
  "vibration_x": 0.123,
  "vibration_y": 0.045,
  "vibration_z": 0.789,
  "sound_level": 53.2,
  "temperature": 46.7,
  "current": 2.3
}
```

- `timestamp` は epoch 秒（小数可）
- 振動 3 軸は G 単位
- `sound_level` は dB、`temperature` は ℃、`current` は A
- 欠損カラムはサーバ側で 0.0 補完される（部分送信OK）

## サンプルレートの目安

- センシング: 1 kHz 推奨（FFT 帯域 ~500Hz まで観測したいため）
- 送信: 100〜500 件 / batch にまとめて Event Hubs に送ると効率的

## ファームウェア側 サンプル擬似コード（Arduino スタイル）

```cpp
#include <SDHCI.h>
#include <Audio.h>
#include <ArduinoJson.h>
#include "EventHubsClient.h"   // 自作ラッパ想定

constexpr uint32_t SAMPLE_RATE_HZ = 1000;
constexpr uint32_t BATCH_SIZE = 200;

float read_vib_x(); float read_vib_y(); float read_vib_z();
float read_sound(); float read_temp(); float read_current();

void setup() {
  Serial.begin(115200);
  EventHubs::begin("<connection-string>", "edgeops-stream");
}

void loop() {
  static StaticJsonDocument<8192> batch;
  static int n = 0;

  JsonObject ev = batch.createNestedObject();
  ev["device_id"]     = "spresense-01";
  ev["equipment_id"]  = "Pump-03";
  ev["timestamp"]     = ((double)millis()) / 1000.0;
  ev["vibration_x"]   = read_vib_x();
  ev["vibration_y"]   = read_vib_y();
  ev["vibration_z"]   = read_vib_z();
  ev["sound_level"]   = read_sound();
  ev["temperature"]   = read_temp();
  ev["current"]       = read_current();

  if (++n >= BATCH_SIZE) {
    String body; serializeJson(batch, body);
    EventHubs::sendBatch(body);  // HTTPS POST or AMQP
    batch.clear(); n = 0;
  }

  delayMicroseconds(1000000 / SAMPLE_RATE_HZ);
}
```

> Spresense は AMQP も HTTPS も投げられますが、消費電力を抑えるなら HTTPS バッチ送信 + 30 秒間隔の sleep 推奨。

## Azure 側のセットアップ（最小）

```powershell
# 1. リソースグループ
az group create -n rg-edgeops-agent -l japaneast

# 2. Event Hubs namespace + ハブ
az eventhubs namespace create -g rg-edgeops-agent -n eh-edgeops --sku Basic --location japaneast
az eventhubs eventhub   create -g rg-edgeops-agent --namespace-name eh-edgeops -n edgeops-stream --partition-count 2

# 3. 送信用 + 受信用 SAS をそれぞれ作成
az eventhubs eventhub authorization-rule create -g rg-edgeops-agent --namespace-name eh-edgeops --eventhub-name edgeops-stream -n send  --rights Send
az eventhubs eventhub authorization-rule create -g rg-edgeops-agent --namespace-name eh-edgeops --eventhub-name edgeops-stream -n listen --rights Listen

# 4. 接続文字列を .env に
$listenConn = az eventhubs eventhub authorization-rule keys list -g rg-edgeops-agent --namespace-name eh-edgeops --eventhub-name edgeops-stream -n listen --query primaryConnectionString -o tsv
# EVENT_HUB_CONNECTION_STRING=$listenConn
# EVENT_HUB_NAME=edgeops-stream
```

## ローカル運用（実機なし）

```powershell
# critical 強度のデータを 5 秒分、ローカル JSONL に流し込む
python data/spresense_simulator.py --equipment-id Pump-03 --intensity critical --duration 5

# Streamlit の Data Upload タブ → 「Spresense ストリーム取り込み」→
# 「📡 直近の Spresense サンプルを取得」を押す → Run Agents
```

Event Hubs が設定されているときは、シミュレータは自動で Event Hubs に送信し、ローカル JSONL は使われません。

## トラブルシュート

| 症状 | 原因 / 対処 |
|---|---|
| `fetch_recent()` が `source=empty` を返す | Event Hubs にメッセージなし、または listen 権限不足。ローカルなら simulator 未実行 |
| FFT ピークが出ない | サンプルレートが極端に低い（< 200Hz）か、振動軸が全て 0。`vibration_z` を必ず含める |
| Event Hubs 受信が遅い | `timeout_seconds` を増やす（`fetch_recent(timeout_seconds=15)`） |
