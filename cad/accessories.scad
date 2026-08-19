// ====================================================================
// Sovereign Mini Datacenter — 3D Printable Accessories & Mounts
// Parametric OpenSCAD models for Fan Shrouds, DIN Clips, OLED Bezel & Jetson Mounts
// ====================================================================

$fn = 60;

// Set to: "all", "fan_shroud", "din_clip", "cable_comb", "jetson_mount", "oled_bezel", "radiator_shroud_240"
part = "all";

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
    difference() {
        // Base plate
        union() {
            cube([65, 45, 6], center = true);
            // Snap tabs for 35mm Top-Hat DIN Rail
            translate([0, 17.5, -4]) cube([50, 4, 6], center = true);
            translate([0, -17.5, -4]) cube([50, 4, 6], center = true);
        }
        // ESP32 NodeMCU mounting holes (M2.5)
        translate([24, 14, 0]) cylinder(r = 1.4, h = 10, center = true);
        translate([-24, 14, 0]) cylinder(r = 1.4, h = 10, center = true);
        translate([24, -14, 0]) cylinder(r = 1.4, h = 10, center = true);
        translate([-24, -14, 0]) cylinder(r = 1.4, h = 10, center = true);
    }
}

module jetson_orin_din_bracket() {
    difference() {
        union() {
            cube([100, 80, 8], center = true);
            // 35mm DIN rail clip engagement
            translate([0, 17.5, -5]) cube([70, 5, 6], center = true);
            translate([0, -17.5, -5]) cube([70, 5, 6], center = true);
        }
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
}

module front_panel_oled_bracket() {
    difference() {
        // 1U EIA-310 Faceplate insert (44.45mm height x 100mm width)
        cube([100, 44, 4], center = true);
        
        // 0.96" I2C OLED display cutout (26.7mm x 19.3mm)
        translate([-15, 0, 0]) cube([27, 20, 8], center = true);
        
        // 4x OLED PCB mounting holes (M2)
        translate([-15 + 12, 10, 0]) cylinder(r = 1.1, h = 10, center = true);
        translate([-15 - 12, 10, 0]) cylinder(r = 1.1, h = 10, center = true);
        translate([-15 + 12, -10, 0]) cylinder(r = 1.1, h = 10, center = true);
        translate([-15 - 12, -10, 0]) cylinder(r = 1.1, h = 10, center = true);
        
        // 3x 5mm Status LEDs (Power, Solar, Space DTN Link)
        for (j = [-10, 0, 10]) {
            translate([25, j, 0]) cylinder(r = 2.6, h = 10, center = true);
        }
        
        // 19" Rack M6 mounting ear holes
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
