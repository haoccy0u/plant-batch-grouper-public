# Plant Batch Grouper · 植物批量分组

Blender 草本植物整理插件：将独立的茎、叶和花头归成枝条，分批合并，再设置枝条根部原点与沿生长方向的局部 **+X**，供后续 Pivot Painter 数据烘焙使用。

**正式版本：v2.3.2** · **作者：haoccy0u / Codex** · **许可证：GPL-3.0-or-later**

[下载安装包](https://github.com/haoccy0u/plant-batch-grouper-public/releases/tag/v2.3.2) · [问题反馈](https://github.com/haoccy0u/plant-batch-grouper-public/issues) · [更新记录](docs/changelog.md)

## 适用范围

- 草、芦苇和草本花卉；提供**连接图**与**原识别**两种方式，可根据模型选择。
- 输入植物应由可识别的独立网格部件组成；不会把整体网格自动切成枝条。灌木不属于当前优化与验收范围。
- 实际验证环境为 **Blender 5.1.0 / Windows**。插件声明最低 Blender 4.2，其他版本和平台尚未完成同等验证。
- 根部或方向不明确时保留待处理，使用 Blender 原生原点编辑修正。不要将自动判断视为所有模型上的保证。

## 安装与升级

1. 从 Releases 下载 `plant_batch_grouper_v2.3.2.zip`，不要选择 GitHub 自动生成的 Source code ZIP。
2. 在 Blender **偏好设置 → 插件 → 从磁盘安装**中选择 ZIP，并启用插件。
3. 在 3D 视图按 **N**，打开“植物分组”。

升级前自行保存工程副本。模块名与旧工程进度格式保持兼容，插件不会自动覆盖保存工程。

如果旧版本出现“未勾选但面板仍在”，可在下载源码并构建后，于 Blender 文本编辑器运行 `scripts/install.py` 修复注册残留。安装脚本使用正式插件启用流程并保存启用偏好；它不会保存工程。

## 工作流

1. **Set Up**：设置来源、输出集合，以及结果前缀、起始编号和数字位数。
2. **自动分组**：按需清理无面对象，选择识别方式，分析并合并无歧义部分。
3. **手动修复**：在视口选择红色部件，预览候选并确认归属；移动修正后可局部刷新。
4. **原点与生长方向**：指定待处理和完成集合，自动设置明确枝条并归档隐藏。
5. **手动原点编辑**：选中剩余枝条，“单独显示并编辑原点”，使用原生移动或旋转工具调整，然后“确认并移入完成集合”。无需重新建立编辑基准，也可直接确认已自行调整的枝条。

合并保留并隐藏来源，不焊接顶点。主动点击“清理无面对象”才会删除符合条件的无面原件。原点确认接受点击时的当前模型与坐标轴；需要规范化缩放时校验操作前后数据，失败保留确认前状态。返回剩余视图保留尚未确认的人工调整。

[分组操作说明](docs/usage.md) · [原点操作说明](docs/pivot-usage.md) · [Game Tools 烘焙与 UE 导入](docs/baking-and-unreal.md)

Game Tools 是独立工具，需要另行安装；本插件不包含烘焙器或 UE 风摆材质。

## 构建与开发

构建只需 Python 3.11 或更高版本的标准库，无需运行 Blender：

```console
python scripts/build.py
```

输出位于 `dist/`：安装 ZIP 和 SHA256 文件。ZIP 包含插件 Python 源码及 LICENSE；不包含真实模型、工程备份、缓存或现场报告。

已有功能回归工具供开发者自行运行：

```console
python scripts/test.py --blender /path/to/blender
```

默认测试生成独立合成模型；`--real-fixtures /path/to/archive` 可运行需要自备资产的历史回归。缺失资产明确显示 SKIP。详见 [测试说明](docs/testing.md)。

v2.3.2 发布整理仅运行静态、语法和打包检查，功能验收由用户在 Blender 中完成。已有用户确认分组与原点流程可用，并完成 92 枝条烘焙及 UE 旧版导入 UV4 的现场验证；不据此宣称全部边界场景、跨版本兼容或 UE 风摆材质已通过验收。[验证范围](docs/validation-v2.3.2.md)

## 项目与许可

- `plant_batch_grouper/`：插件源码。
- `scripts/`：构建、安装、测试入口。
- `tests/`：合成与可选历史回归。
- `docs/`：中文说明、设计和版本记录。

源码按 [GNU GPL v3 或后续版本](LICENSE) 发布，不提供适用于所有资产的保证。haoccy0u 负责项目发布与用户验收，Codex 参与辅助开发。第三方工具和模型不随本项目分发。
