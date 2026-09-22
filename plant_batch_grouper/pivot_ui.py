# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Viewport-led preparation using Blender's native origin editing tools."""
import bpy
from . import pivot


def redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class PBG_PivotSettings(bpy.types.PropertyGroup):
    collection: bpy.props.PointerProperty(name='待处理集合', type=bpy.types.Collection,
                                         description='留空时使用 Set Up 的分组输出集合')
    completed: bpy.props.PointerProperty(name='完成集合', type=bpy.types.Collection)
    completed_name: bpy.props.StringProperty(name='新集合名称', default='PBG_原点完成')
    up: bpy.props.EnumProperty(name='生长轴', items=[('0', 'X', ''), ('1', 'Y', ''), ('2', 'Z', '')],
                              default='2', description='自动判断根端；完成后的本地 +X 指向生长方向')
    show_completed: bpy.props.BoolProperty(default=False, options={'HIDDEN'})
    status: bpy.props.StringProperty(default='')
    last_errors: bpy.props.StringProperty(default='')


class PivotOperator:
    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        try:
            message = self.run(context)
            if message:
                context.scene.pbg_pivot_settings.status = message
                self.report({'INFO'}, message)
            redraw(context)
            return {'FINISHED'}
        except Exception as exc:
            context.scene.pbg_pivot_settings.status = str(exc)
            self.report({'ERROR'}, str(exc))
            redraw(context)
            return {'CANCELLED'}


class PBG_OT_PivotAuto(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_auto'
    bl_label = '自动设置并归档'
    bl_description = '明确枝条设置根部原点和本地 +X，校验后归档隐藏；有效旧应用记录只归档'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        return pivot.auto_archive(context)


class PBG_OT_PivotNative(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_native'
    bl_label = '单独显示并编辑原点'
    bl_description = '隔离活动枝条，启用仅影响原点、本地旋转与原生坐标轴；再次进入保留人工调整'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        return pivot.edit_origin(context)


class PBG_OT_PivotConfirm(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_confirm'
    bl_label = '确认并移入完成集合'
    bl_description = '接受当前模型、原点和坐标轴；必要时处理单位缩放并校验本次处理，随后归档隐藏'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        return pivot.confirm(context)


class PBG_OT_PivotRemaining(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_remaining'
    bl_label = '返回剩余视图'
    bl_description = '保留未确认调整，恢复之前的工具及局部视图；不跳到下一根'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        return pivot.remaining(context)


class PBG_OT_PivotBaseline(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_baseline'
    bl_label = '为副本建立独立记录'
    bl_description = '解除从其他枝条复制来的准备身份，保留当前模型、原点和方向'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        return pivot.reset_identity(context)


class PBG_OT_PivotCollection(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_collection'
    bl_label = '新建／使用完成集合'
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        _, collection = pivot.collections(context, create=True)
        pivot.enforce_completed(context)
        return '完成集合：' + collection.name


class PBG_OT_PivotShow(PivotOperator, bpy.types.Operator):
    bl_idname = 'pbg.pivot_show'
    bl_label = '显示完成集合'
    bl_options = {'REGISTER', 'UNDO'}
    visible: bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'})

    def run(self, context):
        return pivot.show_completed(context, self.visible)


def _label_lines(layout, text, width=26):
    for line in text.splitlines():
        for first in range(0, len(line), width):
            layout.label(text=line[first:first + width])


class PBG_PT_Pivot(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '植物分组'
    bl_label = '原点与生长方向'
    bl_idname = 'PBG_PT_pivot'
    bl_order = 3

    def draw(self, context):
        layout, p = self.layout, context.scene.pbg_pivot_settings
        setup = layout.column(align=True)
        setup.prop(p, 'collection')
        if not p.collection and pivot.output_collection(context.scene):
            setup.label(text='使用：' + pivot.output_collection(context.scene).name)
        setup.prop(p, 'completed')
        if not p.completed:
            row = setup.row(align=True)
            row.prop(p, 'completed_name', text='')
            row.operator('pbg.pivot_collection', text='', icon='ADD')
        setup.prop(p, 'up')
        layout.separator()
        layout.operator('pbg.pivot_auto')
        layout.separator()
        box = layout.box()
        box.label(text='手动编辑')
        obj, entry = context.active_object, None
        if obj and obj.type == 'MESH':
            box.label(text=obj.name, icon='MESH_DATA')
            try:
                entry = pivot.entry_for(context.scene, obj)
                if entry:
                    reason = entry.get('reason', '')
                    if entry.get('version') == 1 and '游标' in reason:
                        reason = '旧版根点或方向尚未明确，可重新自动判断或直接编辑原点'
                    _label_lines(box, reason)
                    if entry.get('last_error'):
                        _label_lines(box, entry['last_error'])
            except (ValueError, TypeError) as exc:
                _label_lines(box, str(exc))
        else:
            box.label(text='在视口选择一根剩余枝条')
        box.operator('pbg.pivot_native')
        box.label(text='确认即接受当前模型、原点与方向')
        box.operator('pbg.pivot_confirm')
        box.operator('pbg.pivot_remaining')
        duplicate = obj and obj.get(pivot.UID) and any(
            other != obj and other.get(pivot.UID) == obj.get(pivot.UID) for other in context.scene.objects)
        if duplicate:
            box.operator('pbg.pivot_baseline')
        row = layout.row(align=True)
        row.operator('pbg.pivot_show', text='显示完成集合').visible = True
        row.operator('pbg.pivot_show', text='隐藏完成集合').visible = False
        if p.status:
            _label_lines(layout, p.status)
        if p.last_errors:
            _label_lines(layout, p.last_errors)


def stop_runtime():
    pivot.native.stop(bpy.context)
    pivot.clear_runtime()


CLASSES = (PBG_PivotSettings, PBG_OT_PivotAuto, PBG_OT_PivotNative, PBG_OT_PivotConfirm,
           PBG_OT_PivotRemaining, PBG_OT_PivotBaseline, PBG_OT_PivotCollection,
           PBG_OT_PivotShow, PBG_PT_Pivot)
