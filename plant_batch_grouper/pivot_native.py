# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Native origin editing, with a restorable per-viewport workspace snapshot."""
import json

import bpy

KEY = 'pbg_pivot_native_json'
_LAST = None


def _read(scene):
    raw = scene.get(KEY)
    return json.loads(raw) if raw else None


def _view(context, state=None):
    candidates = []
    for window in context.window_manager.windows:
        if window.scene != context.scene:
            continue
        for index, area in enumerate(window.screen.areas):
            if area.type == 'VIEW_3D':
                candidates.append((window, area, index))
    if state:
        found = next((v for v in candidates if v[0].screen.name == state['screen']
                      and v[2] == state['area_index']), None)
    else:
        found = next((v for v in candidates if v[1] == context.area), None)
    found = found or (candidates[0] if candidates else None)
    if not found:
        raise ValueError('请在 3D 视图中编辑原点')
    window, area, index = found
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is None:
        raise ValueError('当前 3D 视图没有可用窗口区域')
    return window, area, region, index


def _identity(obj):
    return {'name': obj.name, 'id': obj.get('PBG_PivotID', '')}


def _matches(obj, identity):
    return (obj.get('PBG_PivotID') == identity['id'] if identity.get('id')
            else obj.name == identity['name'])


def _find(scene, identity):
    found = [obj for obj in scene.objects if _matches(obj, identity)]
    return found[0] if len(found) == 1 else None


def active(scene):
    state = _read(scene)
    return _find(scene, state['object']) if state else None


def enter(context, obj):
    global _LAST
    if _read(context.scene):
        if active(context.scene) == obj:
            window, area, region, _ = _view(context, _read(context.scene))
            _isolate(context, window, area, region, obj)
            _configure(context, window, area, region, obj)
            return
        leave(context)
    window, area, region, index = _view(context)
    space = area.spaces.active
    settings = context.scene.tool_settings
    tool = window.workspace.tools.from_space_view3d_mode('OBJECT', create=False)
    slot = context.scene.transform_orientation_slots[0]
    state = dict(screen=window.screen.name, area_index=index, object=_identity(obj),
                 local=bool(space.local_view),
                 members=[_identity(o) for o in context.view_layer.objects
                          if space.local_view and o.local_view_get(space)],
                 tool=tool.idname if tool else 'builtin.select_box',
                 orientation=slot.type,
                 custom_orientation=slot.custom_orientation.name if slot.custom_orientation else '',
                 settings={name: getattr(settings, name) for name in (
                     'use_transform_data_origin', 'use_transform_pivot_point_align',
                     'use_transform_skip_children', 'transform_pivot_point')},
                 show_axis=obj.show_axis,
                 gizmos={name: getattr(space, name) for name in (
                     'show_gizmo', 'show_gizmo_tool', 'show_gizmo_object_rotate')},
                 show_extras=space.overlay.show_extras, show_overlays=space.overlay.show_overlays)
    # Store before changing workspace settings, so partial failures can unwind.
    context.scene[KEY] = json.dumps(state, ensure_ascii=False)
    _LAST = (context.scene, state)
    try:
        _isolate(context, window, area, region, obj)
        _configure(context, window, area, region, obj)
    except Exception:
        leave(context)
        raise


def _isolate(context, window, area, region, obj):
    space = area.spaces.active
    for other in context.view_layer.objects:
        if other.select_get():
            other.select_set(False)
    obj.hide_set(False)
    if space.local_view:
        obj.local_view_set(space, True)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    with context.temp_override(window=window, area=area, region=region):
        if not space.local_view:
            bpy.ops.view3d.localview(frame_selected=False)
        if not space.local_view:
            raise ValueError('无法进入当前枝条的局部视图')
        for other in context.view_layer.objects:
            other.local_view_set(space, other == obj)


def _configure(context, window, area, region, obj):
    settings = context.scene.tool_settings
    settings.use_transform_data_origin = True
    settings.use_transform_pivot_point_align = False
    settings.use_transform_skip_children = False
    settings.transform_pivot_point = 'MEDIAN_POINT'
    context.scene.transform_orientation_slots[0].type = 'LOCAL'
    obj.show_axis = True
    space = area.spaces.active
    space.show_gizmo = space.show_gizmo_tool = space.show_gizmo_object_rotate = True
    space.overlay.show_extras = True
    space.overlay.show_overlays = True
    with context.temp_override(window=window, area=area, region=region):
        bpy.ops.wm.tool_set_by_id(name='builtin.rotate')


def _restore(context, scene, state):
    for name, value in state['settings'].items():
        setattr(scene.tool_settings, name, value)
    slot = scene.transform_orientation_slots[0]
    try:
        slot.type = state.get('custom_orientation') or state['orientation']
    except TypeError:
        slot.type = 'GLOBAL'
    obj = _find(scene, state['object'])
    if obj:
        obj.show_axis = state['show_axis']
    try:
        window, area, region, _ = _view(context, state)
    except ValueError:
        return  # The user closed that viewport; scene settings are restored.
    space = area.spaces.active
    with context.temp_override(window=window, area=area, region=region):
        if not state['local'] and space.local_view:
            bpy.ops.view3d.localview(frame_selected=False)
        elif state['local']:
            members = [o for o in context.view_layer.objects
                       if any(_matches(o, item) for item in state['members'])]
            if not space.local_view and members:
                selected = [o for o in context.view_layer.objects if o.select_get()]
                for other in selected:
                    other.select_set(False)
                for other in members:
                    other.select_set(True)
                bpy.ops.view3d.localview(frame_selected=False)
                for other in members:
                    other.select_set(False)
                for other in selected:
                    other.select_set(True)
            if space.local_view:
                for other in context.view_layer.objects:
                    other.local_view_set(space, other in members)
        for name, value in state['gizmos'].items():
            setattr(space, name, value)
        space.overlay.show_extras = state['show_extras']
        space.overlay.show_overlays = state.get('show_overlays', True)
        try:
            bpy.ops.wm.tool_set_by_id(name=state['tool'])
        except (RuntimeError, TypeError):
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')


def leave(context):
    global _LAST
    state = _read(context.scene)
    if state:
        _restore(context, context.scene, state)
        del context.scene[KEY]
    _LAST = None


def sync(context):
    """Reconcile non-undoable tool state after Undo/Redo and file reopening."""
    global _LAST
    state = _read(context.scene)
    if state:
        obj = _find(context.scene, state['object'])
        if obj is None:
            leave(context)
            raise ValueError('编辑对象缺失或身份重复，已恢复进入前的工具设置')
        if _LAST and _LAST[0] == context.scene and _LAST[1] == state:
            return
        window, area, region, _ = _view(context, state)
        _isolate(context, window, area, region, obj)
        _configure(context, window, area, region, obj)
        _LAST = (context.scene, state)
    elif _LAST:
        scene, previous = _LAST
        _LAST = None
        try:
            if scene == context.scene:
                _restore(context, scene, previous)
        except ReferenceError:
            pass


def stop(context):
    if getattr(context, 'scene', None):
        leave(context)
