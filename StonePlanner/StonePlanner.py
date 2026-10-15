"""3D Slicer scripted module wrapping the stoneplanner package.

Install: Slicer > Edit > Application Settings > Modules > Additional module
paths > add this directory. Restart Slicer. Module appears under a new
"Research" category. The stoneplanner package must be pip-installed into
Slicer's Python (slicer.util.pip_install("-e /path/to/stone-planner")).

UI (keep it minimal; Slicer's own tools do everything else):
    - Input volume selector (the de-identified CT already loaded in Slicer)
    - Side: left / right / auto
    - Sweep angle (deg) and scope depth (mm) spin boxes
    - Button: Run segmentation  -> calls stoneplanner.segment, loads the organ
      and stone label maps as segmentation nodes so the user can fix them in
      the Segment Editor before planning
    - Button: Plan tracts       -> calls stoneplanner.geometry.plan on the
      current segmentation nodes (after any manual edits), draws the top 3
      tracts as line markups. Rank 1 green, 2 yellow, 3 orange. Markup label
      shows reachable fraction and nearest organ distance.
    - Button: Export plan       -> writes the tracts CSV/JSON next to the case

Do not reimplement any geometry here. This file converts between Slicer
nodes and numpy arrays and calls the package. If the package API changes,
this is the only file that should need to change.

Standard Slicer scripted module layout: ScriptedLoadableModule,
ScriptedLoadableModuleWidget, ScriptedLoadableModuleLogic, and a Test class.
Scaffold with the Extension Wizard if starting from nothing.

Implementation notes:

  - Organ segments are named with TotalSegmentator class names through a
    color table built from stoneplanner.segment.ORGAN_LABELS. Planning maps
    segment names back to those label values, so a segment fixed in the
    Segment Editor keeps working as long as its name is kept. A segment with
    any other name is ignored by the planner. The "body" segment is hidden
    by default; it is the skin surface the entry points are sampled from.
  - Stones are a second segmentation node, one segment per stone. Adding,
    removing or painting stone segments before planning changes the targets
    and the reachable fraction.
  - Slicer arrays are indexed [k, j, i]. kji_affine() builds the matching
    voxel-to-RAS affine, so arrays go to the package without a transpose.
  - Segmentation runs TotalSegmentator on a temporary NIfTI saved from the
    volume node. It blocks the UI for the minutes it takes on CPU.
  - A volume under a parent transform is refused; harden the transform
    first so array geometry and RAS agree.
  - Device: CPU by default. CUDA and MPS are offered; MPS is unverified.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import ctk
import numpy as np
import qt
import slicer
import vtk
from slicer.ScriptedLoadableModule import (
    ScriptedLoadableModule,
    ScriptedLoadableModuleLogic,
    ScriptedLoadableModuleTest,
    ScriptedLoadableModuleWidget,
)

TRACT_COLORS = ((0.0, 0.8, 0.0), (1.0, 0.9, 0.0), (1.0, 0.5, 0.0))   # rank 1, 2, 3
N_DRAWN = 3
ORGAN_COLOR_TABLE_NAME = "StonePlanner organ labels"


# ---------------------------------------------------------------------------
# Pure helpers (no MRML), kept separate so they can be checked outside Slicer


def kji_affine(ijk_to_ras: np.ndarray) -> np.ndarray:
    """Voxel-to-RAS affine for an array indexed [k, j, i], from Slicer's IJKToRAS."""
    ijk_to_ras = np.asarray(ijk_to_ras, dtype=float)
    out = ijk_to_ras.copy()
    out[:3, :3] = ijk_to_ras[:3, [2, 1, 0]]
    return out


def tract_label(rank: int, tract) -> str:
    """Markup name: rank, reachable fraction, nearest organ and its distance."""
    text = f"Tract {rank}: reach {tract.reachable_fraction:.0%}"
    if tract.nearest_organ and np.isfinite(tract.nearest_organ_mm):
        text += f", {tract.nearest_organ} {tract.nearest_organ_mm:.0f} mm"
    if tract.supracostal:
        text += ", supracostal"
    if tract.collision:
        text += f", COLLIDES with {tract.collision_organ}"
    return text


def organ_color(name: str) -> tuple[float, float, float]:
    """Fixed colors per organ group so cases look alike."""
    palette = {
        "kidney": (0.85, 0.45, 0.35), "liver": (0.6, 0.3, 0.2), "spleen": (0.55, 0.25, 0.55),
        "colon": (0.8, 0.7, 0.4), "lung": (0.55, 0.75, 0.9), "rib": (0.95, 0.95, 0.85),
        "vertebrae": (0.9, 0.85, 0.7), "hip": (0.9, 0.85, 0.7), "aorta": (0.8, 0.1, 0.1),
        "body": (0.9, 0.8, 0.7),
    }
    for prefix, color in palette.items():
        if name == prefix or name.startswith(prefix + "_"):
            return color
    return (0.5, 0.5, 0.5)


