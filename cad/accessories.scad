// ====================================================================
// Sovereign Mini Datacenter — 3D Printable Accessories & Mounts
// Parametric OpenSCAD models for Fan Shrouds, DIN Clips & Cable Combs
// ====================================================================

$fn = 60;

// Set to: "fan_shroud", "din_clip", or "cable_comb"
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
    translate([-70, 0, 0]) fan_shroud_120mm();
    translate([70, -40, 0]) esp32_din_rail_mount();
    translate([70, 40, 0]) cable_management_comb();
} else if (part == "fan_shroud") {
    fan_shroud_120mm();
} else if (part == "din_clip") {
    esp32_din_rail_mount();
} else if (part == "cable_comb") {
    cable_management_comb();
}
