# EdgeOps Command Agent

開発者: 近藤悠太 (Kondo Yuta)

EdgeOps Command Agent is an Azure-powered maintenance AI agent that converts inspection data into risk assessment, root cause hypotheses, work orders, and management reports.

> **異常検知で終わらせない。** 点検データを、異常判断・原因推定・作業指示・報告書まで一気通貫で変換する現場保全AIエージェントです。

## なぜ作ったか

設備点検の現場では、センサーデータ・点検写真・点検メモ・マニュアル・故障履歴が分断されています。異常を検出しても、

- これは本当に危険なのか
- 原因は何か
- 誰が、いつ、何をすべきか
- 管理者にどう報告するか

の判断が属人化していました。EdgeOps Command Agent は、これらの判断材料を Multi-Agent で整理し、**人間の最終承認を前提に**現場の初動を加速します。

## アーキテクチャ概要

詳細図 (PNG): [docs/architecture.png](docs/architecture.png)

```
                    Spresense / Edge sensors
                              │
                              ▼  (Event Hubs / HTTPS)
Sensor CSV / Image / Memo / PDF Manual / History / Inventory
                              ▼
                  EdgeOps Command Agent (8 Agent)
   ┌─────────────────────────────────────────────┐
   │  ① Intake          (入力品質 / 欠損検証)    │
   │  ② Signal Insight  ─┐                       │
   │  ③ Vision          ─┤  Semantic Kernel +    │
   │  ④ Manual RAG      ─┤  Azure OpenAI         │
   │  ⑤ Root Cause      ─┤  (Microsoft Foundry)  │
   │  ⑥ Action Planning ─┤                       │
   │  ⑦ What-if Sim     ─┘                       │
   │  ⑧ Governance      (不確実性集約 / 承認準備) │
   └─────────────────────────────────────────────┘
                              ▼
   Risk Level / Root Cause / Work Order / Management Report
                              ▼
              Human Approval (承認 / 修正依頼 / 却下)
                              ▼
              Audit Log → Cosmos DB / Teams 通知
```

### 構成要素

- **Frontend (Streamlit)**: 7-tab demo UI（`app.py`）
- **Frontend (Next.js, App Router)**: 別実装の SaaS 風 UI（`frontend/`）
- **Backend (FastAPI)**: REST API として Multi-Agent を公開（`backend/`）
- **Signal Processing**: pandas / numpy / scipy（FFT, RMS, 帯域エネルギー）
- **Agent Layer**: **8 Agent** (Intake → Signal → Vision → Manual RAG → Root Cause → Action Plan → What-if → Governance) + Report Agent。Semantic Kernel (`AzureChatCompletion`)、`src/sk_orchestrator.py`
- **LLM / Vision**: Azure OpenAI（Microsoft Foundry）— Vision は SDK 直叩き
- **RAG**: Azure AI Search（設定済みなら）／ ローカル txt RAG ／ PDF アップロード（in-memory）の 3 段ディスパッチ — `src/rag.py`
- **Persistence**: Azure Blob Storage（画像・PDF）／ Azure Cosmos DB（承認ログ・実行履歴・通知履歴）— 未設定時はローカルフォールバック
- **Notification**: Microsoft Teams（Incoming Webhook / Power Automate Adaptive Card）— `src/teams_notify.py`
- **Edge ingestion**: Azure Event Hubs（Spresense からの JSON サンプル）／ ローカル JSONL シミュレータ — `src/iot_ingest.py`, `data/spresense_simulator.py`
- **Runtime**: Azure Container Apps
- **Human-in-the-loop**: 承認 / 修正依頼 / 却下 ＋ 理由テキスト ＋ Cosmos に永続化された監査ログ

## クイックスタート

### ローカル実行（Streamlit）

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# .env を編集し、Azure OpenAI の情報を入れる
# (未設定でも EDGEOPS_USE_MOCK=true でモック動作)
streamlit run app.py
```

### ローカル実行（Next.js + FastAPI）

```powershell
# Terminal 1: FastAPI（FastAPI/uvicorn は backend/requirements.txt 側）
pip install -r requirements.txt -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000

