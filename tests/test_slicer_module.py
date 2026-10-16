"""Checks on slicer_module/StonePlanner.py that run without 3D Slicer.

The module is imported with stub slicer, qt, ctk and vtk modules, which
catches syntax and import-time errors and lets the pure helpers be tested.
The MRML code paths are covered by the module's own self-test inside Slicer
(Developer mode > Reload and Test), which uses the same synthetic phantom.
"""

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from stoneplanner import geometry, phantom

MODULE_PATH = Path(__file__).resolve().parents[1] / "slicer_module" / "StonePlanner.py"


@pytest.fixture
def module(monkeypatch):
    slicer = types.ModuleType("slicer")
    scripted = types.ModuleType("slicer.ScriptedLoadableModule")
    for name in ("ScriptedLoadableModule", "ScriptedLoadableModuleWidget",
                 "ScriptedLoadableModuleLogic"):
        setattr(scripted, name, type(name, (), {"__init__": lambda self, *a, **k: None}))
    scripted.ScriptedLoadableModuleTest = type("ScriptedLoadableModuleTest", (), {})
    slicer.ScriptedLoadableModule = scripted
    for name, mod in (("slicer", slicer), ("slicer.ScriptedLoadableModule", scripted),
                      ("qt", types.ModuleType("qt")), ("ctk", types.ModuleType("ctk")),
                      ("vtk", types.ModuleType("vtk"))):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("StonePlannerUnderTest", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_module_classes_exist(module):
    for name in ("StonePlanner", "StonePlannerWidget", "StonePlannerLogic", "StonePlannerTest"):
        assert hasattr(module, name)


def test_kji_affine_maps_same_voxel_to_same_point(module):
    ijk_to_ras = np.array([
        [-0.7, 0.0, 0.0, 120.0],
        [0.0, -0.7, 0.0, 90.0],
        [0.0, 0.0, 2.5, -300.0],
        [0.0, 0.0, 0.0, 1.0],
    ])
    affine = module.kji_affine(ijk_to_ras)
    for i, j, k in [(0, 0, 0), (3, 5, 7), (100, 2, 40)]:
        np.testing.assert_allclose(affine[:3, :3] @ [k, j, i] + affine[:3, 3],
                                   ijk_to_ras[:3, :3] @ [i, j, k] + ijk_to_ras[:3, 3])


def test_plan_on_kji_arrays_matches_ijk(module):
    """What the Logic hands the planner gives the same plan as the NIfTI layout."""
    params = geometry.PlanParams(n_entry_points=100_000, top_n=3, target_mode="stone")
    ct, organs, stones = phantom.make_phantom()
    a = geometry.plan(ct, organs, stones, phantom.AFFINE, side="right", params=params)
    kji = [arr.transpose(2, 1, 0) for arr in (ct, organs, stones)]
    b = geometry.plan(*kji, module.kji_affine(phantom.AFFINE), side="right", params=params)
    for ta, tb in zip(a, b):
        assert ta.reachable_fraction == pytest.approx(tb.reachable_fraction)
        assert ta.tract_length_mm == pytest.approx(tb.tract_length_mm, abs=1e-6)
        assert ta.nearest_organ_mm == pytest.approx(tb.nearest_organ_mm, abs=1e-6)


def test_calyx_targets_on_kji_arrays_match_ijk(module):
    from stoneplanner import calyx

    _, organs, _ = phantom.make_phantom()
    kidney = organs == phantom.LABELS["kidney_right"]
    a = calyx.posterior_calyces(kidney, phantom.AFFINE, "right")
    b = calyx.posterior_calyces(kidney.transpose(2, 1, 0), module.kji_affine(phantom.AFFINE),
                                "right")
    assert [t.group for t in a] == [t.group for t in b]
    for ta, tb in zip(a, b):
        np.testing.assert_allclose(ta.point_mm, tb.point_mm, atol=0.5)   # one ray step
        np.testing.assert_allclose(ta.axis_mm, tb.axis_mm, atol=1e-6)


def test_tract_label(module):
    t = geometry.Tract(entry_mm=np.zeros(3), target_mm=np.ones(3), reachable_fraction=0.876,
                       tract_length_mm=80.0, parenchymal_depth_mm=20.0, nearest_organ="colon",
                       nearest_organ_mm=14.3, supracostal=True, collision=False,
                       collision_organ=None, flexible_reachable_fraction=1.0, score=0.71,
                       target_group="lower", target_source="calyx")
    assert module.tract_label(1, t) == (
        "Tract 1 (lower calyx): score 0.71, reach 88% (flex 100%), colon 14 mm, supracostal")
    plain = geometry.Tract(entry_mm=np.zeros(3), target_mm=np.ones(3), reachable_fraction=0.5,
                           tract_length_mm=80.0, parenchymal_depth_mm=20.0, nearest_organ="",
                           nearest_organ_mm=float("inf"), supracostal=False, collision=False,
                           collision_organ=None)
    assert module.tract_label(2, plain) == "Tract 2: reach 50%"


def test_params_from(module, tmp_path):
    path = tmp_path / "weights.json"
    path.write_text('{"w_colon": 0.8, "posterior_calyx_deg": 20}')
    params = module.params_from(path, sweep_deg=30.0, target_mode="calyx")
    assert (params.w_colon, params.posterior_calyx_deg, params.sweep_deg) == (0.8, 20, 30.0)
    assert params.target_mode == "calyx"
    assert module.params_from(None, sweep_deg=20.0).sweep_deg == 20.0
    path.write_text('{"w_colonn": 1}')
    with pytest.raises(ValueError, match="w_colonn"):
        module.params_from(path)


def test_colors_cover_every_organ(module):
    from stoneplanner.segment import ORGAN_LABELS

    assert all(module.organ_color(name) != (0.5, 0.5, 0.5) for name in ORGAN_LABELS)
    assert len(module.TRACT_COLORS) == module.N_DRAWN
