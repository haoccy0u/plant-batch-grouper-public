# Game Tools 烘焙与 UE 导入

本文对应 Game Tools 0.3.2 Object Attributes 和本插件局部 +X 生长方向约定。现场环境为 Blender 5.1.0、Windows、UE 5.8。Game Tools 需单独安装；本插件只准备枝条及坐标轴。

## 1. 选择独立枝条

完成原点整理后，退出原点编辑，关闭“仅影响原点”，显示完成集合。取消其他选择，再在大纲中右键完成集合，选择其中物体。

烘焙输入必须是独立枝条，不要提前 Ctrl+J；也不要选入此前生成的烘焙合并副本。集合对象数量不等于实际烘焙数量，必须核对烘焙报告。

## 2. 设置 Object Attributes

在 Game Tools → Object Attributes 的预设菜单加载 `PivotPainterDefault`。这会重置该工具的相关设置，已有自定义设置时先记录。

`PosIndex` 使用预设：RGB 为位置 XYZ，A 为父级索引。`AxisExtents` 按下表配置：

| 通道 | 类型 | Component | Axis |
|---|---|---|---|
| R | Axis | X | X |
| G | Axis | Y | X |
| B | Axis | Z | X |
| A | Extents | 保持预设 | X |

预设 RGB 的 Axis 默认是 Y，必须改为 X；Component 仍分别为 X、Y、Z。保持 Remap 关闭和 Pivot Painter Packing 开启。对这里的独立枝条流程，关闭 Depth Limit。

Mesh：开启 Duplicate 和 Merge，在副本上烘焙并自动合并；UV 名称保留 `UVMap.OA`，不要覆盖原有贴图 UV。Origin 留空使用世界原点，它不是要求选择某根枝条的根点。若使用自定义参考物体，网格与数据必须采用同一参考坐标系。

场景 Unit Scale 为 1 时，使用 Scale 100、Invert Y 与 Invert V 开启、XYZ 顺序；这对应米到厘米以及坐标约定转换。其他场景单位需单独核对，不能盲目重复缩放或翻转。

## 3. 保存与导出

先另存 `.blend` 工程。未保存工程时，Game Tools 的导出开关和路径不可编辑。`//` 表示工程所在目录，可改为已有的输出文件夹。

- Mesh Name：例如 `Plant_PP2`。
- Mesh → Export 文件名：`SM_<BakeName>`。
- Textures → Export 文件名：`T_<BakeName>_<TextureName>`。

占位符必须原样保留；不要改为 `<Plant_PP2>`，否则 Windows 会因尖括号出现在实际文件名中而拒绝写入。同时启用网格、贴图与 XML 报告导出，完成后点击 Bake。

核对 Report：成功、对象数量等于输入枝条数、数据 UV Index 已记录。例如 92 根枝条生成了 10×10 贴图；若仅为 1 个元素和 1×1 贴图，通常是输入选择不对。尺寸随实际配置变化，不要求所有植物都是 10×10。

导入 UE 的必须是本轮产生的 FBX 和对应 EXR。中断或失败后不要混用旧贴图与新网格；纠正问题后重新选择原来的独立枝条再烘焙。

## 4. UE 数据贴图与网格

两张 EXR 都是数据贴图，先使用以下设置接通效果：

| 设置 | 值 |
|---|---|
| Compression Settings | HDR High Precision (RGBA32F) |
| sRGB | 关闭 |
| Mip Gen Settings | NoMipmaps |
| Filter | Nearest |
| X / Y Tiling Method | Clamp |
| Compress Without Alpha | 关闭 |
| Virtual Texture Streaming | 关闭 |

Alpha 存有父级索引或长度，不能丢弃。不要用普通颜色、法线或 HDR Compressed 压缩处理这些数据。

网格开启 Use Full Precision UVs 并应用。首次验证关闭 Nanite，避免自动生成光照 UV 覆盖数据通道。数据通道以报告为准，从 0 编号；原有四套 UV 后追加的 `UVMap.OA` 为 UV4。颜色和法线贴图仍读取原来的 UV。

每根枝条的数据 UV 聚集到对应纹素中心属于正常现象，但不能只凭“小点”判断通道身份；原始模型的其他通道也可能看起来像小点。

## 5. 当前 UE 导入兼容记录

本次样例中，Blender 与输出 FBX 都包含五套 UV，UV4 含 92 个采样坐标；UE 5.8 的 Interchange 导入结果只显示 UV0～UV3。同一 FBX 换旧版导入器后，用户确认 UV4 出现。该记录仅描述本次环境，未定位到引擎内部具体缺陷，不代表所有版本都会丢失 UV。

遇到相同现象，打开 UE 输出日志，在命令栏输入：

```text
Interchange.FeatureFlags.Import.FBX 0
```

在新文件夹中把 FBX 作为新资产导入，确认出现旧式 FBX Import Options；关闭 Build Nanite 与 Generate Lightmap UVs，再检查 UV 通道。不要只重新导入已有的 Interchange 资产。

完成后可恢复：

```text
Interchange.FeatureFlags.Import.FBX 1
```

切线或副切线接近零的警告不等于数据 UV 丢失；需另行检查法线贴图光照表现，不应为消除该警告重新展开或删除数据 UV。

## 6. 材质格式与验证边界

`PosIndex.RGB` 为位置，A 为 Pivot Painter 编码的父级索引；`AxisExtents.RGB` 为有正负号的方向，A 为轴向长度。未开启 Remap 时，不对方向再做 `×2−1`，也不使用 8 位长度解码。材质使用报告指定的 UV，按一致坐标系处理根点、方向与网格位置，再计算 WPO。

不能未经适配直接将浮点方向贴图套进按 8 位方向编码编写的材质。Epic 默认材质或 Game Tools 示例材质的接入需核对各自的解码约定。本项目不附带 UE 材质；现场已确认烘焙与 UV4 导入，不宣称风摆、法线动画或全部变换情况已验证。

参考：[Game Tools Object Attributes](https://github.com/GhislainGir/GameToolsDoc/wiki/Object-Attributes-%28Pivot-Painter%29)、[数据格式与精度](https://github.com/GhislainGir/GameToolsDoc/wiki/Tech-Art-Compendium)、[Epic Pivot Painter 2](https://dev.epicgames.com/documentation/unreal-engine/pivot-painter-tool-2.0-in-unreal-engine)、[Epic 材质函数](https://dev.epicgames.com/documentation/unreal-engine/painter-tool-2.0-material-functions-in-unreal-engine)。
