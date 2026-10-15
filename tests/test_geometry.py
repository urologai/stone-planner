"""Geometry engine tests on synthetic phantoms. No real imaging in tests.

Build small numpy phantoms (e.g. 64^3, 1 mm isotropic) with:
    - a body mask (ellipsoid)
    - a kidney mask (smaller ellipsoid on one side)
    - a stone (sphere inside the kidney)
    - a colon (tube placed to block some rays)
    - a rib (thin slab)

Cases to cover:
    - a tract with a clear line to the stone has collision == False
    - placing the colon on the line flips collision == True and names "colon"
    - reachable_fraction is 1.0 for a stone entirely inside the cone and
      0.0 for a stone outside it
    - a larger sweep angle never decreases reachable_fraction
    - supracostal flag flips when the entry point moves above the rib slab
"""

import pytest


@pytest.mark.skip(reason="scaffold")
def test_placeholder():
    pass
