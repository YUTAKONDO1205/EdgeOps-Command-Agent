# デモ動画 YouTube 公開メタdata

録画後、YouTube に**限定公開**でアップロードする際にそのまま使える素材。
タイムライン・撮影手順は [demo_script.md](demo_script.md)、字幕は [demo_captions.srt](demo_captions.srt)。

## 公開設定

- 公開範囲: **限定公開（Unlisted）** — ルール上、審査員が視聴できればよい
- 長さ: **3:00 以内**
- 字幕: `demo_captions.srt` をアップロード（日本語）。ミュート視聴でも伝わるように必須

## タイトル候補（どれか1つ）

1. `EdgeOps Command Agent｜点検データを「判断と行動」に変えるAzure保全AIエージェント`
2. `異常検知で終わらせない — 8エージェント保全AI(Azure/Semantic Kernel)デモ`
3. `現場保全AIエージェント EdgeOps Command Agent 3分デモ（Microsoft Agent Hackathon）`

（推奨: 1。何をするものか＋差別化「判断と行動」が一目で伝わる）

## 概要欄（説明文・コピペ用）

```
EdgeOps Command Agent は、設備点検データを「異常判断 → 原因推定 → 作業指示 → 報告書」まで
一気通貫で変換する現場保全AIエージェントです。Microsoft Agent Hackathon（個人部門）提出作品。

▶ 触れる成果物（Azure Container Apps）
https://edgeops-command-agent.victoriousriver-84ba0565.eastus2.azurecontainerapps.io/

▶ ソースコード（GitHub）
https://github.com/YUTAKONDO1205/EdgeOps-Command-Agent

▶ 解説記事（Zenn）
（公開後にURLを記載）

■ 特徴
・8つのAIエージェント（Intake→Signal→Vision→Manual RAG→Root Cause→Action→What-if→Governance）が連携
・Microsoft Foundry / Azure OpenAI + Semantic Kernel
・リスク判定はルールベースで監査可能（LLMの外側）
・放置 vs 早期介入の経済効果を円で自動算出（ROI）
・人間承認（承認/修正依頼/却下＋理由）を前提にした Human-in-the-loop 設計
・Azure AI Search / Cosmos DB / Blob / Event Hubs / Teams 連携

■ チャプター
0:00 課題
0:20 Command Center / Critical 判定
0:35 影響範囲とROI（回避できる金額）
0:55 信号解析（FFT・軸受異常帯）
1:20 画像所見（断定しないVision）
1:35 8エージェントの推論
2:00 作業指示書の自動生成
2:25 管理者報告書（推奨判断＝回避金額）
2:38 判断困難ケース（現場確認推奨）
2:48 What-if 3シナリオ

#Azure #AIエージェント #SemanticKernel #予知保全 #設備保全
```

## サムネイル仕様

- 解像度: 1280×720（YouTube 推奨）
- 主役: **Pump-03 の Command Center**（赤い Critical バッジが見える状態）
- 重ねる文字（大きく・2行まで）: 「異常検知で終わらせない」／「点検データ → 判断と行動」
- 右下に小さく: アーキ図のミニチュア（[architecture.png](architecture.png) を縮小）＋「8 Agents on Azure」
- 配色: 危機感を出す赤 × 信頼感の濃紺。文字は白＋濃い縁取りでスマホでも可読
- 余白に「Microsoft Agent Hackathon」ロゴ/表記を小さく（任意）

## 撮影前チェック（収録をスムーズにする）

- [ ] アプリURLを開き、サイドバーのバッジが **Live AI（緑）** か **Mock（黄）** か確認（実LLM接続でのデモが理想。難しければ黄バッジのまま「決定的デモ」と一言入れる）
- [ ] サイドバーで **Critical** プリセットをロード（冒頭がスムーズ）
- [ ] 1920×1080・シークレットウィンドウ・拡張機能オフ
- [ ] ナレーションは `demo_captions.srt` の文言に沿って読む（字幕と一致させる）
