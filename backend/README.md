# EdgeOps Command Agent — FastAPI Backend

Streamlit 版（`app.py`）と同じ Multi-Agent パイプラインを HTTP / JSON API で公開します。Next.js フロント（`frontend/`）はここを叩きます。

## ローカル実行

```powershell
# 親プロジェクトの requirements を入れたうえで、
pip install -r requirements.txt
pip install -r backend/requirements.txt

# .env は親プロジェクトと共通。
# 起動（プロジェクトルートから）:
uvicorn backend.main:app --reload --port 8000
```

開いて確認: <http://localhost:8000/docs>（Swagger UI）

## エンドポイント概要

| Method | Path | 用途 |
|---|---|---|
| GET  | `/api/health` | 接続済みサービス（mock / Azure 構成）の確認 |
| GET  | `/api/presets` | デモプリセット一覧 |
| POST | `/api/analyze` | プリセット指定で Multi-Agent 解析を実行 |
| POST | `/api/upload/pdf` | PDF マニュアルを Blob に保存 + RAG に取り込み |
| POST | `/api/upload/image` | 点検写真を Blob に保存 |
| POST | `/api/teams/notify` | Teams Incoming Webhook に通知 |
| POST | `/api/approval` | 承認/修正依頼/却下を Cosmos に記録 |
| GET  | `/api/runs` | 直近の実行履歴（全設備横断） |
| GET  | `/api/runs/{equipment_id}` | 設備別の履歴（run/approval/alert） |
| POST | `/api/spresense/ingest` | Event Hubs にエッジデータを流す |
| GET  | `/api/spresense/recent` | 直近の Spresense ストリームを取得 + 即解析 |

## デプロイ（Azure Container Apps）

```powershell
docker build -t edgeops-backend -f backend/Dockerfile .
# docker push ... → containerapp create with --target-port 8000
```

## 環境変数

親プロジェクトの `.env.example` をそのまま使えます。FastAPI 特有の追加は CORS のみ:

```ini
# 本番では Next.js デプロイ URL に絞る
EDGEOPS_CORS_ORIGINS=https://your-frontend.azurewebsites.net
```
