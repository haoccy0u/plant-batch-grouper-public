# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Persistent origin preparation: native manual editing and completed archives."""
import copy
import json
import uuid

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector

from . import pivot_geometry as geometry, pivot_transform as transform
from . import pivot_baseline as baseline, pivot_native as native, pivot_sources

KEY = 'pbg_pivot_prep_json'
UID = 'PBG_PivotID'
COLLECTION_UID = 'PBG_PivotCollectionID'
DIRTY = set()
RUNTIME = {}
_CACHE = {}
_PLAN_CACHE = {}


def load(scene):
    raw = scene.get(KEY, '')
    if not raw:
        return {'version': 2, 'entries': {}}
    cached = _CACHE.get(scene.as_pointer())
    if cached and cached[0] == raw:
        return copy.deepcopy(cached[1])
    data = json.loads(raw)
    if data.get('version') not in (1, 2) or not isinstance(data.get('entries'), dict):
        raise ValueError('原点准备记录版本无法读取，请保留工程并反馈')
    # Migration is lazy and has no object, visibility or collection side effects.
    data['version'] = 2
    _CACHE[scene.as_pointer()] = (raw, data)
    return copy.deepcopy(data)


def save(scene, data):
    data['version'] = 2
    scene[KEY] = json.dumps(data, ensure_ascii=False, allow_nan=False)
    _CACHE.pop(scene.as_pointer(), None)


def _plan(scene):
    raw = scene.get('pbg_plan_json', '{}')
    cached = _PLAN_CACHE.get(scene.as_pointer())
    if cached and cached[0] == raw:
        return cached[1]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = {}
    _PLAN_CACHE[scene.as_pointer()] = (raw, parsed)
    return parsed


def output_collection(scene):
    return (scene.pbg_pivot_settings.collection
            or getattr(getattr(scene, 'pbg_settings', None), 'output', None))


def _tree(collection):
    result = {collection}
    for child in collection.children:
        result.update(_tree(child))
    return result


def collections(context, *, create=False):
    scene, p = context.scene, context.scene.pbg_pivot_settings
    source = output_collection(scene)
    if source is None:
        raise ValueError('请指定待处理集合')
    if source not in _tree(scene.collection):
        raise ValueError('待处理集合不在当前场景中')
    if source.library or not source.is_editable:
        raise ValueError('待处理集合必须可编辑')
    name = p.completed_name.strip()
    destination = p.completed or (bpy.data.collections.get(name) if name else None)
    if destination:
        if (source == destination or destination in _tree(source)
                or source in _tree(destination)):
            raise ValueError('待处理集合与完成集合不能相同或互相嵌套')
        if destination.library or not destination.is_editable:
            raise ValueError('完成集合必须可编辑')
        if any(other != scene and destination in _tree(other.collection) for other in bpy.data.scenes):
            raise ValueError('完成集合与其他场景共享，请选择独立集合')
        if create and destination not in _tree(scene.collection):
            scene.collection.children.link(destination)
    elif create:
        if not name:
            raise ValueError('请输入完成集合名称或选择已有集合')
        destination = bpy.data.collections.new(name)
        scene.collection.children.link(destination)
    elif not name:
        raise ValueError('请输入完成集合名称或选择已有集合')
    if destination and create:
        if not destination.get(COLLECTION_UID):
            destination[COLLECTION_UID] = uuid.uuid4().hex
        p.completed = destination
    return source, destination


def protected(scene, obj):
    completed = {i for row in _plan(scene).get('completed', []) for i in row.get('sources', [])}
    return obj.get('PBG_UID') in completed and not obj.get('PBG_Output')


def object_id(scene, obj, create=False):
    key = obj.get(UID)
    if key and any(other != obj and other.get(UID) == key for other in scene.objects):
        raise ValueError('对象复制了其他枝条的记录，请点击“为副本建立独立记录”')
    if not key and create:
        key = uuid.uuid4().hex
        obj[UID] = key
    return key


def entry_for(scene, obj):
    return load(scene)['entries'].get(obj.get(UID)) if obj and obj.type == 'MESH' else None


