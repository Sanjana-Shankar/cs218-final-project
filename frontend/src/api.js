import { fetchAuthSession } from "aws-amplify/auth";
import sampleAnalysis from "./sampleAnalysis.json";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;
const authConfigured = Boolean(import.meta.env.VITE_COGNITO_USER_POOL_ID);

// No API URL yet → fake responses so the UI can be built and demoed locally
export const usingMocks = !API_BASE_URL;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function apiFetch(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (authConfigured) {
    const { tokens } = await fetchAuthSession();
    headers.Authorization = `Bearer ${tokens?.idToken?.toString()}`;
  }
  const res = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.message || body.error || `Request failed (${res.status})`);
  return body;
}

// POST /uploads → { uploadUrl, analysisId }
export async function requestUploadUrl(file) {
  if (usingMocks) {
    await sleep(400);
    return { uploadUrl: null, analysisId: sampleAnalysis.analysisId };
  }
  return apiFetch("/uploads", {
    method: "POST",
    body: JSON.stringify({ fileName: file.name, contentType: file.type || "text/csv" }),
  });
}

export async function uploadToS3(uploadUrl, file) {
  if (usingMocks) return sleep(800);
  const res = await fetch(uploadUrl, {
    method: "PUT",
    headers: { "Content-Type": file.type || "text/csv" },
    body: file,
  });
  if (!res.ok) throw new Error(`Upload to S3 failed (${res.status})`);
}

// GET /analyses/{id} → analysis (status: QUEUED | PROCESSING | COMPLETED | FAILED)
let mockPolls = 0;
export async function getAnalysis(analysisId) {
  if (usingMocks) {
    await sleep(500);
    mockPolls += 1;
    if (mockPolls % 3 !== 0) return { analysisId, status: "PROCESSING" };
    return sampleAnalysis;
  }
  return apiFetch(`/analyses/${encodeURIComponent(analysisId)}`);
}
