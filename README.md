# Plant Batch Grouper · 植物批量分组

可能有用，但有用又不太可能

Blender 草本植物整理插件：将独立的茎、叶和花头归成枝条，分批合并，再设置枝条根部原点与沿生长方向的局部 **+X**，供后续 Pivot Painter 数据烘焙使用。

[下载安装包](https://github.com/haoccy0u/plant-batch-grouper-public/releases/tag/v2.3.2) · [问题反馈](https://github.com/haoccy0u/plant-batch-grouper-public/issues) · [更新记录](docs/changelog.md)

## 适用范围

- 草、芦苇和草本花卉；提供**连接图**与**原识别**两种方式，可根据模型选择。
- 输入植物应由可识别的独立网格部件组成；不会把整体网格自动切成枝条。灌木搞不了智能搞搞简单的花花草草
- 实际验证环境为 **Blender 5.1.0 / Windows**。插件声明最低 Blender 4.2，其他版本和平台尚未完成同等验证。

## 安装

1. 从 Releases 下载 `plant_batch_grouper_v2.3.2.zip`。
2. 在 Blender **偏好设置 → 插件 → 从磁盘安装**中选择 ZIP，并启用插件。
3. 在 3D 视图按 **N**，打开“植物分组”。

## 工作流

1. **Set Up**：设置来源、输出集合，以及结果前缀、起始编号和数字位数。
2. **自动分组**：按需清理无面对象，选择识别方式，分析并合并无歧义部分。
3. **手动修复**：在视口选择红色部件，预览候选并确认归属。
4. **原点与生长方向**：指定待处理和完成集合，自动设置明确枝条并归档隐藏。
5. **手动原点编辑**：选中剩余枝条，“单独显示并编辑原点”，使用原生移动或旋转工具调整，然后“确认并移入完成集合”。

[分组操作说明](docs/usage.md) · [原点操作说明](docs/pivot-usage.md) · [Game Tools 烘焙与 UE 导入](docs/baking-and-unreal.md)

Game Tools 是独立工具，需要另行安装；本插件不包含烘焙器。

源码按 [GNU GPL v3 或后续版本](LICENSE) 发布，不提供适用于所有资产的保证。haoccy0u 负责项目发布与用户验收，Codex 参与辅助开发。第三方工具和模型不随本项目分发。
