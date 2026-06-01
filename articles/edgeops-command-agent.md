---
title: "異常検知で終わらせない。現場保全の判断と行動を支援するAIエージェントをAzureで作った"
emoji: "🛠"
type: "tech" # tech: 技術記事 / idea: アイデア
topics: ["azure", "openai", "semantickernel", "python", "ai"]
published: true
---

![EdgeOps Command Agent ― 点検データを8つのAIエージェントが判断・原因・初動・報告へ変換する](/images/edgeops/eyecatch.png)

## TL;DR

- **課題**: 設備保全は「異常検知」で止まりがち。**危険判断・原因推定・初動・報告が属人化**し、若手は次に何をすべきか分からない。
- **解決**: 点検データを **異常判断 → 原因推定 → 作業指示 → 管理者報告書** に変換する **8 Agent** のマルチエージェント。
- **価値（円で算出）**: Motor-02 が Critical のとき、早期介入で **約 ¥2,107,800 を回避**（LLM に計算させず、実績CSVから決定論で導出・監査可能）。
- **動く証拠**: 5設備×4強度=20プリセットで **20/20・ポリシーチェック 108/108 合格**。全提案に **人間承認ゲート＋監査ログ**。

🎥 約5分デモ → https://youtu.be/N-M7Sm0fNi8　🌐 今すぐ触る → https://edgeops-command-agent.victoriousriver-84ba0565.eastus2.azurecontainerapps.io/　📦 コード → https://github.com/YUTAKONDO1205/EdgeOps-Command-Agent

> **異常検知で終わらせない。** ―― 本プロジェクトは、この一点に全機能を寄せて設計しました。

私はこれまで Sony Spresense と振動・音響センサを用いた異常検知システムを研究してきました。その中で痛感したのは、**異常を検出するだけでは現場の行動は変わらない**ということです。本当に必要なのは「何が起きているのか」「なぜ起きたのか」「誰が次に何をすべきか」まで判断材料を整理すること。そこで開発したのが **EdgeOps Command Agent** です。

## 1. 解決したい業務課題

設備保全の現場では、判断材料が **バラバラに** 存在しています。

- センサー値・振動/音響ログ
- 点検写真・点検メモ
- 設備マニュアル・点検基準
- 過去の故障履歴・作業報告書
- 部品在庫・作業者の経験

異常が起きても、**「これは本当に危険なのか」「原因は何か」「今すぐ止めるべきか」「誰が何を確認すべきか」「報告書にどう書くか」** の判断が属人化しています。ベテランの頭の中にしかなく、若手は次の一手が分かりません。異常検知 AI を入れても、ここが埋まらなければ現場は動きません。

## 2. いくら得するのか ―― 経済効果を定量化する（ROI）

「異常を早く見つけると何円得するのか」を曖昧にしないため、**判断の経済効果を自動算出**する決定論モジュール（`src/business_case.py`）を入れました。**LLM には一切計算させず**、出荷済みの CSV（`failure_history.csv` の実停止時間、`parts_inventory.csv` の部品単価・在庫・リードタイム）から監査可能な金額を出します。

モデルは「放置（計画外停止）」と「早期介入（計画停止）」の差分です。

```
放置コスト     = 計画外停止 downtime_h × ライン停止コスト/h ＋ 緊急部品（割増）
早期介入コスト = 計画停止枠で実施 → 生産影響≒0、部品は通常価格
回避コスト     = 放置コスト − 早期介入コスト
期待回避コスト = 回避コスト × 悪化確率（リスクレベル依存）
```

たとえば **Motor-02 が Critical** のとき（過去実績 7h 停止・軸受 BRG-M02-6206ZZ）:

| 項目 | 金額 |
|---|---|
| 放置時コスト（計画外停止7h＋緊急部品） | ¥2,123,400 |
| 早期介入時コスト（計画停止・通常調達） | ¥15,600 |
| **期待回避コスト** | **¥2,107,800** |

さらに Motor-02 の軸受は **在庫0・調達5日**。早期検知できれば 5 日前倒しで手配でき、「即日対応できない」リスクそのものを消せます。この金額と調達リスクを Command Center の影響カードと管理者報告書の冒頭（推奨判断）に出します。ライン停止コストの前提（既定 ¥300,000/h）は `EDGEOPS_LINE_STOP_COST_JPY_PER_HOUR` で顧客値に差し替え可能で、前提は画面・報告書に明記します。

