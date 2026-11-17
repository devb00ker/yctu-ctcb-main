# RMUL 2027 3V3 场地底图

源文件：`D:\QQ\download\RMUL2027_3V3_V1.0.0.stp`，由用户提供。SHA256：`7d47c1b6a6a0253a732e46cb122bb84410b11d5134bcb5658104299db4a48ca4`。

通过 Gmsh 4.13.1 内的 OpenCASCADE 读取 STEP，再绘制正交表面投影。模型外包围盒为 12.6×8.6×0.52m（包括边界和底板）。主地坪 surface 109 位于 Y≈0，X=-6…6、Z=-4…4；底板底面 Y=0.1，地形向负Y凸起。因此采用 **从负Y侧俯视、屏幕右为+X、屏幕上为+Z** 的投影，裁切主地坪得到12×8m。

- `rmul2027-top.png`：1800×1200 像素底图，保持模型颜色和几何投影；无裁判坐标校准承诺。
- `rmul2027-top.json`：源文件哈希、轴向、范围、三角面数量与单位。
- 此方向下模型红方色块位于右下、蓝方位于左上。终端局部地图左下对应模型 X=-6、Z=-4；**尚未确认这个局部原点与裁判系统原点一致**。
- 正式模式在 `map_calibrated=false` 时不叠加实时机器人位置且不允许点击生成地图指令；演示模式显示模拟点位用于检查渲染。

重新生成：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-map.txt
.\.venv\Scripts\python.exe scripts\build_field_map.py "D:\QQ\download\RMUL2027_3V3_V1.0.0.stp" --up-axis y --up-sign -1 --bounds -6 -4 6 4
```

此图片为表面三角形正交投影的二维底图，用于操作界面，不用于碰撞检测、可通行性判定或工程尺寸验收。场地模型及衍生图的权利随原资料归属，未将它们重新声明为自有作品。
