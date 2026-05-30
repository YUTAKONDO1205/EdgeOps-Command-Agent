"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  analyze,
  analyzeWithUploads,
  auditExportUrl,
  equipmentSnapshot,
  getHealth,
  listEquipment,
  listPresets,
  openSpresenseStream,
  recentRuns,
  searchStats,
  seedSearchFromLocal,
  sendTeams,
  submitApproval,
  uploadPdf,
  type AnalyzeResponse,
  type EquipmentSnapshot,
  type EquipmentSpec,
  type HealthResponse,
} from "../lib/api";

const RISK_BADGE: Record<string, string> = {
  Normal: "normal",
  Warning: "warning",
  Critical: "critical",
};

type Tab =
  | "command"
  | "vision"
  | "agents"
  | "work_order"
  | "report"
  | "rag"
  | "history"
  | "live"
  | "upload";

const SEVERITY_BADGE: Record<string, { label: string; bg: string; fg: string }> = {
  normal:   { label: "Normal",   bg: "#dcfce7", fg: "#166534" },
  minor:    { label: "Minor",    bg: "#fef3c7", fg: "#92400e" },
  moderate: { label: "Moderate", bg: "#fed7aa", fg: "#9a3412" },
  severe:   { label: "Severe",   bg: "#fecaca", fg: "#991b1b" },
};

