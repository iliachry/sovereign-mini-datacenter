# 19-Inch 9U Ruggedized Enclosure — Manufacturing Guide

> Reference model: `cad/rack_enclosure.scad`  
> Standard: EIA-310-D 19" Rack (IEC 60297)

### Key dimensions (parameters of `rack_enclosure.scad`)

| Dimension | Value | Source |
|:--|:--|:--|
| Equipment front-panel width | 482.6mm (19.000") | EIA-310-D |
| Mounting-hole spacing, centre-to-centre (horizontal) | 465.1mm (18.312") | EIA-310-D |
| Clear opening between rails | 450.85mm (17.750") minimum | EIA-310-D |
| Rack unit | 44.45mm (1.750") | EIA-310-D |
| Hole centres above each U boundary | 6.35 / 22.225 / 38.1mm (spacings 15.875, 15.875, 12.7mm) | EIA-310-D |
| Usable interior height | 400.05mm (9U) | model |
| External size (W × D × H) | 540 × 550 × 404.05mm | model |
| Sheet thickness | 2.0mm | model |

The model is a simplified envelope (shell with open front, four rails, rear radiator openings, side vent slots). Bend flanges, corner extrusions and fasteners are not modelled.

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

The top-level object of `rack_enclosure.scad` is 3D, and OpenSCAD can only write DXF from a 2D object. Select a flat 2D profile with the `part` parameter for each DXF, and export the default 3D assembly as STL:

```bash
# 2D profiles for CNC / laser cutting
openscad -D 'part="rear_panel"' -o rear_panel.dxf cad/rack_enclosure.scad
openscad -D 'part="side_panel"' -o side_panel.dxf cad/rack_enclosure.scad
openscad -D 'part="rail"'       -o rail.dxf       cad/rack_enclosure.scad

# 3D assembly for visualization and tolerance checking
openscad -o rack_enclosure.stl cad/rack_enclosure.scad
```

The DXF profiles contain the panel outline and its cutouts only. Add the bend flanges of Step 3 in your CAM / sheet-metal tool before cutting. The top and bottom panels are plain 540 × 550mm rectangles and have no dedicated profile.

### Step 2 — Laser Cutting / CNC Punching

- Cut 2.0mm aluminum side (×2), top (×1), bottom (×1) panels from DXF.
- Cut the rear panel (×1) with its **radiator openings:** 2× 360mm × 120mm (W × H), stacked with a 40mm web and centred on the panel (`rad_opening_*` parameters, `rear_openings_2d()` module). Each opening exposes the 3 × 120mm fan area of one 360mm radiator, whose body is roughly 120mm × 400mm overall. Verify the opening and the fan-screw pattern against the datasheet of the radiator you actually fit before cutting.
- Cut **side vent slots** on both side panels per the `vent_slots_2d()` module (8 × 9 grid of 8mm × 30mm slots).
- Tolerance: ±0.2mm for panel dimensions; ±0.1mm for rail hole pitch.

### Step 3 — CNC Bending

- Bend side panels on CNC press brake:
  - 90° folds, 2.0mm inner bend radius (matches material thickness).
  - Flanges: 20mm top/bottom, 15mm front/rear for frame attachment.
- Deburr all cut edges with a deburring tool before assembly.

### Step 4 — Rail Preparation

- Drill the EIA-310-D universal hole pattern along the full rail length: three holes per rack unit, centred 6.35mm, 22.225mm and 38.1mm above each U boundary (spacings 15.875mm, 15.875mm, 12.7mm — the pitch is **not** uniform). 9U = 27 holes per rail (`part="rail"` DXF).
- Hole diameter: **5.0mm tap drill**, then tap M6 × 1.0. In 2.0mm steel a tapped thread engages only about two turns; if that is not enough for your load, use rivet nuts or switch to 9.5mm square holes with M6 cage nuts.
- Mount the left and right rails so that the **mounting holes are 465.1mm centre-to-centre** and the clear opening between the rails is at least **450mm** (EIA-310-D: 450.85mm). The 482.6mm (19.0") figure is the width of the equipment front panel, not the rail spacing.

### Step 5 — Assembly & Fastening

1. Assemble corner extrusions first — use 4.8mm × 10mm **stainless steel blind rivets** at 80mm spacing.
2. Slide and bolt 19" rails into extrusion channels — M6 × 12mm SHCS, Nyloc nut.
3. Attach top/bottom panels — M4 × 8mm countersunk screws into tapped extrusion holes.
4. Press-fit rubber corner guards into extrusion tongue-and-groove edges.
5. Mount rear radiator brackets — 3mm aluminum L-bracket, M4 × 10mm.

### Step 6 — Quality Checks

- [ ] Verify mounting-hole spacing = **465.1mm ± 0.5mm** centre-to-centre, left rail to right rail (critical for equipment fitment)
- [ ] Verify the clear opening between rails is **≥ 450mm** and that a 482.6mm (19") panel seats flat on both rails
- [ ] Check the hole pattern repeats every **44.45mm** (1U = 1.75") with holes at 6.35 / 22.225 / 38.1mm inside each U
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
