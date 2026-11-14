// ====================================================================
// Sovereign Mini Datacenter — 3D Printable Accessories & Mounts
// Parametric OpenSCAD models for Fan Shrouds, DIN Rail Mounts, OLED Bezel & Jetson Mounts
// ====================================================================

$fn = 60;

// Set to: "all", "fan_shroud", "din_clip", "cable_comb", "jetson_mount", "oled_bezel"
part = "all";

// ── 35 mm top-hat DIN rail (EN 60715 TS35 x 7.5) hook geometry ─────
din_rail_width  = 35;    // rail width across both flanges
din_flange_t    = 1.0;   // flange thickness of the 7.5 mm deep rail
din_clearance   = 0.5;   // sliding clearance (tune for your printer)
din_hook_wall   = 3;     // thickness of the wall beside the rail edge
din_hook_lip    = 1.5;   // how far the lip reaches under each flange
din_hook_lip_t  = 2;     // lip thickness

// Two fixed hooks that wrap both flanges of a 35 mm rail. The mount slides
// onto the rail from its end; it is not a snap-on clip.
//   length = hook length along the rail (X), z_top = underside of the plate
module din_rail_hooks(length, z_top) {
    slot_h   = din_flange_t + din_clearance;
    hook_h   = slot_h + din_hook_lip_t;
    half_gap = (din_rail_width + din_clearance) / 2;
    for (m = [0, 1]) {
        mirror([0, m, 0]) {
            // Wall beside the rail edge (let 0.5 mm into the plate)
            translate([-length / 2, half_gap, z_top - hook_h])
                cube([length, din_hook_wall, hook_h + 0.5]);
            // Retaining lip under the rail flange
            translate([-length / 2, half_gap - din_hook_lip, z_top - hook_h])
                cube([length, din_hook_lip + din_hook_wall, din_hook_lip_t]);
        }
    }
}

module fan_shroud_120mm() {
    difference() {
        // Outer duct body
        cube([120, 120, 25], center = true);
        
        // Circular airflow tunnel
        cylinder(r = 58, h = 30, center = true);
        
        // 105mm corner mounting holes for M4 fan screws
        for (x = [-52.5, 52.5]) {
            for (y = [-52.5, 52.5]) {
                translate([x, y, 0])
                    cylinder(r = 2.2, h = 30, center = true);
            }
        }
    }
}

module esp32_din_rail_mount() {
    union() {
        difference() {
            // Base plate
            cube([65, 45, 6], center = true);
            // ESP32 NodeMCU mounting holes (M2.5)
            translate([24, 14, 0]) cylinder(r = 1.4, h = 10, center = true);
            translate([-24, 14, 0]) cylinder(r = 1.4, h = 10, center = true);
            translate([24, -14, 0]) cylinder(r = 1.4, h = 10, center = true);
            translate([-24, -14, 0]) cylinder(r = 1.4, h = 10, center = true);
        }
        // Slide-on hooks for a 35 mm top-hat DIN rail (plate underside at z = -3)
        din_rail_hooks(50, -3);
    }
}

module jetson_orin_din_bracket() {
    union() {
        difference() {
            cube([100, 80, 8], center = true);
            // Jetson Nano / Orin Nano 58x87mm M3 mounting pattern
            translate([43.5, 29.0, 0]) cylinder(r = 1.7, h = 15, center = true);
            translate([-43.5, 29.0, 0]) cylinder(r = 1.7, h = 15, center = true);
            translate([43.5, -29.0, 0]) cylinder(r = 1.7, h = 15, center = true);
            translate([-43.5, -29.0, 0]) cylinder(r = 1.7, h = 15, center = true);
            // Passive ventilation grid
            for (i = [-30:15:30]) {
                translate([i, 0, 0]) cube([6, 35, 12], center = true);
            }
        }
        // Slide-on hooks for a 35 mm top-hat DIN rail (plate underside at z = -4)
        din_rail_hooks(70, -4);
    }
}

// OLED module mounting-hole pitch. 0.96" SSD1306 breakout boards differ between
// vendors: MEASURE your module and set these two values. The defaults only
// guarantee that the holes clear the 27 x 20 mm display cutout.
oled_hole_pitch_x = 24;
oled_hole_pitch_y = 25;

module front_panel_oled_bracket() {
    difference() {
        // 100 x 44 mm faceplate insert (fits within the height of a 1U / 44.45 mm panel)
        cube([100, 44, 4], center = true);
        
        // 0.96" I2C OLED display cutout (27 x 20 mm)
        translate([-15, 0, 0]) cube([27, 20, 8], center = true);
        
        // 4x OLED PCB mounting holes (M2), outside the display cutout
        for (dx = [-oled_hole_pitch_x / 2, oled_hole_pitch_x / 2]) {
            for (dy = [-oled_hole_pitch_y / 2, oled_hole_pitch_y / 2]) {
                translate([-15 + dx, dy, 0]) cylinder(r = 1.1, h = 10, center = true);
            }
        }
        
        // 3x 5mm Status LEDs (Power, Solar, Space DTN Link)
        for (j = [-10, 0, 10]) {
            translate([25, j, 0]) cylinder(r = 2.6, h = 10, center = true);
        }
        
        // Insert fixing holes (M6 clearance, 88 mm apart). This 100 mm insert
        // does not reach 19" rack rails; it bolts to a carrier panel.
        translate([-44, 0, 0]) cylinder(r = 3.2, h = 10, center = true);
        translate([44, 0, 0]) cylinder(r = 3.2, h = 10, center = true);
    }
}

module cable_management_comb() {
    difference() {
        cube([140, 20, 15], center = true);
        
        // 8x Cable routing slots for 10GbE SFP+ & DC cables
        for (i = [0:7]) {
            translate([-55 + i * 15.5, 0, 2])
                cube([8, 25, 16], center = true);
        }
        // Chassis mounting screw slots (M4)
        translate([-64, 0, 0]) cylinder(r = 2.2, h = 20, center = true);
        translate([64, 0, 0]) cylinder(r = 2.2, h = 20, center = true);
    }
}

if (part == "all") {
    translate([-80, -60, 0]) fan_shroud_120mm();
    translate([60, -60, 0]) esp32_din_rail_mount();
    translate([60, 40, 0]) cable_management_comb();
    translate([-80, 60, 0]) jetson_orin_din_bracket();
    translate([0, 100, 0]) front_panel_oled_bracket();
} else if (part == "fan_shroud") {
    fan_shroud_120mm();
} else if (part == "din_clip") {
    esp32_din_rail_mount();
} else if (part == "cable_comb") {
    cable_management_comb();
} else if (part == "jetson_mount") {
    jetson_orin_din_bracket();
} else if (part == "oled_bezel") {
    front_panel_oled_bracket();
}