export default function Home() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [presets, setPresets] = useState<{ key: string; label: string; equipment_id: string; intensity?: string; memo: string }[]>([]);
  const [equipment, setEquipment] = useState<EquipmentSpec[]>([]);
  const [activeEquipmentId, setActiveEquipmentId] = useState<string>("Pump-03");
  const [activeIntensity, setActiveIntensity] = useState<string>("warning");
  const [fleetSnapshots, setFleetSnapshots] = useState<Record<string, EquipmentSnapshot>>({});
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [tab, setTab] = useState<Tab>("command");
  const [running, setRunning] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [runs, setRuns] = useState<any[]>([]);
  const [pdfStatus, setPdfStatus] = useState<string>("");
  const [approvalComment, setApprovalComment] = useState("");
  const [search, setSearch] = useState<any | null>(null);

  const selectedPreset = `${activeEquipmentId}:${activeIntensity}`;

  // Live tab state
  const [liveEvents, setLiveEvents] = useState<any[]>([]);
  const [liveOn, setLiveOn] = useState(false);
  const liveCloseRef = useRef<(() => void) | null>(null);

  // Upload tab state — default to a real catalog asset so the analyse-with-uploads
  // path doesn't 404 if the user just clicks Run without renaming.
  const [upEquip, setUpEquip] = useState("Pump-03");
  const [upMemo, setUpMemo] = useState("");
  const [upCsv, setUpCsv] = useState<File | null>(null);
  const [upImg, setUpImg] = useState<File | null>(null);

  useEffect(() => {
    refreshHealth();
    listPresets().then(r => setPresets(r.presets)).catch(() => {});
    listEquipment().then(r => setEquipment(r.equipment)).catch(() => {});
    recentRuns(10).then(r => setRuns(r.runs)).catch(() => {});
    return () => liveCloseRef.current?.();
  }, []);

  // Refresh fleet snapshots whenever the intensity changes or the equipment
  // catalog finishes loading.
  useEffect(() => {
    if (equipment.length === 0) return;
    Promise.all(
      equipment.map(eq => equipmentSnapshot(eq.id, activeIntensity).then(s => [eq.id, s] as const))
    ).then(pairs => {
      const map: Record<string, EquipmentSnapshot> = {};
      for (const [id, snap] of pairs) map[id] = snap;
      setFleetSnapshots(map);
    }).catch(() => {});
  }, [equipment, activeIntensity]);

  async function refreshHealth() {
    try {
      setHealth(await getHealth());
      setSearch(await searchStats());
    } catch {
      setHealth(null);
    }
  }

  function flash(msg: string) {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3500);
  }

  async function run() {
    setRunning(true);
    try {
      const r = await analyze(selectedPreset);
      setResult(r);
      flash(`解析が完了しました（${r.risk.risk_level}）`);
      recentRuns(10).then(x => setRuns(x.runs)).catch(() => {});
    } catch (e: any) {
      flash(`実行失敗: ${e.message}`);
    } finally {
      setRunning(false);
    }
  }

  async function runWithUploads() {
    if (!upCsv) {
      flash("センサー CSV を選んでください");
      return;
    }
    setRunning(true);
    try {
      const r = await analyzeWithUploads({
        equipmentId: upEquip,
        inspectionMemo: upMemo,
        sensorCsv: upCsv,
        image: upImg,
      });
      setResult(r);
      flash(`カスタム解析完了（${r.risk.risk_level}）`);
      recentRuns(10).then(x => setRuns(x.runs)).catch(() => {});
      setTab("command");
    } catch (e: any) {
      flash(`実行失敗: ${e.message}`);
    } finally {
      setRunning(false);
    }
  }

  async function notifyTeams() {
    if (!result) return;
    const plan = result.agents.action_plan.output ?? {};
    const r = await sendTeams({
      equipment_id: result.equipment_id,
      risk_level: result.risk.risk_level,
      health_score: result.risk.health_score,
      primary_concern: result.risk.primary_concern,
      deadline_hours: plan.deadline_hours ?? null,
      body_lines: [
        `主要懸念: ${result.risk.primary_concern}`,
        `ヘルススコア: ${result.risk.health_score}/100`,
      ],
    });
    flash(r.ok ? `Teams 通知を送信しました（${r.payload_kind}）` : `Teams 通知失敗: ${r.detail}`);
  }

  async function approve(artifact: string, action: string) {
    if (!result) return;
    await submitApproval({
      equipment_id: result.equipment_id,
      artifact,
      action,
      comment: approvalComment,
      risk_level: result.risk.risk_level,
    });
    flash(`${artifact}: ${action}を記録しました`);
    setApprovalComment("");
  }

  async function handlePdf(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setPdfStatus(`アップロード中: ${file.name}`);
    try {
      const r: any = await uploadPdf(file);
      setPdfStatus(
        `${file.name} を取り込みました — chunks=${r.rag.chunks_extracted}, Azure=${r.rag.azure_status}, Blob=${r.blob.backend}`
      );
      setSearch(await searchStats());
    } catch (err: any) {
      setPdfStatus(`失敗: ${err.message}`);
    } finally {
      e.target.value = "";
    }
  }

  async function seedSearch() {
    flash("AI Search に投入中…");
    try {
      const r: any = await seedSearchFromLocal();
      flash(`status=${r.status} / uploaded=${r.uploaded}`);
      setSearch(await searchStats());
    } catch (e: any) {
      flash(`失敗: ${e.message}`);
    }
  }

  function toggleLive() {
    if (liveOn) {
      liveCloseRef.current?.();
      liveCloseRef.current = null;
      setLiveOn(false);
      return;
    }
    const equip = result?.equipment_id ?? "Pump-03";
    setLiveEvents([]);
    liveCloseRef.current = openSpresenseStream(
      equip,
      ev => setLiveEvents(prev => [{ ...ev, _t: Date.now() }, ...prev].slice(0, 50)),
      () => flash("SSE 接続が切れました"),
      2,
    );
    setLiveOn(true);
  }

  const badge = result ? RISK_BADGE[result.risk.risk_level] ?? "muted" : "muted";

  return (
    <div>
      <h1 style={{ marginTop: 0 }}>Multi-Agent 解析ダッシュボード</h1>

      {health && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
          <span className={`health-pill ${health.mock_mode ? "off" : ""}`}>
            <span className="dot" /> Azure OpenAI: {health.mock_mode ? "Mock" : "Live"}
          </span>
          <span className={`health-pill ${health.rag_backend === "azure_ai_search" ? "" : "off"}`}>
            <span className="dot" /> RAG: {health.rag_backend}
          </span>
          <span className={`health-pill ${health.blob_configured ? "" : "off"}`}>
            <span className="dot" /> Blob: {health.blob_configured ? "Azure" : "Local"}
          </span>
          <span className={`health-pill ${health.cosmos_configured ? "" : "off"}`}>
            <span className="dot" /> Cosmos: {health.cosmos_configured ? "Azure" : "Local"}
          </span>
          <span className={`health-pill ${health.teams_configured ? "" : "off"}`}>
            <span className="dot" /> Teams: {health.teams_configured ? "Configured" : "Off"}
          </span>
          <span className={`health-pill ${health.spresense_source === "event_hubs" ? "" : "off"}`}>
            <span className="dot" /> Spresense: {health.spresense_source}
          </span>
          <span className={`health-pill ${health.ai_search_configured ? "" : "off"}`}>
            <span className="dot" /> AI Search: {health.ai_search_configured ? `${search?.azure_doc_count ?? "?"} docs` : "Off"}
          </span>
          <button className="ghost" style={{ marginLeft: "auto" }} onClick={refreshHealth}>↻ 再読み込み</button>
        </div>
      )}

      <div className="panel" style={{ marginBottom: 16 }}>
        <h3 style={{ marginTop: 0 }}>設備とシナリオ</h3>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {equipment.map(eq => {
            const isActive = eq.id === activeEquipmentId;
            return (
              <button
                key={eq.id}
                type="button"
                onClick={() => setActiveEquipmentId(eq.id)}
                aria-pressed={isActive}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "10px 14px",
                  borderRadius: 12,
                  border: isActive ? `2px solid ${eq.kind_accent}` : "1px solid var(--border)",
                  background: isActive ? `${eq.kind_accent}14` : "var(--card, #fff)",
                  color: "inherit",
                  cursor: "pointer",
                  boxShadow: isActive ? `0 6px 16px ${eq.kind_accent}26` : "none",
                  transition: "transform 120ms ease, box-shadow 120ms ease, border-color 120ms ease",
                  fontFamily: "inherit",
                }}
                onMouseEnter={e => { e.currentTarget.style.transform = "translateY(-1px)"; }}
                onMouseLeave={e => { e.currentTarget.style.transform = "translateY(0)"; }}
              >
                <span
                  style={{
                    width: 32, height: 32, borderRadius: 9,
                    background: `${eq.kind_accent}1A`, color: eq.kind_accent,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 17, fontWeight: 700,
                  }}
                  aria-hidden
                >
                  {eq.kind_icon}
                </span>
                <span style={{ textAlign: "left", lineHeight: 1.2 }}>
                  <div style={{ fontWeight: 700, fontSize: 13 }}>{eq.id}</div>
                  <div style={{
                    fontSize: 10, color: eq.kind_accent,
                    textTransform: "uppercase", letterSpacing: "0.04em",
                  }}>
                    {eq.kind}
                  </div>
                </span>
              </button>
            );
          })}
        </div>
        {equipment.find(e => e.id === activeEquipmentId) && (
          <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 10, marginBottom: 0 }}>
            {equipment.find(e => e.id === activeEquipmentId)!.description}
          </p>
        )}
        <div style={{
          display: "flex", alignItems: "center", gap: 12,
          flexWrap: "wrap", marginTop: 14,
        }}>
          <span className="metric-label" style={{ margin: 0 }}>シナリオ強度</span>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {["normal", "warning", "critical"].map(int_ => (
              <button
                key={int_}
                className={activeIntensity === int_ ? "" : "secondary"}
                onClick={() => setActiveIntensity(int_)}
              >
                {int_}
              </button>
            ))}
          </div>
          <button onClick={run} disabled={running} style={{ marginLeft: "auto" }}>
            {running ? "解析中…" : `▶ Run Agents (${selectedPreset})`}
          </button>
        </div>
      </div>

      <div className="tabs">
        <button className={tab === "command" ? "active" : ""} onClick={() => setTab("command")}>🏠 Command Center</button>
        <button className={tab === "vision" ? "active" : ""} onClick={() => setTab("vision")}>🖼 Vision Inspection</button>
        <button className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}>🤖 Agent Reasoning</button>
        <button className={tab === "work_order" ? "active" : ""} onClick={() => setTab("work_order")}>📋 Work Order</button>
        <button className={tab === "report" ? "active" : ""} onClick={() => setTab("report")}>📑 Management Report</button>
        <button className={tab === "rag" ? "active" : ""} onClick={() => setTab("rag")}>📄 PDF / RAG</button>
        <button className={tab === "upload" ? "active" : ""} onClick={() => setTab("upload")}>📤 Custom Analysis</button>
        <button className={tab === "live" ? "active" : ""} onClick={() => setTab("live")}>📡 Live</button>
        <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>🗄 Past Cases</button>
      </div>

      {tab === "command" && (
        <div>
          <h2 style={{ marginTop: 0 }}>設備一覧（{activeIntensity} 強度で評価）</h2>
          <div className="grid grid-3">
            {equipment.map(eq => {
              const snap = fleetSnapshots[eq.id];
              const isActive = eq.id === activeEquipmentId;
              const lvl = snap?.risk_level ?? "—";
              const score = snap?.health_score ?? 0;
              const concern = snap?.primary_concern ?? "（取得中）";
              return (
                <div
                  key={eq.id}
                  className="panel"
                  style={{
                    cursor: "pointer",
                    border: isActive ? `2px solid ${eq.kind_accent}` : undefined,
                    boxShadow: isActive ? `0 8px 24px ${eq.kind_accent}26` : undefined,
                    transition: "transform 120ms ease, box-shadow 120ms ease",
                  }}
                  onClick={() => setActiveEquipmentId(eq.id)}
                  onMouseEnter={e => { e.currentTarget.style.transform = "translateY(-2px)"; }}
                  onMouseLeave={e => { e.currentTarget.style.transform = "translateY(0)"; }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div
                      style={{
                        width: 36, height: 36, borderRadius: 10,
                        background: `${eq.kind_accent}1A`, color: eq.kind_accent,
                        display: "flex", alignItems: "center", justifyContent: "center",
                        fontSize: 18, fontWeight: 700,
                      }}
                    >
                      {eq.kind_icon}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 700, fontSize: 14 }}>{eq.id}</div>
                      <div style={{ fontSize: 11, color: eq.kind_accent, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {eq.kind}
                      </div>
                    </div>
                    <span className={`badge ${RISK_BADGE[lvl] ?? "muted"}`}>{lvl}</span>
                  </div>
                  <p style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 8, marginBottom: 6 }}>
                    📍 {eq.location}
                  </p>
                  <p style={{ fontSize: 12, marginBottom: 4 }}>主要懸念: {concern}</p>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <div style={{ flex: 1, height: 5, borderRadius: 3, background: "#e2e8f0", overflow: "hidden" }}>
                      <div style={{
                        width: `${Math.max(0, Math.min(100, score))}%`,
                        height: "100%",
                        background: lvl === "Critical" ? "var(--critical)" : lvl === "Warning" ? "var(--warning)" : "var(--normal)",
                        transition: "width 240ms ease",
                      }} />
                    </div>
                    <span style={{ fontSize: 11, color: "var(--muted)", fontVariantNumeric: "tabular-nums" }}>{score}</span>
                  </div>
                  {isActive && (
                    <p style={{ fontSize: 11, color: eq.kind_accent, marginTop: 6, fontWeight: 600 }}>● 解析対象</p>
                  )}
                </div>
              );
            })}
          </div>
          <h2>解析結果</h2>
          {result ? (
            <>
              <div className="grid grid-4">
                <div className="metric"><div className="metric-label">設備</div><div className="metric-value">{result.equipment_id}</div></div>
                <div className="metric"><div className="metric-label">リスク</div><div className="metric-value"><span className={`badge ${badge}`}>{result.risk.risk_level}</span></div></div>
                <div className="metric"><div className="metric-label">ヘルススコア</div><div className="metric-value">{result.risk.health_score}/100</div></div>
                <div className="metric"><div className="metric-label">主要懸念</div><div className="metric-value" style={{ fontSize: 15 }}>{result.risk.primary_concern}</div></div>
              </div>
              <h2>ルールベース判定</h2>
              <div className="panel">
                <table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                      <th>Indicator</th><th>Value</th><th>Threshold</th><th>Level</th><th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.risk.findings.map((f, i) => (
                      <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                        <td>{f.indicator}</td><td>{f.value}</td><td>{f.threshold}</td>
                        <td><span className={`badge ${RISK_BADGE[f.level] ?? "muted"}`}>{f.level}</span></td>
                        <td>{f.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {result.risk.risk_level !== "Normal" && (
                <>
                  <h2>Teams 通知</h2>
                  <div className="panel">
                    <p style={{ marginTop: 0 }}>このリスクを Teams (Incoming Webhook / Power Automate) に Adaptive Card で通知します。</p>
                    <button onClick={notifyTeams} disabled={!health?.teams_configured}>
                      {health?.teams_configured ? "📣 Teams に通知を送る" : "Teams 未設定（TEAMS_WEBHOOK_URL）"}
                    </button>
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="panel">上のプリセットを選んで「▶ Run Agents」を押してください。</div>
          )}
        </div>
      )}

      {tab === "vision" && (
        <div>
          {result ? <VisionInspectionPanel vision={result.agents.vision} /> : (
            <div className="panel">まだ Vision Agent が実行されていません。</div>
          )}
        </div>
      )}

      {tab === "agents" && (
        <div className="grid">
          {result ? (
            ["intake","signal","vision","manual_rag","root_cause","action_plan","whatif","governance"]
              .filter(k => (result.agents as any)[k])
              .map(k => {
              const a = (result.agents as any)[k];
              return (
                <details key={k} className="panel">
                  <summary style={{ cursor: "pointer", fontWeight: 600 }}>{k} <span className="badge muted">{a.source}</span></summary>
                  <pre style={{ background: "#0f172a", color: "#e2e8f0", padding: 12, borderRadius: 8, overflowX: "auto", marginTop: 8 }}>
                    {JSON.stringify(a.output, null, 2)}
                  </pre>
                </details>
              );
            })
          ) : <div className="panel">まだ解析が走っていません。</div>}
        </div>
      )}

      {tab === "work_order" && (
        <div>
          {result ? (
            <>
              <div className="panel markdown"><ReactMarkdown>{result.work_order_md}</ReactMarkdown></div>
              {ApprovalPanel("Work Order", approvalComment, setApprovalComment, approve, result.equipment_id)}
            </>
          ) : <div className="panel">まだ作業指示書が生成されていません。</div>}
        </div>
      )}

      {tab === "report" && (
        <div>
          {result ? (
            <>
              <div className="panel markdown"><ReactMarkdown>{result.management_report_md}</ReactMarkdown></div>
              {ApprovalPanel("Management Report", approvalComment, setApprovalComment, approve, result.equipment_id)}
            </>
          ) : <div className="panel">まだ報告書が生成されていません。</div>}
        </div>
      )}

      {tab === "rag" && (
        <div>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>マニュアル PDF を取り込み</h3>
            <p style={{ color: "var(--muted)" }}>
              アップロードした PDF は (1) Blob Storage に保存、(2) ローカル RAG にチャンク登録、
              (3) Azure AI Search が設定済みなら同じインデックスにもアップロードされます。
            </p>
            <input type="file" accept="application/pdf" onChange={handlePdf} />
            {pdfStatus && <p style={{ marginTop: 12 }}>{pdfStatus}</p>}
          </div>
          <div className="panel" style={{ marginTop: 12 }}>
            <h3 style={{ marginTop: 0 }}>Azure AI Search オペレーション</h3>
            <p>
              バックエンド: <code>{search?.active_backend ?? "?"}</code>{" "}
              · インデックスドキュメント数: <code>{search?.azure_doc_count ?? 0}</code>{" "}
              · uploaded chunks (in-memory): <code>{search?.uploaded_chunk_count ?? 0}</code>
            </p>
            <button
              onClick={seedSearch}
              disabled={!health?.ai_search_configured}
              title={health?.ai_search_configured ? "" : "AZURE_SEARCH_ENDPOINT / API_KEY が必要"}
            >
              ローカルマニュアルを AI Search に投入
            </button>
            {!health?.ai_search_configured && (
              <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 8 }}>
                Azure AI Search は未設定です（ローカル RAG にフォールバック）。`.env` に
                <code>AZURE_SEARCH_ENDPOINT</code> と <code>AZURE_SEARCH_API_KEY</code> を設定するとここから投入できます。
              </p>
            )}
          </div>
        </div>
      )}

      {tab === "upload" && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>カスタム解析（CSV + 写真をアップロード）</h3>
          <p style={{ color: "var(--muted)" }}>
            プリセットを使わず、独自データを Multi-Agent パイプラインに流します。画像は Blob に保存され、Vision Agent から参照されます。
          </p>
          <div className="grid grid-2">
            <div>
              <label className="metric-label">設備 ID</label>
              <input type="text" value={upEquip} onChange={e => setUpEquip(e.target.value)} />
            </div>
            <div>
              <label className="metric-label">点検メモ</label>
              <textarea rows={2} value={upMemo} onChange={e => setUpMemo(e.target.value)}
                        placeholder="例: 軸受周辺で異音あり" />
            </div>
            <div>
              <label className="metric-label">センサー CSV</label>
              <input type="file" accept=".csv" onChange={e => setUpCsv(e.target.files?.[0] ?? null)} />
              {upCsv && <p style={{ fontSize: 12, color: "var(--muted)" }}>{upCsv.name}</p>}
            </div>
            <div>
              <label className="metric-label">点検写真（任意）</label>
              <input type="file" accept="image/*" onChange={e => setUpImg(e.target.files?.[0] ?? null)} />
              {upImg && <p style={{ fontSize: 12, color: "var(--muted)" }}>{upImg.name}</p>}
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <button onClick={runWithUploads} disabled={running || !upCsv}>
              {running ? "解析中…" : "▶ アップロードで解析"}
            </button>
          </div>
        </div>
      )}

      {tab === "live" && (
        <div>
          <div className="panel">
            <h3 style={{ marginTop: 0 }}>Spresense ライブ（SSE）</h3>
            <p style={{ color: "var(--muted)" }}>
              <code>/api/spresense/stream</code> を購読し、Event Hubs / ローカル JSONL の最新データを 2 秒ごとに再解析します。
              ローカルでは <code>python data/spresense_simulator.py --intensity critical --duration 5</code>{" "}
              を別ターミナルで実行するとイベントが流れ込みます。
            </p>
            <button onClick={toggleLive}>
              {liveOn ? "■ 停止" : "▶ ライブ開始"}
            </button>
            <span style={{ marginLeft: 12, color: "var(--muted)", fontSize: 12 }}>
              対象設備: <code>{result?.equipment_id ?? "Pump-03"}</code> ／ 受信イベント: {liveEvents.length}
            </span>
          </div>
          <div className="panel" style={{ marginTop: 12 }}>
            <table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                  <th>時刻</th><th>件数</th><th>リスク</th><th>ヘルス</th><th>振動RMS</th><th>温度</th><th>音響</th>
                </tr>
              </thead>
              <tbody>
                {liveEvents.map((ev, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td>{new Date(ev._t).toLocaleTimeString()}</td>
                    <td>{ev.record_count}</td>
                    <td>{ev.risk_level ? <span className={`badge ${RISK_BADGE[ev.risk_level] ?? "muted"}`}>{ev.risk_level}</span> : "—"}</td>
                    <td>{ev.health_score ?? "—"}</td>
                    <td>{ev.vibration_rms?.toFixed(3) ?? "—"}</td>
                    <td>{ev.temperature_max_c?.toFixed(1) ?? "—"}</td>
                    <td>{ev.sound_max_db?.toFixed(1) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === "history" && (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>過去事例（直近 10 件）</h3>
          {result?.equipment_id && (
            <div style={{ marginBottom: 12, display: "flex", gap: 8 }}>
              <a href={auditExportUrl(result.equipment_id, "json")} target="_blank" rel="noreferrer">
                <button className="secondary">監査ログ JSON</button>
              </a>
              <a href={auditExportUrl(result.equipment_id, "csv")} target="_blank" rel="noreferrer">
                <button className="secondary">監査ログ CSV (Excel)</button>
              </a>
              <a href={auditExportUrl(result.equipment_id, "csv", "approval")} target="_blank" rel="noreferrer">
                <button className="secondary">承認ログのみ CSV</button>
              </a>
            </div>
          )}
          {runs.length === 0 ? (
            <p style={{ color: "var(--muted)" }}>まだ履歴がありません。Run Agents を実行すると Cosmos / ローカルログに記録されます。</p>
          ) : (
            <table style={{ width: "100%", fontSize: 12.5, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                  <th>日時</th><th>設備</th><th>リスク</th><th>ヘルス</th><th>主要懸念</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r, i) => (
                  <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                    <td>{r.timestamp}</td>
                    <td>{r.equipment_id}</td>
                    <td><span className={`badge ${RISK_BADGE[r.risk_level] ?? "muted"}`}>{r.risk_level}</span></td>
                    <td>{r.health_score}</td>
                    <td>{r.primary_concern}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function ConfidenceMeter({ score }: { score: number }) {
  const s = Math.max(0, Math.min(100, Math.round(score ?? 0)));
  const color = s >= 80 ? "var(--normal)" : s >= 60 ? "var(--warning)" : "var(--critical)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 80, height: 6, borderRadius: 3, background: "#e2e8f0", overflow: "hidden" }}>
        <div style={{ width: `${s}%`, height: "100%", background: color, transition: "width 240ms ease" }} />
      </div>
      <span style={{ fontSize: 10, color: "var(--muted)" }}>{s}%</span>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const style = SEVERITY_BADGE[(severity ?? "").toLowerCase()] ?? { label: severity || "—", bg: "#e2e8f0", fg: "#475569" };
  return (
    <span style={{
      display: "inline-block", padding: "2px 10px", borderRadius: 9999,
      background: style.bg, color: style.fg, fontSize: 11, fontWeight: 600,
    }}>{style.label}</span>
  );
}

function VisionInspectionPanel({ vision }: { vision: { source: string; output: any } }) {
  const v = vision?.output ?? {};
  const overview = v.overview ?? "";
  const overallConf = v.overall_confidence_score ?? 0;
  const regions: any[] = Array.isArray(v.regions) ? v.regions : [];
  const correlation = v.signal_correlation ?? "";
  const comparison = v.comparison_to_normal ?? "";
  const shots: string[] = Array.isArray(v.recommended_additional_shots) ? v.recommended_additional_shots : [];
  const evidence = v.evidence_images ?? null;
  const crops: Record<string, string> = (evidence && typeof evidence === "object" && evidence.crops) || {};

  return (
    <div>
      {overview && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <strong style={{ fontSize: 14 }}>全体所見</strong>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>総合確信度</span>
              <ConfidenceMeter score={overallConf} />
            </div>
          </div>
          <p style={{ marginTop: 6, marginBottom: 0, color: "var(--text)", fontSize: 13, lineHeight: 1.6 }}>{overview}</p>
        </div>
      )}

      {evidence && (evidence.overlay || evidence.enhanced) && (
        <div className="panel" style={{ marginBottom: 12 }}>
          <h3 style={{ marginTop: 0 }}>AI が見たもの（判断根拠の画像）</h3>
          <div className="grid grid-2">
            {evidence.overlay && (
              <div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>
                  領域別ハイライト（bbox + severity）
                </div>
                <img
                  src={evidence.overlay}
                  alt="vision overlay"
                  style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }}
                />
              </div>
            )}
            {evidence.enhanced && (
              <div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>
                  自動コントラスト強調（微小変化を強調）
                </div>
                <img
                  src={evidence.enhanced}
                  alt="enhanced view"
                  style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {regions.length > 0 && (
        <>
          <h3 style={{ marginTop: 0 }}>領域別所見（{regions.length} 件）</h3>
          <div className="grid">
            {regions.map((r, i) => {
              const crop = crops[r.region_id];
              return (
                <div key={i} className="panel" style={{ padding: 12, display: "flex", gap: 12 }}>
                  {crop && (
                    <img
                      src={crop}
                      alt={r.region_id}
                      style={{
                        width: 120, height: 120, objectFit: "cover", borderRadius: 8,
                        border: "1px solid var(--border)", flexShrink: 0,
                      }}
                    />
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                      <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: 4, fontSize: 12 }}>
                        {r.region_id ?? "—"}
                      </code>
                      <SeverityBadge severity={r.severity ?? "—"} />
                      <div style={{ marginLeft: "auto" }}>
                        <ConfidenceMeter score={r.confidence_score ?? 0} />
                      </div>
                    </div>
                    <p style={{ fontSize: 13, margin: "4px 0 6px" }}>{r.observation ?? ""}</p>
                    {Array.isArray(r.evidence) && r.evidence.length > 0 && (
                      <details style={{ marginBottom: 4 }}>
                        <summary style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }}>
                          視覚的根拠 ({r.evidence.length})
                        </summary>
                        <ul style={{ margin: "6px 0 0 18px", padding: 0, fontSize: 12, color: "var(--muted)" }}>
                          {r.evidence.map((e: string, j: number) => <li key={j}>{e}</li>)}
                        </ul>
                      </details>
                    )}
                    {r.recommended_action && (
                      <p style={{ fontSize: 12, color: "#1e40af", margin: 0 }}>→ {r.recommended_action}</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {(correlation || comparison) && (
        <div className="grid grid-2" style={{ marginTop: 12 }}>
          {correlation && (
            <div className="panel" style={{ background: "#eff6ff", borderColor: "#bfdbfe" }}>
              <div style={{ fontSize: 11, color: "#1e3a8a", fontWeight: 600, marginBottom: 4 }}>センサーとの整合性</div>
              <p style={{ fontSize: 13, color: "#1e40af", margin: 0, lineHeight: 1.5 }}>{correlation}</p>
            </div>
          )}
          {comparison && (
            <div className="panel" style={{ background: "#f5f3ff", borderColor: "#ddd6fe" }}>
              <div style={{ fontSize: 11, color: "#5b21b6", fontWeight: 600, marginBottom: 4 }}>正常時との比較</div>
              <p style={{ fontSize: 13, color: "#6d28d9", margin: 0, lineHeight: 1.5 }}>{comparison}</p>
            </div>
          )}
        </div>
      )}

      {shots.length > 0 && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h3 style={{ marginTop: 0 }}>追加撮影指示</h3>
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            {shots.map((s, i) => <li key={i} style={{ fontSize: 13 }}>{s}</li>)}
          </ul>
        </div>
      )}

      {v.human_confirmation_required && (
        <div className="panel" style={{ marginTop: 12, background: "#fef9c3", borderColor: "#fde68a" }}>
          確定診断には現場での人間確認が必要です。
        </div>
      )}

      <p style={{ fontSize: 11, color: "var(--muted)", marginTop: 12 }}>
        source: {vision.source} ／ confidence: {v.confidence ?? "?"}
      </p>
    </div>
  );
}

function ApprovalPanel(
  artifact: string,
  comment: string,
  setComment: (s: string) => void,
  approve: (a: string, action: string) => void,
  equipmentId: string,
) {
  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h3 style={{ marginTop: 0 }}>承認</h3>
      <textarea
        rows={3}
        placeholder="却下理由 / 修正指示（任意）"
        value={comment}
        onChange={e => setComment(e.target.value)}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
        <button onClick={() => approve(artifact, "承認")}>✅ 承認</button>
        <button className="secondary" onClick={() => approve(artifact, "修正依頼")}>🛠 修正依頼</button>
        <button className="secondary" onClick={() => approve(artifact, "却下")}>⛔ 却下</button>
        <a href={auditExportUrl(equipmentId, "json")} target="_blank" rel="noreferrer" style={{ marginLeft: "auto" }}>
          <button className="ghost">↓ 監査ログ JSON</button>
        </a>
        <a href={auditExportUrl(equipmentId, "csv")} target="_blank" rel="noreferrer">
          <button className="ghost">↓ 監査ログ CSV</button>
        </a>
      </div>
    </div>
  );
}
