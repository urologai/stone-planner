import { useEffect, useRef, useState } from "react";
import { api, type AdminView, type CheckResult, type Provider, type SegTest } from "./api";

// Setup for a local install: where segmentation runs, and the linked case
// workbook. Secrets are typed here and stored on this computer; the server
// never sends them back, so their boxes show only whether one is saved.

const PROVIDERS: [Provider, string, string][] = [
  ["modal", "Cloud GPU (Modal)",
    "De-identified CTs go to the Modal function deployed from modal_app/segment_gpu.py."],
  ["remote", "Home PC GPU",
    "De-identified CTs go to the GPU worker on your own PC, over Tailscale."],
  ["local", "This computer",
    "TotalSegmentator runs here, on the processor. Slow but needs no other machine."],
  ["none", "Off", "No segmentation; upload organ and stone maps made elsewhere."],
];

const POLL_MS = 2000;

function Secret({ label, saved, value, onChange, testId }: {
  label: string;
  saved: boolean;
  value: string;
  onChange: (v: string) => void;
  testId?: string;
}) {
  return (
    <label>
      {label}
      <input type="password" autoComplete="new-password" value={value} data-testid={testId}
        placeholder={saved ? "saved; type to replace" : "not set"}
        onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export default function Admin({ onChanged }: { onChanged: () => void }) {
  const [view, setView] = useState<AdminView | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [test, setTest] = useState<SegTest | null>(null);
  const [book, setBook] = useState("");
  const timer = useRef<number | undefined>(undefined);

  function load(v: AdminView) {
    setView(v);
    const s = v.settings;
    setDraft({
      segmentation_provider: s.segmentation_provider,
      modal_app_name: s.modal_app_name,
      modal_function_name: s.modal_function_name,
      modal_token_id: "",
      modal_token_secret: "",
      remote_url: s.remote_url,
      remote_token: "",
      local_device: s.local_device,
      workbook_path: s.workbook_path,
    });
  }

  useEffect(() => {
    api.admin().then(load).catch((err) => setError(err.message));
    api.segTest().then((t) => t.state !== "idle" && setTest(t)).catch(() => undefined);
    return () => window.clearTimeout(timer.current);
  }, []);

  const set = (key: string, value: string) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setSaved("");
  };

  async function save() {
    setError("");
    setCheck(null);
    try {
      load(await api.saveAdmin(draft));
      setSaved("Saved.");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    }
  }

  async function runCheck() {
    setCheck(null);
    try {
      setCheck(await api.checkSegmentation());
    } catch (err) {
      setCheck({ ok: false, detail: err instanceof Error ? err.message : "Check failed" });
    }
  }

  function poll() {
    timer.current = window.setTimeout(async () => {
      try {
        const t = await api.segTest();
        setTest(t);
        if (t.state === "running") poll();
      } catch (err) {
        setTest({ state: "error", detail: err instanceof Error ? err.message : "Lost the test" });
      }
    }, POLL_MS);
  }

  async function runTest() {
    try {
      setTest(await api.startSegTest());
      poll();
    } catch (err) {
      setTest({ state: "error", detail: err instanceof Error ? err.message : "Could not start" });
    }
  }

  async function checkBook() {
    setBook("Reading the workbook");
    try {
      const r = await api.linkedWorkbook();
      const ready = r.rows.filter((x) => x.status === "ready").length;
      setBook(`${r.rows.length} case rows, ${ready} ready to ingest.`
        + (r.problems.length ? ` ${r.problems.join(" ")}` : ""));
    } catch (err) {
      setBook(err instanceof Error ? err.message : "Could not read the workbook");
    }
  }

  if (!view) return <div className="admin">{error ? <p className="error">{error}</p> : null}</div>;
  const s = view.settings;
  const provider = draft.segmentation_provider as Provider;
  const changed = JSON.stringify(draft) !== JSON.stringify({
    segmentation_provider: s.segmentation_provider, modal_app_name: s.modal_app_name,
    modal_function_name: s.modal_function_name, modal_token_id: "", modal_token_secret: "",
    remote_url: s.remote_url, remote_token: "", local_device: s.local_device,
    workbook_path: s.workbook_path,
  });

  return (
    <div className="admin">
      {error && <p className="error">{error}</p>}
      <section className="card">
        <h2>Segmentation</h2>
        <p className="muted small">
          Where TotalSegmentator runs. Only de-identified CTs are sent anywhere. Saved choice:
          {" "}<strong data-testid="saved-provider">
            {PROVIDERS.find(([p]) => p === s.segmentation_provider)?.[1]}
          </strong>
          {view.segmentation_configured ? ", ready to use." : ", not set up yet."}
        </p>
        <div className="providers" data-testid="provider">
          {PROVIDERS.map(([value, label, help]) => (
            <label key={value} className="provider">
              <input type="radio" name="provider" checked={provider === value}
                onChange={() => set("segmentation_provider", value)} />
              <span><strong>{label}</strong><br /><span className="muted small">{help}</span></span>
            </label>
          ))}
        </div>

        {provider === "modal" && (
          <div className="two">
            <Secret label="Modal token ID" saved={s.has_modal_token_id}
              value={draft.modal_token_id} onChange={(v) => set("modal_token_id", v)} />
            <Secret label="Modal token secret" saved={s.has_modal_token_secret}
              value={draft.modal_token_secret} onChange={(v) => set("modal_token_secret", v)} />
            <label>
              Modal app
              <input value={draft.modal_app_name} onChange={(e) => set("modal_app_name", e.target.value)} />
            </label>
            <label>
              Modal function
              <input value={draft.modal_function_name}
                onChange={(e) => set("modal_function_name", e.target.value)} />
            </label>
          </div>
        )}
        {provider === "remote" && (
          <div className="two">
            <label>
              GPU worker address
              <input value={draft.remote_url} placeholder="http://home-pc:8100"
                data-testid="remote-url" onChange={(e) => set("remote_url", e.target.value)} />
            </label>
            <Secret label="Worker token" saved={s.has_remote_token} value={draft.remote_token}
              testId="remote-token" onChange={(v) => set("remote_token", v)} />
          </div>
        )}
        {provider === "local" && (
          <label>
            Run on
            <select value={draft.local_device} onChange={(e) => set("local_device", e.target.value)}>
              <option value="cpu">processor (cpu)</option>
              <option value="mps">Apple GPU (mps, unverified)</option>
            </select>
          </label>
        )}

        <div className="row">
          <button onClick={save} disabled={!changed}>Save</button>
          <button className="secondary" onClick={runCheck} disabled={changed}
            title={changed ? "Save first" : ""}>Check connection</button>
          <button className="secondary" onClick={runTest}
            disabled={changed || !view.segmentation_configured || test?.state === "running"}>
            {test?.state === "running" ? "Test running" : "Test segmentation"}
          </button>
        </div>
        {saved && <p className="small muted">{saved}</p>}
        {check && (
          <p className={`small ${check.ok ? "ok-text" : "error"}`} data-testid="check-result">
            {check.detail}
          </p>
        )}
        {test && test.state !== "idle" && (
          <p className={`small ${test.state === "error" ? "error" : "muted"}`} data-testid="test-result">
            Test {test.state}
            {test.source ? ` on ${test.source}` : ""}
            {test.seconds !== undefined ? `, ${test.seconds} s` : ""}
            {test.peak_gpu_gb ? `, peak graphics memory ${test.peak_gpu_gb} GB` : ""}
            {test.detail ? `. ${test.detail}` : ""}
          </p>
        )}
        <p className="muted small">
          The test sends the newest ingested case's de-identified CT (or a synthetic phantom if
          there is none), and checks that the organ map comes back on the CT's grid.
        </p>
      </section>

      <section className="card">
        <h2>Case workbook</h2>
        <p className="muted small">
          The shared workbook in the OneDrive folder on this computer. It is only read, never
          changed. In Finder, set its folder to Always keep on this device.
        </p>
        <label>
          Workbook path (.xlsx)
          <input value={draft.workbook_path} data-testid="workbook-path" spellCheck={false}
            placeholder="/Users/you/Library/CloudStorage/OneDrive-University/Stone/cases.xlsx"
            onChange={(e) => set("workbook_path", e.target.value)} />
        </label>
        <div className="row">
          <button onClick={save} disabled={!changed}>Save</button>
          <button className="secondary" onClick={checkBook} disabled={changed || !s.workbook_path}>
            Check workbook
          </button>
        </div>
        {book && <p className="small muted" data-testid="workbook-check">{book}</p>}
      </section>

      <section className="card">
        <h2>Folders on this computer</h2>
        <table className="series">
          <tbody>
            <tr><td>Case folders</td><td><code>{view.paths.data_dir}</code></td></tr>
            <tr><td>MRN key</td><td><code>{view.paths.key_file}</code></td></tr>
            <tr><td>These settings</td><td><code>{view.paths.settings_file}</code></td></tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}
