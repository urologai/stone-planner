import { describe, expect, it } from "vitest";
import { organColorMap, organRgb, stoneColorMap, tractColorMap } from "./colors";
import { importValues, overrides, otherNames, SECTIONS } from "./params";

const defaults = { w_colon: 0.5, target_mode: "both", parenchyma_band_mm: [10, 30], top_n: 10 };

describe("params", () => {
  it("sends only changed values", () => {
    const values = { ...defaults, w_colon: 0.8, parenchyma_band_mm: [10, 30] };
    expect(overrides(values, defaults)).toEqual({ w_colon: 0.8 });
    expect(overrides({ ...defaults, parenchyma_band_mm: [5, 30] }, defaults)).toEqual({
      parenchyma_band_mm: [5, 30],
    });
  });

  it("imports known fields and reports unknown ones", () => {
    const { values, ignored } = importValues(defaults, defaults,
      '{"w_colon": 0.9, "w_colonn": 1}');
    expect(values.w_colon).toBe(0.9);
    expect(ignored).toEqual(["w_colonn"]);
    expect(() => importValues(defaults, defaults, "[1, 2]")).toThrow();
  });

  it("puts ungrouped fields under advanced", () => {
    const fields = Object.entries(defaults).map(([name, d]) => ({ name, default: d }));
    expect(otherNames(fields)).toEqual(["top_n"]);
    expect(SECTIONS.flatMap((s) => s.names)).toContain("w_infundibular");
  });
});

describe("colours", () => {
  it("colours the compact organ map by group", () => {
    expect(organRgb("rib_left_12")).toEqual(organRgb("rib_right_1"));
    const cm = organColorMap(["kidney", "liver", "rib"]);
    expect(cm.I).toEqual([0, 1, 2, 3]);
    expect(cm.A).toEqual([0, 255, 255, 255]);
    expect(cm.labels[3]).toBe("rib");
    expect([cm.R[1], cm.G[1], cm.B[1]]).toEqual(organRgb("kidney"));
  });

  it("builds stone and tract tables", () => {
    expect(stoneColorMap(3).I).toEqual([0, 1, 2, 3]);
    expect(stoneColorMap(0).I).toEqual([0, 1]);
    expect(tractColorMap().R).toEqual([0, 0, 255, 255]);
  });
});
