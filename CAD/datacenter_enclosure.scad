// ====================================================================
// SOVEREIGN MINI DATACENTER - PARAMETRIC ENCLOSURE CAD MODEL
// Metatopia Studio (c) 2026
// ====================================================================

$fn = 60; // Smooth curves

// --- GLOBAL PARAMETERS (in mm) ---
enclosure_width = 540;   // 19" Rack Standard outer width
enclosure_height = 650;  // ~12U Height
enclosure_depth = 600;   // Server depth
wall_thickness = 4;

// DGX Spark Module Dimensions
spark_w = 440;
spark_h = 88;            // 2U Height per module
spark_d = 480;

// Battery Bank Dimensions (LiFePO4 5.12kWh)
bat_w = 440;
bat_h = 133;             // 3U Height
bat_d = 500;

// Liquid Radiator Assembly
rad_w = 480;
rad_h = 120;
rad_d = 60;

// --- COLOR PALETTE ---
color_chassis = [0.1, 0.1, 0.12, 0.95];
color_glass = [0.8, 0.9, 1.0, 0.25];
color_spark = [0.2, 0.2, 0.22, 1.0];
color_accent = [0.0, 1.0, 0.4, 1.0]; // Lime accent
color_copper = [0.85, 0.53, 0.1, 1.0];
color_battery = [0.15, 0.15, 0.18, 1.0];

// --- MAIN ASSEMBLY ---
module main_assembly() {
    chassis();
    
    // Internal Components
    translate([(enclosure_width-spark_w)/2, 50, 400])
        dgx_spark_node();
        
    translate([(enclosure_width-spark_w)/2, 50, 300])
        dgx_spark_node();
        
    translate([(enclosure_width-bat_w)/2, 40, 120])
        battery_pack();
        
    translate([(enclosure_width-rad_w)/2, enclosure_depth-70, 450])
        liquid_radiator();
        
    // Coolant Tubing
    coolant_tubes();
}

// --- CHASSIS ENCLOSURE ---
module chassis() {
    color(color_chassis) {
        difference() {
            // Main Outer Box
            cube([enclosure_width, enclosure_depth, enclosure_height]);
            
            // Hollow Inside
            translate([wall_thickness, wall_thickness, wall_thickness])
                cube([enclosure_width - 2*wall_thickness, enclosure_depth - 2*wall_thickness, enclosure_height - 2*wall_thickness]);
                
            // Front Door Cutout
            translate([wall_thickness*2, -1, wall_thickness*2])
                cube([enclosure_width - 4*wall_thickness, wall_thickness*3, enclosure_height - 4*wall_thickness]);
        }
    }
    
    // Front Glass Door
    color(color_glass) {
        translate([wall_thickness*2, 0, wall_thickness*2])
            cube([enclosure_width - 4*wall_thickness, 3, enclosure_height - 4*wall_thickness]);
    }
}

// --- DGX SPARK NODE ---
module dgx_spark_node() {
    color(color_spark) {
        cube([spark_w, spark_d, spark_h]);
    }
    // Status LED Strip
    color(color_accent) {
        translate([10, -2, spark_h - 15])
            cube([spark_w - 20, 3, 4]);
    }
    // Water Blocks (Copper)
    color(color_copper) {
        translate([120, 150, spark_h])
            cylinder(h=15, r=25);
        translate([280, 150, spark_h])
            cylinder(h=15, r=25);
    }
}

// --- LIFEPO4 BATTERY PACK ---
module battery_pack() {
    color(color_battery) {
        cube([bat_w, bat_d, bat_h]);
    }
    // Terminals
    color([0.9, 0.1, 0.1, 1.0]) translate([40, -2, bat_h/2]) cylinder(h=10, r=8);
    color([0.1, 0.1, 0.9, 1.0]) translate([bat_w - 40, -2, bat_h/2]) cylinder(h=10, r=8);
}

// --- LIQUID RADIATOR ASSEMBLY ---
module liquid_radiator() {
    color([0.1, 0.1, 0.1, 1.0]) {
        cube([rad_w, rad_d, rad_h]);
    }
    // Fan Grills
    color([0.3, 0.3, 0.3, 1.0]) {
        translate([80, -2, rad_h/2]) rotate([-90, 0, 0]) cylinder(h=5, r=45);
        translate([240, -2, rad_h/2]) rotate([-90, 0, 0]) cylinder(h=5, r=45);
        translate([400, -2, rad_h/2]) rotate([-90, 0, 0]) cylinder(h=5, r=45);
    }
}

// --- COOLANT TUBING ---
module coolant_tubes() {
    color([0.0, 0.8, 1.0, 0.7]) {
        // Tube 1: Node 1 to Radiator
        translate([145, 200, 490])
            rotate([0, 90, 0])
                cylinder(h=200, r=6);
    }
}

// Render Main Assembly
main_assembly();
