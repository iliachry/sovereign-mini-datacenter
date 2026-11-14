// ====================================================================
// 19-INCH 9U RUGGEDIZED RACK ENCLOSURE & CHASSIS
// Metatopia Studio (c) 2026
// Units: millimeters
// Standard: EIA-310-D 19" Rack (IEC 60297)
//
// Simplified parametric envelope model: sheet-metal shell (open front),
// four mounting rails with the EIA-310 hole pattern, rear radiator
// openings and side vent slots. Bend flanges, corner extrusions and
// fasteners are not modelled.
//
// Select what to export with -D 'part="<name>"':
//   "assembly"    3D enclosure (default; STL export)
//   "rear_panel"  2D outline + radiator openings (DXF export)
//   "side_panel"  2D outline + vent slots        (DXF export)
//   "rail"        2D rail face + EIA-310 holes   (DXF export)
// ====================================================================

$fn = 60;

part = "assembly";

// ── EIA-310-D reference dimensions (do not change) ────────────────
eia_panel_width  = 482.6;      // 19.000 in: equipment front-panel width
eia_hole_spacing = 465.1;      // 18.312 in: mounting-hole centre-to-centre (horizontal)
eia_rail_opening = 450.85;     // 17.750 in: minimum clear opening between the rails
unit_height      = 44.45;      // 1U = 1.750 in
eia_hole_offsets = [6.35, 22.225, 38.1];  // hole centres above each U boundary

// ── Enclosure dimensions ──────────────────────────────────────────
rack_u_count      = 9;                             // usable rack units
rack_inner_height = unit_height * rack_u_count;    // 400.05 mm clear height (true 9U)
panel_thickness   = 2.0;                           // 2 mm 6061-T6 aluminium sheet
rack_outer_width  = 540;                           // external width  (X)
rack_depth        = 550;                           // external depth  (Y, front = 0)
rack_outer_height = rack_inner_height + 2 * panel_thickness;  // external height (Z)

// ── Mounting rails ────────────────────────────────────────────────
rail_width    = (eia_panel_width - eia_rail_opening) / 2;  // 15.875 mm rail face
rail_depth    = 15;      // rail size front-to-back
rail_setback  = 10;      // front rails sit this far behind the open front edge
rail_hole_d   = 5.0;     // M6 x 1.0 tap drill
rail_hole_inset = (eia_panel_width - eia_hole_spacing) / 2;  // 8.75 mm from the rail's outer edge

rail_x_left   = (rack_outer_width - eia_panel_width) / 2;
rail_x_right  = (rack_outer_width + eia_rail_opening) / 2;
hole_x_left   = (rack_outer_width - eia_hole_spacing) / 2;
hole_x_right  = (rack_outer_width + eia_hole_spacing) / 2;
rail_y_front  = rail_setback;
rail_y_rear   = rack_depth - panel_thickness / 2 - rail_depth;
rail_z_bottom = panel_thickness / 2;                 // rails are let into the floor and roof
rail_length   = rack_outer_height - panel_thickness;

// ── Rear radiator openings (one per 360 mm radiator) ──────────────
// A 360 mm radiator is roughly 120 mm x 400 mm overall; the opening exposes
// its 3 x 120 mm fan area. Check the datasheet of the radiator actually used.
rad_opening_width  = 360;
rad_opening_height = 120;
rad_opening_count  = 2;
rad_opening_gap    = 40;     // vertical web between the two openings

rad_stack_height = rad_opening_count * rad_opening_height + (rad_opening_count - 1) * rad_opening_gap;
rad_x0           = (rack_outer_width - rad_opening_width) / 2;
rad_z0           = (rack_outer_height - rad_stack_height) / 2;

// ── Side vent slots (both side panels) ────────────────────────────
vent_slot_w  = 8;        // slot size front-to-back
vent_slot_h  = 30;       // slot height
vent_gap     = 6;        // web between slots
vent_cols    = 8;
vent_y0      = 60;       // first slot, measured from the front edge
vent_margin  = 30;       // solid margin kept at the bottom and top of the panel
vent_rows    = floor((rack_outer_height - 2 * vent_margin + vent_gap) / (vent_slot_h + vent_gap));

cut_overshoot = 1;       // cutters extend this far beyond the sheet on both sides

// ── Consistency checks ────────────────────────────────────────────
assert(rack_outer_width - 2 * panel_thickness >= eia_panel_width, "enclosure too narrow for 19-inch equipment");
assert(rad_x0 > rail_x_left + rail_width, "radiator openings collide with the rear rails");
assert(rad_z0 > panel_thickness, "radiator openings do not fit the rear panel height");
assert(vent_rows >= 1, "side panel too short for vent slots");
assert(vent_y0 + vent_cols * (vent_slot_w + vent_gap) < rack_depth - panel_thickness, "vent slots run past the rear of the side panel");

