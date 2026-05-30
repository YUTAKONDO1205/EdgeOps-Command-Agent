// Thin client for the FastAPI backend. Centralizes the base URL plus
// the few shapes we return so components stay typed.

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type AgentResultLite = { source: string; output: any };
export type AnalyzeResponse = {
  equipment_id: string;
  preset_key: string;
  risk: {
    risk_level: "Normal" | "Warning" | "Critical";
    health_score: number;
    primary_concern: string;
    ambiguity_flag: boolean;
    findings: { indicator: string; value: number; threshold: number; level: string; note: string }[];
  };
  agents: {
    intake?: AgentResultLite;
    signal: AgentResultLite;
    vision: AgentResultLite;
    manual_rag: AgentResultLite;
    root_cause: AgentResultLite;
    action_plan: AgentResultLite;
    whatif: AgentResultLite;
    governance?: AgentResultLite;
  };
  work_order_md: string;
  management_report_md: string;
};

export type HealthResponse = {
  status: string;
  mock_mode: boolean;
  rag_backend: string;
  blob_configured: boolean;
  cosmos_configured: boolean;
  teams_configured: boolean;
  spresense_source: string;
  ai_search_configured: boolean;
};

export type RunRecord = {
  timestamp: string;
  equipment_id: string;
  risk_level: string;
  health_score: number;
  primary_concern: string;
};

export type TeamsNotifyResponse = {
  ok: boolean;
  payload_kind?: string;
  detail?: string;
};

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  return res.json() as Promise<T>;
}

export async function getHealth(): Promise<HealthResponse> {
  return asJson(await fetch(`${BASE}/api/health`, { cache: "no-store" }));
}

export async function listPresets(): Promise<{ presets: { key: string; label: string; equipment_id: string; intensity?: string; memo: string }[] }> {
  return asJson(await fetch(`${BASE}/api/presets`, { cache: "no-store" }));
}

export type EquipmentSpec = {
  id: string;
  label: string;
  kind: string;
  kind_icon: string;
  kind_accent: string;
  location: string;
  description: string;
  rotation_hz: number;
  downstream: string[];
  normal_state: { vibration_amp: number; sound_db: number; temperature_c: number; current_a: number };
};

export async function listEquipment(): Promise<{ equipment: EquipmentSpec[] }> {
  return asJson(await fetch(`${BASE}/api/equipment`, { cache: "no-store" }));
}

export type EquipmentSnapshot = {
  equipment_id: string;
  intensity: string;
  risk_level: "Normal" | "Warning" | "Critical";
  health_score: number;
  primary_concern: string;
  ambiguity_flag: boolean;
};

export async function equipmentSnapshot(equipmentId: string, intensity: string = "normal"): Promise<EquipmentSnapshot> {
  return asJson(await fetch(
    `${BASE}/api/equipment/${encodeURIComponent(equipmentId)}/snapshot?intensity=${encodeURIComponent(intensity)}`,
    { cache: "no-store" }));
}

export async function analyze(presetKey: string, equipmentId?: string): Promise<AnalyzeResponse> {
  return asJson(await fetch(`${BASE}/api/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset_key: presetKey, equipment_id: equipmentId ?? null }),
  }));
}

export async function uploadPdf(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return asJson(await fetch(`${BASE}/api/upload/pdf`, { method: "POST", body: fd }));
}

export async function sendTeams(body: any): Promise<TeamsNotifyResponse> {
  return asJson(await fetch(`${BASE}/api/teams/notify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

export async function submitApproval(body: any) {
  return asJson(await fetch(`${BASE}/api/approval`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

export async function recentRuns(limit: number = 10): Promise<{ runs: RunRecord[] }> {
  return asJson(await fetch(`${BASE}/api/runs?limit=${limit}`, { cache: "no-store" }));
}

export async function spresenseRecent(equipmentId?: string) {
  const q = equipmentId ? `?equipment_id=${encodeURIComponent(equipmentId)}` : "";
  return asJson(await fetch(`${BASE}/api/spresense/recent${q}`, { cache: "no-store" }));
}

export async function analyzeWithUploads(args: {
  equipmentId: string;
  inspectionMemo: string;
  sensorCsv: File;
  image?: File | null;
}): Promise<AnalyzeResponse> {
  const fd = new FormData();
  fd.append("equipment_id", args.equipmentId);
  fd.append("inspection_memo", args.inspectionMemo);
  fd.append("sensor_csv", args.sensorCsv);
  if (args.image) fd.append("image", args.image);
  return asJson(await fetch(`${BASE}/api/analyze/with-uploads`, { method: "POST", body: fd }));
}

export async function uploadImage(file: File, equipmentId: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("equipment_id", equipmentId);
  return asJson(await fetch(`${BASE}/api/upload/image`, { method: "POST", body: fd }));
}

export async function searchStats() {
  return asJson<{ active_backend: string; uploaded_chunk_count: number;
                   azure_search_configured: boolean; azure_doc_count: number }>(
    await fetch(`${BASE}/api/search/stats`, { cache: "no-store" }));
}

export async function seedSearchFromLocal() {
  return asJson(await fetch(`${BASE}/api/search/seed-from-local`, { method: "POST" }));
}

export function auditExportUrl(equipmentId: string, format: "json" | "csv",
                                docType?: "run" | "approval" | "alert"): string {
  const params = new URLSearchParams({ format });
  if (docType) params.set("doc_type", docType);
  return `${BASE}/api/runs/${encodeURIComponent(equipmentId)}/export?${params}`;
}

// SSE stream — returns a function that closes the connection.
export function openSpresenseStream(
  equipmentId: string,
  onEvent: (data: any) => void,
  onError?: (e: Event) => void,
  pollSeconds: number = 2,
): () => void {
  const url = `${BASE}/api/spresense/stream?equipment_id=${encodeURIComponent(equipmentId)}&poll_seconds=${pollSeconds}`;
  const es = new EventSource(url);
  es.onmessage = ev => {
    try { onEvent(JSON.parse(ev.data)); } catch { /* ignore */ }
  };
  if (onError) es.onerror = onError;
  return () => es.close();
}