def is_completed(scene, obj, entries=None):
    entry = (entries if entries is not None else load(scene)['entries']).get(obj.get(UID), {})
    if entry.get('state') != 'DONE' or not entry.get('completed_collection'):
        return False
    duplicates = [other for other in scene.objects if other.get(UID) == obj.get(UID)]
    if len(duplicates) > 1:
        # A copied object inherits ID properties. The recorded archive link and
        # last committed name distinguish it until the user gives it a new ID.
        archived = [other for other in duplicates if any(
            c.get(COLLECTION_UID) == entry['completed_collection'] for c in other.users_collection)]
        if len(archived) == 1:
            return obj == archived[0]
        return obj.name == entry.get('object_name')
    return True


def targets(context):
    source, _ = collections(context)
    entries = load(context.scene)['entries']
    return sorted((o for o in source.all_objects if o.type == 'MESH'
                   and not protected(context.scene, o)
                   and not is_completed(context.scene, o, entries)), key=lambda o: o.name)


def fresh(obj, entry):
    return (entry.get('matrix_digest') == transform.matrix_digest(obj)
            and entry.get('geometry_digest') == transform.geometry_digest(obj))


def _matrix(matrix):
    return [list(row) for row in matrix]


def _frame(obj):
    basis = obj.matrix_world.to_3x3().normalized()
    return basis


def _compute_one(context, obj, previous=None):
    checks = transform.check_object(obj)
    if protected(context.scene, obj):
        checks.append('来源备份不参与原点准备，请选择合并结果')
    entry = dict(object_name=obj.name, state='REVIEW', root_reliable=False,
                 direction_reliable=False, up=context.scene.pbg_pivot_settings.up)
    if checks:
        entry.update(state='BLOCKED', reason='；'.join(checks), blocked_reasons=checks)
        return entry
    if (previous and previous.get('state') == 'READY'
            and previous.get('geometry_digest') == transform.geometry_digest(obj)
            and (previous.get('manual_confirmed') or previous.get('up') == entry['up'])
            and previous.get('root_local') is not None and previous.get('tip_local') is not None):
        # Valid saved polarity is evidence, including previously confirmed
        # downward/horizontal branches. Do not reinterpret it using world up.
        root, tip = world_endpoints(obj, previous)
        transform.make_basis(root, tip)
        entry.update(copy.deepcopy(previous))
        entry.update(root_reliable=True, direction_reliable=True,
                     reason='沿用有效的已保存根点与方向', matrix_digest=transform.matrix_digest(obj))
        return entry
    evidence = pivot_sources.resolve_stems(context, obj, up=int(entry['up']))
    result = geometry.analyze_stems(evidence['records'], up=int(entry['up']),
                                    trusted=evidence.get('trusted', False))
    inverse = obj.matrix_world.inverted()
    for key in ('root', 'tip'):
        value = result.get(key)
        entry[key + '_local'] = list(inverse @ Vector(value)) if value is not None else None
    entry.update({key: result.get(key, False if key.endswith('_reliable') else '')
                  for key in ('root_reliable', 'direction_reliable', 'root_reason', 'direction_reason')})
    entry.update(reason=result['reason'], provenance=evidence.get('provenance', ''),
                 stem_ids=evidence.get('stem_ids', []), source_group=obj.get('PBG_Group'),
                 geometry_digest=transform.geometry_digest(obj), matrix_digest=transform.matrix_digest(obj))
    if entry['root_reliable'] and entry['direction_reliable']:
        entry['state'] = 'READY'
    return entry


def world_endpoints(obj, entry):
    return tuple(obj.matrix_world @ Vector(entry[key]) if entry.get(key) is not None else None
                 for key in ('root_local', 'tip_local'))


def _layer_paths(context, collection, show):
    def visit(layer):
        found = layer.collection == collection
        for child in layer.children:
            found = visit(child) or found
        if found and show:
            layer.exclude = False
            layer.hide_viewport = False
            layer.collection.hide_viewport = False
        elif layer.collection == collection:
            layer.hide_viewport = True
        return found
    return visit(context.view_layer.layer_collection)


