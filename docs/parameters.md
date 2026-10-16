# Planner parameters for surgeons to set

Every number below is a placeholder. None of them came from surgeons yet. Each one changes which tract the planner ranks first, so each needs a surgeon's value, or at least a surgeon's agreement, before the Phase 2 results mean anything.

To try different values, put them in a JSON file and pass it to the planner (`stoneplanner-plan ... --params-json my_values.json`) or pick it in the Slicer module under "Parameters file". The values used are written into every plan's output, so results stay traceable to the settings that produced them.

```
{"w_colon": 0.8, "colon_margin_mm": 15, "posterior_calyx_deg": 20}
```

## Scope model

| Parameter | Default | What it does | Question for surgeons |
|---|---|---|---|
| `sweep_deg` | 25 | Half-angle of the cone a rigid nephroscope can cover by pivoting at the parenchymal entry | How far can you lever a rigid scope in the kidney without tearing parenchyma? |
| `scope_depth_mm` | 120 | How far past the entry the rigid scope reaches | Working length past the sheath tip |
| `flex_sweep_deg` | 90 | Half-angle for the flexible scope. 90 covers everything ahead of the entry; above 90 reaches behind it | What fraction of the collecting system does a flexible scope reach in practice? |
| `flex_depth_mm` | 120 | Reach of the flexible scope | |

## Calyx proxy (noncontrast CT has no collecting system)

| Parameter | Default | What it does | Question for surgeons |
|---|---|---|---|
| `posterior_calyx_deg` | 30 | Posterior calyces are assumed to point this far posterior to the kidney's own coronal plane (after Brodel) | 20 or 30? Does it vary by pole? |
| `calyx_depth_mm` | 15 | The calyx target sits this far in from the kidney surface along that direction | Typical parenchymal thickness at a posterior calyx |
| `calyx_levels` | lower 0.2, mid 0.5, upper 0.8 | Where along the kidney's length each target calyx is placed | Are upper and lower pole calyces nearer the poles than this? |
| `target_mode` | both | Aim at stones, at calyx proxies, or both | |

## Penalties

The score is the rigid reachable fraction minus each weight times its penalty. Every penalty runs from 0 (no concern) to 1 (worst). A weight of 0.5 means the worst case of that penalty costs as much as half the stone. Collision with colon, spleen, liver, lung or rib is still an outright exclusion, not a penalty.

| Parameter | Default | Penalty | Question for surgeons |
|---|---|---|---|
| `w_colon`, `colon_margin_mm` | 0.5, 20 mm | Rises linearly from 0 at the margin to 1 at contact with colon | How close to colon is acceptable on a supine CT, given colon moves when prone? |
| `w_pleura`, `pleura_margin_mm` | 0.5, 20 mm | Same, for distance to lung (the pleura proxy; the costophrenic recess below the lung is not modelled) | |
| `w_supracostal` | 0.1 | 1 for any supracostal tract | How much reach would you trade to stay below the 12th rib? |
| `w_parenchyma`, `parenchyma_band_mm` | 0.1, 10 to 30 mm | 0 when the tract crosses 10 to 30 mm of kidney, rising outside that band | Is a long parenchymal path worse than a short one, or both? |
| `w_infundibular` | 0.3 | Angle between the tract and the calyx axis divided by 90 degrees (calyx targets only) | How much does an off-axis puncture cost you? |

With every weight set to 0, the ranking is the Phase 1 ranking: reachable fraction alone.
