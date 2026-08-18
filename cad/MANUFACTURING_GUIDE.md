# 19-Inch 9U Ruggedized Enclosure — Manufacturing Guide

> Reference model: `cad/rack_enclosure.scad`  
> Standard: EIA-310-D 19" Rack (IEC 60297)

---

## 1. Materials Specification

| Part | Material | Thickness | Finish |
|:--|:--|:--|:--|
| Side, Top, Bottom Panels | 6061-T6 Aluminum Sheet | 2.0mm | Black anodized, 15µm |
| Front & Rear Frames | 6061-T6 Aluminum Extrusion | 3.0mm wall | Black anodized |
| Mounting Rails (x4) | Cold-Rolled Steel (CRS) | 2.0mm | Zinc-phosphate + black powder coat |
| Corner Guards | Vulcanized Rubber (Shore 60A) | 6mm radius | Natural black |
| Fasteners | A2 Stainless Steel | M4 / M6 | — |

---

## 2. Fabrication Steps

### Step 1 — DXF Export from OpenSCAD

```bash
openscad -o rack_enclosure.dxf --export-format dxf cad/rack_enclosure.scad
openscad -o rack_enclosure.stl cad/rack_enclosure.scad
```

Export DXF projection views per panel for CNC/laser cutting. Export STL for 3D visualization and tolerance checking.

### Step 2 — Laser Cutting / CNC Punching

- Cut 2.0mm aluminum side (×2), top (×1), bottom (×1) panels from DXF.
- Cut **rear cutouts:** 2× 360mm × 360mm radiator fan openings (see `rear_cutouts()` module).
- Cut **side vent mesh** patterns per `vent_mesh()` module (8×N grid, 8mm × 30mm slots).
- Tolerance: ±0.2mm for panel dimensions; ±0.1mm for rail hole pitch.

### Step 3 — CNC Bending

- Bend side panels on CNC press brake:
  - 90° folds, 2.0mm inner bend radius (matches material thickness).
  - Flanges: 20mm top/bottom, 15mm front/rear for frame attachment.
- Deburr all cut edges with a deburring tool before assembly.

### Step 4 — Rail Preparation

- Tap all M6 holes at 15.875mm pitch (EIA-310-D) along full rail length.
- Hole diameter: 7.5mm pre-tap, M6 thread, 10mm engagement depth minimum.
- Rails spaced at **482.6mm center-to-center** (19.0" standard).

### Step 5 — Assembly & Fastening

1. Assemble corner extrusions first — use 4.8mm × 10mm **stainless steel blind rivets** at 80mm spacing.
2. Slide and bolt 19" rails into extrusion channels — M6 × 12mm SHCS, Nyloc nut.
3. Attach top/bottom panels — M4 × 8mm countersunk screws into tapped extrusion holes.
4. Press-fit rubber corner guards into extrusion tongue-and-groove edges.
5. Mount rear radiator brackets — 3mm aluminum L-bracket, M4 × 10mm.

### Step 6 — Quality Checks

- [ ] Verify rail span = **482.6mm ± 0.5mm** (critical for equipment fitment)
- [ ] Check rack unit holes align to **44.45mm pitch** (1U = 1.75")
- [ ] Confirm all panel edges are deburred and anodized
- [ ] Pressure-test coolant pass-through grommets with soapy water
- [ ] Test-fit one 1U blanking panel and one test 1U device

---

## 3. Finish & Identification

- Laser engrave **"Metatopia Studio — Sovereign MDC"** on front top panel, 3mm Helvetica Neue.
- Engrave serial number on rear panel (format: `SMD-2026-XXXX`).
- Apply 3M 5400-series anti-slip rubber feet (×4) to bottom panel.

---

## 4. Tooling Checklist

| Tool | Spec |
|:--|:--|
| CNC Laser Cutter | 2kW+ fiber, 1500×3000mm bed, ±0.1mm |
| CNC Press Brake | 40T minimum, 2m bed |
| Rivet Gun | Pneumatic blind rivet, 4.8mm capacity |
| M6 Tap Set | HSS spiral flute, M6×1.0mm |
| Deburring Tool | Rotary, suitable for 2mm aluminum |
