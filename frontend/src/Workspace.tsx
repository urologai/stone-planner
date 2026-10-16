import { useCallback, useEffect, useState } from "react";
import { api, bundleUrl, fileUrl, type CaseSummary, type ParamField } from "./api";
import Admin from "./Admin";
import Ingest from "./Ingest";
import { defaultsOf, overrides, type ParamValues } from "./params";
import ParamsForm from "./ParamsForm";
import TractsTable from "./TractsTable";
import Viewer from "./Viewer";

const POLL_MS = 1500;

function Upload({ onCase }: { onCase: (c: CaseSummary) => void }) {
  const [ct, setCt] = useState<File>();
  const [organs, setOrgans] = useState<File>();
  const [stones, setStones] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function send(files: Parameters<typeof api.createCase>[0]) {
    setBusy(true);
    setError("");
    try {
      onCase(await api.createCase(files));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="upload">
      <section className="card">
        <h2>New case</h2>
        <p className="muted small">
          De-identified CT as NIfTI only (run <code>stoneplanner-ingest</code> on your machine
          first). DICOM is refused. Add organ and stone maps if you segmented locally.
        </p>
        <label className="file">
          CT (.nii.gz)
          <input type="file" accept=".nii,.gz" data-testid="ct-input"
            onChange={(e) => setCt(e.target.files?.[0])} />
        </label>
        <label className="file">
          Organ map (optional)
          <input type="file" accept=".nii,.gz" data-testid="organs-input"
            onChange={(e) => setOrgans(e.target.files?.[0])} />
        </label>
        <label className="file">
          Stone map (optional)
          <input type="file" accept=".nii,.gz" data-testid="stones-input"
            onChange={(e) => setStones(e.target.files?.[0])} />
        </label>
        <button disabled={!ct || busy} onClick={() => send({ ct, organs, stones })}>
          {busy ? "Uploading" : "Upload"}
        </button>
      </section>
      <section className="card">
        <h2>Open saved case</h2>
        <p className="muted small">
          A case saved from here (.zip) reopens with its segmentation and plan, so nothing is
          recomputed.
        </p>
        <label className="file">
          Saved case (.zip)
          <input type="file" accept=".zip" data-testid="bundle-input" disabled={busy}
            onChange={(e) => e.target.files?.[0] && send({ bundle: e.target.files[0] })} />
        </label>
      </section>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default function Workspace({ gpu, onLogout, onSettingsChanged }: {
  gpu: boolean;
  onLogout: () => void;
  onSettingsChanged: () => void;
}) {
  const [current, setCurrent] = useState<CaseSummary | null>(null);
  const [fields, setFields] = useState<ParamField[]>([]);
  const [values, setValues] = useState<ParamValues>({});
  const [side, setSide] = useState("auto");
  const [version, setVersion] = useState(0);
  const [focus, setFocus] = useState<number[] | null>(null);
  const [error, setError] = useState("");
  // The ingest page exists only on a local run with DATA_DIR and KEY_FILE set.
  const [ingest, setIngest] = useState(false);
  const [tab, setTab] = useState<"ingest" | "planner" | "admin">("planner");
  const [saved, setSaved] = useState("");
  const defaults = defaultsOf(fields);

  useEffect(() => {
    api.ingestStatus().then((s) => {
      setIngest(s.enabled);
      if (s.enabled) setTab("ingest");
    }).catch(() => undefined);
    api.params().then((p) => {
      setFields(p.fields);
      setValues(defaultsOf(p.fields));
    }).catch((err) => setError(err.message));
  }, []);

  const running = current
    ? Object.values(current.jobs).some((j) => j?.state === "running")
    : false;

  const refresh = useCallback(async (id: string, bump: boolean) => {
    try {
      const next = await api.getCase(id);
      setCurrent(next);
      if (bump) setVersion((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Lost the case");
      setCurrent(null);
    }
  }, []);

  useEffect(() => {
    if (!current || !running) return;
    const timer = setTimeout(() => refresh(current.id, false).then(() => undefined), POLL_MS);
    return () => clearTimeout(timer);
  }, [current, running, refresh]);

  // When a job finishes, reload the images it changed.
  const [lastJobs, setLastJobs] = useState("");
  useEffect(() => {
    if (!current) return;
    const key = JSON.stringify(current.jobs);
    if (key !== lastJobs) {
      const finished = Object.values(current.jobs).some((j) => j?.state === "done");
      if (lastJobs && finished && !running) setVersion((v) => v + 1);
      setLastJobs(key);
    }
  }, [current, lastJobs, running]);

  // Centre the viewer on the rank 1 target whenever a new plan arrives.
  const topTarget = current?.plan?.tracts[0]?.target_xyz_mm;
  const topKey = topTarget ? topTarget.join(",") : "";
  useEffect(() => {
    if (topTarget) setFocus([...topTarget]);
    // topKey stands in for the array so the effect runs once per new target
  }, [topKey, version]);

  async function act(fn: () => Promise<CaseSummary>) {
    setError("");
    try {
      setCurrent(await fn());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    }
  }

  async function closeCase() {
    if (current) await api.deleteCase(current.id).catch(() => undefined);
    setCurrent(null);
    setFocus(null);
    setSaved("");
  }

  async function openFromLibrary(c: CaseSummary) {
    if (current) await api.deleteCase(current.id).catch(() => undefined);
    setFocus(null);
    setSaved("");
    setCurrent(c);
    setVersion((v) => v + 1);
    setTab("planner");
  }

  async function saveToLibrary() {
    if (!current) return;
    setError("");
    try {
      const res = await api.saveToLibrary(current.id);
      setSaved(`Saved ${res.written.length} files to ${current.source_case}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  const job = (kind: "segment" | "plan") => current?.jobs[kind];

  return (
    <div className="app">
      <header>
        <strong>Stone planner</strong>
        <span className="muted small">Research software. Not for clinical use.</span>
        {ingest && (
          <nav className="tabs">
            <button className={tab === "ingest" ? "" : "secondary"} data-testid="tab-ingest"
              onClick={() => setTab("ingest")}>Ingest</button>
            <button className={tab === "planner" ? "" : "secondary"} data-testid="tab-planner"
              onClick={() => setTab("planner")}>Planner</button>
            <button className={tab === "admin" ? "" : "secondary"} data-testid="tab-admin"
              onClick={() => setTab("admin")}>Admin</button>
          </nav>
        )}
        <span className="spacer" />
        {current && <span className="case-id">{current.case_id}</span>}
        {current && <button className="secondary" onClick={closeCase}>Close case</button>}
        <button className="secondary" onClick={onLogout}>Log out</button>
      </header>
      {error && <p className="error banner">{error}</p>}
      {tab === "admin" ? (
        <Admin onChanged={onSettingsChanged} />
      ) : tab === "ingest" ? (
        <Ingest onOpen={openFromLibrary} />
      ) : !current ? (
        <Upload onCase={(c) => { setCurrent(c); setVersion((v) => v + 1); }} />
      ) : (
        <main className="layout">
          <aside>
            <section className="card">
              <h2>Case</h2>
              <p className="small">
                {current.shape.join(" x ")} voxels, {current.spacing_mm.join(" x ")} mm
              </p>
              <p className="small">
                Segmentation: {current.has.organs ? "ready" : "not yet"}
                {current.stones.length > 0 &&
                  `, ${current.stones.length} stone${current.stones.length > 1 ? "s" : ""} (${
                    current.stones.reduce((a, s) => a + Number(s.volume_mm3), 0).toFixed(0)
                  } mm3)`}
              </p>
              {!current.has.organs && (
                <button disabled={!gpu || running}
                  onClick={() => act(() => api.segment(current.id))}
                  title={gpu ? "" : ingest ? "Choose where segmentation runs on the Admin tab"
                    : "GPU segmentation is not configured on this server"}>
                  Segment
                </button>
              )}
              {job("segment") && (
                <p className={`small ${job("segment")!.state === "error" ? "error" : "muted"}`}>
                  Segmentation {job("segment")!.state}. {job("segment")!.message}
                </p>
              )}
            </section>
            <section className="card">
              <h2>Plan</h2>
              <label className="field">
                <span>Side</span>
                <select value={side} onChange={(e) => setSide(e.target.value)}>
                  <option value="auto">auto (more stone volume)</option>
                  <option value="left">left</option>
                  <option value="right">right</option>
                </select>
              </label>
              {fields.length > 0 && (
                <details className="params-box">
                  <summary>
                    Planner parameters
                    {(() => {
                      const n = Object.keys(overrides(values, defaults)).length;
                      return n ? ` (${n} changed)` : " (defaults)";
                    })()}
                  </summary>
                  <ParamsForm fields={fields} values={values} defaults={defaults}
                    onChange={setValues} />
                </details>
              )}
              <button disabled={!current.has.organs || running}
                onClick={() => act(() => api.plan(current.id, side, overrides(values, defaults)))}>
                {job("plan")?.state === "running" ? "Planning" : "Plan tracts"}
              </button>
              {job("plan") && (
                <p className={`small ${job("plan")!.state === "error" ? "error" : "muted"}`}
                  data-testid="plan-status">
                  Plan {job("plan")!.state}. {job("plan")!.message}
                </p>
              )}
            </section>
            <section className="card">
              <h2>Save</h2>
              {current.source_case ? (
                <p className="muted small">
                  Save to case folder writes the segmentation and plan next to the CT in{" "}
                  {current.source_case}, where reopening and validate.py find them.
                </p>
              ) : (
                <p className="muted small">
                  The server keeps nothing once you leave. Save the case to reopen it later
                  without recomputing.
                </p>
              )}
              <div className="row">
                {current.source_case && (
                  <button disabled={running || !current.has.organs} onClick={saveToLibrary}
                    data-testid="save-to-library">
                    Save to case folder
                  </button>
                )}
                <a className={current.source_case ? "button secondary" : "button"} href={bundleUrl(current.id)}>Save case</a>
                {current.has.tracts_json && (
                  <>
                    <a className="button secondary" href={fileUrl(current.id, "tracts_csv")}>
                      Tracts CSV
                    </a>
                    <a className="button secondary" href={fileUrl(current.id, "tracts_json")}>
                      Tracts JSON
                    </a>
                  </>
                )}
              </div>
              {saved && <p className="small muted" data-testid="saved-status">{saved}</p>}
            </section>
          </aside>
          <section className="main">
            <Viewer c={current} version={version} focus={focus} />
            {current.plan && (
              <div className="card">
                <h2>
                  Ranked tracts, {current.plan.side} side
                </h2>
                <TractsTable tracts={current.plan.tracts}
                  onSelect={(t) => setFocus(t.target_xyz_mm)} />
              </div>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
