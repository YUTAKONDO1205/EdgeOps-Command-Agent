# EdgeOps Command Agent — Frontend (Next.js)

Streamlit 版と並列に動く Next.js (App Router) フロントです。バックエンドの FastAPI (`backend/`) と接続します。

## ローカル実行

```powershell
cd frontend
npm install
# バックエンド URL は環境変数で渡す（省略時は http://localhost:8000）
$env:NEXT_PUBLIC_API_BASE = "http://localhost:8000"
npm run dev
```

ブラウザ: <http://localhost:3000>

## 画面構成

| タブ | 内容 |
|---|---|
| 🏠 Command Center | リスクメトリクス・findings 表・Teams 通知ボタン |
| 🤖 Agent Reasoning | 8 Agent それぞれの JSON 出力（折りたたみ） |
| 📋 Work Order | 作業指示書 Markdown + 承認 UI |
| 📑 Management Report | 管理者向け1ページ報告書 + 承認 UI |
| 📄 PDF / RAG | マニュアル PDF アップロード → Blob 保存 + Azure AI Search 投入 |
| 🗄 Past Cases | Cosmos に記録された過去 10 件 |

## ビルド & デプロイ

```powershell
npm run build && npm run start          # 本番ビルド
docker build -t edgeops-frontend .       # コンテナ化
```

Azure Static Web Apps か Container Apps に乗せる想定。
