import { useEffect, useRef, useState } from "react";
import {
  api,
  manualCsvUrl,
  scanFolder,
  workbookTemplateUrl,
  type CaseSummary,
  type Clinical,
  type IngestResult,
  type LibraryCase,
  type ScanResult,
  type SeriesInfo,
  type WorkbookResult,
  type WorkbookRow,
} from "./api";

// One page for everything README.md asks for per case. Every clinical field is
// a choice, a number or yes/no: there is deliberately no free-text box, so a
// name or date cannot end up in the data folder. The MRN goes only to the key
// file on this machine.

type Tract = { calyx: string; approach: string };
type YesNo = "" | "yes" | "no";

const CALYX = ["upper", "mid", "lower", "pelvis"];

type Form = {
  case_id: string;
  mrn: string;
  ct_position: string;
  ct_site: string;
  laterality: string;
  or_position: string;
  tracts: Tract[];
  sheath_fr: string;
  guidance: string;
  location: string[];
  staghorn: string;
  largest_mm: string;
  manual_volume_mm3: string;
  stone_free: YesNo;
  residual_mm: string;
  pleural_injury: YesNo;
  colon_injury: YesNo;
  transfusion: YesNo;
  age: string;
  sex: string;
  bmi: string;
};

const EMPTY: Form = {
  case_id: "",
  mrn: "",
  ct_position: "supine",
  ct_site: "",
  laterality: "",
  or_position: "",
  tracts: [{ calyx: "", approach: "" }],
  sheath_fr: "",
  guidance: "",
  location: [],
  staghorn: "",
  largest_mm: "",
  manual_volume_mm3: "",
  stone_free: "",
  residual_mm: "",
  pleural_injury: "no",
  colon_injury: "no",
  transfusion: "no",
  age: "",
  sex: "",
  bmi: "",
};

function Radio({ name, value, options, onChange, testId }: {
  name: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
  testId?: string;
}) {
  return (
    <span className="radios" data-testid={testId}>
      {options.map(([v, label]) => (
        <label key={v} className="inline">
          <input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} />
          {label}
        </label>
      ))}
    </span>
  );
}