def enforce_completed(context):
    if not hasattr(context.scene, 'pbg_pivot_settings'):
        return
    entries = load(context.scene)['entries']
    hidden = not context.scene.pbg_pivot_settings.show_completed
    for obj in context.view_layer.objects:
        if is_completed(context.scene, obj, entries):
            obj.hide_set(hidden)
    if context.scene.pbg_pivot_settings.completed:
        _layer_paths(context, context.scene.pbg_pivot_settings.completed, not hidden)


def show_completed(context, visible):
    native.leave(context)
    context.scene.pbg_pivot_settings.show_completed = visible
    entries = load(context.scene)['entries']
    for obj in context.scene.objects:
        if is_completed(context.scene, obj, entries):
            for collection in obj.users_collection:
                _layer_paths(context, collection, visible)
    enforce_completed(context)
    return '完成集合已显示' if visible else '完成集合已隐藏'


def _show_pending(context):
    source = output_collection(context.scene)
    if source:
        _layer_paths(context, source, True)
        for collection in _tree(source):
            _layer_paths(context, collection, True)
        entries = load(context.scene)['entries']
        for obj in source.all_objects:
            if (obj.type == 'MESH' and obj.name in context.view_layer.objects
                    and not protected(context.scene, obj) and not is_completed(context.scene, obj, entries)):
                obj.hide_set(False)
    context.scene.pbg_pivot_settings.show_completed = False
    enforce_completed(context)


def _safe_pending(context):
    try:
        _show_pending(context)
        return ''
    except Exception as exc:
        # Geometry/archives have already committed. A viewport error must not
        # cancel their operator (and lose its dedicated Undo step).
        return '；剩余视图恢复未完成：' + str(exc)


def remaining(context):
    native.leave(context)
    _show_pending(context)
    return '已返回剩余枝条；原点调整已保留，未确认枝条仍待处理'


def _check_archive(context, obj):
    problems = transform.check_object(obj)
    if protected(context.scene, obj):
        problems.append('来源备份不参与原点准备')
    if any(other != context.scene and obj.name in other.objects for other in bpy.data.scenes):
        problems.append('枝条与其他场景共享，不能移动集合')
    if any(c.library or not c.is_editable for c in obj.users_collection):
        problems.append('枝条存在只读集合关联')
    if problems:
        raise ValueError('；'.join(problems))


def _snapshot(obj):
    return dict(mesh=obj.data, matrix=obj.matrix_world.copy(),
                channels={name: (getattr(obj, name) if name == 'rotation_mode' else tuple(getattr(obj, name)))
                          for name in ('rotation_mode', 'location', 'rotation_euler', 'rotation_quaternion',
                                       'rotation_axis_angle', 'scale')},
                slots=[(slot.link, slot.material) for slot in obj.material_slots],
                source=obj.get(pivot_sources.KEY), collections=list(obj.users_collection),
                hidden=obj.hide_get(), selected=obj.select_get())


def _rollback(context, obj, old, prepared):
    if prepared:
        obj.data = old['mesh']
        for name, value in old['channels'].items():
            setattr(obj, name, value)
        for slot, (link, material) in zip(obj.material_slots, old['slots']):
            slot.link, slot.material = link, material
    if old['source'] is None:
        if pivot_sources.KEY in obj:
            del obj[pivot_sources.KEY]
    else:
        obj[pivot_sources.KEY] = old['source']
    for collection in old['collections']:
        if obj.name not in collection.objects:
            collection.objects.link(obj)
    for collection in list(obj.users_collection):
        if collection not in old['collections']:
            collection.objects.unlink(obj)
    context.view_layer.update()
    obj.hide_set(old['hidden'])
    obj.select_set(old['selected'])
    if prepared and prepared['mesh'].users == 0:
        bpy.data.meshes.remove(prepared['mesh'])