![Command Center の影響範囲カード：Motor-02 Critical で早期介入の想定回避コスト ¥2,107,800、軸受の調達リスク（在庫0・調達5日）、下流設備（コンベア-2 / 充填ライン3）への波及を一望](/images/edgeops/roi-card.png)

## 3. 作ったもの

**EdgeOps Command Agent** は、点検データを読み取り、

1. **Intake Agent** が入力データの品質を検証し
2. 異常傾向を検出（**ルールベース**で監査可能に）
3. 原因仮説を尤度付きで出し（**Multi-Agent**）
4. マニュアル根拠を引き（**RAG**）
5. 作業指示を生成し
6. 管理者向け報告書を生成し
7. **Governance Agent** が不確実性を集約して **人間承認** に回す

までを一気通貫で行います。8 つの Agent がそれぞれ単一責任を持ち、Root Cause Agent が中央で他 Agent の出力を統合します。

🎥 **まずは約5分のデモ動画でご覧ください**（やや長めですが、全機能を通しでご覧いただけます）:

https://youtu.be/N-M7Sm0fNi8

### 画面構成

7つのタブで業務フロー全体を再現:

1. **Command Center** — 設備一覧とリスク状態・回避コスト
2. **Data Upload** — **生データCSV取り込み（列自動マッピング＋fs推定）** / 画像 / メモ / マニュアル投入
3. **Signal Analysis** — 時系列・FFT・統計量・ルール判定
4. **Vision Inspection** — 写真と AI 所見
5. **Agent Reasoning** — Multi-Agent の思考過程 ＋ 根拠付き追質問Q&A
6. **Work Order** — 作業指示書（承認ボタン付き）
7. **Management Report** — 管理者向け1ページ報告書

## 4. システム構成

![EdgeOps Command Agent Architecture](/images/edgeops/architecture.png)

```
[Streamlit / Next.js UI]
    │
    ▼
[8-Agent Orchestrator (Semantic Kernel)]
  Intake → Signal → Vision → Manual RAG → Root Cause
         → Action Planning → What-if → Governance
    │            │             │
    ▼            ▼             ▼
[Risk Engine]  [Azure OpenAI]  [RAG: Azure AI Search / ローカル txt / PDF]
 (監査可能)     (Foundry)
    │
    ▼
[Cosmos DB 監査ログ] [Blob 画像/PDF] [Teams 通知] [Event Hubs (Spresense)]
```

| レイヤ | 採用技術 |
|--------|----------|
| アプリ | Python + Streamlit（メインデモ）／ Next.js + FastAPI（別実装の SaaS 風 UI） |
| 信号解析 | pandas / numpy / scipy（FFT, RMS, Welch PSD） |
| グラフ | Plotly |
| Agent 制御 | **Semantic Kernel**（`Kernel` + `AzureChatCompletion`）が一次経路。失敗時のみ Azure OpenAI SDK 直叩きにフォールバック |
| 生成AI | Azure OpenAI（Microsoft Foundry） |
| 画像解析 | Azure OpenAI Vision 対応モデル（マルチモーダル）|
| RAG | 3段ディスパッチ: Azure AI Search → ローカル txt → アップロード PDF（インターフェース互換） |
| データ保存 | **Azure Blob**（画像/PDF）＋ **Azure Cosmos DB**（承認ログ/実行履歴/通知履歴）。未設定時はローカル `_uploaded/` / JSONL に自動フォールバック |
| 通知 / エッジ | **Microsoft Teams**（Adaptive Card v1.4）＋ **Azure Event Hubs**（Spresense ストリーム）。いずれもローカルフォールバックあり |
| 実行基盤 | Azure Container Apps（`az containerapp up --source .`、CD は GitHub Actions） |
| 監査 | 承認 / 修正依頼 / 却下＋理由テキスト＋ Cosmos 監査ログ＋ JSON/CSV エクスポート |

## 5. Agent 設計 ―― なぜ「マルチエージェント」なのか

Agent は単一責任に分け、Root Cause Agent が他 Agent の出力を統合する形にしています。

