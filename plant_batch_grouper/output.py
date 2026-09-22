# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Validated, all-or-nothing batches into a user-selected collection."""
import bpy
import json
import uuid
from . import workflow as w
from .mesh_ops import signature, schema, validate_source, prepare_join_normals
from .validation import mark_copies, equivalent, clean_markers
from .pivot_sources import capture_sources


def next_name(scene, plan):
    p=scene.pbg_settings
    number=max(p.start_number,plan.get('next_number',p.start_number))
    while f'{p.prefix}{number:0{p.digits}d}' in bpy.data.objects:
        number+=1
    name=f'{p.prefix}{number:0{p.digits}d}'
    if len(name.encode('utf-8'))>63:
        raise ValueError('前缀太长，请缩短到结果名称不超过 63 字节')
    return name,number


def merge(context, plan, group_ids=None):
    if context.mode!='OBJECT':
        raise ValueError('请切换到物体模式后合并')
    _,destination=w.validate_collections(context,plan)
    plan=w.sync(context,plan)
    groups=[g for g in w.pending_groups(plan) if g.get('confirmed') and (group_ids is None or g['id'] in group_ids)]
    if not groups:
        raise ValueError('没有已确认组；若刚修改模型，请先复查受影响的组')
    index=w.index_objects(context.scene,plan)
    for g in groups:
        originals=[index[i] for i in g['members']]
        for obj in originals:
            validate_source(obj)
        schemas=[schema(o) for o in originals]
        if any(s!=schemas[0] for s in schemas[1:]):
            raise ValueError(f"组 {g['id']} 的 UV 或颜色属性结构不一致，请先修复")
    selected=[o for o in context.selected_objects]
    active=context.view_layer.objects.active
    stage=bpy.data.collections.new('__PBG_临时校验')
    context.scene.collection.children.link(stage)
    meshes,outputs=[],[]
    pivot_summaries={}
    number=max(plan['next_number'],context.scene.pbg_settings.start_number)
    p=context.scene.pbg_settings
    try:
        for g in groups:
            originals=[index[i] for i in g['members']]
            before=signature(originals)
            copies=[]
            for original in originals:
                clone=original.copy()
                clone.data=original.data.copy()
                meshes.append(clone.data)
                matrix=original.matrix_world.copy()
                clone.parent=None
                clone.matrix_world=matrix
                clone.name='__PBG_TMP_'+uuid.uuid4().hex
                clone[w.UID]=uuid.uuid4().hex
                clone.hide_viewport=False
                clone.hide_select=False
                clone.hide_render=True
                stage.objects.link(clone)
                clone.hide_set(False)
                copies.append(clone)
            prepare_join_normals(copies)
            markers=mark_copies(copies)
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in copies:
                obj.select_set(True)
            context.view_layer.objects.active=copies[0]
            if len(copies)>1:
                with context.temp_override(object=copies[0],active_object=copies[0],selected_objects=copies,selected_editable_objects=copies):
                    result=bpy.ops.object.join()
                if 'FINISHED' not in result:
                    raise ValueError('Blender 没有完成合并')
            output=copies[0]
            after=signature([output])
            if not equivalent(originals,output,markers,before,after):
                labels={'vertices':'顶点位置','edges':'边属性','faces':'UV、颜色、材质或法线','point_colors':'顶点颜色'}
                failed='、'.join(labels.get(k,k) for k in before if before[k]!=after[k]) or '网格对应关系'
                raise ValueError(f"组 {g['id']} 校验失败（{failed}）；本批已撤回，原件和已确认进度保留")
            # Optional origin-preparation provenance uses the verified Join IDs.
            # A missing snapshot must never discard an otherwise valid merge.
            try:
                pivot_summaries[g['id']]=capture_sources(output,originals,g,plan,markers)
            except Exception:
                if 'PBG_PivotSource' in output:
                    del output['PBG_PivotSource']
            clean_markers(output,markers)
            desired=f'{p.prefix}{number:0{p.digits}d}'
            while desired in bpy.data.objects:
                number+=1
                desired=f'{p.prefix}{number:0{p.digits}d}'
            if len(desired.encode('utf-8'))>63:
                raise ValueError('前缀过长，请缩短结果名称')
            output.name=desired
            if output.name!=desired:
                raise ValueError('结果名称无法完整写入，请缩短前缀')
            output.data.name=desired+'_Mesh'
            number+=1
            output['PBG_Output']=True
            output['PBG_SourceObjects']=json.dumps([o.name for o in originals],ensure_ascii=False)
            output['PBG_SourceIDs']=json.dumps(g['members'])
            output['PBG_Group']=g['id']
            output['PBG_Session']=plan['session']
            output['PBG_Validation']='PASS: world geometry, face corners, UV, colors, materials, normals'
            output.color=(.8,.8,.8,1)
            outputs.append((g,output))
        # All validation is finished before linking to the user's collection.
        for g,obj in outputs:
            destination.objects.link(obj)
    except Exception:
        for obj in list(stage.objects):
            bpy.data.objects.remove(obj,do_unlink=True)
        raise
    finally:
        bpy.data.collections.remove(stage)
        for mesh in meshes:
            try:
                if mesh.users==0:
                    bpy.data.meshes.remove(mesh)
            except ReferenceError:
                pass
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        if active and active.name in context.view_layer.objects:
            context.view_layer.objects.active=active
    for g,obj in outputs:
        g.update(done=True,output=w.uid(obj))
        completed=dict(output=w.uid(obj),name=obj.name,sources=list(g['members']),
                       source_names=[index[i].name for i in g['members']],group=g['id'])
        if g['id'] in pivot_summaries:
            completed['pivot_source']=pivot_summaries[g['id']]
        plan['completed'].append(completed)
    plan['next_number']=number
    if outputs and plan.get('engine') == 'GRAPH':
        plan['graph_dirty']=True
    w.save(context.scene,plan)
    w.enforce_completed(context,plan)
    w.output_visibility(context,True)
    return [obj.name for _,obj in outputs]
