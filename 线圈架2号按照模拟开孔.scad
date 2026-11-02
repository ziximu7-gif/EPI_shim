// ---------------- 参数设置 ----------------
$fn = 64;              // 预览可 50，导出可 100
radius = 130;          // 内半径
thickness = 2;         // 薄板厚度
height = 330;          // 总高度
groove_h = 5;          // 凹槽宽度
groove_d = 0.8;        // 凹槽深度
gap = 2;               // 两片之间显示间隙

// ---------------- 固定线圈参数 ----------------
// 这里的 coil_radius 对于“方形线圈”表示：半边长
coil_radius = [25,30,24,50,50,21,49,50];
coil_position_z = [225,241,205,116,95,192,149,220];
coil_position_theta = [1,186,315,188,4,40,2,190];   // 度，0-360度之间

// ---------------- 开孔参数 ----------------
hole_w = 4;          // 沿圆周方向的孔宽
hole_h = 2;            // 轴向孔高
hole_pitch = 15;        // 沿线圈路径每隔 20mm 一个孔
hole_cut_extra = 2;//切孔方块额外厚度
hole_pair_spacing_theta = 6;
hole_pair_spacing_z = 6;   // 两个孔中心之间的间距，单位 mm（沿圆柱表面圆周方向）
corner_skip = 5;   // 距离每个转角前后各跳过多少 mm 的开孔

// ---------------- 显示控制 ----------------
show_piece_A = true;   // A片：X < 0 半圆
show_piece_B = true;   // B片：X > 0 半圆

// 判断角度是否落在 A 片 (X<0) 或 B 片 (X>0)
// 这里采用标准极角：
// 0°   -> +X
// 90°  -> +Y
// 180° -> -X
// 270° -> -Y
//
// A片 = X<0 = 90°~270°
// B片 = X>0 = 0°~90°+270~360
function ang_in_piece(theta_deg, piece) =
    piece == "A"
        ? (theta_deg >= 90 && theta_deg <= 270)
        : (theta_deg <= 90 || theta_deg >= 270);

// 方形线圈周长 = 8 * r
function square_perimeter(r) = 8 * r;

// 根据间距决定采样数
function square_sample_count(r, pitch) = max(4, ceil(square_perimeter(r) / pitch));

// 方形线圈在“展开平面”上的点
// 返回 [x_local, z_local]
// r 是半边长
// u 范围 [0,4)，分别对应四条边
function square_local_point(r, u) =
    (u < 1) ? [ -r + 2*r*u, -r ] :
    (u < 2) ? [  r, -r + 2*r*(u-1) ] :
    (u < 3) ? [  r - 2*r*(u-2),  r ] :
              [ -r,  r - 2*r*(u-3) ];

// 展开平面映射回圆柱表面：
// x_local / radius -> 周向角增量（弧度），再转成度
function wrap_theta(theta0_deg, x_local, r_cyl) =
    theta0_deg + x_local * 180 / PI / r_cyl;

// 圆柱表面点的笛卡尔坐标
function cyl_point(theta_deg, z_pos, r_cyl) =
    [ r_cyl * cos(theta_deg),
      r_cyl * sin(theta_deg),
      z_pos ];
// 判断当前采样点是否靠近方形线圈的转角
function near_square_corner(u, r, skip_mm) =
    let(du = skip_mm / (2 * r))
    (u < du) ||
    (abs(u - 1) < du) ||
    (abs(u - 2) < du) ||
    (abs(u - 3) < du) ||
    (4 - u < du);
// 环形凹槽
module groove_ring() {
    difference() {
        cylinder(h = groove_h, r = radius + thickness + 0.5);
        cylinder(h = groove_h, r = radius + thickness - groove_d);
    }
}

// A片：保留 X < 0 半圆
module half_cylinder_shell_A() {
    difference() {
        cylinder(h = height, r = radius + thickness);
        translate([0, 0, -1])
            cylinder(h = height + 2, r = radius);

        // 切掉 X > 0 的部分
        translate([0, -(radius + thickness + 1), -1])
    cube([radius + thickness + 1,
          (radius + thickness + 1) * 2,
          height + 2]);

        // 上下凹槽
        translate([0, 0, height * 0.1]) groove_ring();
        translate([0, 0, height * 0.9 - groove_h]) groove_ring();
    }
}