def _require_package():
    try:
        import stoneplanner  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "The stoneplanner package is not installed in Slicer's Python. Run "
            'slicer.util.pip_install("-e /path/to/stone-planner") in the Python console.'
        ) from exc


# ---------------------------------------------------------------------------
# Module


class StonePlanner(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "Stone Planner"
        self.parent.categories = ["Research"]
        self.parent.dependencies = []
        self.parent.contributors = ["stone-planner authors"]
        self.parent.helpText = (
            "Ranks PCNL access tracts by reachable stone fraction with hard-fail "
            "collision checks, from autocontoured noncontrast CT. Research proof of "
            "concept. Not for clinical use. Planning only, no navigation."
        )
        self.parent.acknowledgementText = (
            "Organ segmentation by TotalSegmentator (Wasserthal et al.). Stone "
            "thresholding after Elton et al. 2022."
        )


# ---------------------------------------------------------------------------
# Widget


class StonePlannerWidget(ScriptedLoadableModuleWidget):
    def setup(self):
        ScriptedLoadableModuleWidget.setup(self)
        self.logic = StonePlannerLogic()

        box = ctk.ctkCollapsibleButton()
        box.text = "Plan"
        self.layout.addWidget(box)
        form = qt.QFormLayout(box)

        self.volumeSelector = slicer.qMRMLNodeComboBox()
        self.volumeSelector.nodeTypes = ["vtkMRMLScalarVolumeNode"]
        self.volumeSelector.selectNodeUponCreation = True
        self.volumeSelector.addEnabled = False
        self.volumeSelector.removeEnabled = False
        self.volumeSelector.noneEnabled = False
        self.volumeSelector.showChildNodeTypes = False
        self.volumeSelector.setMRMLScene(slicer.mrmlScene)
        self.volumeSelector.setToolTip("De-identified noncontrast CT")
        form.addRow("CT volume:", self.volumeSelector)

        self.sideCombo = qt.QComboBox()
        self.sideCombo.addItems(["auto", "left", "right"])
        form.addRow("Side:", self.sideCombo)

        self.sweepSpin = qt.QDoubleSpinBox()
        self.sweepSpin.setRange(1.0, 89.0)
        self.sweepSpin.setValue(25.0)
        self.sweepSpin.setSuffix(" deg")
        form.addRow("Sweep half-angle:", self.sweepSpin)

        self.depthSpin = qt.QDoubleSpinBox()
        self.depthSpin.setRange(10.0, 400.0)
        self.depthSpin.setValue(120.0)
        self.depthSpin.setSuffix(" mm")
        form.addRow("Scope depth:", self.depthSpin)

        self.deviceCombo = qt.QComboBox()
        self.deviceCombo.addItems(["cpu", "cuda", "mps"])
        self.deviceCombo.setToolTip("TotalSegmentator device. MPS is unverified.")
        form.addRow("Segmentation device:", self.deviceCombo)

        self.segmentButton = qt.QPushButton("Run segmentation")
        self.planButton = qt.QPushButton("Plan tracts")
        self.exportButton = qt.QPushButton("Export plan")
        for button in (self.segmentButton, self.planButton, self.exportButton):
            form.addRow(button)
        self.exportButton.enabled = False

        self.resultLabel = qt.QLabel("")
        self.resultLabel.wordWrap = True
        form.addRow(self.resultLabel)
        self.layout.addStretch(1)

        self.segmentButton.connect("clicked(bool)", self.onSegment)
        self.planButton.connect("clicked(bool)", self.onPlan)
        self.exportButton.connect("clicked(bool)", self.onExport)

    def _params(self):
        from stoneplanner.geometry import PlanParams

        return PlanParams(sweep_deg=self.sweepSpin.value, scope_depth_mm=self.depthSpin.value)

    def onSegment(self):
        volume = self.volumeSelector.currentNode()
        with slicer.util.tryWithErrorDisplay("Segmentation failed.", waitCursor=True):
            _, _, table = self.logic.runSegmentation(volume, self.deviceCombo.currentText)
            total = sum(row["volume_mm3"] for row in table)
            self.resultLabel.text = (f"{len(table)} stones, {total:.0f} mm3 total. "
                                     "Correct organs and stones in Segment Editor, then plan.")

    def onPlan(self):
        volume = self.volumeSelector.currentNode()
        with slicer.util.tryWithErrorDisplay("Planning failed.", waitCursor=True):
            tracts = self.logic.planTracts(volume, self.sideCombo.currentText, self._params())
            if not tracts:
                self.resultLabel.text = "No tracts: no stones on the operative side."
                return
            lines = [tract_label(rank, t) for rank, t in enumerate(tracts[:N_DRAWN], start=1)]
            self.resultLabel.text = f"Side {self.logic.lastSide}.\n" + "\n".join(lines)
            self.exportButton.enabled = True

    def onExport(self):
        volume = self.volumeSelector.currentNode()
        start = self.logic.caseDirectory(volume) or str(Path.home())
        out_dir = qt.QFileDialog.getExistingDirectory(None, "Export plan to", start)
        if not out_dir:
            return
        with slicer.util.tryWithErrorDisplay("Export failed."):
            csv_path, json_path = self.logic.exportPlan(volume, Path(out_dir))
            self.resultLabel.text += f"\nWrote {csv_path.name} and {json_path.name}."


# ---------------------------------------------------------------------------
# Logic


class StonePlannerLogic(ScriptedLoadableModuleLogic):
    """Converts between MRML nodes and numpy arrays and calls the package."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        self.lastTracts = []
        self.lastSide = None
        self.lastParams = None
        self._lineNodeIds = []

    # Node names follow the volume, so several cases can sit in one scene.
    @staticmethod
    def organNodeName(volume):
        return f"{volume.GetName()} organs"

    @staticmethod
    def stoneNodeName(volume):
        return f"{volume.GetName()} stones"

    @staticmethod
    def caseId(volume):
        name = volume.GetName()
        for suffix in (".nii.gz", ".nii"):
            name = name.removesuffix(suffix)
        return name

    @staticmethod
    def caseDirectory(volume):
        storage = volume.GetStorageNode()
        if storage and storage.GetFileName():
            return os.path.dirname(storage.GetFileName())
        return None

    @staticmethod
    def _checkVolume(volume):
        if volume is None:
            raise ValueError("Select a CT volume first.")
        if volume.GetParentTransformNode():
            raise ValueError("The volume is under a transform. Harden the transform first.")

    @staticmethod
    def _ijkToRas(volume):
        matrix = vtk.vtkMatrix4x4()
        volume.GetIJKToRASMatrix(matrix)
        return slicer.util.arrayFromVTKMatrix(matrix)

    # -- color table and segmentation nodes ---------------------------------

    def organColorTable(self):
        from stoneplanner.segment import ORGAN_LABELS

        node = slicer.mrmlScene.GetFirstNodeByName(ORGAN_COLOR_TABLE_NAME)
        if node:
            return node
        node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLColorTableNode", ORGAN_COLOR_TABLE_NAME)
        node.SetTypeToUser()
        node.SetNumberOfColors(max(ORGAN_LABELS.values()) + 1)
        node.SetColor(0, "background", 0.0, 0.0, 0.0, 0.0)
        for name, value in ORGAN_LABELS.items():
            node.SetColor(value, name, *organ_color(name), 1.0)
        return node

    def segmentationFromLabelArray(self, labels_kji, volume, name, colorTable=None):
        """Segmentation node from a [k, j, i] label array on the volume's grid."""
        labelNode = slicer.util.addVolumeFromArray(
            labels_kji, self._ijkToRas(volume), name=f"{name} labelmap",
            nodeClassName="vtkMRMLLabelMapVolumeNode")
        try:
            if colorTable is not None:
                labelNode.CreateDefaultDisplayNodes()
                labelNode.GetDisplayNode().SetAndObserveColorNodeID(colorTable.GetID())
            old = slicer.mrmlScene.GetFirstNodeByName(name)
            if old and old.IsA("vtkMRMLSegmentationNode"):
                slicer.mrmlScene.RemoveNode(old)
            segNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
            segNode.CreateDefaultDisplayNodes()
            segNode.SetReferenceImageGeometryParameterFromVolumeNode(volume)
            if not slicer.modules.segmentations.logic().ImportLabelmapToSegmentationNode(
                    labelNode, segNode):
                raise RuntimeError(f"Could not import {name} into a segmentation")
        finally:
            slicer.mrmlScene.RemoveNode(labelNode)
        return segNode

    def labelArrayFromSegmentation(self, segNode, volume, colorTable=None):
        """[k, j, i] label array on the volume's grid.

        With a color table, label values come from segment names; without one,
        segments are numbered 1..N in segment order.
        """
        ids = vtk.vtkStringArray()
        segNode.GetSegmentation().GetSegmentIDs(ids)
        labelNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "__sp_temp__")
        try:
            if ids.GetNumberOfValues() == 0:
                return np.zeros(slicer.util.arrayFromVolume(volume).shape, dtype=np.int16)
            ok = slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(
                segNode, ids, labelNode, volume,
                slicer.vtkSegmentation.EXTENT_REFERENCE_GEOMETRY, colorTable)
            if not ok:
                raise RuntimeError(f"Could not export {segNode.GetName()} to a label map")
            return np.array(slicer.util.arrayFromVolume(labelNode), copy=True)
        finally:
            if labelNode.GetDisplayNode() and labelNode.GetDisplayNode().GetColorNode():
                colorNode = labelNode.GetDisplayNode().GetColorNode()
                if colorNode is not colorTable:
                    slicer.mrmlScene.RemoveNode(colorNode)
            slicer.mrmlScene.RemoveNode(labelNode)

    def _hideBody(self, segNode):
        segment_id = segNode.GetSegmentation().GetSegmentIdBySegmentName("body")
        if segment_id:
            segNode.GetDisplayNode().SetSegmentVisibility(segment_id, False)

    def loadSegmentations(self, volume, organs_kji, stones_kji):
        """Create the organ and stone segmentation nodes from [k, j, i] arrays."""
        organNode = self.segmentationFromLabelArray(
            organs_kji.astype(np.uint8), volume, self.organNodeName(volume), self.organColorTable())
        self._hideBody(organNode)
        stoneNode = self.segmentationFromLabelArray(
            stones_kji.astype(np.int16), volume, self.stoneNodeName(volume))
        segmentation = stoneNode.GetSegmentation()
        for n in range(segmentation.GetNumberOfSegments()):
            segment = segmentation.GetNthSegment(n)
            segment.SetName(f"stone_{segment.GetLabelValue()}")
            segment.SetColor(1.0, 1.0, 1.0)
        return organNode, stoneNode

    # -- the three actions ---------------------------------------------------

    def runSegmentation(self, volume, device="cpu"):
        """TotalSegmentator + stone thresholding. Returns (organ node, stone node, table)."""
        _require_package()
        import nibabel as nib

        from stoneplanner import segment

        self._checkVolume(volume)
        ct_kji = slicer.util.arrayFromVolume(volume)
        ijk_to_ras = self._ijkToRas(volume)
        with tempfile.TemporaryDirectory() as tmp:
            # Written with nibabel, not slicer.util.saveNode, so the volume's
            # storage node keeps pointing at the case file. NIfTI wants [i, j, k].
            ct_path = Path(tmp) / f"{self.caseId(volume)}.nii.gz"
            nib.save(nib.Nifti1Image(np.asarray(ct_kji).transpose(2, 1, 0), ijk_to_ras),
                     str(ct_path))
            organ_path = segment.run_totalsegmentator(ct_path, Path(tmp), device=device)
            organs_kji = np.asarray(nib.load(str(organ_path)).dataobj).transpose(2, 1, 0)
        if organs_kji.shape != ct_kji.shape:
            raise RuntimeError(f"Organ map shape {organs_kji.shape} != volume {ct_kji.shape}")
        affine = kji_affine(ijk_to_ras)
        stones_kji, table = segment.segment_stones(ct_kji, organs_kji, affine)
        organNode, stoneNode = self.loadSegmentations(volume, organs_kji, stones_kji)
        return organNode, stoneNode, table

    def clearTracts(self):
        for node_id in self._lineNodeIds:
            node = slicer.mrmlScene.GetNodeByID(node_id)
            if node:
                slicer.mrmlScene.RemoveNode(node)
        self._lineNodeIds = []

    def drawTracts(self, tracts):
        self.clearTracts()
        for rank, tract in enumerate(tracts[:N_DRAWN], start=1):
            line = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsLineNode",
                                                      tract_label(rank, tract))
            line.AddControlPoint(*[float(v) for v in tract.entry_mm], "entry")
            line.AddControlPoint(*[float(v) for v in tract.target_mm], "target")
            line.SetLocked(True)
            display = line.GetDisplayNode()
            display.SetSelectedColor(*TRACT_COLORS[rank - 1])
            display.SetColor(*TRACT_COLORS[rank - 1])
            display.SetPropertiesLabelVisibility(True)
            display.SetPointLabelsVisibility(False)
            self._lineNodeIds.append(line.GetID())

    def planTracts(self, volume, side="auto", params=None):
        """Plan on the current (possibly edited) segmentations and draw the top 3."""
        _require_package()
        from stoneplanner import geometry

        self._checkVolume(volume)
        organNode = slicer.mrmlScene.GetFirstNodeByName(self.organNodeName(volume))
        stoneNode = slicer.mrmlScene.GetFirstNodeByName(self.stoneNodeName(volume))
        if organNode is None or stoneNode is None:
            raise ValueError("Run segmentation first (or load the organ and stone "
                             f"segmentations as '{self.organNodeName(volume)}' and "
                             f"'{self.stoneNodeName(volume)}').")
        params = params or geometry.PlanParams()
        ct = slicer.util.arrayFromVolume(volume)
        organs = self.labelArrayFromSegmentation(organNode, volume, self.organColorTable())
        stones = self.labelArrayFromSegmentation(stoneNode, volume)
        affine = kji_affine(self._ijkToRas(volume))
        if side == "auto":
            side = geometry.resolve_side(stones, organs, affine, params)
        tracts = geometry.plan(ct, organs, stones, affine, side=side, params=params)
        self.lastTracts, self.lastSide, self.lastParams = tracts, side, params
        self.drawTracts(tracts)
        return tracts

    def exportPlan(self, volume, out_dir):
        """Write <case_id>_tracts.csv and .json for the last plan."""
        from stoneplanner import geometry

        if not self.lastTracts:
            raise ValueError("Plan tracts before exporting.")
        return geometry.write_outputs(self.lastTracts, Path(out_dir), self.caseId(volume),
                                      self.lastSide, self.lastParams)


