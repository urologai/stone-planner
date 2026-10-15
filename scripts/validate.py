"""Phase 1 exit-criterion report over the 20-case set.

Inputs:
    cases_dir/              one subfolder per case_id holding the ingest,
                            segment, and plan outputs
    manual.csv              case_id, manual_stone_volume_mm3, laterality,
                            operative_calyx (upper|mid|lower|pelvis),
                            operative_supracostal (0|1), stone_free (0|1),
                            residual_mm (blank if stone free)

Outputs:
    validation.csv          per case: automated vs manual volume, percent
                            error, top-ranked tract calyceal group, match (0|1),
                            top-ranked supracostal, match (0|1)
    validation_summary.txt  mean and median absolute percent error on volume,
                            fraction within 10%, calyceal concordance count,
                            supracostal concordance count

Calyceal group of a tract in Phase 1: assign by the target stone centroid's
craniocaudal position within the kidney mask (upper third / middle third /
lower third) or "pelvis" if the centroid lies in the renal sinus region
(inside the kidney convex hull but outside the parenchyma mask). This is
crude and is replaced in Phase 2 by the calyx proxy.

The manual.csv and all imaging stay outside the repo.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("cases_dir", type=Path)
    parser.add_argument("manual_csv", type=Path)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