def _commit_mesh(context, obj, prepared, old, source_matrix=None):
    obj.data = prepared['mesh']
    obj.matrix_world = prepared['matrix']
    context.view_layer.update()
    prepared['stats'].update(transform.validate_committed(obj, old['mesh'], old['matrix'], prepared['matrix']))
    if old['slots'] != [(slot.link, slot.material) for slot in obj.material_slots]:
        raise ValueError('对象材质引用在换入网格后发生变化')
    pivot_sources.rebase_source(obj, obj.matrix_world.inverted() @ (source_matrix or old['matrix']))


def _archive(context, obj, destination, entry):
    if obj.name not in destination.objects:
        destination.objects.link(obj)
    for collection in list(obj.users_collection):
        if collection != destination:
            collection.objects.unlink(obj)
    context.view_layer.update()
    obj.hide_set(True)
    obj.select_set(False)
    entry.update(state='DONE', completed_collection=destination[COLLECTION_UID],
                 completed_collection_name=destination.name, object_name=obj.name,
                 geometry_digest=transform.geometry_digest(obj), matrix_digest=transform.matrix_digest(obj),
                 reason='原点与本地 +X 已确认，已移入完成集合')
    # These are frame markers, not an inferred botanical tip. Old coordinates
    # must not survive alongside the new mesh digest after a rebase.
    entry.update(root_local=[0.0, 0.0, 0.0], tip_local=[1.0, 0.0, 0.0],
                 basis_local=_matrix(Matrix.Identity(3)), endpoint_kind='FRAME',
                 root_reliable=True, direction_reliable=True)
    entry.pop('last_error', None)
    entry.pop('baseline_error', None)


def auto_archive(context):
    if context.mode != 'OBJECT':
        raise ValueError('请先切换到物体模式')
    source, destination = collections(context, create=True)
    native.leave(context)
    p = context.scene.pbg_pivot_settings
    if not p.is_property_set('up'):
        p.up = getattr(getattr(context.scene, 'pbg_settings', None), 'up', '2')
    objects = targets(context)
    data = load(context.scene)
    success, pending, failures = 0, 0, []
    for obj in objects:
        prepared, old, key, previous = None, None, None, None
        try:
            _check_archive(context, obj)
            key = object_id(context.scene, obj, create=True)
            previous = copy.deepcopy(data['entries'].get(key))
            if previous and previous.get('manual_started'):
                pending += 1
                continue
            # Old valid APPLIED entries are archived without touching mesh/matrix.
            if previous and previous.get('state') == 'APPLIED':
                if not fresh(obj, previous):
                    entry = copy.deepcopy(previous)
                    entry.update(reason='旧应用记录与当前数据不同，请单独显示并确认现有原点',
                                 needs_manual=True)
                    data['entries'][key] = entry
                    pending += 1
                    continue
                entry = copy.deepcopy(previous)
            else:
                entry = _compute_one(context, obj, previous)
            entry['object_id'] = key
            if entry.get('state') not in ('READY', 'APPLIED'):
                data['entries'][key] = entry
                pending += 1
                continue
            old = _snapshot(obj)
            if entry['state'] == 'READY':
                root, tip = world_endpoints(obj, entry)
                prepared = transform.prepare_rebase(obj, root, tip)
                _commit_mesh(context, obj, prepared, old)
                entry['validation'] = prepared['stats']
            _archive(context, obj, destination, entry)
            data['entries'][key] = entry
            save(context.scene, data)
            success += 1
        except Exception as exc:
            if old:
                _rollback(context, obj, old, prepared)
            if key:
                data['entries'][key] = previous or dict(object_id=key, state='REVIEW', object_name=obj.name)
                data['entries'][key]['last_error'] = str(exc)
            failures.append(obj.name + '：' + str(exc))
    save(context.scene, data)
    view_message = _safe_pending(context)
    p.last_errors = '\n'.join(failures)
    return f'已完成并隐藏 {success} 根，待手动处理 {pending} 根，失败 {len(failures)} 根' + view_message