| # | Agent | 役割 | 不確実性管理 |
|---|-------|------|--------------|
| 1 | **Intake** | 入力データの品質・欠損・期間妥当性をパイプライン入口で検証 | `data_quality` (good/acceptable/degraded), `downstream_warnings` |
| 2 | Signal Insight | センサー特徴量を所見に翻訳 | `uncertainty_notes` |
| 3 | Vision Inspection | 写真から外観異常候補（領域 + severity + bbox + crop）を出す | `confidence_score`, `human_confirmation_required` |
| 4 | Manual RAG | マニュアルから判断ルールを引く（Azure AI Search 互換） | `applicable_rules` の `judgement` |
| 5 | Root Cause | ②③④ を統合し、原因仮説を尤度付きで3つ出す | `uncertainty`, `human_confirmation_required` |
| 6 | Action Planning | 作業手順・工具・部品・安全注意を組み立てる | `manager_approval_required` |
| 7 | What-if Simulator | 「今点検 / 3日後 / 1週間放置」の対応タイミング別リスク比較 | `recommended` フラグ |
| 8 | **Governance** | 全 Agent の出力を集約し、人間承認の確認項目を提示するゲートウェイ | `overall_confidence`, `auto_executable`, `fallback_used` |
| ＋ | Report | 管理者向け1ページ報告書（render 時呼び出し） | — |

### 「LLM を1回呼ぶ」では足りない理由

「マルチエージェントです」と言うだけなら誰でも言えます。本作で重要なのは、**各 Agent が単一責任で次の Agent に判断材料を引き渡し、最後に Governance がゲートを閉じる**という、課題に対して論理的な分業になっている点です。実際に Motor-02 が Critical のときの 1 実行は、こう流れます。

| Agent | この実行での結論（要約） |
|---|---|
| Intake | 入力 4 ソース揃い `data_quality=good`。観測 4.0s で傾向解析に十分 |
| Signal Insight | 振動RMS・軸受帯（100–300Hz）エネルギーが同時上昇。軸受異常帯域にピーク |
| Vision | フレーム表面に錆び/腐食の進行（severity=severe, confidence 88） |
| Manual RAG | 「軸受温度しきい値超過は Critical」「振動＋温度同時上昇は軸受摩耗を疑う」が **該当** |
| Root Cause | 仮説①軸受摩耗（**likelihood: high**）／②固定部緩み（medium）／③潤滑不良（medium） |
| Action Planning | priority=Critical, **deadline 24h**, 必要部品 BRG-M02-6206ZZ, **管理者承認必須** |
| What-if | 今点検=低リスク（**推奨**）／3日後=中／**1週間放置=高（非推奨）** |
| Governance | overall_confidence、**`auto_executable=false`（人間承認ゲート）**、確認項目と安全制約を提示 |

単体の LLM 呼び出しでは、この「**誰がどの根拠で何を結論したか**」が1つの文章に溶けてしまい、監査も差し替えもできません。責任分割しているからこそ、**Manual RAG だけ Azure AI Search に差し替える / Vision だけ別モデルにする**といった現場適合が効きます。

![Agent Reasoning タブ：8つのエージェントが順に判断材料を積み上げ（各カードは Azure OpenAI 実行）、最後に人間承認ゲートへ渡る思考トレース](/images/edgeops/agent-reasoning.png)

### なぜ「ルールベース判定」と「LLM 推論」を分けたか

設備保全で AI に最終判断を委ねるのは現状非現実的です。**Risk Level は監査可能でなければならない**ため、しきい値による分類（`src/risk_engine.py`）は LLM の外側に固定しました。LLM が担うのは「数値の解釈」「自然言語化」「原因仮説」「報告書生成」だけです。判定の根拠を後から追えるようにするための線引きで、ここを設計の核にしました。

## 6. プロンプト設計の工夫

すべての Agent 出力は **JSON** に固定し、`response_format={"type": "json_object"}` で構造化しました。設計原則は3つ:

1. **断定禁止**: 「腐食です」ではなく「腐食の可能性があります」
2. **不確実性の明示**: 各 Agent が `uncertainty` と `human_confirmation_required` を返す
3. **根拠と推測を分ける**: `evidence` / `root_cause_hypotheses` の構造化

例: Root Cause Agent のプロンプト（一部）

