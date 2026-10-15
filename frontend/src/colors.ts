// Label colour tables for NiiVue (its ColorMap: parallel R, G, B, A arrays at
// label indices I). Organ colours match the Slicer module's palette so a case
// looks the same in both. Tables stay small and dense: NiiVue mis-indexes a
// large sparse table (label 0 takes label 1's colour), which is why the server
// sends the organ map compacted to one value per organ group.

export type LabelColorMap = {
  R: number[];
  G: number[];
  B: number[];
  A: number[];
  I: number[];
  labels: string[];
};

const ORGAN_RGB: [string, [number, number, number]][] = [
  ["kidney", [217, 115, 89]],
  ["liver", [153, 77, 51]],
  ["spleen", [140, 64, 140]],
  ["colon", [204, 179, 102]],
  ["lung", [140, 191, 230]],
  ["rib", [242, 242, 217]],
  ["vertebrae", [230, 217, 179]],
  ["hip", [230, 217, 179]],
  ["aorta", [204, 26, 26]],
];

export const TRACT_RGB: [number, number, number][] = [
  [0, 204, 0], // rank 1 green
  [255, 230, 0], // rank 2 yellow
  [255, 128, 0], // rank 3 orange
];

export function organRgb(name: string): [number, number, number] {
  for (const [prefix, rgb] of ORGAN_RGB) {
    if (name === prefix || name.startsWith(prefix + "_")) return rgb;
  }
  return [128, 128, 128];
}

function build(entries: [number, string, [number, number, number], number][]): LabelColorMap {
  const sorted = [[0, "", [0, 0, 0], 0] as [number, string, [number, number, number], number],
    ...entries].sort((a, b) => a[0] - b[0]);
  return {
    I: sorted.map((e) => e[0]),
    labels: sorted.map((e) => e[1]),
    R: sorted.map((e) => e[2][0]),
    G: sorted.map((e) => e[2][1]),
    B: sorted.map((e) => e[2][2]),
    A: sorted.map((e) => e[3]),
  };
}

/** Table for the server's compact organ map: value g is display group g (1-based). */
export function organColorMap(groups: string[]): LabelColorMap {
  return build(groups.map((name, i) => [i + 1, name, organRgb(name), 255] as [number, string,
    [number, number, number], number]));
}

export function stoneColorMap(count: number): LabelColorMap {
  const entries: [number, string, [number, number, number], number][] = [];
  for (let i = 1; i <= Math.max(count, 1); i++) entries.push([i, `stone ${i}`, [255, 40, 40], 255]);
  return build(entries);
}

export function tractColorMap(): LabelColorMap {
  return build(TRACT_RGB.map((rgb, i) => [i + 1, `tract ${i + 1}`, rgb, 255]));
}
