// --- 参数设置 ---
$fn = 50;            // 建议预览时设为 50，导出时再设为 100
radius = 130;         // 内径
thickness = 2;        // 薄板厚度
height = 330;         // 总高度
groove_h = 5;        // 凹槽宽度
groove_d = 0.8;       // 凹槽深度
gap = 2;              // 两片之间的安装间隙

// --- 固定线圈开孔参数 ---
hole_w = 5.5;         // 孔的宽度
hole_h = 2;           // 孔的高度
n_holes_col = 20;     // 一列孔数目
n_holes_raw = 40;     // 圆周总孔数目
margin = 30;          // 上下留白

// 计算参数
step_col = (n_holes_col > 1) ? (height - 2 * margin) / (n_holes_col - 1) : 0;
angle_step = 360 / n_holes_raw;

// --- 辅助模块：环形凹槽 ---
module groove_ring() {
    difference() {
        cylinder(h = groove_h, r = radius + thickness + 0.5);
        cylinder(h = groove_h, r = radius + thickness - groove_d);
    }
}

// --- 核心模块：半圆薄板主体 ---
module half_cylinder_shell() {
    difference() {
        // 1. 主体管壁
        cylinder(h = height, r = radius + thickness);
        translate([0, 0, -1])
            cylinder(h = height + 2, r = radius);
        
        // 2. 切掉一半 (保留 Y < 0 的部分)
        translate([-(radius + thickness + 1), 0, -1])
            cube([(radius + thickness + 1) * 2, radius + thickness + 1, height + 2]);
        
        // 3. 上下凹槽
        translate([0, 0, height * 0.1]) groove_ring();
        translate([0, 0, height * 0.9 - groove_h]) groove_ring();
    }
}

// --- 打孔模块 ---
module holes_coil_stick(){
    // 遍历圆周（只在半圆 180 度范围内旋转）
    for (i_raw = [0 : n_holes_raw / 2]) {
        rotate([0, 0, i_raw * angle_step + 180]) {
            for (i_col = [0 : n_holes_col - 1]) {
                z_pos = margin + i_col * step_col;
                // 确保 cube 足够长，能完全切透壁厚
                translate([radius - 1, -hole_w/2, z_pos - hole_h/2])
                    cube([thickness + 2, hole_w, hole_h]);
            }
        }
    }
}

// --- 最终组合模块 ---
module coil_stick_final(){
    difference (){
        half_cylinder_shell();
        // 使用 render 提高渲染效率，防止计算溢出
        render(convexity = 10) holes_coil_stick();
    }
}

// --- 渲染部分 ---
// 第一片
color("Cyan")
    translate([0, -gap/2, 0]) 
    coil_stick_final();

// 第二片
color("Orange")
    translate([0, gap/2, 0]) 
    rotate([0, 0, 180])
    coil_stick_final();