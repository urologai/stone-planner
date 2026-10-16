import type { TractRow } from "./api";
import { TRACT_RGB } from "./colors";

const pct = (v: number | null) => (v === null ? "" : `${Math.round(v * 100)}%`);
const mm = (v: number | null) => (v === null ? "" : `${Math.round(v)}`);

export default function TractsTable({ tracts, onSelect }: {
  tracts: TractRow[];
  onSelect: (t: TractRow) => void;
}) {
  return (
    <table className="tracts" data-testid="tracts-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Target</th>
          <th>Score</th>
          <th>Reach</th>
          <th>Flex</th>
          <th>Length mm</th>
          <th>Nearest organ</th>
          <th>Colon mm</th>
          <th>Pleura mm</th>
          <th>Angle</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {tracts.map((t) => {
          const rgb = TRACT_RGB[t.rank - 1];
          return (
            <tr key={t.rank} onClick={() => onSelect(t)} title="Show the target in the viewer">
              <td>
                {rgb && <span className="swatch" style={{ background: `rgb(${rgb.join(",")})` }} />}
                {t.rank}
              </td>
              <td>{t.target_group} {t.target_source}</td>
              <td>{t.score === null ? "" : t.score.toFixed(2)}</td>
              <td>{pct(t.reachable_fraction)}</td>
              <td>{pct(t.flexible_reachable_fraction)}</td>
              <td>{mm(t.tract_length_mm)}</td>
              <td>{t.nearest_organ ? `${t.nearest_organ}, ${mm(t.nearest_organ_mm)} mm` : ""}</td>
              <td>{mm(t.colon_mm)}</td>
              <td>{mm(t.pleura_mm)}</td>
              <td>{t.infundibular_angle_deg === null ? "" : `${Math.round(t.infundibular_angle_deg)}°`}</td>
              <td>
                {t.collision ? <span className="tag bad">hits {t.collision_organ}</span> : null}
                {t.supracostal ? <span className="tag">supracostal</span> : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
