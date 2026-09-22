# Plant Batch Grouper v2.3.2

用于 Blender 中草、芦苇与草本花卉的部件分组、分批合并，以及枝条根部原点和局部 +X 生长方向准备。

## 安装

下载本页附件 `plant_batch_grouper_v2.3.2.zip`，在 Blender 偏好设置的“从磁盘安装”中安装并启用。不要使用 GitHub 自动生成的 Source code ZIP 作为插件安装包。校验值见 `plant_batch_grouper_v2.3.2.zip.sha256`。

升级前自行保存工程副本；模块名与旧进度格式保持，插件不会自动覆盖保存工程。

## 本版内容

- 保留连接图与原识别两种分组方式，视口处理歧义，完成组分批输出并隐藏。
- 自动设置明确枝条的根部原点和局部 +X，移入完成集合。
- 使用 Blender 原生“仅影响原点”修正剩余枝条，手动完成后直接确认，无需重新建立基准。
- 保留来源备份；必要的坐标变换进行当前数据校验，失败回滚。
- 附中文分组、原点、Game Tools 烘焙与 UE 导入说明，含本次 UV4 导入问题的处理方法。

## 环境与验证范围

实际验证环境：Blender 5.1.0 / Windows。最低声明为 Blender 4.2，其他版本与平台未完成同等验证。灌木不属于当前优化与验收范围。

用户完成 92 枝条原点整理、Game Tools 烘焙以及 UE 5.8 旧版 FBX 导入 UV4 的现场验证。本轮发布通过静态、语法、ZIP 和干净源码重复构建检查，没有独立运行功能回归，不宣称全部边界情况、自动识别准确率或 UE 风摆材质已经验证。

Game Tools 和 UE 材质需另行配置；本插件不附带它们。在当前样例中，Interchange 导入缺少 UV4，旧版 FBX 导入可以保留；这不是对所有引擎版本的结论。

## 许可与反馈

作者：haoccy0u / Codex。许可证：GPL-3.0-or-later；完整 LICENSE 随源码与安装包提供。

[中文说明](https://github.com/haoccy0u/plant-batch-grouper-public#readme) · [问题反馈](https://github.com/haoccy0u/plant-batch-grouper-public/issues)

反馈时请附 Blender 版本、使用的识别方式、简要操作与报错；请勿上传没有分发权限的模型或工程。
