// Planner parameters in the browser: grouping for the form, and the overrides
// sent to the server (only values that differ from the defaults, so a plan's
// JSON shows what was changed). The same JSON works with
// `stoneplanner-plan --params-json` and the Slicer "Parameters file".

import type { ParamField } from "./api";

export type ParamValue = number | string | number[] | Record<string, number>;
export type ParamValues = Record<string, ParamValue>;

export const SECTIONS: { title: string; names: string[] }[] = [
  { title: "Scope", names: ["sweep_deg", "scope_depth_mm", "flex_sweep_deg", "flex_depth_mm"] },
  {
    title: "Targets",
    names: ["target_mode", "posterior_calyx_deg", "calyx_depth_mm", "calyx_levels",
      "min_target_stone_mm3"],
  },
  {
    title: "Penalty weights",
    names: ["w_colon", "colon_margin_mm", "w_pleura", "pleura_margin_mm", "w_supracostal",
      "w_parenchyma", "parenchyma_band_mm", "w_infundibular"],
  },
];

export const LABELS: Record<string, string> = {
  sweep_deg: "Rigid sweep half-angle (deg)",
  scope_depth_mm: "Rigid scope depth (mm)",
  flex_sweep_deg: "Flexible sweep half-angle (deg)",
  flex_depth_mm: "Flexible scope depth (mm)",
  target_mode: "Targets",
  posterior_calyx_deg: "Posterior calyx angle (deg)",
  calyx_depth_mm: "Calyx depth below capsule (mm)",
  calyx_levels: "Calyx levels along kidney (0 to 1)",
  min_target_stone_mm3: "Also target stones over (mm3)",
  w_colon: "Colon weight",
  colon_margin_mm: "Colon margin (mm)",
  w_pleura: "Pleura weight",
  pleura_margin_mm: "Pleura margin (mm)",
  w_supracostal: "Supracostal weight",
  w_parenchyma: "Parenchyma weight",
  parenchyma_band_mm: "Parenchyma band (mm, min and max)",
  w_infundibular: "Infundibular angle weight",
  n_entry_points: "Candidate entry points",
  top_n: "Tracts listed",
  ray_step_mm: "Ray sampling step (mm)",
  body_hu_threshold: "Body threshold (HU), if no body label",
  midaxillary_band_mm: "Mid-axillary band (mm)",
  rib_edge_bin_mm: "Rib edge bin width (mm)",
  sinus_closing_mm: "Sinus closing radius (mm)",
};

export function otherNames(fields: ParamField[]): string[] {
  const grouped = new Set(SECTIONS.flatMap((s) => s.names));
  return fields.map((f) => f.name).filter((n) => !grouped.has(n));
}

export function defaultsOf(fields: ParamField[]): ParamValues {
  return Object.fromEntries(fields.map((f) => [f.name, f.default as ParamValue]));
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function overrides(values: ParamValues, defaults: ParamValues): ParamValues {
  return Object.fromEntries(
    Object.entries(values).filter(([k, v]) => k in defaults && !same(v, defaults[k])),
  );
}

/** Merge an imported JSON object into the form, ignoring fields the form does not have. */
export function importValues(
  current: ParamValues,
  defaults: ParamValues,
  text: string,
): { values: ParamValues; ignored: string[] } {
  const parsed = JSON.parse(text);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("expected a JSON object of parameter names and values");
  }
  const values = { ...current };
  const ignored: string[] = [];
  for (const [k, v] of Object.entries(parsed)) {
    if (k in defaults) values[k] = v as ParamValue;
    else ignored.push(k);
  }
  return { values, ignored };
}
