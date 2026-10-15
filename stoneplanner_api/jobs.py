"""Background jobs: segmentation (via Modal) and planning (here, on CPU).

Both take seconds to minutes, longer than a request should wait, so they run
in a small thread pool and the browser polls the case for their state. One
job of each kind per case at a time.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields

import nibabel as nib
import numpy as np

from stoneplanner import geometry, segment
from stoneplanner_api.cases import (
    Case,
    CaseError,
    CaseStore,
    check_grid,
    clean_image,
    load_nifti,
)
from stoneplanner_api.overlay import organ_display, tract_overlay
from stoneplanner_api.segmentation import Segmenter

# PlanParams fields a user may set from the browser. Organ label IDs and the
# collision organ list stay as the code defines them.
LOCKED_PARAMS = {"organ_labels", "collision_organs"}
DISPLAY_TOP_N = 10


class JobBusy(RuntimeError):
    pass


class Jobs:
    def __init__(self, workers: int):
        self._pool = ThreadPoolExecutor(max_workers=max(workers, 1))
        self._lock = threading.Lock()
        self._state: dict[tuple[str, str], dict] = {}

    def submit(self, token: str, kind: str, fn, *args) -> None:
        key = (token, kind)
        with self._lock:
            if self._state.get(key, {}).get("state") == "running":
                raise JobBusy(f"{kind} is already running for this case")
            self._state[key] = {"state": "running", "started": time.time(), "message": ""}

        def run():
            try:
                message = fn(*args) or ""
                result = {"state": "done", "message": message}
            except (CaseError, ValueError) as exc:
                result = {"state": "error", "message": str(exc)}
            except Exception as exc:  # noqa: BLE001  report, do not crash the worker
                result = {"state": "error", "message": f"{type(exc).__name__}: {exc}"}
            with self._lock:
                self._state[key] = {**self._state[key], **result, "finished": time.time()}

        self._pool.submit(run)

    def status(self, token: str) -> dict[str, dict]:
        with self._lock:
            return {kind: dict(v) for (t, kind), v in self._state.items() if t == token}

    def forget(self, token: str) -> None:
        with self._lock:
            for key in [k for k in self._state if k[0] == token]:
                del self._state[key]


# ---------------------------------------------------------------------------
# Parameters


def param_fields() -> list[dict]:
    """Editable PlanParams fields with their defaults, for the browser form."""
    defaults = geometry.PlanParams()
    out = []
    for f in fields(geometry.PlanParams):
        if f.name in LOCKED_PARAMS:
            continue
        out.append({"name": f.name, "default": getattr(defaults, f.name)})
    return out


def _same_kind(value, default) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool) \
            and math.isfinite(value)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list) and len(value) == len(default) and all(
            _same_kind(v, d) for v, d in zip(value, default))
    if isinstance(default, dict):
        return isinstance(value, dict) and all(
            isinstance(k, str) and _same_kind(v, 0.0) for k, v in value.items())
    return False


def plan_params(overrides: dict) -> geometry.PlanParams:
    """PlanParams from browser overrides, refusing unknown or mistyped fields."""
    defaults = geometry.PlanParams()
    allowed = {f.name for f in fields(geometry.PlanParams)} - LOCKED_PARAMS
    unknown = set(overrides) - allowed
    if unknown:
        raise CaseError(f"unknown parameters: {sorted(unknown)}")
    for name, value in overrides.items():
        if not _same_kind(value, getattr(defaults, name)):
            raise CaseError(f"parameter {name} has the wrong type")
    if overrides.get("target_mode", "both") not in ("stone", "calyx", "both"):
        raise CaseError("target_mode must be stone, calyx or both")
    params = geometry.PlanParams(**overrides)
    if not 1 <= params.top_n <= 1000 or not 50 <= params.n_entry_points <= 20000:
        raise CaseError("top_n or n_entry_points out of range")
    return params


# ---------------------------------------------------------------------------
# Job bodies


def _load(case: Case, key: str) -> tuple[np.ndarray, np.ndarray]:
    img = nib.load(str(case.file(key)))
    return np.asanyarray(img.dataobj), img.affine


def run_segmentation(store: CaseStore, case: Case, segmenter: Segmenter) -> str:
    """Organs from the GPU service, then stones here. Replaces any earlier maps."""
    ct_img = nib.load(str(case.file("ct")))
    organs_img = load_nifti(segmenter(case.file("ct").read_bytes()), "segmentation result")
    check_grid(ct_img, organs_img, "segmentation result")
    organs_img = clean_image(organs_img, labels=True)
    ct = np.asanyarray(ct_img.dataobj)
    organs = np.asanyarray(organs_img.dataobj)
    stones, table = segment.segment_stones(ct, organs, ct_img.affine)
    store.clear_plan(case)
    nib.save(organs_img, str(case.file("organs")))
    case.file("organs_display").unlink(missing_ok=True)
    nib.save(clean_image(nib.Nifti1Image(stones, ct_img.affine), labels=True),
             str(case.file("stones")))
    segment.write_stone_table(table, case.file("stones_csv"))
    total = sum(r["volume_mm3"] for r in table)
    return f"{len(table)} stones, {total:.0f} mm3"


def ensure_stone_table(case: Case) -> None:
    """Stone CSV for cases whose stone map was uploaded rather than computed."""
    if case.has("stones") and case.has("organs") and not case.has("stones_csv"):
        ct, affine = _load(case, "ct")
        stones, _ = _load(case, "stones")
        organs, _ = _load(case, "organs")
        segment.write_stone_table(segment.stone_table(stones, ct, organs, affine),
                                  case.file("stones_csv"))


def display_organs(case: Case) -> None:
    """Write the viewer's compact organ map if it is missing."""
    if case.has("organs") and not case.has("organs_display"):
        img = nib.load(str(case.file("organs")))
        nib.save(organ_display(img, segment.ORGAN_LABELS), str(case.file("organs_display")))


def write_overlay(case: Case) -> None:
    rows = json.loads(case.file("tracts_json").read_text())["tracts"]
    img = nib.load(str(case.file("ct")))
    nib.save(tract_overlay(rows, img.shape, img.affine), str(case.file("overlay")))


def run_plan(store: CaseStore, case: Case, side: str, params: geometry.PlanParams) -> str:
    if not (case.has("organs") and case.has("stones")):
        raise CaseError("segment the case (or upload organ and stone maps) before planning")
    ct, affine = _load(case, "ct")
    organs, _ = _load(case, "organs")
    stones, _ = _load(case, "stones")
    if side == "auto":
        side = geometry.resolve_side(stones, organs, affine, params)
    tracts = geometry.plan(ct, organs, stones, affine, side=side, params=params,
                           return_all=True)
    if not tracts:
        raise CaseError("no tracts: no targets on the operative side")
    csv_path, json_path = geometry.write_outputs(tracts, case.path, case.case_id, side, params)
    os.replace(csv_path, case.file("tracts_csv"))
    os.replace(json_path, case.file("tracts_json"))
    write_overlay(case)
    clear = sum(not t.collision for t in tracts)
    return f"{len(tracts)} candidate tracts, {clear} collision-free, side {side}"
