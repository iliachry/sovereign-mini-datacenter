// ====================================================================
// 19-INCH 9U RUGGEDIZED RACK ENCLOSURE & CHASSIS
// Metatopia Studio (c) 2026
// Units: millimeters
// Standard: EIA-310-D 19" Rack (IEC 60297)
// ====================================================================

$fn = 60;

// ── Rack Dimensions (Standard 19" EIA-310-D) ─────────────────────
rack_outer_width  = 540;       // External enclosure width
rack_inner_width  = 482.6;     // 19.0 inches — standard rail span
unit_height       = 44.45;     // 1U = 1.75 inches
rack_u_count      = 9;         // Total rack units
rack_height       = unit_height * rack_u_count;  // 400.05 mm
rack_depth        = 550;
panel_thickness   = 2.0;       // 2mm 6061-T6 Aluminum sheet
rail_width        = 15;
rail_depth        = 15;
corner_guard_r    = 6;         // Rubber corner guard radius

// ── Colors ────────────────────────────────────────────────────────
col_frame = [0.68, 0.68, 0.72, 1.0];   // Anodized aluminum
col_rail  = [0.20, 0.20, 0.20, 1.0];   // Black steel rails
col_panel = [0.60, 0.60, 0.65, 0.85];  // Semi-transparent side panels

// ── Module: Outer Aluminum Frame ──────────────────────────────────
module aluminum_frame() {
    color(col_frame)
    difference() {
        cube([rack_outer_width, rack_depth, rack_height]);
        translate([panel_thickness, panel_thickness, panel_thickness])
            cube([
                rack_outer_width - panel_thickness * 2,
                rack_depth       - panel_thickness * 2,
                rack_height      - panel_thickness * 2
            ]);
    }
}

// ── Module: Mounting Rails (4x, M6 tapped) ────────────────────────
module rack_rails() {
    rail_x_left  = (rack_outer_width - rack_inner_width) / 2;
    rail_x_right = rail_x_left + rack_inner_width - rail_width;

    color(col_rail) {
        // Front-left
        translate([rail_x_left, 0, 0])
            cube([rail_width, rail_depth, rack_height]);
        // Front-right
        translate([rail_x_right, 0, 0])
            cube([rail_width, rail_depth, rack_height]);
        // Rear-left
        translate([rail_x_left, rack_depth - rail_depth, 0])
            cube([rail_width, rail_depth, rack_height]);
        // Rear-right
        translate([rail_x_right, rack_depth - rail_depth, 0])
            cube([rail_width, rail_depth, rack_height]);
    }
}

// ── Module: M6 Cage Nut Hole Pattern on one rail ───────────────────
// Spacing per EIA-310-D: 15.875mm (5/8") repeating pitch
module rail_holes(holes_per_u = 3) {
    hole_pitch = unit_height / holes_per_u;
    for (u = [0 : rack_u_count - 1]) {
        for (h = [0 : holes_per_u - 1]) {
            z_pos = u * unit_height + h * hole_pitch + hole_pitch / 2;
            translate([0, rail_depth / 2, z_pos])
                rotate([90, 0, 0])
                    cylinder(d = 7.5, h = rail_depth + 1, center = true);
        }
    }
}

// ── Module: Rear Fan / Radiator Cutouts (2x 360mm) ────────────────
module rear_cutouts() {
    fan_size   = 360;
    fan_offset = 20;
    color([0.3, 0.3, 0.3])
    union() {
        // Bottom 360mm radiator cutout
        translate([rack_outer_width / 2 - fan_size / 2, rack_depth - panel_thickness - 1, fan_offset])
            cube([fan_size, panel_thickness + 2, fan_size]);
        // Top 360mm radiator cutout
        translate([rack_outer_width / 2 - fan_size / 2, rack_depth - panel_thickness - 1,
                   fan_offset + fan_size + 20])
            cube([fan_size, panel_thickness + 2, fan_size]);
    }
}

// ── Module: Side Ventilation Mesh Pattern ────────────────────────
module vent_mesh(side = "left") {
    vent_w  = 8;
    vent_h  = 30;
    gap     = 6;
    cols    = 8;
    rows    = floor(rack_height / (vent_h + gap));
    x_sign  = (side == "left") ? 0 : rack_outer_width - panel_thickness;

    color(col_panel)
    for (c = [0 : cols - 1]) {
        for (r = [0 : rows - 1]) {
            x = x_sign;
            y = 60 + c * (vent_w + gap);
            z = 30 + r * (vent_h + gap);
            translate([x - 1, y, z])
                cube([panel_thickness + 2, vent_w, vent_h]);
        }
    }
}

// ── Main Assembly ─────────────────────────────────────────────────
aluminum_frame();
rack_rails();
rear_cutouts();

// U-marker lines (visual reference)
for (u = [0 : rack_u_count]) {
    translate([0, 0, u * unit_height])
    color([0.4, 0.4, 0.4, 0.4])
        cube([rack_outer_width, 2, 0.5]);
}
