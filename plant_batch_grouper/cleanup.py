# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Delete only eligible whole, zero-face source objects; operators supply Undo."""
import json
import bpy
from . import workflow as w


def cleanup(context):
    if context.mode != 'OBJECT':
        raise ValueError('请切换到物体模式后清理')
    scene = context.scene
    settings = scene.pbg_settings
    source = settings.source
    if not source:
        raise ValueError('请先指定来源集合')
    if source not in w.descendants(scene.collection):
        raise ValueError('来源集合必须属于当前场景')
    plan = w.migrate(context) if scene.get(w.KEY) else None
    source_collections = w.descendants(source)
    finished = w.completed_ids(plan) if plan else set()
    output_ids = {c['output'] for c in plan.get('completed',[])} if plan else set()
    completed_names = set()
    for result in scene.objects:
        if not result.get('PBG_Output'):
            continue
        for field, target in [('PBG_SourceIDs',finished),('PBG_SourceObjects',completed_names)]:
            try:
                saved = json.loads(result.get(field,'[]'))
                if isinstance(saved,list):
                    target.update(value for value in saved if isinstance(value,str))
            except (ValueError,TypeError):
                pass
    eligible, details = [], []
    for obj in source.all_objects:
        if obj.type != 'MESH' or len(obj.data.polygons):
            continue
        object_id = obj.get(w.UID)
        reason = None
        if obj == settings.reference or (settings.protect_numbered and obj.name.isdigit()):
            reason = '参考或受保护对象'
        elif obj.get('PBG_Output') or object_id in output_ids:
            reason = '已有输出'
        elif object_id in finished or obj.name in completed_names:
            reason = '已完成组的来源备份'
        elif (obj.library or obj.data.library or obj.override_library or obj.data.override_library
              or not obj.is_editable or not obj.data.is_editable):
            reason = '只读或非本地对象'
        elif any(c not in source_collections for c in obj.users_collection):
            reason = '与来源范围外集合共享'
        elif any(s != scene for s in obj.users_scene):
            reason = '与其他场景共享'
        if reason:
            details.append(dict(name=obj.name,reason=reason))
        else:
            eligible.append(obj)
    if not eligible:
        return dict(deleted=0,skipped=len(details),details=details)
    # Prepare the complete JSON before deleting anything. One datablock operation
    # and one scene-property update belong to the caller's single Blender undo step.
    payload = None
    deleted_ids = {o.get(w.UID) for o in eligible if o.get(w.UID)}
    if plan:
        w.forget_objects(plan,deleted_ids)
        w.summarize(plan)
        payload = json.dumps(plan,ensure_ascii=False)
    count = len(eligible)
    bpy.data.batch_remove(ids=eligible)
    if payload is not None:
        scene[w.KEY] = payload
    w.DIRTY.difference_update(deleted_ids)
    return dict(deleted=count,skipped=len(details),details=details)
