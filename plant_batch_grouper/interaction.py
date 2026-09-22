# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Viewport selection is the only manual-repair target; previews never assign."""
import bpy
from . import workflow as w, output


def clear_preview(context):
    p=context.scene.pbg_settings
    p.preview_leaf_uid=''
    p.preview_group_id=0


def active_leaf(context, plan):
    obj=context.view_layer.objects.active
    i=obj.get(w.UID) if obj and obj.select_get() else None
    a=plan['assignments'].get(i)
    if i not in w.pending_ids(plan) or not a or a['kind']=='STEM':
        return None, None
    return obj,a


def candidates(plan, assignment):
    pending={g['id'] for g in w.pending_groups(plan)}
    return [c for c in assignment.get('candidates',[]) if c['group'] in pending]


def bound_target(context,plan):
    p=context.scene.pbg_settings
    obj,a=active_leaf(context,plan)
    if not obj or obj.get(w.UID)!=p.preview_leaf_uid or not p.preview_group_id:
        raise ValueError('请先选择叶片，再点击它的候选枝条')
    if p.preview_group_id not in {c['group'] for c in candidates(plan,a)}:
        raise ValueError('局部判断已改变，请重新选择当前叶片的候选枝条')
    return obj,a,w.group(plan,p.preview_group_id)


def remaining_questions(plan, gid, resolving=None):
    g=w.group(plan,gid)
    structural = {'anchor'} if plan.get('engine')=='GRAPH' and (g.get('stem_review') or g.get('stem_uncertain')) else set()
    return (structural | set(g.get('blockers',[])) | {
        i for i in g['members'] if plan['assignments'][i].get('manual_review')
    })-{resolving}


def preview_candidate(context, plan, leaf_uid, gid):
    if context.mode!='OBJECT':
        raise ValueError('请先结束网格编辑，切换到物体模式')
    obj,a=active_leaf(context,plan)
    if not obj or obj.get(w.UID)!=leaf_uid:
        raise ValueError('当前活动叶片已改变，请重新选择候选')
    w.sync(context,plan)
    obj,a=active_leaf(context,plan)
    if not obj or gid not in {c['group'] for c in candidates(plan,a)}:
        clear_preview(context)
        raise ValueError('该候选已不适用于当前叶片，请查看刷新后的候选')
    g=w.group(plan,gid)
    w.preview(context,plan)
    visible=set(g['members'])|set(g.get('blockers',[]))|{leaf_uid}
    index=w.index_objects(context.scene,plan)
    for i in w.pending_ids(plan):
        item=index.get(i)
        if item and item.name in context.view_layer.objects:
            item.hide_set(i not in visible)
            item.select_set(i==leaf_uid)
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active=obj
    p=context.scene.pbg_settings
    p.preview_leaf_uid=leaf_uid
    p.preview_group_id=gid
    w.enforce_completed(context,plan)
    w.output_visibility(context,True)


def confirm_selected(context, plan):
    if context.mode!='OBJECT':
        raise ValueError('请先结束网格编辑，切换到物体模式')
    # Verify before and after refresh: changes must never redirect a click.
    obj,_,target=bound_target(context,plan)
    leaf_uid=obj[w.UID]
    gid=target['id']
    w.validate_collections(context,plan)
    w.sync(context,plan)
    obj,_,target=bound_target(context,plan)
    # edit_members uses selection; explicitly limit its scope to this leaf.
    selected=list(context.selected_objects)
    for item in selected:
        item.select_set(False)
    obj.select_set(True)
    try:
        w.edit_members(context,plan,gid,'ADD')
    finally:
        for item in selected:
            if item.name in context.view_layer.objects and not item.hide_get():
                item.select_set(True)
        context.view_layer.objects.active=obj
    w.sync(context,plan)  # includes schema of the newly assigned member
    names=[]
    try:
        if not remaining_questions(plan,gid):
            w.confirm(plan,gid)
            w.save(context.scene,plan)
            names=output.merge(context,plan,[gid])
    finally:
        # A failed validation keeps the assignment and the original geometry.
        clear_preview(context)
        w.show_remaining(context,plan)
    return names


def auto_merge(context,plan):
    w.validate_collections(context,plan)
    w.sync(context,plan)
    ids=[g['id'] for g in w.pending_groups(plan) if g['ready']]
    if not ids:
        return []
    for gid in ids:
        w.confirm(plan,gid)
    w.save(context.scene,plan)
    try:
        names=output.merge(context,plan,ids)
    finally:
        clear_preview(context)
        w.show_remaining(context,plan)
    return names
