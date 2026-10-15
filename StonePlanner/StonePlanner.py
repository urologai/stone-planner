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

Phase 2 additions:

  - Targets: stone centroids, posterior calyx proxies, or both (combo box).
    A point list chosen as manual targets replaces them (the manual
    target-calyx pick from PLAN.md).
  - Parameters file: an optional JSON object of PlanParams fields (weights,
    margins, calyx angle), the same file stoneplanner-plan --params-json
    takes, so surgeons can argue with the defaults without code.
  - Load segmentation files: reads <case_id>_organs.nii.gz and
    <case_id>_stones.nii.gz from the CT's folder (from stoneplanner-segment),
    for cases segmented in batch outside Slicer.
  - Correction loop: the automatic label maps are kept when segmentation is
    run or loaded. Export compares them with the edited segmentations and
    writes <case_id>_corrections.json (stoneplanner.corrections), plus
    <case_id>_organs_corrected.nii.gz, <case_id>_stones_corrected.nii.gz and
    <case_id>_stones_corrected.csv so the validation uses the corrected
    stones. Originals live in memory: if the scene is reloaded, load the
    segmentation files again before editing.
  - Tract labels show score, rigid and flexible reach, target group, and
    the nearest organ.
"""

from __future__ import annotations

import json
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
    """Markup name: rank, target, score, reach (rigid and flexible), nearest organ."""
    target = f" ({tract.target_group} {tract.target_source})" if tract.target_group else ""
    text = f"Tract {rank}{target}:"
    if np.isfinite(tract.score):
        text += f" score {tract.score:.2f},"
    text += f" reach {tract.reachable_fraction:.0%}"
    if np.isfinite(tract.flexible_reachable_fraction):
        text += f" (flex {tract.flexible_reachable_fraction:.0%})"
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


def params_from(path=None, **fields):
    """PlanParams from an optional JSON file of overrides, then explicit fields."""
    import json

    from stoneplanner.geometry import PlanParams

    overrides = json.loads(Path(path).read_text()) if path else {}
    unknown = set(overrides) - set(PlanParams.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Unknown PlanParams fields in {path}: {sorted(unknown)}")
    return PlanParams(**{**fields, **overrides})


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

        self.targetCombo = qt.QComboBox()
        self.targetCombo.addItems(["both", "calyx", "stone"])
        self.targetCombo.setToolTip("Stone centroids, posterior calyx proxies, or both")
        form.addRow("Targets:", self.targetCombo)

        self.manualTargets = slicer.qMRMLNodeComboBox()
        self.manualTargets.nodeTypes = ["vtkMRMLMarkupsFiducialNode"]
        self.manualTargets.noneEnabled = True
        self.manualTargets.addEnabled = True
        self.manualTargets.removeEnabled = False
        self.manualTargets.setMRMLScene(slicer.mrmlScene)
        self.manualTargets.setToolTip("Optional point list: plan to these points instead")
        form.addRow("Manual targets:", self.manualTargets)

        self.paramsPath = ctk.ctkPathLineEdit()
        self.paramsPath.filters = ctk.ctkPathLineEdit.Files
        self.paramsPath.nameFilters = ["JSON (*.json)"]
        self.paramsPath.setToolTip("Optional JSON object of PlanParams fields (weights, margins)")
        form.addRow("Parameters file:", self.paramsPath)

        self.deviceCombo = qt.QComboBox()
        self.deviceCombo.addItems(["cpu", "cuda", "mps"])
        self.deviceCombo.setToolTip("TotalSegmentator device. MPS is unverified.")
        form.addRow("Segmentation device:", self.deviceCombo)

        self.segmentButton = qt.QPushButton("Run segmentation")
        self.loadButton = qt.QPushButton("Load segmentation files")
        self.planButton = qt.QPushButton("Plan tracts")
        self.exportButton = qt.QPushButton("Export plan")
        for button in (self.segmentButton, self.loadButton, self.planButton, self.exportButton):
            form.addRow(button)
        self.exportButton.enabled = False

        self.resultLabel = qt.QLabel("")
        self.resultLabel.wordWrap = True
        form.addRow(self.resultLabel)
        self.layout.addStretch(1)

        self.segmentButton.connect("clicked(bool)", self.onSegment)
        self.loadButton.connect("clicked(bool)", self.onLoad)
        self.planButton.connect("clicked(bool)", self.onPlan)
        self.exportButton.connect("clicked(bool)", self.onExport)

    def _params(self):
        path = self.paramsPath.currentPath
        return params_from(path if path else None, sweep_deg=self.sweepSpin.value,
                           scope_depth_mm=self.depthSpin.value,
                           target_mode=self.targetCombo.currentText)

    def onSegment(self):
        volume = self.volumeSelector.currentNode()
        with slicer.util.tryWithErrorDisplay("Segmentation failed.", waitCursor=True):
            _, _, table = self.logic.runSegmentation(volume, self.deviceCombo.currentText)
            total = sum(row["volume_mm3"] for row in table)
            self.resultLabel.text = (f"{len(table)} stones, {total:.0f} mm3 total. "
                                     "Correct organs and stones in Segment Editor, then plan.")

    def onLoad(self):
        volume = self.volumeSelector.currentNode()
        with slicer.util.tryWithErrorDisplay("Loading segmentation failed.", waitCursor=True):
            organ_path, stone_path = self.logic.loadSegmentationFiles(volume)
            self.resultLabel.text = (f"Loaded {organ_path.name} and {stone_path.name}. "
                                     "Correct in Segment Editor, then plan.")

    def onPlan(self):
        volume = self.volumeSelector.currentNode()
        with slicer.util.tryWithErrorDisplay("Planning failed.", waitCursor=True):
            tracts = self.logic.planTracts(volume, self.sideCombo.currentText, self._params(),
                                           self.manualTargets.currentNode())
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
            written = self.logic.exportPlan(volume, Path(out_dir))
            self.resultLabel.text += "\nWrote " + ", ".join(p.name for p in written) + "."


# ---------------------------------------------------------------------------
# Logic


class StonePlannerLogic(ScriptedLoadableModuleLogic):
    """Converts between MRML nodes and numpy arrays and calls the package."""

    def __init__(self):
        ScriptedLoadableModuleLogic.__init__(self)
        self.lastTracts = []
        self.lastSide = None
        self.lastParams = None
        self.lastLabels = None        # (organs_kji, stones_kji) the last plan used
        self._originals = {}          # volume ID -> (organs_kji, stones_kji) as segmented
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

    def recordOriginals(self, volume, organs_kji, stones_kji):
        """Keep the automatic label maps for the correction log."""
        self._originals[volume.GetID()] = (np.array(organs_kji, copy=True),
                                           np.array(stones_kji, copy=True))

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
        self.recordOriginals(volume, organs_kji, stones_kji)
        return organNode, stoneNode, table

    def loadSegmentationFiles(self, volume, case_dir=None):
        """Load <case_id>_organs.nii.gz and _stones.nii.gz from the CT's folder."""
        _require_package()
        import nibabel as nib

        self._checkVolume(volume)
        case_dir = Path(case_dir or self.caseDirectory(volume) or ".")
        case_id = self.caseId(volume)
        ijk_to_ras = self._ijkToRas(volume)
        arrays = []
        paths = [case_dir / f"{case_id}_organs.nii.gz", case_dir / f"{case_id}_stones.nii.gz"]
        for path in paths:
            if not path.exists():
                raise FileNotFoundError(f"{path} not found; run stoneplanner-segment first")
            img = nib.load(str(path))
            if not np.allclose(img.affine, ijk_to_ras, atol=1e-3):
                raise ValueError(f"{path.name} is not on the grid of the loaded CT")
            arrays.append(np.asarray(img.dataobj).transpose(2, 1, 0))
        organs_kji, stones_kji = arrays
        self.loadSegmentations(volume, organs_kji, stones_kji)
        self.recordOriginals(volume, organs_kji, stones_kji)
        return paths[0], paths[1]

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

    def planTracts(self, volume, side="auto", params=None, targetsNode=None):
        """Plan on the current (possibly edited) segmentations and draw the top 3.

        targetsNode: optional point list; its points replace the automatic targets.
        Returns every candidate tract, ranked.
        """
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
        targets = None
        if targetsNode is not None and targetsNode.GetNumberOfControlPoints() > 0:
            targets = slicer.util.arrayFromMarkupsControlPoints(targetsNode, world=True)
        tracts = geometry.plan(ct, organs, stones, affine, side=side, params=params,
                               targets=targets, return_all=True)
        self.lastTracts, self.lastSide, self.lastParams = tracts, side, params
        self.lastLabels = (organs, stones)
        self.drawTracts(tracts)
        return tracts

    def exportPlan(self, volume, out_dir):
        """Write the plan, the correction log and the corrected label maps.

        Returns the list of paths written.
        """
        import nibabel as nib

        from stoneplanner import corrections, geometry, segment

        if not self.lastTracts:
            raise ValueError("Plan tracts before exporting.")
        out_dir = Path(out_dir)
        case_id = self.caseId(volume)
        written = list(geometry.write_outputs(self.lastTracts, out_dir, case_id,
                                              self.lastSide, self.lastParams))
        organs, stones = self.lastLabels
        ijk_to_ras = self._ijkToRas(volume)
        for name, arr, dtype in (("organs", organs, np.uint8), ("stones", stones, np.int16)):
            path = out_dir / f"{case_id}_{name}_corrected.nii.gz"
            img = nib.Nifti1Image(np.asarray(arr).transpose(2, 1, 0).astype(dtype), ijk_to_ras)
            nib.save(img, str(path))
            written.append(path)
        ct = slicer.util.arrayFromVolume(volume)
        table = segment.stone_table(stones, ct, organs, kji_affine(ijk_to_ras))
        written.append(segment.write_stone_table(
            table, out_dir / f"{case_id}_stones_corrected.csv"))
        original = self._originals.get(volume.GetID())
        if original is not None:
            record = corrections.correction_record(
                case_id, original[0], organs, original[1], stones, kji_affine(ijk_to_ras),
                segment.ORGAN_LABELS)
            written.append(corrections.write_correction_record(record, out_dir))
        return written


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
        logic.recordOriginals(volume, organs_kji, stones_kji)
        tracts = logic.planTracts(volume, "auto", geometry.PlanParams(target_mode="stone"))
        self.assertEqual(logic.lastSide, "right")
        self.assertFalse(tracts[0].collision)
        self.assertAlmostEqual(tracts[0].reachable_fraction, 1.0)
        np.testing.assert_allclose(tracts[0].target_mm, phantom.STONE_C, atol=0.2)
        lines = slicer.util.getNodesByClass("vtkMRMLMarkupsLineNode")
        self.assertEqual(len(lines), N_DRAWN)

        self.delayDisplay("Manual target")
        points = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", "targets")
        manual = phantom.STONE_C + np.array([0.0, 0.0, 6.0])
        points.AddControlPoint(*[float(v) for v in manual])
        tracts = logic.planTracts(volume, "right", geometry.PlanParams(), points)
        self.assertTrue(all(t.target_source == "manual" for t in tracts))

        self.delayDisplay("Correction log")
        with tempfile.TemporaryDirectory() as tmp:
            written = logic.exportPlan(volume, Path(tmp))
            names = {p.name for p in written}
            self.assertIn("phantom_tracts.json", names)
            self.assertIn("phantom_corrections.json", names)
            record = json.loads((Path(tmp) / "phantom_corrections.json").read_text())
            self.assertFalse(record["any_edit"])     # nothing was edited

            seg = stoneNode.GetSegmentation()
            seg.RemoveSegment(seg.GetSegmentIdBySegmentName("stone_1"))   # a "false positive"
            logic.planTracts(volume, "right", geometry.PlanParams(), points)
            logic.exportPlan(volume, Path(tmp))
            record = json.loads((Path(tmp) / "phantom_corrections.json").read_text())
            self.assertTrue(record["stones"]["edited"])
        self.delayDisplay("Stone Planner self-test passed")
