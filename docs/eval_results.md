# エージェント品質 評価マトリクス

`python scripts/run_eval.py` で自動生成。全 20 プリセット（5設備 × 4強度）を mock モードで決定論的に実行し、出力がスキーマ妥当かつ**ポリシー準拠**かを検証した結果です。

**結果: 20/20 プリセットが全ポリシー合格 ／ 総チェック 108/108 件合格**

| 設備 | 強度 | リスク判定 | あいまい | 適用チェック | 結果 |
|---|---|---|---|---|---|
| Pump-03 | normal | Normal | — | 5/5 | ✅ |
| Pump-03 | warning | Warning | あり | 6/6 | ✅ |
| Pump-03 | critical | Critical | — | 6/6 | ✅ |
| Pump-03 | ambiguous | Warning | あり | 5/5 | ✅ |
| Pump-01 | normal | Normal | — | 5/5 | ✅ |
| Pump-01 | warning | Warning | あり | 6/6 | ✅ |
| Pump-01 | critical | Critical | — | 6/6 | ✅ |
| Pump-01 | ambiguous | Warning | あり | 5/5 | ✅ |
| Motor-02 | normal | Normal | — | 5/5 | ✅ |
| Motor-02 | warning | Warning | あり | 6/6 | ✅ |
| Motor-02 | critical | Critical | — | 6/6 | ✅ |
| Motor-02 | ambiguous | Normal | あり | 5/5 | ✅ |
| Fan-04 | normal | Normal | — | 5/5 | ✅ |
| Fan-04 | warning | Warning | あり | 6/6 | ✅ |
| Fan-04 | critical | Critical | — | 6/6 | ✅ |
| Fan-04 | ambiguous | Normal | — | 4/4 | ✅ |
| Compressor-05 | normal | Normal | — | 5/5 | ✅ |
| Compressor-05 | warning | Warning | あり | 6/6 | ✅ |
| Compressor-05 | critical | Critical | — | 6/6 | ✅ |
| Compressor-05 | ambiguous | Normal | — | 4/4 | ✅ |

## 検証したポリシー不変条件
- **8エージェント構造**: Intake→…→Governance の8体が全て dict 出力
- **リスク判定一致**: normal/warning/critical 強度が期待リスクレベルに一致
- **What-if 3シナリオ**: 「今 / 3日後 / 1週間放置」3件を常に返す
- **放置=非推奨**: 「放置」シナリオは recommended=false
- **Critical→管理者承認&24h**: Critical は管理者承認必須・期限24h以内
- **承認ゲート(自動実行不可)**: 非Normal は auto_executable=false（人間承認ゲート）
- **不確実時→人間確認**: ambiguity 検出時は human_confirmation_required=true
- **Normal→承認不要**: Normal は管理者承認を強制しない
