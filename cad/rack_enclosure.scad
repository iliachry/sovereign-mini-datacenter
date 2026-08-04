// ====================================================================
// 19-INCH 9U RUGGEDIZED RACK ENCLOSURE & CHASSIS
// Metatopia Studio (c) 2026
// ====================================================================

$fn = 40;

// Dimensions in mm (Standard 19" Rack Specs)
rack_inner_width = 482.6; // 19 inches
rack_outer_width = 540;
unit_height = 44.45;      // 1U = 1.75 inches
rack_u_count = 9;         // 9U Total Height
rack_height = unit_height * rack_u_count; // 400.05 mm
rack_depth = 550;

module rack_rails() {
    color([0.2, 0.2, 0.2]) {
        // Left Front Rail
        translate([20, 0, 0]) cube([15, 15, rack_height]);
        // Right Front Rail
        translate([rack_outer_width - 35, 0, 0]) cube([15, 15, rack_height]);
        // Left Rear Rail
        translate([20, rack_depth - 15, 0]) cube([15, 15, rack_height]);
        // Right Rear Rail
        translate([rack_outer_width - 35, rack_depth - 15, 0]) cube([15, 15, rack_height]);
    }
}

module aluminum_frame() {
    color([0.7, 0.7, 0.75]) {
        difference() {
            cube([rack_outer_width, rack_depth, rack_height]);
            translate([3, 3, 3])
                cube([rack_outer_width - 6, rack_depth - 6, rack_height - 6]);
        }
    }
}

// Render Rack Outer Frame and Mounting Rails
aluminum_frame();
rack_rails();
