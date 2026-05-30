# EdgeOps Command Agent — アーキテクチャ

> 図解版（PNG）: [./architecture.png](./architecture.png)
> 再生成: `python tools/generate_architecture_diagram.py`

## 1. 全体構成

```
                     ┌─── Spresense / Sensor CSV / 画像 / PDF / メモ ────┐
                     ▼                                                    │
     ┌────────────────────────────┐                                       │
     │  Azure Event Hubs (任意)    │ ←─── 未設定なら local JSONL          │
     └────────────┬───────────────┘                                       │
                  ▼                                                       │
┌──────────────────────────────────────────────────────────────────────────┐│
│        UI Layer (Streamlit 7 tabs) + Next.js Web (FastAPI 経由)         │ │
└──────────────────┬──────────────────────────────────┬────────────────────┘│
                   │                                  │                    │
                   ▼ FastAPI                          │                    │
┌──────────────────────────────────────────────────────────────────────────┐│
│             Multi-Agent Orchestrator (src/agents.py, 8 Agent)            ││
│                                                                          ││
│   ① Intake          ② Signal Insight    ③ Vision Inspection             ││
│   ④ Manual RAG      ⑤ Root Cause        ⑥ Action Planning               ││
│   ⑦ What-if         ⑧ Governance                                         ││
│                                                                          ││
│   ＋ Report Agent (Management Report レンダリング時)                     ││
└────┬───────────────────┬───────────────────┬─────────────────────────────┘│
     │                   │                   │                              │
     ▼                   ▼                   ▼                              │
┌──────────────┐ ┌─────────────────────┐ ┌──────────────────────────────┐  │
│  Signal /    │ │  Semantic Kernel +  │ │  Azure AI Search             │  │
│  Risk Engine │ │  Azure OpenAI       │ │   (Manual RAG)               │  │
│  (rule-based)│ │  (Microsoft Foundry)│ │  未設定時はローカル txt RAG  │  │
│  監査可能     │ │  ＋ Vision モデル   │ │                              │  │
└──────────────┘ └─────────────────────┘ └──────────────────────────────┘  │
     │                   │                              │                  │
     ▼                   ▼                              ▼                  │
┌──────────────────────────────────────────────────────────────────────────┐│
│  Outputs:  Work Order / Management Report / Teams Adaptive Card / 監査  ││
└────┬───────────────────┬──────────────────────────┬──────────────────────┘│
     ▼                   ▼                          ▼                       │
 Cosmos DB        Azure Blob Storage          Teams Webhook                 │
 (監査・実行履歴)  (画像 / PDF / CSV)           (Power Automate)              │
                                                                            │
実行基盤: Azure Container Apps  ／  CD: GitHub Actions (ACR push → update)
```

## 2. レイヤ分離

このシステムは「**ルールベース判定**」と「**LLM による解釈・生成**」を明確に分けています。

| レイヤ | 担当 | 採用技術 |
|--------|------|----------|
| **計測** | センサーCSV を取り込み、FFT/RMS/勾配などの数値特徴量を計算 | numpy, scipy, pandas |
| **判定** | マニュアル由来の閾値で Normal/Warning/Critical を決定。**ここは LLM を介さない** | Python（[src/risk_engine.py](../src/risk_engine.py)） |
| **推論** | Multi-Agent で原因仮説・作業手順・報告書を組み立てる | Semantic Kernel `Kernel` + `AzureChatCompletion`（[src/sk_orchestrator.py](../src/sk_orchestrator.py)）。`LLMClient.complete()` がテキスト全 Agent を SK にルーティングし、失敗時のみ Azure OpenAI SDK 直叩きにフォールバック |
| **検索** | マニュアルから根拠を引いてくる | ローカル RAG（Azure AI Search に置換可能） |
| **承認** | AI の提案は **人間承認** をもって有効化 | Streamlit UI（Human-in-the-loop） |

> **なぜルールベースを残すか:** 設備保全の現場で AI に最終判定を委ねるのは現状非現実的です。**Risk Level は監査可能でなければならない**ため、しきい値による分類は LLM の外側で固定します。LLM は「数値の解釈」「自然言語化」「原因仮説」「報告書生成」を担います。

## 3. Multi-Agent オーケストレーション (8 Agent)