```python
ROOT_CAUSE_AGENT_PROMPT = """\
あなたは「Root Cause Agent」です。
信号解析、画像所見、マニュアル根拠、過去故障履歴を統合し、
原因仮説を尤度付きで3つまで出してください。

# 出力スキーマ（JSONのみ）
{
  "abnormality_summary": "...",
  "evidence": [...],
  "root_cause_hypotheses": [
    {"cause": "...", "likelihood": "high|medium|low", "reason": "...",
     "additional_checks": [...]}
  ],
  "uncertainty": "...",
  "human_confirmation_required": true
}

# 制約
- 1つに断定しない。複数仮説を出す
- 根拠は入力データに対応していること
- 不確実なら likelihood を low にする
"""
```

## 7. 「分からない」と言えるAI ―― 判断困難ケース

デモ用に4種類のセンサーCSV を用意しました。

| データ | 状態 | 見せたい価値 |
|--------|------|--------------|
| `normal_sensor.csv` | 正常 | AIが過剰反応しない |
| `warning_sensor.csv` | 軽度異常 | 経過観察と追加確認を提案 |
| `critical_sensor.csv` | 深刻異常 | 作業指示・報告書まで生成 |
| `ambiguous_sensor.csv` | **判断困難** | **不確実性と人間確認を出す** |

特に `ambiguous` は本作の主張そのものです。多くの AI デモは「答えを出して終わり」ですが、本システムは **断片的なスパイク** に対して「確信度低、現場確認推奨」と返します。自信たっぷりに間違えるより、正直に「分からない」と言って人間に渡す方が現場では信頼されます。これが実務で本当に欲しい挙動です。

## 8. Human-in-the-loop

すべての提案には「人間承認が必要」フラグが付与され、UI 上で **承認 / 修正依頼 / 却下** を選べます。これは飾りではなく、Work Order と Management Report の両方に承認ウィジェットを実装し、**承認・却下・修正依頼の理由テキスト**を残して **Cosmos DB に監査ログ**として永続化（JSON/CSV エクスポート可）しています。設備保全に AI を入れるとき必ず要る合意形成のプロセスを、画面の機能として作り込みました。

![Work Order の人間承認ウィジェット：承認／修正依頼／却下を理由テキスト付きで選べ、判断は監査ログに永続化される](/images/edgeops/work-order-approval.png)

## 9. 軸受異常を周波数帯から特定する

センサー解析の見せ場として、**100–300Hz 帯のエネルギー比率** を入れました。遠心ポンプの軸受異常は、シャフト回転周波数（50Hz）の高調波として 100Hz / 240Hz 付近にピークが現れます。

```python
def _bearing_band_ratio(signal, fs):
    f, pxx = welch(signal - signal.mean(), fs=fs, nperseg=256)
    total = trapezoid(pxx, f)
    mask = (f >= 100.0) & (f <= 300.0)
    band = trapezoid(pxx[mask], f[mask]) if mask.any() else 0.0
    return band / total
```

Critical デモでは比率が **0.69**（正常時は 0.10 程度）。FFT グラフ上にも 120Hz のピークがはっきり見えます。これは私の Spresense × 振動センサ研究で扱ってきた手法そのものです。

![Signal Analysis タブ：振動・音響・温度の時系列に加え、FFT スペクトラムに 120Hz の軸受帯ピーク（100–300Hz エネルギー比 0.69）が明瞭に現れる](/images/edgeops/fft-peak.png)

## 10. Azure 構成とデプロイ

```bash
az login
az group create --name rg-edgeops-agent --location japaneast
az containerapp up \
  --name edgeops-command-agent \
  --resource-group rg-edgeops-agent \
  --location japaneast \
  --source . \
  --ingress external \
  --target-port 8501
```

これだけです。`az containerapp up --source .` がローカルの Dockerfile をビルドし、ACR にプッシュし、Container Apps を立ち上げます。

> **補足**: Azure CLI 2.86.0 では `az containerapp up --source .` 内部で `OS.linux.value` を参照する箇所が None となり失敗する既知症状に当たりました。回避策として、`az acr create` → `docker build` → `docker push` → `az containerapp env create` → `az containerapp create --image ...` の 5 ステップに分解してデプロイしました（GitHub の README に再現手順を残してあります）。

