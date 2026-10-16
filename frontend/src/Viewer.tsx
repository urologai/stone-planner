import { Niivue, NVImage } from "@niivue/niivue";
import { useEffect, useRef, useState } from "react";
import { api, fileUrl, type CaseSummary } from "./api";
import { organColorMap, stoneColorMap, tractColorMap, type LabelColorMap } from "./colors";

type Layer = "organs" | "stones" | "overlay";
const LAYERS: Layer[] = ["organs", "stones", "overlay"];
const OPACITY: Record<Layer, number> = { organs: 0.4, stones: 1, overlay: 1 };
// Abdominal soft-tissue window; stones and bone saturate to white.
const CT_WINDOW = { cal_min: -160, cal_max: 340 };

export default function Viewer({
  c,
  version,
  focus,
}: {
  c: CaseSummary;
  version: number;
  focus: number[] | null;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const nvRef = useRef<Niivue | null>(null);
  // Overlays by layer. NiiVue does not keep list order when it loads several
  // volumes at once, so overlays are added one by one and tracked by handle.
  const overlays = useRef<Partial<Record<Layer, NVImage>>>({});
  const [visible, setVisible] = useState<Record<Layer, boolean>>({
    organs: true,
    stones: true,
    overlay: true,
  });
  const [status, setStatus] = useState("");

  useEffect(() => {
    const nv = new Niivue({
      backColor: [0.06, 0.07, 0.09, 1],
      show3Dcrosshair: true,
      crosshairColor: [0.3, 0.7, 1, 0.8],
      isResizeCanvas: true,
      logLevel: "error",
    });
    nvRef.current = nv;
    nv.attachToCanvas(canvas.current!).then(() => nv.setSliceType(nv.sliceTypeMultiplanar));
  }, []);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    let cancelled = false;
    (async () => {
      try {
        setStatus("Loading images");
        const v = `?v=${version}`;
        await nv.loadVolumes([{ url: fileUrl(c.id, "ct") + v, name: "ct.nii.gz",
          colormap: "gray", opacity: 1, ...CT_WINDOW }]);
        overlays.current = {};
        const labels = c.has.organs ? await api.labels() : null;
        const tables: Record<Layer, () => LabelColorMap | null> = {
          organs: () => (labels ? organColorMap(labels.display_groups) : null),
          stones: () => stoneColorMap(c.stones.length),
          overlay: () => tractColorMap(),
        };
        for (const layer of LAYERS) {
          if (cancelled) return;
          const present = layer === "overlay" ? c.has.overlay : c.has[layer];
          if (!present) continue;
          const file = layer === "organs" ? "organs_display" : layer;
          const vol = await NVImage.loadFromUrl({ url: fileUrl(c.id, file) + v,
            name: `${layer}.nii.gz`, opacity: visible[layer] ? OPACITY[layer] : 0 });
          const table = tables[layer]();
          if (!table) continue;
          vol.setColormapLabel(table);
          nv.addVolume(vol);
          overlays.current[layer] = vol;
        }
        nv.updateGLVolume();
        if (!cancelled) setStatus("");
      } catch (err) {
        if (!cancelled) setStatus(err instanceof Error ? err.message : "Could not load images");
      }
    })();
    return () => {
      cancelled = true;
    };
    // Layer visibility is read at load time and then applied by the effect below,
    // so toggling a layer does not reload the images.
  }, [c.id, version, c.has.organs, c.has.stones, c.has.overlay]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;
    for (const layer of LAYERS) {
      const vol = overlays.current[layer];
      if (!vol) continue;
      const index = nv.getVolumeIndexByID(vol.id);
      if (index >= 0) nv.setOpacity(index, visible[layer] ? OPACITY[layer] : 0);
    }
  }, [visible]);

  useEffect(() => {
    const nv = nvRef.current;
    if (!nv || !focus || nv.volumes.length === 0) return;
    nv.scene.crosshairPos = nv.mm2frac(focus);
    nv.drawScene();
  }, [focus]);

  return (
    <div className="viewer">
      <div className="viewer-bar">
        {LAYERS.map((layer) => (
          <label key={layer} className="toggle">
            <input
              type="checkbox"
              checked={visible[layer]}
              onChange={(e) => setVisible({ ...visible, [layer]: e.target.checked })}
            />
            {layer === "overlay" ? "tracts" : layer}
          </label>
        ))}
        {status && <span className="muted small" data-testid="viewer-status">{status}</span>}
      </div>
      <div className="viewer-canvas">
        <canvas ref={canvas} data-testid="viewer-canvas" />
      </div>
    </div>
  );
}