# ---------------------------------------------------------------------------
# Self-test (Developer mode > Reload and Test). Synthetic phantom only.


class StonePlannerTest(ScriptedLoadableModuleTest):
    def setUp(self):
        slicer.mrmlScene.Clear()

    def runTest(self):
        self.setUp()
        self.test_kji_affine()
        self.test_plan_on_phantom()

    def _phantomVolume(self):
        from stoneplanner import phantom

        ct, organs, stones = phantom.make_phantom()
        # Phantom arrays are [i, j, k] under phantom.AFFINE; Slicer wants [k, j, i].
        volume = slicer.util.addVolumeFromArray(ct.transpose(2, 1, 0), phantom.AFFINE,
                                                name="phantom")
        return volume, organs.transpose(2, 1, 0), stones.transpose(2, 1, 0)

    def test_kji_affine(self):
        volume, _, _ = self._phantomVolume()
        logic = StonePlannerLogic()
        affine = kji_affine(logic._ijkToRas(volume))
        ijk_to_ras = logic._ijkToRas(volume)
        i, j, k = 3, 5, 7
        np.testing.assert_allclose(affine[:3, :3] @ [k, j, i] + affine[:3, 3],
                                   ijk_to_ras[:3, :3] @ [i, j, k] + ijk_to_ras[:3, 3])

    def test_plan_on_phantom(self):
        from stoneplanner import geometry, phantom

        self.delayDisplay("Building phantom")
        volume, organs_kji, stones_kji = self._phantomVolume()
        logic = StonePlannerLogic()
        organNode, stoneNode = logic.loadSegmentations(volume, organs_kji, stones_kji)
        seg = organNode.GetSegmentation()
        self.assertTrue(seg.GetSegmentIdBySegmentName("kidney_right"))
        self.assertTrue(seg.GetSegmentIdBySegmentName("rib_right_12"))
        self.assertTrue(stoneNode.GetSegmentation().GetSegmentIdBySegmentName("stone_1"))

        # Round trip through the segmentation nodes keeps label values.
        organs_back = logic.labelArrayFromSegmentation(organNode, volume, logic.organColorTable())
        np.testing.assert_array_equal(organs_back, organs_kji)

        self.delayDisplay("Planning")
        tracts = logic.planTracts(volume, "auto", geometry.PlanParams())
        self.assertEqual(logic.lastSide, "right")
        self.assertFalse(tracts[0].collision)
        self.assertAlmostEqual(tracts[0].reachable_fraction, 1.0)
        np.testing.assert_allclose(tracts[0].target_mm, phantom.STONE_C, atol=0.2)
        lines = slicer.util.getNodesByClass("vtkMRMLMarkupsLineNode")
        self.assertEqual(len(lines), N_DRAWN)

        with tempfile.TemporaryDirectory() as tmp:
            csv_path, json_path = logic.exportPlan(volume, Path(tmp))
            self.assertTrue(csv_path.exists() and json_path.exists())
        self.delayDisplay("Stone Planner self-test passed")