// B片：保留 X > 0 半圆
module half_cylinder_shell_B() {
    difference() {
        cylinder(h = height, r = radius + thickness);
        translate([0, 0, -1])
            cylinder(h = height + 2, r = radius);

        // 切掉 X < 0 的部分
        translate([-(radius + thickness + 1), -(radius + thickness + 1), -1])
    cube([radius + thickness + 1,
          (radius + thickness + 1) * 2,
          height + 2]);

        // 上下凹槽
        translate([0, 0, height * 0.1]) groove_ring();
        translate([0, 0, height * 0.9 - groove_h]) groove_ring();
    }
}
// 在圆柱指定角度/高度打一个矩形孔
// 孔沿径向切穿，宽度沿周向，孔高沿Z方向
module radial_rect_hole(theta_deg, z_pos) {
    rotate([0, 0, theta_deg])
        translate([radius - 1, -hole_w/2, z_pos - hole_h/2])
            cube([thickness + hole_cut_extra, hole_w, hole_h]);
}
// 沿 z 方向上下对称开双孔
module radial_rect_hole_pair_z(theta_deg, z_pos) {
    radial_rect_hole(theta_deg, z_pos - hole_pair_spacing_z/2);
    radial_rect_hole(theta_deg, z_pos + hole_pair_spacing_z/2);
}
// theta 方向专用孔：交换径向和周向尺寸，z 不变
module radial_rect_hole_theta(theta_deg, z_pos) {
    rotate([0, 0, theta_deg])
        translate([radius - 1, -hole_h/2, z_pos - hole_w/2])
            cube([thickness + hole_cut_extra, hole_h, hole_w]);
}
// 沿 theta 方向左右对称开双孔（使用调换长宽后的孔）
module radial_rect_hole_pair_theta(theta_deg, z_pos) {
    theta_offset = (hole_pair_spacing_theta / 2) * 180 / PI / radius;
    radial_rect_hole_theta(theta_deg - theta_offset, z_pos);
    radial_rect_hole_theta(theta_deg + theta_offset, z_pos);
}
// 为单个方形线圈生成离散孔
// piece = "A" 或 "B"
module square_coil_holes_one(z0, theta0_deg, r_coil, piece) {
    n = square_sample_count(r_coil, hole_pitch);

    for (i = [0 : n - 1]) {
        u = 4 * i / n;
        pt = square_local_point(r_coil, u);

        x_local = pt[0];
        z_local = pt[1];

        theta_i = wrap_theta(theta0_deg, x_local, radius);
        z_i = z0 + z_local;

        // 只切当前这片半圆范围内的孔
        if (z_i >= 0 && z_i <= height &&
    ang_in_piece(theta_i, piece) &&
    !near_square_corner(u, r_coil, corner_skip)) {

    // 水平边：u ∈ [0,1) 或 [2,3)
    // 双孔沿 z 方向
    if ((u < 1) || (u >= 2 && u < 3)) {
        radial_rect_hole_pair_z(theta_i, z_i);
    }

    // 竖直边：u ∈ [1,2) 或 [3,4)
    // 双孔沿 theta 方向
    else {
        radial_rect_hole_pair_theta(theta_i, z_i);
    }
}
    }
}

// 当前 piece 的全部线圈开孔
module all_square_coil_holes(piece) {
    for (k = [0 : len(coil_radius) - 1]) {
        square_coil_holes_one(
            coil_position_z[k],
            coil_position_theta[k],
            coil_radius[k],
            piece
        );
    }
}

module piece_A_final() {
    difference() {
        half_cylinder_shell_A();
        render(convexity = 10)
            all_square_coil_holes("A");
    }
}

module piece_B_final() {
    difference() {
        half_cylinder_shell_B();
        render(convexity = 10)
            all_square_coil_holes("B");
    }
}

if (show_piece_A)
    color("Cyan")
        translate([0, -gap/2, 0])
            piece_A_final();

if (show_piece_B)
    color("Orange")
        translate([0, gap/2, 0])
            piece_B_final();