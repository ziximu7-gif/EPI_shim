//参数根据实际情况调整
R_in = 50;          // 圆柱筒的外径 
Thickness = 8;      // 支架的总厚度
Height = 60;        // 支架的高度 (沿圆柱轴向)
Width = 60;

$fn = 100;          // 让圆弧变平滑 (采样率)

// 1. 创建一个圆环 
module ring_base() {
    //这个是定义一个模块，ring_base是它的名字
    difference() {
    // 语法是第一个形状减去；第二个形状；第三个形状……
        cylinder(h = Height, r = R_in + Thickness, center = true);
    // 减去内圆柱，形成空心管
        cylinder(h = Height + 1, r = R_in, center = true);
    }
}

module tile_base(){
    intersection() {
    //语法是取A；B交集
        ring_base();
        translate([R_in, 0, 0]) 
    //translate是移动位置向量
            cube([Thickness * 2, Width, Height + 1], center = true);
    }
}
tile_base();


//!my_tile(); // 预览窗口将只显示这个模块，忽略代码中其他所有东西