```
[Sensor / 画像 / Memo / PDF]
        │
        ▼
① Intake Agent — 入力品質・欠損・期間を検証 (degraded なら下流に警告)
        │
        ├─► ② Signal Insight Agent  (FFT / RMS / 軸受帯域)
        │
        ├─► ③ Vision Inspection Agent  (領域分割 + severity + crop)
        │
        ├─► ④ Manual RAG Agent  (Azure AI Search / ローカル txt RAG)
        │
        ▼
⑤ Root Cause Agent (②③④ + 故障履歴 を統合 → 尤度付き仮説 ×3)
        │
        ├─► ⑥ Action Planning Agent (作業手順 / 工具 / 部品 / 期限)
        │
        ├─► ⑦ What-if Simulator (今点検 / 3日後 / 1週間放置 比較)
        │
        ▼
⑧ Governance Agent — 全 Agent の不確実性集約・人間承認チェックポイント生成
        │
        ▼
Work Order  +  Management Report (Report Agent: render 時呼び出し)
        │
        ▼
人間承認 (承認 / 修正依頼 / 却下) → Cosmos 監査ログ → Teams 通知
```

各 Agent は単一責任を持ちます。**Intake** がパイプラインの入口で入力データの品質を保証し、
**Governance** がパイプラインの出口で AI 判断のリスクを管理者向けに要約します。

実装は [src/agents.py](../src/agents.py) の `run_pipeline()` に集約されています。LLM 呼び出しは `LLMClient` 経由で、Azure OpenAI が設定されていない場合は決定的なモックにフォールバックします。これにより **ネットワーク無しでもデモが完走する** ことを保証しています（ハッカソン審査で重要）。Governance Agent の `fallback_used` フィールドで、どの Agent がモックに落ちたかを監査ログに残します。

## 4. プロンプト設計

- **すべての Agent 出力は JSON**。`response_format={"type": "json_object"}` で構造化。
- **断定禁止**: "腐食です" ではなく "腐食の可能性があります"。
- **不確実性の明示**: 各 Agent は `uncertainty` / `human_confirmation_required` を返す。
- プロンプトは [src/prompts.py](../src/prompts.py) に集約。記事から参照しやすい。

## 5. RAG レイヤ

MVP は `data/maintenance_manual.txt` に対するキーワードマッチ（[src/rag.py](../src/rag.py)）。

```python
def search(query_terms: list[str], top_k: int = 3) -> list[RetrievalResult]:
    ...
```

返却型 `RetrievalResult` は Azure AI Search の検索結果と互換にしてあるので、本番では `src/rag.py` を Azure AI Search クライアントに置き換えるだけで RAG レイヤを差し替えられます。Zenn 記事に書く「実運用は Azure AI Search に置換可能」の根拠です。

## 6. Azure 構成（推奨）

| 役割 | サービス | 備考 |
|------|---------|------|
| 実行基盤 | **Azure Container Apps** | `az containerapp up --source .` 一発デプロイ |
| LLM | **Azure OpenAI (Microsoft Foundry)** | GPT-4o 系を推奨。Vision にも同モデルを流用可 |
| 検索 | **Azure AI Search**（任意） | MVP は不要、本番は強く推奨 |
| ストレージ | Azure Blob Storage | センサーCSV / 画像 / マニュアル |
| 認証 | Microsoft Entra ID（任意） | 審査時はパスワード認証で代替可 |

ハッカソン要件である「Microsoft Azure 実行基盤」「Microsoft AI 技術（Foundry / Semantic Kernel）」を両方満たします。

## 7. Human-in-the-loop

すべての Agent 出力に `human_confirmation_required` を含めます。Work Order と Management Report の UI には **承認 / 修正依頼 / 却下** のボタンが並びます。これは飾りではなく、設備保全に AI を投入する際の合意形成プロセスを **実装レベルで** 想定しているという意思表示です。

## 8. ディレクトリ構成

```
edgeops-command-agent/
├── app.py                  # Streamlit エントリポイント
├── requirements.txt
├── Dockerfile
├── .env.example
├── README.md
├── data/
│   ├── generate_demo_data.py
│   ├── normal_sensor.csv      ── 正常
│   ├── warning_sensor.csv     ── 軽度異常
│   ├── critical_sensor.csv    ── 深刻異常
│   ├── ambiguous_sensor.csv   ── 判断困難
│   ├── maintenance_manual.txt
│   ├── failure_history.csv
│   └── parts_inventory.csv
├── assets/
│   ├── generate_placeholders.py
│   ├── pump_normal.png
│   ├── pump_warning.png
│   ├── pump_critical.png
│   ├── motor_*.png
│   ├── fan_*.png
│   └── compressor_*.png
├── src/
│   ├── __init__.py
│   ├── utils.py            # JSON抽出、env、Demo presets
│   ├── signal_analysis.py  # FFT, RMS, trend, bearing band
│   ├── risk_engine.py      # ルールベース判定
│   ├── prompts.py          # 全Agent のプロンプト
│   ├── rag.py              # マニュアル検索
│   ├── agents.py           # 8 Agent + パイプライン
│   └── report_generator.py # Work Order / Management Report
└── docs/
    ├── architecture.md     # 本書
    ├── demo_script.md
    └── zenn_article_draft.md
```
