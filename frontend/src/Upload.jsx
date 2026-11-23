import { useState } from "react";
import { requestUploadUrl, uploadToS3, getAnalysis } from "./api.js";

const MAX_BYTES = 10 * 1024 * 1024;
const POLL_MS = 3000;
const MAX_POLLS = 60;

const STEPS = {
  uploading: "Uploading file…",
  QUEUED: "Uploaded. Waiting to be processed…",
  PROCESSING: "Analyzing your billing data…",
};

export default function Upload({ onComplete }) {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [inputKey, setInputKey] = useState(0);
  const busy = status !== null;

  function pickFile(e) {
    const f = e.target.files[0];
    setError(null);
    if (!f) return setFile(null);
    if (!f.name.toLowerCase().endsWith(".csv")) {
      setFile(null);
      return setError("Please choose a .csv billing file.");
    }
    if (f.size > MAX_BYTES) {
      setFile(null);
      return setError("File is larger than 10 MB.");
    }
    setFile(f);
  }

  async function start() {
    setError(null);
    try {
      setStatus("uploading");
      const { uploadUrl, analysisId } = await requestUploadUrl(file);
      await uploadToS3(uploadUrl, file);

      setStatus("QUEUED");
      for (let i = 0; i < MAX_POLLS; i++) {
        const analysis = await getAnalysis(analysisId);
        if (analysis.status === "COMPLETED") {
          setStatus(null);
          setFile(null);
          setInputKey((k) => k + 1); // remount input to clear the shown file name
          return onComplete(analysis, file.name);
        }
        if (analysis.status === "FAILED") {
          throw new Error(analysis.errorMessage || "Processing failed. Check the file format and try again.");
        }
        setStatus(analysis.status);
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
      throw new Error("Processing is taking longer than expected. Try again in a few minutes.");
    } catch (err) {
      setStatus(null);
      setError(err.message);
    }
  }

  return (
    <section className="panel upload">
      <h3>Upload a billing file</h3>
      <p className="muted">CSV export of your cloud billing and usage data.</p>
      <div className="upload-row">
        <input key={inputKey} type="file" accept=".csv,text/csv" onChange={pickFile} disabled={busy} />
        <button onClick={start} disabled={!file || busy}>
          {busy ? "Working…" : "Upload and analyze"}
        </button>
      </div>
      {busy && (
        <p className="status">
          <span className="spinner" /> {STEPS[status] ?? "Working…"}
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
