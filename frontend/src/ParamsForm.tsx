import { useRef, useState } from "react";
import {
  importValues,
  LABELS,
  otherNames,
  overrides,
  SECTIONS,
  type ParamValue,
  type ParamValues,
} from "./params";
import type { ParamField } from "./api";

function NumberInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <input
      type="number"
      step="any"
      value={Number.isFinite(value) ? value : ""}
      onChange={(e) => onChange(e.target.valueAsNumber)}
    />
  );
}

function Field({ name, value, onChange }: {
  name: string;
  value: ParamValue;
  onChange: (v: ParamValue) => void;
}) {
  let input;
  if (name === "target_mode") {
    input = (
      <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
        <option value="both">stones and calyx proxies</option>
        <option value="calyx">calyx proxies</option>
        <option value="stone">stone centroids</option>
      </select>
    );
  } else if (Array.isArray(value)) {
    input = (
      <span className="pair">
        {value.map((v, i) => (
          <NumberInput key={i} value={v} onChange={(n) => {
            const next = [...value];
            next[i] = n;
            onChange(next);
          }} />
        ))}
      </span>
    );
  } else if (typeof value === "object") {
    input = (
      <span className="pair">
        {Object.entries(value).map(([k, v]) => (
          <span key={k} className="sub">
            {k}
            <NumberInput value={v} onChange={(n) => onChange({ ...value, [k]: n })} />
          </span>
        ))}
      </span>
    );
  } else if (typeof value === "number") {
    input = <NumberInput value={value} onChange={onChange} />;
  } else {
    input = <input value={String(value)} onChange={(e) => onChange(e.target.value)} />;
  }
  return (
    <label className="field">
      <span>{LABELS[name] ?? name}</span>
      {input}
    </label>
  );
}

export default function ParamsForm({ fields, values, defaults, onChange }: {
  fields: ParamField[];
  values: ParamValues;
  defaults: ParamValues;
  onChange: (v: ParamValues) => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  const changed = overrides(values, defaults);
  const set = (name: string) => (v: ParamValue) => onChange({ ...values, [name]: v });

  async function load(file: File) {
    try {
      const { values: next, ignored } = importValues(values, defaults, await file.text());
      onChange(next);
      setNote(ignored.length ? `Ignored unknown fields: ${ignored.join(", ")}` : "Loaded.");
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Could not read that file");
    }
  }

  function save() {
    const blob = new Blob([JSON.stringify(changed, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "planner_params.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="params">
      <p className="muted small">
        Clinical defaults are placeholders until surgeons set them. Changed values are sent with
        the plan and recorded in its output.
      </p>
      {SECTIONS.map((section) => (
        <fieldset key={section.title}>
          <legend>{section.title}</legend>
          {section.names.filter((n) => n in values).map((n) => (
            <Field key={n} name={n} value={values[n]} onChange={set(n)} />
          ))}
        </fieldset>
      ))}
      <details>
        <summary>Advanced</summary>
        {otherNames(fields).map((n) => (
          <Field key={n} name={n} value={values[n]} onChange={set(n)} />
        ))}
      </details>
      <div className="row">
        <button type="button" className="secondary" onClick={() => fileInput.current?.click()}>
          Load parameters
        </button>
        <button type="button" className="secondary" onClick={save}
          disabled={Object.keys(changed).length === 0}>
          Save parameters
        </button>
        <button type="button" className="secondary" onClick={() => onChange(defaults)}
          disabled={Object.keys(changed).length === 0}>
          Reset
        </button>
        <input ref={fileInput} type="file" accept=".json,application/json" hidden
          onChange={(e) => e.target.files?.[0] && load(e.target.files[0])} />
      </div>
      {note && <p className="muted small">{note}</p>}
    </div>
  );
}
