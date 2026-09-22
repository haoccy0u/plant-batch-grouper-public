# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

import bpy
import json
from pathlib import Path
from bpy.app.handlers import persistent
from . import workflow as w, output, interaction as repair, cleanup, pivot, pivot_ui
from .core import DEFAULTS


class PBG_Settings(bpy.types.PropertyGroup):
    source:bpy.props.PointerProperty(name='来源集合',type=bpy.types.Collection)
    output:bpy.props.PointerProperty(name='输出集合',type=bpy.types.Collection)
    output_name:bpy.props.StringProperty(name='新集合名称',default='PBG_合并结果')
    prefix:bpy.props.StringProperty(name='结果前缀',default='')
    start_number:bpy.props.IntProperty(name='起始编号',default=1,min=1)
    digits:bpy.props.IntProperty(name='数字位数',default=3,min=1,max=8)
    reference:bpy.props.PointerProperty(name='参考枝条',type=bpy.types.Object,poll=lambda self,o:o.type=='MESH')
    analysis_method:bpy.props.EnumProperty(name='识别方式',items=[
        ('GRAPH','连接图','沿网格路径寻找枝条，以连接关系分配侧枝和附属部件'),
        ('LEGACY','原识别','原有按生长轴、根部与茎片配对规则')],default='GRAPH')
    selected_only:bpy.props.BoolProperty(name='只分析来源集合中的所选对象',default=False)
    protect_numbered:bpy.props.BoolProperty(name='保护已有纯数字命名对象',default=True)
    up:bpy.props.EnumProperty(name='生长轴',items=[('0','X',''),('1','Y',''),('2','Z','')],default='2')
    stem_ratio:bpy.props.FloatProperty(name='最小细长度',default=25,min=2,max=10000)
    min_stem_fraction:bpy.props.FloatProperty(name='最小茎高 / 植株高',default=.14,min=.001,max=1)
    root_fraction:bpy.props.FloatProperty(name='根部范围 / 植株高',default=.06,min=.001,max=.5)
    pair_fraction:bpy.props.FloatProperty(name='茎片配对容差 / 植株高',default=.012,min=.00001,max=.2,precision=4)
    attach_fraction:bpy.props.FloatProperty(name='叶柄容差 / 植株高',default=.005,min=.00001,max=.1,precision=4)
    flower_fraction:bpy.props.FloatProperty(name='花头容差 / 植株高',default=.025,min=.00001,max=.2,precision=4)
    ambiguity_ratio:bpy.props.FloatProperty(name='歧义距离比',default=1.8,min=1.01,max=5)
    # Compatibility only: old files retain this value, but repair never reads it.
    current_group:bpy.props.IntProperty(default=0,options={'HIDDEN'})
    preview_leaf_uid:bpy.props.StringProperty(options={'SKIP_SAVE','HIDDEN'})
    preview_group_id:bpy.props.IntProperty(default=0,options={'SKIP_SAVE','HIDDEN'})
    status:bpy.props.StringProperty(default='')
    report_path:bpy.props.StringProperty(name='报告路径',subtype='FILE_PATH',default='//plant_group_report.json')


def require_object(context):
    if context.mode!='OBJECT':
        raise ValueError('请先结束网格编辑，切换到物体模式')


def existing_plan(context):
    require_object(context)
    plan=w.migrate(context)
    w.validate_collections(context,plan)
    w.gather_results(context,plan)
    return plan


class SafeOperator:
    def execute(self,context):
        try:
            message=self.run(context)
            if message:
                self.report({'INFO'},message)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}