審査員はこの URL を開くだけで全機能を試せます。Azure OpenAI のクレデンシャルが未設定の場合は **Mock mode** に自動フォールバックする実装にしてあり、ネットワーク不調時もデモが完走します。

> 誤解のないよう補足すると、**本番の推論経路は Azure OpenAI（Microsoft Foundry）です。** mock は鍵未設定・ネットワーク不調時にデモを完走させるための **耐障害フォールバック** であり、Azure 接続時は常に実モデルが優先されます（`EDGEOPS_USE_MOCK` または endpoint/key の有無で切替）。「動かないときに止まらない」ことを、実務運用の最低条件として実装しています。

## 11. 実務導入を想定した工夫

- **ルールベース判定は LLM の外**で監査可能に
- **デモデータ4種類**（normal / warning / critical / ambiguous）で過剰反応も判断困難ケースもカバー
- **断定禁止プロンプト** で「腐食です」ではなく「腐食の可能性があります」
- **JSON 構造化出力** で UI 表示が崩れない
- **モックフォールバック** で Azure OpenAI が落ちてもデモが完走
- **生データ取り込み** で実機CSV（任意の列名・サンプリングレート）も列自動マッピングで解析可能。デモデータ依存にしない
- **根拠付き追質問Q&A** で解析結果を対話的に深掘り（断定回避・根拠Agent明示）
- **Markdown ダウンロード** で報告書を Teams や紙にそのまま渡せる
- **Human-in-the-loop 承認** を UI レベルで実装。承認・却下・修正依頼に **理由テキスト** を残せ、**監査ログ** を JSON で出力可能
- **Microsoft Teams 通知**（Adaptive Card v1.4）を Command Center に組み込み、Power Automate / Logic Apps による実通知への拡張を意識した形に
- **影響範囲（インパクト分析）カード** を Command Center に追加。Warning / Critical 時に「影響規模ラベル」「想定停止対応期限」「想定生産影響」「連動・下流設備（例: Pump-03 → Tank-A 送液停止 / Reactor-1 原料供給遅延）」を一望できる。実運用では設備マスタや BOM 連携で依存マップを差し替える設計

## 12. エージェント品質をどう検証したか

「8 Agent が『設計どおりに』振る舞う」ことを、人手の採点なしで示すために評価ハーネス（`scripts/run_eval.py`）を用意しました。**5 設備 × 4 強度＝20 プリセット**を mock モードで決定論実行し、出力がスキーマ妥当かつ**ポリシー準拠**かを検証します（CI でも `tests/test_agent_eval.py` が回ります）。

検証する不変条件（一部）:

- Critical の作業指示は**必ず管理者承認必須・期限24h以内**
- 非 Normal は `auto_executable=false`（人間承認ゲートが必ず閉じる）
- ambiguity 検出時は `human_confirmation_required=true`
- What-if は常に「今 / 3日後 / 1週間放置」の 3 シナリオを返し、「放置」は `recommended=false`

結果は **20/20 プリセットが全ポリシー合格（総チェック 108/108 件合格）**。詳細マトリクスは `docs/eval_results.md`。「動きました」を主観で言い切らず、満たすべき不変条件として機械検証できる形にしてあります。

## 13. 今後の展望

実装済み（Cosmos 永続化・Teams Adaptive Card・Event Hubs/Spresense 取り込み・Azure AI Search RAG）を踏まえ、次の一手:

- 過去事例（Cosmos 蓄積）を Root Cause Agent の RAG ソースに組み込む事例ベース推論
- Power Automate と連携した **異常 → Teams 通知 → 承認 → 作業指示配布** のフル業務フロー化
- ライン停止コスト・BOM・設備マスタの実データ連携で ROI を現場の実数に
- 実エッジ（Spresense 実機）からの常時ストリーミング運用

---

研究では「異常を見つける」ところまで何度も作ってきましたが、その先の判断を現場が動く形にするのは、検知とはまったく別の難しさでした。EdgeOps Command Agent は、その先を 8 つの Agent と人間承認で埋めようとした試みです。

同じように「検知のその先」で詰まっている方の手がかりになれば嬉しいです。実際に触れる URL とソースコードは冒頭に置いてあります。