# Terminal 2: Next.js
cd frontend
npm install
$env:NEXT_PUBLIC_API_BASE = "http://localhost:8000"
npm run dev
```

- Streamlit: <http://localhost:8501>
- Next.js  : <http://localhost:3000>
- Swagger  : <http://localhost:8000/docs>

### Docker

```powershell
# Streamlit 単体
docker build -t edgeops-command-agent .
docker run --env-file .env -p 8501:8501 edgeops-command-agent

# Backend のみ
docker build -t edgeops-backend -f backend/Dockerfile .
docker run --env-file .env -p 8000:8000 edgeops-backend

# Frontend のみ
docker build -t edgeops-frontend -f frontend/Dockerfile ./frontend
docker run -p 3000:3000 -e NEXT_PUBLIC_API_BASE=http://host.docker.internal:8000 edgeops-frontend
```

### Azure Container Apps へデプロイ

```powershell
az login
az group create --name rg-edgeops-agent --location japaneast
az containerapp up `
  --name edgeops-command-agent `
  --resource-group rg-edgeops-agent `
  --location japaneast `
  --source . `
  --ingress external `
  --target-port 8501
```

> **既知の回避手順**: Azure CLI 2.86.0 では `az containerapp up --source .` 内部で `AttributeError: 'NoneType' object has no attribute 'linux'` が発生して途中で失敗するケースがあります。その場合は ACR 手動 build + Container App create の 5 ステップで回避できます。
>
> ```powershell
> # 1. ACR を作成して認証情報を取得
> az acr create -n edgeopscmdacr -g rg-edgeops-agent --sku Basic --admin-enabled true
> $cred = az acr credential show -n edgeopscmdacr | ConvertFrom-Json
>
> # 2. ローカル Docker でビルドして ACR に push
> docker build -t edgeopscmdacr.azurecr.io/edgeops-command-agent:v1 .
> docker login edgeopscmdacr.azurecr.io -u $cred.username -p $cred.passwords[0].value
> docker push edgeopscmdacr.azurecr.io/edgeops-command-agent:v1
>
> # 3. Container Apps Environment を作成
> az containerapp env create -n edgeops-command-agent-env -g rg-edgeops-agent --location eastus2
>
> # 4. Container App を作成（API キーは Secret に登録して secretref で参照）
> az containerapp create `
>   --name edgeops-command-agent `
>   --resource-group rg-edgeops-agent `
>   --environment edgeops-command-agent-env `
>   --image edgeopscmdacr.azurecr.io/edgeops-command-agent:v1 `
>   --registry-server edgeopscmdacr.azurecr.io `
>   --registry-username $cred.username `
>   --registry-password $cred.passwords[0].value `
>   --target-port 8501 --ingress external `
>   --cpu 1.0 --memory 2.0Gi --min-replicas 1 --max-replicas 2
>
> # 5. Azure OpenAI 接続情報を Secret + 環境変数で投入
> az containerapp secret set -n edgeops-command-agent -g rg-edgeops-agent `
>   --secrets azure-openai-key=<your-key>
> az containerapp update -n edgeops-command-agent -g rg-edgeops-agent `
>   --set-env-vars EDGEOPS_USE_MOCK=false `
>     AZURE_OPENAI_ENDPOINT=<your-endpoint> `
>     AZURE_OPENAI_DEPLOYMENT=<your-deployment> `
>     AZURE_OPENAI_API_VERSION=2024-10-21 `
>     AZURE_OPENAI_VISION_DEPLOYMENT=<your-vision-deployment> `
>     AZURE_OPENAI_API_KEY=secretref:azure-openai-key
> ```

## 7つの画面（Streamlit 版）

1. **Command Center** — 設備一覧とリスク状態、影響範囲分析（**回避コスト ROI**）、Teams 通知プレビュー、**Teams 実送信ボタン**、**過去事例パネル（Cosmos）**
2. **Data Upload** — **生データ取り込み（任意の列名CSV→列自動マッピング＋サンプリングレート推定）** / 画像 / メモ / **PDF マニュアル** / **Spresense ストリーム取り込み**
3. **Signal Analysis** — 時系列・FFT・異常スコア
4. **Vision Inspection** — 画像所見と確信度
5. **Agent Reasoning** — Multi-Agent の思考過程 ＋ **エージェントへの追質問（根拠付きQ&A）**
6. **Work Order** — 自動生成された作業指示書 + 承認（Cosmos 永続化）
7. **Management Report** — 管理者向け1ページ報告書（冒頭に**推奨判断＝回避金額**）+ 承認（Cosmos 永続化）

## 機能ごとの設定方法

すべてオプション。未設定なら自動でフォールバックします。

| 機能 | 必要な env | 未設定時の挙動 |
|---|---|---|
| Azure OpenAI | `AZURE_OPENAI_*` | Mock 応答（`EDGEOPS_USE_MOCK=true`） |
| Azure AI Search | `AZURE_SEARCH_ENDPOINT` / `AZURE_SEARCH_API_KEY` / `AZURE_SEARCH_INDEX` | ローカル txt RAG にフォールバック |
| Blob Storage | `AZURE_STORAGE_CONNECTION_STRING` | `_uploaded/` 配下にローカル保存 |
| Cosmos DB | `COSMOS_ENDPOINT` / `COSMOS_KEY` | `_local_cosmos.jsonl` に追記 |
| Teams 通知 | `TEAMS_WEBHOOK_URL` | プレビューカード表示のみ |
| Event Hubs (Spresense) | `EVENT_HUB_CONNECTION_STRING` / `EVENT_HUB_NAME` | `_spresense_stream.jsonl` を読む |

### PDF マニュアル取り込みフロー

1. Streamlit / Next.js から PDF をアップロード
2. `src/pdf_loader.py` がヘッダ検出 or 固定長で分割
3. `src/rag.py` がローカル in-memory ストアに登録
4. Azure AI Search が設定済みなら同じインデックスにも投入
5. 次回の Manual RAG Agent 実行から自動で参照される

### Teams 通知

`TEAMS_WEBHOOK_URL` に Power Automate の HTTP トリガー URL（推奨）または Teams Incoming Webhook URL を設定すると、Command Center に「📣 Teams に通知を送る」ボタンが現れます。

- 送信形式: AdaptiveCard v1.4（リスクレベルでカラー / facts / OpenUrl アクション）
- レガシー Incoming Webhook 用の MessageCard 形式にも自動フォールバック
- 送信結果は Cosmos に `doc_type=alert` で記録される

### Spresense / Event Hubs 連携

詳細は [docs/spresense_firmware.md](docs/spresense_firmware.md) を参照。

実機なしでも次のコマンドでローカルにストリームを流せます:

```powershell
python data/spresense_simulator.py --equipment-id Pump-03 --intensity critical --duration 5
```

Streamlit → Data Upload → 「Spresense ストリーム取り込み」→ 「📡 直近の Spresense サンプルを取得」 → Run Agents で、エッジ → AI 判断 → 報告書 まで一気通貫で確認できます。

## 設備カタログ（マルチ設備対応）

`src/equipment_catalog.py` に登録された **5 種類の設備**を、それぞれ独自のセンサー特性・リスク閾値・下流影響マップで扱います。

| 設備 ID | 種別 | 位置 | 特徴 | リスク閾値の特殊化 |
|---|---|---|---|---|
| Pump-03 | pump | 製造ライン1 / 給水系 | 3000rpm 基幹ポンプ | デフォルト |
| Pump-01 | pump | 製造ライン1 / 冷却 | 冗長化なし循環ポンプ | デフォルト |
| Motor-02 | motor | 搬送ライン2 | 11kW 三相誘導 | 電流許容 5.0A、温度許容 58℃ |
| Fan-04 | fan | 乾燥炉-1 | V ベルト駆動排気送風機 | 音響許容 62dB、振動感度↑ |
| Compressor-05 | compressor | ユーティリティ室 | 0.7MPa 空気圧供給 | 温度許容 65℃、電流許容 6.0A |

各設備 × 4 強度（normal / warning / critical / ambiguous）の **20 プリセット**が利用可能。プリセットキーは `<設備ID>:<強度>` 形式 (`"Motor-02:critical"` など)。後方互換のため `"normal"` / `"warning"` / `"critical"` / `"ambiguous"` は Pump-03 のエイリアスとして残しています。

センサーデータは設備種別ごとの「個性」を持って動的合成されます:

- **Pump**: 50Hz 主回転、175Hz 軸受帯域、温度 35℃
- **Motor**: 30Hz 主回転、160Hz 軸受帯域、温度 40℃、電流 3.5A
- **Fan**: 20Hz、低振動・高騒音ベース、温度 30℃
- **Compressor**: 40Hz、高温運転 42℃、電流 3.8A

### ショップフロア・ビュー

Streamlit / Next.js の Command Center は、現在の強度で全 5 設備をリアルタイム評価してカード表示します。任意のカードをクリックすると、その設備が解析対象に切り替わります（クリック分析）。

### デモデータ（後方互換）

- `data/normal_sensor.csv` — 正常（Pump-03 オリジナル）
- `data/warning_sensor.csv` — 軽度異常
- `data/critical_sensor.csv` — 深刻異常
- `data/ambiguous_sensor.csv` — 判断困難（人間確認推奨）

`ambiguous` データは、AI が断定せず "human confirmation required" を返すことで、実務導入時の Human-in-the-loop を表現します。

## Human-in-the-loop

すべての作業指示書・報告書には「人間承認が必要」フラグが付与され、画面上で **承認 / 修正依頼 / 却下** を選べます。判断時には **却下理由 / 修正指示のテキスト** を残せ、**監査ログ** として Cosmos DB（または `_local_cosmos.jsonl`）に永続化、 JSON でエクスポートできます。AI に最終判断を委ねない設計です。

## 生データ取り込み（実データ対応）

デモのプリセットだけでなく、**任意の実機センサーCSV**を取り込んで同じ8エージェント解析にかけられます（`src/raw_ingest.py`）。

- **列の自動マッピング**: `accel_z` / `振動Z(g)` / `mic_dB` / `temp_C` / `motor_amps` のような非正準な列名を、正準チャンネル（`vibration_z` / `sound_level` / `temperature` / `current` / `timestamp`）へ EN/JP エイリアスで自動割当。UI で手動修正も可能。
- **サンプリングレート**: `timestamp` 列から自動推定（無ければ手動指定）。FFT・軸受帯域解析に反映。
- **検証プレビュー**: 割当済みチャンネル・サンプル数・ルールベース判定を取り込み前にプレビュー。主振動軸（`vibration_z`）未割当時は警告。

Data Upload タブ →「🛠 生データ取り込み」から、アップロード → 列割当 → サンプリングレート → 設備しきい値 → プレビュー → 「この生データで解析する」。FastAPI の `/api/analyze/with-uploads` も同じ正規化を通します（`sample_rate_hz` 任意指定可）。

## エージェントへの追質問（根拠付きQ&A）

Agent Reasoning タブに **対話パネル**を追加。生成済みの解析結果に対して「なぜこの判断？」「いつまでに対応すべき？」「放置するとどうなる？」「コストは？」を自由に質問でき、回答は**8エージェントの出力だけを根拠**にします（`agents.run_followup_qa`）。各回答に**根拠Agent**・**確信度**・**要人間確認**フラグを表示。Azure OpenAI 接続時は実LLM、未接続時も解析結果の実データから根拠付きで応答する決定論フォールバックで動作します。

## ビジネスインパクト（ROI 自動算出）

`src/business_case.py` が「放置（計画外停止）」と「早期介入（計画停止）」の差分を**円で自動算出**します。LLM には計算させず、`data/failure_history.csv`（実停止時間）と `data/parts_inventory.csv`（部品単価・在庫・リードタイム）から監査可能な金額を導出。Command Center の影響カードと管理者報告書の冒頭（推奨判断）に表示します。

- 例: **Motor-02 Critical** → 放置時 約 ¥2,123,400 / 期待回避コスト 約 ¥2,107,800（軸受は在庫0・調達5日の調達リスクも併記）
- ライン停止コスト前提（既定 ¥300,000/h）は `EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR` で調整可。前提は画面・報告書に明記。

## エージェント品質の検証

`scripts/run_eval.py` が **5 設備 × 4 強度 = 20 プリセット**で 8 Agent パイプラインを mock モードで決定論実行し、出力がスキーマ妥当かつ**ポリシー準拠**（Critical→承認必須・24h以内 / 非Normal→自動実行不可 / ambiguity→人間確認 / What-if 3シナリオ・放置は非推奨 等）かを検証します。

```powershell
python scripts/run_eval.py   # マトリクスを表示し docs/eval_results.md を生成
```

結果は **20/20 プリセット・108/108 チェック合格**（[docs/eval_results.md](docs/eval_results.md)）。`tests/test_agent_eval.py` で CI にも組み込み済み。

## ディレクトリ構成

```
EdgeOps Command Agent/
├── app.py                          # Streamlit エントリポイント
├── backend/                        # FastAPI バックエンド
│   ├── main.py
│   ├── Dockerfile
│   └── README.md
├── frontend/                       # Next.js (App Router) フロント
│   ├── app/
│   │   ├── page.tsx
│   │   ├── layout.tsx
│   │   └── globals.css
│   ├── lib/api.ts
│   ├── package.json
│   ├── Dockerfile
│   └── README.md
├── src/
│   ├── agents.py                   # 8 Agent + run_pipeline
│   ├── sk_orchestrator.py          # Semantic Kernel ラッパ
│   ├── signal_analysis.py          # FFT / RMS / 帯域エネルギー
│   ├── risk_engine.py              # ルールベース判定
│   ├── prompts.py                  # 全プロンプトテンプレ
│   ├── rag.py                      # AI Search / PDF / 局所 RAG ディスパッチ
│   ├── raw_ingest.py               # 生データCSV→正準スキーマ（列自動マッピング/fs推定）
│   ├── ai_search.py                # Azure AI Search クライアント
│   ├── pdf_loader.py               # PDF 抽出 + チャンク化
│   ├── blob_store.py               # Blob Storage ラッパ
│   ├── cosmos_store.py             # Cosmos DB ラッパ
│   ├── teams_notify.py             # Teams Webhook 送信
│   ├── iot_ingest.py               # Event Hubs 受信 + ローカル JSONL
│   ├── report_generator.py         # Work Order / Management Report
│   ├── business_case.py            # ROI / 経済効果の決定論算出
│   └── utils.py
├── scripts/
│   └── run_eval.py                 # 20プリセットのエージェント品質評価ハーネス
├── data/
│   ├── generate_demo_data.py
│   ├── spresense_simulator.py      # Spresense シミュレータ
│   ├── *_sensor.csv
│   ├── failure_history.csv
│   ├── parts_inventory.csv
│   └── maintenance_manual.txt
├── docs/
│   ├── architecture.md
│   ├── demo_script.md
│   ├── eval_results.md             # 評価マトリクス（run_eval.py が生成）
│   ├── spresense_firmware.md       # Spresense + Event Hubs ガイド
│   └── ...
└── assets/
    ├── compressor_*.png         # compressor demo photos
    ├── fan_*.png                # fan demo photos
    ├── motor_*.png              # motor demo photos
    └── pump_*.png               # pump demo photos
```

## ライセンス

MIT