class PBG_OT_Cleanup(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.cleanup'
    bl_label='清理无面对象'
    bl_description='删除来源及子集合中整个对象都没有面的本地网格；保留参考、结果和共享对象，可撤销'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        require_object(context)
        result=cleanup.cleanup(context)
        repair.clear_preview(context)
        return f"已清理 {result['deleted']} 个无面对象；跳过 {result['skipped']} 个受保护、只读或共享对象"


class PBG_OT_Analyze(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.analyze'
    bl_label='分析分组'
    bl_description='按当前识别参数分析；普通模型修正请使用手动修复中的局部刷新'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        require_object(context)
        plan=w.start_analysis(context)
        repair.clear_preview(context)
        w.gather_results(context,plan)
        w.show_remaining(context,plan)
        return '分析完成，可合并无歧义部分'


class PBG_OT_AutoMerge(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.auto_merge'
    bl_label='合并无歧义部分'
    bl_description='刷新局部判断，直接输出明确的枝条并隐藏原件；可多次使用'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        plan=existing_plan(context)
        names=repair.auto_merge(context,plan)
        if not names and plan.get('summary',{}).get('all_complete'):
            return '全部处理完成'
        return f'已完成 {len(names)} 根枝条，结果与原件已隐藏' if names else '没有可自动合并的枝条，请在视口选择红色部件修复'


class PBG_OT_Candidate(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.preview_candidate'
    bl_label='预览候选枝条'
    bl_description='仅预览，保留当前叶片为活动对象；点击确认后才保存归属'
    bl_options={'REGISTER','UNDO'}
    group_id:bpy.props.IntProperty()
    def run(self,context):
        plan=existing_plan(context)
        obj,_=repair.active_leaf(context,plan)
        if not obj:
            raise ValueError('请在视口选择未完成的叶片或花头')
        repair.preview_candidate(context,plan,obj[w.UID],self.group_id)


class PBG_OT_Confirm(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.confirm_selected'
    bl_label='确认归属'
    bl_description='确认当前叶片属于刚预览的枝条；整根明确后自动合并并隐藏'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        names=repair.confirm_selected(context,existing_plan(context))
        return '已确认并合并：'+', '.join(names) if names else '归属已保存；请在视口选择剩余红色部件'


class PBG_OT_Refresh(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.refresh_local'
    bl_label='局部刷新'
    bl_description='移动或修正模型后刷新连接判断，保留其他枝条的进度和人工归属'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        plan=existing_plan(context)
        w.sync(context,plan)
        repair.clear_preview(context)
        w.show_remaining(context,plan)
        info=plan.get('last_refresh',{})
        return f"已刷新 {info.get('changed',0)} 个变化部件"


class PBG_OT_Remaining(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.return_remaining'
    bl_label='返回剩余视图'
    bl_description='退出候选预览，显示所有未完成部件，不改变归属'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        plan=existing_plan(context)
        repair.clear_preview(context)
        w.show_remaining(context,plan)


class PBG_OT_Anchor(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.set_anchor'
    bl_label='设为主枝'
    bl_description='将活动对象作为完整主枝起点；已有配对主枝保持整组，不切分网格'
    bl_options={'REGISTER','UNDO'}
    def run(self,context):
        from . import graph_workflow
        plan=existing_plan(context)
        gid=graph_workflow.set_anchor(context,plan)
        repair.clear_preview(context)
        w.show_remaining(context,plan)
        return f'已指定主枝 {gid:03d}，相关连接已刷新'


class PBG_OT_Utility(SafeOperator,bpy.types.Operator):
    bl_idname='pbg.utility'
    bl_label='植物分组设置'
    bl_options={'REGISTER','UNDO'}
    action:bpy.props.StringProperty()
    def run(self,context):
        p=context.scene.pbg_settings
        if self.action=='NEW_OUTPUT':
            name=p.output_name.strip()
            if not name:
                raise ValueError('请输入新集合名称')
            if name in bpy.data.collections:
                raise ValueError('该集合已存在，请在输出集合中直接选择')
            p.output=bpy.data.collections.new(name)
            context.scene.collection.children.link(p.output)
            w.uid(p.output)
            w.output_visibility(context,True)
        elif self.action in {'HIDE_OUTPUT','SHOW_OUTPUT'}:
            w.output_visibility(context,self.action=='HIDE_OUTPUT')
        elif self.action=='RESTORE':
            repair.clear_preview(context)
            w.restore_view(context)
        elif self.action=='REPORT':
            plan=w.migrate(context)
            path=Path(bpy.path.abspath(p.report_path))
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
            return '报告已保存：'+str(path)


def utility(layout,text,action):
    layout.operator('pbg.utility',text=text).action=action


def read_plan(context):
    try:
        plan=w.load(context.scene)
        return plan if plan.get('version')==2 else None
    except (ValueError,KeyError):
        return None


class PanelBase:
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='植物分组'


class PBG_PT_SetUp(PanelBase,bpy.types.Panel):
    bl_label='Set Up'
    bl_idname='PBG_PT_setup'
    bl_order=0
    def draw(self,context):
        l,p=self.layout,context.scene.pbg_settings
        l.prop(p,'source')
        l.prop(p,'output')
        row=l.row(align=True)
        row.prop(p,'output_name',text='新建')
        utility(row,'创建','NEW_OUTPUT')
        l.prop(p,'prefix')
        row=l.row(align=True)
        row.prop(p,'start_number')
        row.prop(p,'digits')
        try:
            name,_=output.next_name(context.scene,read_plan(context) or {})
            l.label(text='下个结果：'+name)
        except ValueError:
            l.label(text='前缀过长，请缩短',icon='ERROR')
        l.prop(p,'reference')


class PBG_PT_Automatic(PanelBase,bpy.types.Panel):
    bl_label='自动分组'
    bl_idname='PBG_PT_automatic'
    bl_order=1
    def draw(self,context):
        l=self.layout
        l.operator('pbg.cleanup')
        l.prop(context.scene.pbg_settings,'analysis_method')
        l.operator('pbg.analyze')
        plan=read_plan(context)
        if plan and plan.get('engine','LEGACY')!=context.scene.pbg_settings.analysis_method:
            l.label(text='切换方式后，点击分析分组生效',icon='INFO')
        row=l.row()
        row.enabled=bool(context.scene.get(w.KEY))
        row.operator('pbg.auto_merge')


class PBG_PT_Manual(PanelBase,bpy.types.Panel):
    bl_label='手动修复'
    bl_idname='PBG_PT_manual'
    bl_order=2
    def draw(self,context):
        l,p=self.layout,context.scene.pbg_settings
        plan=read_plan(context)
        obj,a=repair.active_leaf(context,plan) if plan else (None,None)
        if obj:
            l.label(text=obj.name,icon='OUTLINER_OB_MESH')
            l.label(text=a.get('reason','请选择候选枝条'))
            choices=repair.candidates(plan,a)
            for c in choices:
                gid=c['group']
                selected=p.preview_leaf_uid==obj.get(w.UID) and p.preview_group_id==gid
                op=l.operator('pbg.preview_candidate',text=f"候选枝条 {gid:03d}",depress=selected,
                              icon='RADIOBUT_ON' if selected else 'RADIOBUT_OFF')
                op.group_id=gid
            if not choices:
                l.label(text='暂无候选；请修正位置后局部刷新')
        else:
            if plan and plan.get('summary',{}).get('all_complete'):
                l.label(text='全部处理完成',icon='CHECKMARK')
            else:
                l.label(text='在视口选择红色部件' if plan else '请先在自动分组中分析')
        row=l.row()
        row.enabled=plan is not None
        row.operator('pbg.refresh_local')
        if plan and plan.get('engine')=='GRAPH':
            row=l.row()
            active=context.view_layer.objects.active
            row.enabled=bool(active and active.select_get() and active.get(w.UID) in w.pending_ids(plan))
            row.operator('pbg.set_anchor')
        row=l.row()
        try:
            target_obj,_,g=repair.bound_target(context,plan) if plan else (None,None,None)
        except ValueError:
            g=None
        row.enabled=g is not None
        label='确认并合并' if g and not repair.remaining_questions(plan,g['id'],target_obj.get(w.UID)) else '确认归属'
        row.operator('pbg.confirm_selected',text=label)
        row=l.row()
        row.enabled=plan is not None
        row.operator('pbg.return_remaining')


class PBG_PT_Advanced(PanelBase,bpy.types.Panel):
    bl_label='高级设置'
    bl_idname='PBG_PT_advanced'
    bl_order=4
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l,p=self.layout,context.scene.pbg_settings
        l.prop(p,'selected_only')
        l.prop(p,'protect_numbered')
        graph_labels={'stem_ratio':'枝条细长判定', 'min_stem_fraction':'主枝最小长度比例',
                      'pair_fraction':'配对容差 / 局部路径长', 'attach_fraction':'附着容差 / 局部路径长',
                      'flower_fraction':'花头容差 / 枝条路径长'} if p.analysis_method=='GRAPH' else {}
        for key in DEFAULTS:
            if key in graph_labels:
                l.prop(p,key,text=graph_labels[key])
            else:
                l.prop(p,key)
        if p.analysis_method=='GRAPH':
            l.label(text='连接图按局部宽度与路径判断')
            l.label(text='参考枝条只记录形状，不自动拟合参数')
        l.label(text='修改识别参数后需重新分析')
        row=l.row(align=True)
        utility(row,'隐藏结果','HIDE_OUTPUT')
        utility(row,'显示结果','SHOW_OUTPUT')
        utility(l,'退出预览','RESTORE')
        l.prop(p,'report_path')
        utility(l,'导出分组报告','REPORT')
        if p.status:
            l.label(text=p.status,icon='INFO')


_LAST_SELECTION=None

def _context_ready(context):
    """Official add-on registration deliberately has no scene context."""
    scene=getattr(context,'scene',None)
    return bool(scene is not None and hasattr(scene,'pbg_settings') and
                getattr(context,'view_layer',None) is not None)


def selection_changed(context):
    global _LAST_SELECTION
    if not _context_ready(context):
        return
    obj=context.view_layer.objects.active
    key=(context.scene.as_pointer(),obj.as_pointer() if obj and obj.select_get() else 0)
    if key!=_LAST_SELECTION:
        _LAST_SELECTION=key
        p=context.scene.pbg_settings
        if p.preview_leaf_uid and (not obj or not obj.select_get() or obj.get(w.UID)!=p.preview_leaf_uid):
            repair.clear_preview(context)
        for area in context.screen.areas if context.screen else []:
            if area.type=='VIEW_3D':
                area.tag_redraw()


@persistent
def on_selection(_scene,_depsgraph):
    selection_changed(bpy.context)


def selection_timer():
    if not hasattr(bpy.types.Scene,'pbg_settings'):
        return None
    selection_changed(bpy.context)
    pivot.tick(bpy.context)
    return .2


def resume(context):
    global _LAST_SELECTION
    if not _context_ready(context):
        return False
    _LAST_SELECTION=None
    repair.clear_preview(context)
    # Group recovery historically hides all merge results. Preserve the saved
    # input visibility once this scene has started origin preparation.
    pivot_layers=[]
    pivot_input=pivot.output_collection(context.scene)
    if context.scene.get(pivot.KEY) and pivot_input:
        pivot_layers=[(layer,layer.hide_viewport,layer.exclude)
                      for layer in w.layer_collections(context.view_layer.layer_collection)
                      if layer.collection==pivot_input]
    if context.scene.get(w.KEY):
        try:
            plan=w.migrate(context)
            if not context.scene.pbg_settings.is_property_set('analysis_method'):
                context.scene.pbg_settings.analysis_method=plan.get('engine','LEGACY')
            w.enforce_completed(context,plan)
            if context.scene.pbg_settings.source and context.scene.pbg_settings.output:
                w.gather_results(context,plan)
            if context.scene.get(w.VIEW):
                w.show_remaining(context,plan)
            context.scene.pbg_settings.status=''
        except (ValueError,KeyError) as exc:
            context.scene.pbg_settings.status=str(exc)
    for layer,hidden,excluded in pivot_layers:
        layer.exclude=excluded
        layer.hide_viewport=hidden
    pivot.enforce_completed(context)
    if context.scene.get(pivot.native.KEY):
        pivot._show_pending(context)
        pivot.native.sync(context)
    return True


def deferred_resume():
    """Run only after addon_utils has left Blender's restricted context."""
    if not hasattr(bpy.types.Scene,'pbg_settings'):
        return None
    if not _context_ready(bpy.context):
        return .1
    try:
        resume(bpy.context)
    except Exception as exc:
        # Bad scene data must not leave an enabled add-on only half registered.
        bpy.context.scene.pbg_settings.status='恢复工作区失败：'+str(exc)
        print('Plant Batch Grouper: workspace resume failed:',repr(exc))
    return None


def schedule_resume():
    """Public lifecycle hook for load handlers and managed upgrades."""
    if not bpy.app.timers.is_registered(deferred_resume):
        bpy.app.timers.register(deferred_resume,first_interval=0.0)


def cancel_deferred_resume():
    if bpy.app.timers.is_registered(deferred_resume):
        bpy.app.timers.unregister(deferred_resume)


CLASSES=(PBG_Settings,PBG_OT_Cleanup,PBG_OT_Analyze,PBG_OT_AutoMerge,PBG_OT_Candidate,
         PBG_OT_Confirm,PBG_OT_Refresh,PBG_OT_Remaining,PBG_OT_Anchor,PBG_OT_Utility,
         PBG_PT_SetUp,PBG_PT_Automatic,PBG_PT_Manual,PBG_PT_Advanced)+pivot_ui.CLASSES


def register():
    # addon_utils.enable calls this inside RestrictBlend. Do not inspect the
    # scene here. Record ownership to unwind only resources from this attempt.
    classes_before={cls:bool(getattr(cls,'is_registered',False)) for cls in CLASSES}
    pointers=(('pbg_settings',PBG_Settings),('pbg_pivot_settings',pivot_ui.PBG_PivotSettings))
    pointer_before={name:hasattr(bpy.types.Scene,name) for name,_ in pointers}
    handlers=((bpy.app.handlers.depsgraph_update_post,w.track_changes),
              (bpy.app.handlers.load_post,w.loaded),
              (bpy.app.handlers.depsgraph_update_post,on_selection),
              (bpy.app.handlers.depsgraph_update_post,pivot.changed),
              (bpy.app.handlers.load_post,pivot.clear_runtime),
              (bpy.app.handlers.undo_post,pivot.clear_runtime),
              (bpy.app.handlers.redo_post,pivot.clear_runtime))
    handlers_before=[fn in collection for collection,fn in handlers]
    timers=(selection_timer,deferred_resume)
    timers_before={fn:bpy.app.timers.is_registered(fn) for fn in timers}
    try:
        for cls in CLASSES:
            if not classes_before[cls]:
                bpy.utils.register_class(cls)
        for name,cls in pointers:
            if not pointer_before[name]:
                setattr(bpy.types.Scene,name,bpy.props.PointerProperty(type=cls))
        for collection,fn in handlers:
            if fn not in collection:
                collection.append(fn)
        if not bpy.app.timers.is_registered(selection_timer):
            bpy.app.timers.register(selection_timer,persistent=True)
        schedule_resume()
    except Exception:
        for fn in reversed(timers):
            if not timers_before[fn] and bpy.app.timers.is_registered(fn):
                bpy.app.timers.unregister(fn)
        for (collection,fn),existed in reversed(list(zip(handlers,handlers_before))):
            if not existed:
                while fn in collection:
                    collection.remove(fn)
        for name,_ in reversed(pointers):
            if not pointer_before[name] and hasattr(bpy.types.Scene,name):
                delattr(bpy.types.Scene,name)
        for cls in reversed(CLASSES):
            if not classes_before[cls] and getattr(cls,'is_registered',False):
                bpy.utils.unregister_class(cls)
        raise


def unregister(*,restore_view=True):
    """Tear down completely even after partial initialization.

    Managed upgrades may pass restore_view=False to keep their captured view.
    Cleanup warnings are returned for their installer to inspect.
    """
    global _LAST_SELECTION
    errors=[]
    try:
        pivot_ui.stop_runtime()
    except Exception as exc:
        errors.append(str(exc))
    for fn in (deferred_resume,selection_timer):
        try:
            if bpy.app.timers.is_registered(fn):
                bpy.app.timers.unregister(fn)
        except Exception as exc:
            errors.append(str(exc))
    for collection,fn in ((bpy.app.handlers.depsgraph_update_post,on_selection),
                          (bpy.app.handlers.depsgraph_update_post,w.track_changes),
                          (bpy.app.handlers.load_post,w.loaded),
                          (bpy.app.handlers.depsgraph_update_post,pivot.changed),
                          (bpy.app.handlers.load_post,pivot.clear_runtime),
                          (bpy.app.handlers.undo_post,pivot.clear_runtime),
                          (bpy.app.handlers.redo_post,pivot.clear_runtime)):
        try:
            while fn in collection:
                collection.remove(fn)
        except Exception as exc:
            errors.append(str(exc))
    try:
        if restore_view and _context_ready(bpy.context):
            w.restore_view(bpy.context)
    except Exception as exc:
        errors.append('恢复视图失败：'+str(exc))
    for name in ('pbg_pivot_settings','pbg_settings'):
        if hasattr(bpy.types.Scene,name):
            try:
                delattr(bpy.types.Scene,name)
            except Exception as exc:
                errors.append(str(exc))
    for cls in reversed(CLASSES):
        try:
            if getattr(cls,'is_registered',False):
                bpy.utils.unregister_class(cls)
        except Exception as exc:
            errors.append(str(exc))
    _LAST_SELECTION=None
    w.DIRTY.clear()
    if errors:
        print('Plant Batch Grouper: cleanup warnings:', '; '.join(errors))
    return errors