function FolderPicker({ label, testId, onScanned, kind }: {
  label: string;
  testId: string;
  kind: "ct" | "postop";
  onScanned: (scan: ScanResult | null, chosen: string | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [state, setState] = useState("");
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);

  useEffect(() => {
    // React does not type the folder-picker attribute.
    input.current?.setAttribute("webkitdirectory", "");
  }, []);

  async function pick(files: FileList | null) {
    if (!files || files.length === 0) return;
    setScan(null);
    onScanned(null, null);
    setState(`Uploading ${files.length} files`);
    try {
      const result = await scanFolder(Array.from(files), (f) =>
        setState(f < 1 ? `Uploading ${files.length} files: ${Math.round(f * 100)}%`
          : "Reading DICOM headers"));
      const pick = kind === "ct" ? result.recommended_ct
        : result.recommended_ct ?? result.recommended_radiograph;
      setScan(result);
      setChosen(pick);
      onScanned(result, pick);
      setState(`${result.n_files} files, ${result.series.length} series`);
    } catch (err) {
      setState(err instanceof Error ? err.message : "Could not read that folder");
    }
  }

  const usable = (s: SeriesInfo) => (kind === "ct" ? s.kind === "ct" : s.usable);

  return (
    <div className="folder">
      <label className="file">
        {label}
        <input ref={input} type="file" multiple data-testid={testId}
          onChange={(e) => pick(e.target.files)} />
      </label>
      {state && <p className="muted small">{state}</p>}
      {scan && (
        <table className="series">
          <thead>
            <tr><th></th><th>#</th><th>Series</th><th>Type</th><th>Images</th><th>Slice mm</th></tr>
          </thead>
          <tbody>
            {scan.series.map((s) => (
              <tr key={s.series_uid} className={usable(s) ? "" : "muted"}>
                <td>
                  <input type="radio" name={`${testId}-series`} disabled={!usable(s)}
                    checked={chosen === s.series_uid}
                    onChange={() => { setChosen(s.series_uid); onScanned(scan, s.series_uid); }} />
                </td>
                <td>{s.series_number}</td>
                <td>{s.description}</td>
                <td>{s.kind === "ct" ? `CT ${s.orientation}` : s.kind === "radiograph"
                  ? `X-ray (${s.modality})` : s.modality}</td>
                <td>{s.n_images}</td>
                <td>{s.slice_thickness_mm ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function missing(f: Form, preop: boolean): string[] {
  const out: string[] = [];
  if (!f.mrn.trim()) out.push("MRN");
  if (!preop) out.push("preoperative CT series");
  if (!f.ct_site) out.push("CT done here or outside");
  if (!f.laterality) out.push("laterality");
  if (!f.or_position) out.push("OR position");
  f.tracts.forEach((t, i) => {
    if (!t.calyx || !t.approach) out.push(`tract ${i + 1}`);
  });
  if (f.location.length === 0) out.push("stone location");
  if (!f.staghorn) out.push("staghorn");
  if (!f.largest_mm) out.push("largest stone dimension");
  if (!f.stone_free) out.push("stone free");
  if (f.stone_free === "no" && !f.residual_mm) out.push("residual fragment size");
  if (!f.pleural_injury) out.push("pleural injury");
  if (!f.colon_injury) out.push("colon injury");
  if (!f.transfusion) out.push("transfusion");
  if (!f.age) out.push("age");
  if (!f.sex) out.push("sex");
  if (!f.bmi) out.push("BMI");
  return out;
}

const num = (s: string) => (s.trim() === "" ? null : Number(s));

const str = (v: string | number | null | undefined) => (v === null || v === undefined ? "" : String(v));
const yesNo = (v: boolean | null): YesNo => (v === true ? "yes" : v === false ? "no" : "");

/** The form filled from one workbook row; blanks stay blank so the form lists them. */
function formFromRow(row: WorkbookRow, locations: string[]): Form {
  const c: Clinical = row.clinical;
  return {
    case_id: row.case_id,
    mrn: row.mrn,
    ct_position: c.ct.position ?? "supine",
    ct_site: str(c.ct.site),
    laterality: str(c.operative.laterality),
    or_position: str(c.operative.or_position),
    tracts: c.operative.tracts.length > 0 ? c.operative.tracts.map((t) => ({ ...t }))
      : [{ calyx: "", approach: "" }],
    sheath_fr: str(c.operative.sheath_fr),
    guidance: str(c.operative.guidance),
    location: c.stone.location.filter((l) => locations.includes(l)),
    staghorn: str(c.stone.staghorn),
    largest_mm: str(c.stone.largest_mm),
    manual_volume_mm3: str(c.stone.manual_volume_mm3),
    stone_free: yesNo(c.postop.stone_free),
    residual_mm: str(c.postop.residual_mm),
    pleural_injury: yesNo(c.complications.pleural_injury),
    colon_injury: yesNo(c.complications.colon_injury),
    transfusion: yesNo(c.complications.transfusion),
    age: str(c.demographics.age),
    sex: str(c.demographics.sex),
    bmi: str(c.demographics.bmi),
  };
}

const STATUS_LABEL: Record<WorkbookRow["status"], string> = {
  ready: "ready",
  incomplete: "incomplete",
  ingested: "already ingested",
  conflict: "conflict",
};

function WorkbookImport({ onFill, ingested }: {
  onFill: (row: WorkbookRow) => void;
  ingested: string[];
}) {
  const [book, setBook] = useState<WorkbookResult | null>(null);
  const [state, setState] = useState("");

  async function load(file: File | undefined) {
    if (!file) return;
    setBook(null);
    setState("Reading the workbook");
    try {
      const result = await api.importWorkbook(file);
      setBook(result);
      setState(`${result.rows.length} case row${result.rows.length === 1 ? "" : "s"}`);
    } catch (err) {
      setState(err instanceof Error ? err.message : "Could not read that workbook");
    }
  }

  return (
    <section className="card">
      <div className="row spread">
        <h2>Import from the case workbook</h2>
        <a className="button secondary" href={workbookTemplateUrl}>Download blank workbook</a>
      </div>
      <p className="muted small">
        Fill the shared workbook on the university server, then choose it here. Nothing from it
        is kept on this computer except what you ingest. Pick a row to fill the form below, then
        choose the CT folder.
      </p>
      <label className="file">
        Workbook (.xlsx)
        <input type="file" accept=".xlsx" data-testid="workbook-input"
          onChange={(e) => { load(e.target.files?.[0]); e.target.value = ""; }} />
      </label>
      {state && <p className="muted small">{state}</p>}
      {book && book.problems.map((p) => <p key={p} className="small error">{p}</p>)}
      {book && book.rows.length > 0 && (
        <table className="series" data-testid="workbook-table">
          <thead>
            <tr><th>Row</th><th>Study ID</th><th>Status</th><th>Still needed or wrong</th><th></th></tr>
          </thead>
          <tbody>
            {book.rows.map((r) => {
              // Cases ingested since the import count as ingested too.
              const status = r.status !== "conflict" && ingested.includes(r.case_id)
                ? "ingested" : r.status;
              return (
                <tr key={`${r.row}-${r.case_id}`}>
                  <td>{r.row}</td>
                  <td>{r.case_id}</td>
                  <td className={status === "conflict" ? "error" : ""}>{STATUS_LABEL[status]}</td>
                  <td className="small">{r.errors.join("; ")}</td>
                  <td>
                    <button className="secondary"
                      disabled={status === "ingested" || status === "conflict"}
                      onClick={() => onFill(r)}>Fill form</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

export default function Ingest({ onOpen }: { onOpen: (c: CaseSummary) => void }) {
  const [form, setForm] = useState<Form>(EMPTY);
  const [locations, setLocations] = useState<string[]>([]);
  const [cases, setCases] = useState<LibraryCase[]>([]);
  const [preop, setPreop] = useState<{ staging: string; series_uid: string } | null>(null);
  const [postop, setPostop] = useState<{ staging: string; series_uid: string } | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<IngestResult | null>(null);
  const [height, setHeight] = useState("");
  const [filled, setFilled] = useState<WorkbookRow | null>(null);
  const [weight, setWeight] = useState("");

  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  useEffect(() => {
    api.ingestStatus().then((s) => {
      if (!s.enabled) return;
      setCases(s.cases);
      setLocations(s.stone_locations);
      setForm((f) => ({ ...f, case_id: s.next_case_id }));
    }).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    const h = Number(height) / 100;
    const w = Number(weight);
    if (h > 0.5 && w > 10) set("bmi", (w / (h * h)).toFixed(1));
  }, [height, weight]);

  function setTractCount(n: number) {
    setForm((f) => {
      const tracts = [...f.tracts];
      while (tracts.length < n) tracts.push({ calyx: "", approach: "" });
      return { ...f, tracts: tracts.slice(0, n) };
    });
  }

  const todo = missing(form, preop !== null);

  function fillFromWorkbook(row: WorkbookRow) {
    setForm(formFromRow(row, locations));
    setPreop(null);
    setPostop(null);
    setHeight("");
    setWeight("");
    setResult(null);
    setError("");
    setFilled(row);
    setFormKey((k) => k + 1);          // clears the folder pickers
  }

  async function submit() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const yes = (v: YesNo) => v === "yes";
      const res = await api.ingestCase({
        case_id: form.case_id.trim() || null,
        mrn: form.mrn.trim(),
        preop,
        postop,
        clinical: {
          ct: { position: form.ct_position, site: form.ct_site },
          operative: {
            laterality: form.laterality,
            or_position: form.or_position,
            tracts: form.tracts,
            sheath_fr: num(form.sheath_fr),
            guidance: form.guidance || null,
          },
          stone: {
            location: form.location,
            staghorn: form.staghorn,
            largest_mm: num(form.largest_mm),
            manual_volume_mm3: num(form.manual_volume_mm3),
          },
          postop: { stone_free: yes(form.stone_free), residual_mm: num(form.residual_mm) },
          complications: {
            pleural_injury: yes(form.pleural_injury),
            colon_injury: yes(form.colon_injury),
            transfusion: yes(form.transfusion),
          },
          demographics: { age: num(form.age), sex: form.sex, bmi: num(form.bmi) },
        },
      });
      setResult(res);
      setCases(res.cases);
      setForm({ ...EMPTY, case_id: res.next_case_id });
      setPreop(null);
      setPostop(null);
      setHeight("");
      setWeight("");
      setFilled(null);
      setFormKey((k) => k + 1);          // clears the folder pickers
      window.scrollTo({ top: 0 });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setBusy(false);
    }
  }

  async function open(caseId: string) {
    setError("");
    try {
      onOpen(await api.openLibraryCase(caseId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open the case");
    }
  }

  return (
    <div className="ingest">
      {result && (
        <div className="card ok" data-testid="ingest-result">
          <strong>{result.case_id} ingested.</strong>
          {result.notes.map((n) => <p key={n} className="small">{n}</p>)}
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <WorkbookImport onFill={fillFromWorkbook} ingested={cases.map((c) => c.case_id)} />
      {filled && (
        <p className="card small" data-testid="filled-from">
          Form filled from workbook row {filled.row} ({filled.case_id}). Choose the CT folder
          {filled.postop_imaging && filled.postop_imaging !== "none"
            ? ` and the postop ${filled.postop_imaging} folder` : ""}, check the form, then
          press Ingest case.
        </p>
      )}

      <div className="ingest-grid" key={formKey}>
        <div>
          <section className="card">
            <h2>Study ID</h2>
            <p className="muted small">
              The MRN is written only to the key file on this computer, never into the case
              folder. Keep the key file off shared drives.
            </p>
            <div className="two">
              <label>
                Case ID
                <input value={form.case_id} onChange={(e) => set("case_id", e.target.value)} />
              </label>
              <label>
                MRN
                <input value={form.mrn} autoComplete="off" spellCheck={false}
                  onChange={(e) => set("mrn", e.target.value)} />
              </label>
            </div>
          </section>

          <section className="card">
            <h2>Preoperative CT</h2>
            <p className="muted small">
              Noncontrast CT, full axial series as DICOM; thin slices (1 to 3 mm) if available.
              Choose the exported study folder; the axial series is picked for you.
            </p>
            <FolderPicker label="CT folder" testId="preop-folder" kind="ct"
              onScanned={(scan, uid) => setPreop(scan && uid
                ? { staging: scan.staging, series_uid: uid } : null)} />
            <div className="two">
              <label>
                Patient position on the CT
                <select value={form.ct_position} onChange={(e) => set("ct_position", e.target.value)}>
                  <option value="supine">supine</option>
                  <option value="prone">prone</option>
                  <option value="other">other</option>
                </select>
              </label>
              <div className="group" role="group">
                <span>CT done</span>
                <Radio name="ct_site" value={form.ct_site} testId="ct-site"
                  options={[["here", "here"], ["outside", "outside"]]}
                  onChange={(v) => set("ct_site", v)} />
              </div>
            </div>
          </section>

          <section className="card">
            <h2>Operative report</h2>
            <div className="two">
              <div className="group" role="group">
                <span>Laterality</span>
                <Radio name="lat" value={form.laterality} testId="laterality"
                  options={[["left", "left"], ["right", "right"]]}
                  onChange={(v) => set("laterality", v)} />
              </div>
              <div className="group" role="group">
                <span>OR position</span>
                <Radio name="orpos" value={form.or_position} testId="or-position"
                  options={[["prone", "prone"], ["supine", "supine"]]}
                  onChange={(v) => set("or_position", v)} />
              </div>
            </div>
            <label>
              Number of tracts
              <select value={form.tracts.length}
                onChange={(e) => setTractCount(Number(e.target.value))}>
                {[1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            {form.tracts.map((t, i) => (
              <div key={i} className="tract-row" data-testid={`tract-${i + 1}`}>
                <span className="small">Tract {i + 1}</span>
                <select value={t.calyx} aria-label={`Tract ${i + 1} calyceal group`}
                  onChange={(e) => {
                    const tracts = [...form.tracts];
                    tracts[i] = { ...t, calyx: e.target.value };
                    set("tracts", tracts);
                  }}>
                  <option value="">calyceal group</option>
                  {CALYX.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <Radio name={`approach-${i}`} value={t.approach}
                  options={[["subcostal", "subcostal"], ["supracostal", "supracostal"]]}
                  onChange={(v) => {
                    const tracts = [...form.tracts];
                    tracts[i] = { ...t, approach: v };
                    set("tracts", tracts);
                  }} />
              </div>
            ))}
            <div className="two">
              <label>
                Sheath size, Fr (optional)
                <input type="number" value={form.sheath_fr}
                  onChange={(e) => set("sheath_fr", e.target.value)} />
              </label>
              <label>
                Access guidance (optional)
                <select value={form.guidance} onChange={(e) => set("guidance", e.target.value)}>
                  <option value="">not recorded</option>
                  <option value="fluoroscopy">fluoroscopy</option>
                  <option value="ultrasound">ultrasound</option>
                  <option value="combined">combined</option>
                </select>
              </label>
            </div>
          </section>
        </div>

        <div>
          <section className="card">
            <h2>Stone</h2>
            <span className="small muted">Location (all that apply)</span>
            <div className="checks" data-testid="stone-location">
              {locations.map((loc) => (
                <label key={loc} className="inline">
                  <input type="checkbox" checked={form.location.includes(loc)}
                    onChange={(e) => set("location", e.target.checked
                      ? [...form.location, loc] : form.location.filter((l) => l !== loc))} />
                  {loc}
                </label>
              ))}
            </div>
            <div className="two">
              <label>
                Staghorn
                <select value={form.staghorn} onChange={(e) => set("staghorn", e.target.value)}>
                  <option value="">choose</option>
                  <option value="none">no</option>
                  <option value="partial">partial</option>
                  <option value="complete">complete</option>
                </select>
              </label>
              <label>
                Largest dimension (mm)
                <input type="number" value={form.largest_mm}
                  onChange={(e) => set("largest_mm", e.target.value)} />
              </label>
            </div>
            <label>
              Manual stone volume, mm3 (optional)
              <input type="number" value={form.manual_volume_mm3}
                onChange={(e) => set("manual_volume_mm3", e.target.value)} />
            </label>
          </section>

          <section className="card">
            <h2>Postoperative outcome</h2>
            <div className="two">
              <div className="group" role="group">
                <span>Stone free</span>
                <Radio name="sf" value={form.stone_free} testId="stone-free"
                  options={[["yes", "yes"], ["no", "no, residual fragments"]]}
                  onChange={(v) => set("stone_free", v as YesNo)} />
              </div>
              {form.stone_free === "no" && (
                <label>
                  Largest residual fragment (mm)
                  <input type="number" value={form.residual_mm}
                    onChange={(e) => set("residual_mm", e.target.value)} />
                </label>
              )}
            </div>
            <FolderPicker label="Postop CT or KUB folder (optional)" testId="postop-folder"
              kind="postop"
              onScanned={(scan, uid) => setPostop(scan && uid
                ? { staging: scan.staging, series_uid: uid } : null)} />
          </section>

          <section className="card">
            <h2>Complications</h2>
            {([["pleural_injury", "Pleural injury"], ["colon_injury", "Colon injury"],
              ["transfusion", "Transfusion"]] as const).map(([key, label]) => (
              <div key={key} className="group row-label" role="group">
                <span>{label}</span>
                <Radio name={key} value={form[key]}
                  options={[["no", "no"], ["yes", "yes"]]}
                  onChange={(v) => set(key, v as YesNo)} />
              </div>
            ))}
          </section>

          <section className="card">
            <h2>Patient</h2>
            <div className="three">
              <label>
                Age
                <input type="number" value={form.age} onChange={(e) => set("age", e.target.value)} />
              </label>
              <label>
                Sex
                <select value={form.sex} onChange={(e) => set("sex", e.target.value)}>
                  <option value="">choose</option>
                  <option value="female">female</option>
                  <option value="male">male</option>
                  <option value="other">other</option>
                </select>
              </label>
              <label>
                BMI
                <input type="number" value={form.bmi} onChange={(e) => set("bmi", e.target.value)} />
              </label>
            </div>
            <p className="muted small">Or work out BMI from height and weight:</p>
            <div className="two">
              <label>
                Height (cm)
                <input type="number" value={height} onChange={(e) => setHeight(e.target.value)} />
              </label>
              <label>
                Weight (kg)
                <input type="number" value={weight} onChange={(e) => setWeight(e.target.value)} />
              </label>
            </div>
          </section>

          <section className="card">
            {todo.length > 0 && (
              <p className="muted small" data-testid="ingest-missing">Still needed: {todo.join(", ")}.</p>
            )}
            <button disabled={busy || todo.length > 0} onClick={submit}>
              {busy ? "De-identifying and saving" : "Ingest case"}
            </button>
          </section>
        </div>
      </div>

      <section className="card">
        <div className="row spread">
          <h2>Cases on this computer</h2>
          <a className="button secondary" href={manualCsvUrl}>Download manual.csv</a>
        </div>
        <table className="tracts" data-testid="library-table">
          <thead>
            <tr>
              <th>Case</th><th>Side</th><th>Tracts</th><th>Stone free</th><th>Postop imaging</th>
              <th>Segmented</th><th>Planned</th><th></th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr key={c.case_id}>
                <td>
                  {c.case_id}{" "}
                  {c.burned_in_suspected && <span className="tag bad">check CT for burned-in text</span>}
                  {c.postop_burned_in_suspected && (
                    <span className="tag bad">check postop images for burned-in text</span>
                  )}
                </td>
                <td>{c.laterality}</td>
                <td>{c.tracts.join(", ")}</td>
                <td>{c.stone_free ? "yes" : "no"}</td>
                <td>{c.postop_imaging}</td>
                <td>{c.segmented ? "yes" : ""}</td>
                <td>{c.planned ? "yes" : ""}</td>
                <td><button className="secondary" onClick={() => open(c.case_id)}>Open in planner</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