// ── Colors ────────────────────────────────────────────────────────
col_frame = [0.68, 0.68, 0.72, 1.0];   // Anodized aluminum
col_rail  = [0.20, 0.20, 0.20, 1.0];   // Black steel rails

// ── 2D profiles (single source for the 3D cutters and the DXF parts) ──

// Radiator openings, drawn in the rear panel plane (X, Z).
module rear_openings_2d() {
    for (i = [0 : rad_opening_count - 1]) {
        translate([rad_x0, rad_z0 + i * (rad_opening_height + rad_opening_gap)])
            square([rad_opening_width, rad_opening_height]);
    }
}

// Vent slot grid, drawn in the side panel plane (Y, Z).
module vent_slots_2d() {
    for (c = [0 : vent_cols - 1]) {
        for (r = [0 : vent_rows - 1]) {
            translate([vent_y0 + c * (vent_slot_w + vent_gap),
                       vent_margin + r * (vent_slot_h + vent_gap)])
                square([vent_slot_w, vent_slot_h]);
        }
    }
}

// EIA-310 hole column: three holes per U at 6.35 / 22.225 / 38.1 mm above the
// U boundary (spacings 15.875, 15.875, 12.7 mm). Origin = bottom of U1.
module eia_hole_column_2d() {
    for (u = [0 : rack_u_count - 1]) {
        for (dz = eia_hole_offsets) {
            translate([0, u * unit_height + dz])
                circle(d = rail_hole_d);
        }
    }
}

// Left and right hole columns in the enclosure's (X, Z) plane.
module rail_holes_2d() {
    for (x = [hole_x_left, hole_x_right]) {
        translate([x, panel_thickness])
            eia_hole_column_2d();
    }
}

// ── 2D parts for DXF export ───────────────────────────────────────
module rear_panel_2d() {
    difference() {
        square([rack_outer_width, rack_outer_height]);
        rear_openings_2d();
    }
}

module side_panel_2d() {
    difference() {
        square([rack_depth, rack_outer_height]);
        vent_slots_2d();
    }
}

module rail_2d() {
    difference() {
        square([rail_width, rack_inner_height]);
        translate([rail_hole_inset, 0])
            eia_hole_column_2d();
    }
}

// ── Module: Sheet-metal shell (open front) ────────────────────────
module aluminum_frame() {
    color(col_frame)
    difference() {
        cube([rack_outer_width, rack_depth, rack_outer_height]);

        // Interior cavity, open towards the front (Y = 0)
        translate([panel_thickness, -cut_overshoot, panel_thickness])
            cube([
                rack_outer_width - panel_thickness * 2,
                rack_depth - panel_thickness + cut_overshoot,
                rack_inner_height
            ]);

        // Rear radiator openings, cut through the rear panel
        translate([0, rack_depth + cut_overshoot, 0])
            rotate([90, 0, 0])
                linear_extrude(height = panel_thickness + 2 * cut_overshoot)
                    rear_openings_2d();

        // Vent slots, cut through the left and right side panels
        for (x = [-cut_overshoot, rack_outer_width - panel_thickness - cut_overshoot]) {
            translate([x, 0, 0])
                rotate([90, 0, 90])
                    linear_extrude(height = panel_thickness + 2 * cut_overshoot)
                        vent_slots_2d();
        }
    }
}

// ── Module: Mounting Rails (4x, M6 tapped, EIA-310 hole pattern) ──
module rack_rails() {
    color(col_rail)
    difference() {
        union() {
            for (x = [rail_x_left, rail_x_right]) {
                for (y = [rail_y_front, rail_y_rear]) {
                    translate([x, y, rail_z_bottom])
                        cube([rail_width, rail_depth, rail_length]);
                }
            }
        }

        // Holes run front-to-back through the front and the rear rail pair
        for (y = [rail_y_front, rail_y_rear]) {
            translate([0, y + rail_depth + cut_overshoot, 0])
                rotate([90, 0, 0])
                    linear_extrude(height = rail_depth + 2 * cut_overshoot)
                        rail_holes_2d();
        }
    }
}

// ── Output ────────────────────────────────────────────────────────
if (part == "rear_panel") {
    rear_panel_2d();
} else if (part == "side_panel") {
    side_panel_2d();
} else if (part == "rail") {
    rail_2d();
} else {
    aluminum_frame();
    rack_rails();
}