def _current(context, *, reset_identity=False):
    if context.mode != 'OBJECT':
        raise ValueError('请先切换到物体模式')
    source, _ = collections(context)
    obj = context.active_object
    if not obj or obj.type != 'MESH' or obj.name not in source.all_objects:
        raise ValueError('请在视口选择待处理集合中的一根枝条')
    if is_completed(context.scene, obj):
        raise ValueError('当前枝条已完成')
    if not reset_identity:
        object_id(context.scene, obj)
    _check_archive(context, obj)
    return obj


def _ensure_private(context, obj):
    if obj.data.users <= 1:
        return
    old = _snapshot(obj)
    original_digest = transform.geometry_digest(obj)
    prepared = {'mesh': obj.data.copy(), 'matrix': obj.matrix_world.copy()}
    try:
        obj.data = prepared['mesh']
        context.view_layer.update()
        if (transform.geometry_digest(obj) != original_digest
                or old['slots'] != [(slot.link, slot.material) for slot in obj.material_slots]):
            raise ValueError('独立化网格后数据不一致，已保留原网格')
    except Exception:
        _rollback(context, obj, old, prepared)
        raise


def edit_origin(context):
    obj = _current(context)
    key = object_id(context.scene, obj, create=True)
    data = load(context.scene)
    previous = copy.deepcopy(data['entries'].get(key))
    if previous and previous.get('manual_started'):
        entry = data['entries'][key]
        entry.pop('baseline_error', None)
        save(context.scene, data)
        _ensure_private(context, obj)
        _show_pending(context)
        native.enter(context, obj)
        return '继续编辑现有原点与坐标轴；已有人工调整保持不变'
    old, prepared = _snapshot(obj), None
    try:
        # Never reseed previously applied or explicitly adjusted old endpoints.
        keep_existing = previous and (previous.get('state') == 'APPLIED' or previous.get('manual_endpoints'))
        try:
            entry = copy.deepcopy(previous) if keep_existing else _compute_one(context, obj, previous)
        except Exception as exc:
            entry = dict(reason='自动起点无法计算，可直接编辑原点：' + str(exc),
                         root_reliable=False, direction_reliable=False)
        root = obj.matrix_world.translation.copy()
        basis = _frame(obj)
        if not keep_existing:
            proposed_root, proposed_tip = world_endpoints(obj, entry)
            if entry.get('root_reliable') and proposed_root is not None:
                root = proposed_root
            if entry.get('direction_reliable') and proposed_root is not None and proposed_tip is not None:
                basis = transform.make_basis(proposed_root, proposed_tip)
            if entry.get('root_reliable') or entry.get('direction_reliable'):
                try:
                    carrier = max(1.0, *(abs(value) for value in root)) * 4.0
                    prepared = transform.prepare_rebase(obj, root, root + basis.col[0] * carrier, basis)
                    _commit_mesh(context, obj, prepared, old)
                except Exception as exc:
                    _rollback(context, obj, old, prepared)
                    prepared = None
                    entry['reason'] = '自动起点未通过校验，保留当前状态供手动编辑：' + str(exc)
        if prepared is None and obj.data.users > 1:
            original_digest = transform.geometry_digest(obj)
            prepared = {'mesh': obj.data.copy(), 'matrix': obj.matrix_world.copy()}
            obj.data = prepared['mesh']
            context.view_layer.update()
            if (transform.geometry_digest(obj) != original_digest
                    or old['slots'] != [(slot.link, slot.material) for slot in obj.material_slots]):
                raise ValueError('独立化网格后数据不一致，已保留原网格')
        entry.update(object_id=key, object_name=obj.name, state='MANUAL', manual_started=True,
                     source_matrix=_matrix(obj.matrix_world), confirmation_policy='CURRENT_STATE')
        entry.pop('baseline_error', None)
        data['entries'][key] = entry
        save(context.scene, data)
        _show_pending(context)
        native.enter(context, obj)
    except Exception:
        _rollback(context, obj, old, prepared)
        if previous:
            data['entries'][key] = previous
        else:
            data['entries'].pop(key, None)
        save(context.scene, data)
        raise
    return '已启用仅影响原点；旋转本地 +X 对齐生长方向，需要时直接移动原点'


