"""
Iterative draft of equipment bbox layouts.

This file is a *working* draft that the verification loop edits in place.
Once the layouts here match the photos tightly, copy them back into
``src/equipment_catalog.py:BBOX_LAYOUTS`` and delete this file.

Coordinates are normalised (x0, y0, x1, y1) ∈ [0, 1] against the photo
in ``assets/<kind>_normal.png`` (1448 × 1086 for all kinds).
"""
from __future__ import annotations

DRAFT: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "pump": {
        # Upper half of the volute back-cover bolt circle
        "bolt-upper-row":   (0.20, 0.30, 0.50, 0.42),
        # Lower half of the volute back-cover bolt circle
        "bolt-lower-row":   (0.20, 0.53, 0.50, 0.65),
        # Cylindrical bearing housing between volute back-cover and shaft coupling
        "bearing-housing":  (0.50, 0.36, 0.58, 0.55),
        # Mechanical seal area at the volute back-cover / shaft exit
        "mechanical-seal":  (0.45, 0.42, 0.53, 0.55),
        # Pump-to-motor shaft coupling (stainless, between bearing housing and motor)
        "shaft-coupling":   (0.58, 0.20, 0.73, 0.46),
        # Whole volute casing (blue cast iron body)
        "casing-surface":   (0.20, 0.18, 0.55, 0.74),
        # Suction-side flange disc at the far left
        "pipe-flange":      (0.07, 0.36, 0.18, 0.58),
        # Drain plug area at the bottom of the volute
        "drain-port":       (0.28, 0.65, 0.40, 0.74),
    },
    "motor": {
        # Terminal box on top of the motor frame
        "terminal-box":      (0.36, 0.04, 0.66, 0.21),
        # Longitudinal cooling fins along the cylindrical body
        "cooling-fins":      (0.21, 0.33, 0.74, 0.66),
        # Rear fan cover with the mesh grille (left end, opposite shaft)
        "fan-cover":         (0.06, 0.36, 0.21, 0.72),
        # Cable gland where the supply cable enters the terminal box
        "cable-gland":       (0.30, 0.13, 0.40, 0.24),
        # Slotted mesh ventilation on the fan-cover end
        "ventilation-slots": (0.07, 0.42, 0.20, 0.66),
        # Whole motor frame surface (cylindrical body)
        "frame-surface":     (0.09, 0.30, 0.78, 0.74),
        # Shaft / drive end with the stainless coupling
        "shaft-end":         (0.77, 0.31, 0.96, 0.50),
        # Name plate on the side of the frame
        "name-plate":        (0.42, 0.50, 0.62, 0.66),
    },
    "fan": {
        # V-belt loop spanning the two pulleys
        "v-belt":           (0.42, 0.60, 0.86, 0.82),
        # Upper fan blade tip visible at the top of the inlet
        "blade-tip":        (0.38, 0.06, 0.54, 0.22),
        # Central hub where the three blades converge
        "blade-hub":        (0.18, 0.32, 0.40, 0.60),
        # Mesh inlet guard visible at the back of the housing opening
        "guard-mesh":       (0.05, 0.25, 0.50, 0.80),
        # Pillow-block bearing on the fan shaft
        "bearing-housing":  (0.39, 0.49, 0.52, 0.62),
        # Drive pulley on the motor side
        "pulley":           (0.74, 0.58, 0.86, 0.80),
        # Shaft / coupling between bearing housing and pulley
        "shaft-coupling":   (0.50, 0.50, 0.74, 0.62),
        # Whole fan assembly including the protective ring
        "frame-surface":    (0.02, 0.05, 0.96, 0.92),
    },
    "compressor": {
        # Round dial pressure gauge on the side of the manifold block
        "pressure-gauge":   (0.06, 0.30, 0.20, 0.48),
        # Oil sight window on the crankcase
        "oil-sight-glass":  (0.46, 0.32, 0.53, 0.42),
        # Safety relief valve on top of the cylinder manifold
        "safety-valve":     (0.28, 0.08, 0.36, 0.20),
        # Belt cover between cylinder block and motor
        "belt-cover":       (0.55, 0.20, 0.66, 0.42),
        # Cylinder head block with finned cylinders
        "cylinder-head":    (0.18, 0.04, 0.55, 0.30),
        # Visible delivery pipe coming down from the head
        "pipe-fitting":     (0.34, 0.16, 0.46, 0.46),
        # Drain valve at the bottom of the receiver tank
        "drain-valve":      (0.06, 0.68, 0.20, 0.82),
        # Finned cylinder cooling surface
        "cooling-fins":     (0.22, 0.06, 0.52, 0.28),
        # Horizontal receiver tank
        "tank-surface":     (0.10, 0.46, 0.96, 0.80),
        # Status indicator LED on the control box
        "status-lamp":      (0.81, 0.42, 0.86, 0.48),
    },
}


if __name__ == "__main__":
    import sys
    from pathlib import Path

    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(PROJECT_ROOT))

    from tools.bbox_verify import draw_all_for_kind, draw_single, OUTPUT_DIR

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=list(DRAFT.keys()))
    ap.add_argument("--region", default=None)
    args = ap.parse_args()

    layouts = DRAFT[args.kind]
    if not layouts:
        raise SystemExit(f"no draft for {args.kind}")
    if args.region:
        bbox = layouts[args.region]
        path = draw_single(args.kind, args.region, bbox=bbox,
                           out=OUTPUT_DIR / f"{args.kind}_{args.region}_draft.png")
    else:
        path = draw_all_for_kind(args.kind, layouts=layouts,
                                 out=OUTPUT_DIR / f"{args.kind}_ALL_draft.png")
    print(path)
