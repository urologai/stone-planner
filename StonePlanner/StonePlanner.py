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
"""

# Scaffold with: Slicer Extension Wizard -> Create Module -> Scripted
# Then replace the generated logic with calls into stoneplanner.