def reset_identity(context):
    """Detach a copied preparation ID, without imposing a mesh-baseline step."""
    obj = _current(context, reset_identity=True)
    data = load(context.scene)
    key = obj.get(UID)
    duplicate = key and any(o != obj and o.get(UID) == key for o in context.scene.objects)
    if duplicate:
        key = None
    if not key:
        key = uuid.uuid4().hex
        obj[UID] = key
    entry = copy.deepcopy(data['entries'].get(key, {}))
    entry.update(object_id=key, object_name=obj.name, state='MANUAL', manual_started=True,
                 confirmation_policy='CURRENT_STATE', reason='已建立独立记录，保留当前模型、原点与方向')
    entry.setdefault('source_matrix', _matrix(obj.matrix_world))
    entry.pop('baseline_error', None)
    entry.pop('last_error', None)
    data['entries'][key] = entry
    save(context.scene, data)
    return entry['reason']


def confirm(context):
    obj = _current(context)
    key = object_id(context.scene, obj, create=True)
    data = load(context.scene)
    entry = data['entries'].get(key) or dict(object_id=key, object_name=obj.name, state='MANUAL')
    _, destination = collections(context, create=True)
    old, previous, prepared = _snapshot(obj), copy.deepcopy(entry), None
    try:
        source_matrix = Matrix(entry['source_matrix']) if entry.get('source_matrix') else old['matrix']
        # Clicking Confirm accepts the user's CURRENT mesh, origin and axes.
        # An old edit-session baseline is neither a gate nor an auto-restore.
        # A unit frame needs no second rebase: avoid needless float round trips.
        if baseline.current_frame_is_unit(obj):
            pivot_sources.rebase_source(obj, obj.matrix_world.inverted() @ source_matrix)
            entry['validation'] = dict(no_transform_needed=True, current_state_accepted=True,
                                      vertices=len(obj.data.vertices), faces=len(obj.data.polygons))
        else:
            prepared = baseline.prepare_current(obj)
            _commit_mesh(context, obj, prepared, old, source_matrix)
            entry['validation'] = prepared['stats']
            entry['validation']['current_state_accepted'] = True
        entry.update(confirmation_policy='CURRENT_STATE', manual_confirmed=True,
                     source_matrix=_matrix(obj.matrix_world))
        _archive(context, obj, destination, entry)
        data['entries'][key] = entry
        save(context.scene, data)
    except Exception as exc:
        _rollback(context, obj, old, prepared)
        previous['last_error'] = str(exc)
        data['entries'][key] = previous
        save(context.scene, data)
        return '确认失败，枝条保持原处：' + str(exc)
    # Old baseline records stay compatible, but no longer gate manual completion.
    p = context.scene.pbg_pivot_settings
    p.last_errors = '\n'.join(line for line in p.last_errors.splitlines()
                              if not line.startswith(obj.name + '：'))
    view_message = ''
    try:
        native.leave(context)
    except Exception as exc:
        view_message = '；编辑工具恢复未完成：' + str(exc)
    view_message += _safe_pending(context)
    return '已采用当前原点和坐标轴，移入完成集合并隐藏' + view_message


@persistent
def changed(scene, depsgraph):
    for update in depsgraph.updates:
        if update.is_updated_geometry or update.is_updated_transform:
            item = update.id
            if isinstance(item, bpy.types.Object) and item.get(UID):
                DIRTY.add(item.original.as_pointer())


@persistent
def clear_runtime(*_args):
    DIRTY.clear()
    RUNTIME.clear()
    _CACHE.clear()
    _PLAN_CACHE.clear()


def tick(context):
    if not getattr(context, 'scene', None) or not hasattr(context.scene, 'pbg_pivot_settings'):
        return
    try:
        native.sync(context)
    except (ValueError, RuntimeError, ReferenceError, TypeError) as exc:
        context.scene.pbg_pivot_settings.status = '原点编辑视图恢复：' + str(exc)
