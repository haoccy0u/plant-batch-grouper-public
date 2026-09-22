# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Persistent world-space baselines for Blender's manual origin editing.

A baseline is a private, fake-user Mesh datablock, never a scene object.  Its
original world matrix and material references survive saving/reopening a blend.
Local coordinates are allowed to change: validation compares the resulting
world positions and corner normals by exact element correspondence instead.
"""
import json
import math
import uuid

import bpy
from mathutils import Matrix

from . import pivot_transform as transform


VERSION = 1
_TOKEN = '_PBG_PivotBaselineToken'
_META = '_PBG_PivotBaselineMetadata'
_MATRIX = '_PBG_PivotBaselineWorldMatrix'
_MATERIAL_PREFIX = '_PBG_PivotBaselineMaterial_'


def _resolve(record):
    if (not isinstance(record, dict) or record.get('version') != VERSION
            or not isinstance(record.get('token'), str) or not record['token']):
        raise ValueError('手动编辑基准记录无效，请重新建立基准')
    token = record['token']
    matches = [mesh for mesh in bpy.data.meshes if mesh.get(_TOKEN) == token]
    if len(matches) != 1:
        raise ValueError('手动编辑基准缺失或标识重复，请重新建立基准')
    mesh = matches[0]
    try:
        metadata = json.loads(mesh[_META])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('手动编辑基准数据无法读取，请重新建立基准') from exc
    if (not isinstance(metadata, dict) or metadata.get('version') != VERSION or metadata.get('token') != token
            or not isinstance(metadata.get('material_slots'), list)):
        raise ValueError('手动编辑基准数据不完整，请重新建立基准')
    return mesh, metadata


def _read_matrix(mesh):
    try:
        values = list(mesh[_MATRIX])
        if len(values) != 16 or not all(math.isfinite(float(value)) for value in values):
            raise ValueError('invalid matrix')
        matrix = Matrix([values[i:i + 4] for i in range(0, 16, 4)])
        if transform._component_error(matrix[3], (0.0, 0.0, 0.0, 1.0)) > transform.BASIS_TOLERANCE:
            raise ValueError('not affine')
        matrix.inverted()
        return matrix
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('基准世界矩阵无效，请重新建立基准') from exc


def snapshot_matrix(record):
    """Read the persisted original world matrix without depending on names."""
    mesh, _metadata = _resolve(record)
    return _read_matrix(mesh)


def _check_materials(obj, snapshot, metadata):
    slots = metadata['material_slots']
    if len(slots) != len(obj.material_slots):
        raise ValueError('材质槽数量与手动编辑前不同，当前改动已保留')
    for index, (slot, saved) in enumerate(zip(obj.material_slots, slots)):
        if not isinstance(saved, dict) or saved.get('link') not in {'DATA', 'OBJECT'}:
            raise ValueError('基准材质记录损坏，请重新建立基准')
        if saved.get('empty') is True:
            material = None
        elif saved.get('empty') is False:
            material = snapshot.get(_MATERIAL_PREFIX + str(index))
            if material is None:
                raise ValueError('基准材质引用已丢失，请重新建立基准')
        else:
            raise ValueError('基准材质记录不完整，请重新建立基准')
        if slot.link != saved['link'] or slot.material != material:
            raise ValueError('材质引用与手动编辑前不同，当前改动已保留')


def _compare_world(obj, snapshot, old_matrix):
    mesh = obj.data
    if (transform._static_mesh_data(mesh) != transform._static_mesh_data(snapshot)
            or tuple(mesh.materials) != tuple(snapshot.materials)):
        raise ValueError('拓扑、图层、UV、颜色、权重或材质与基准不同；当前改动已保留，请重新建立基准')
    actual = obj.matrix_world.copy()
    position_error = 0.0
    for index, (vertex, previous) in enumerate(zip(mesh.vertices, snapshot.vertices)):
        error = transform._component_error(tuple(actual @ vertex.co), tuple(old_matrix @ previous.co))
        position_error = max(position_error, error)
        if error > transform.POSITION_TOLERANCE:
            raise ValueError('顶点 %d 的世界位置与基准误差 %.9g 超过 %.9g；'
                             '世界形状未保持或超出精度，当前改动已保留'
                             % (index, error, transform.POSITION_TOLERANCE))
    normals, old_normals = mesh.corner_normals, snapshot.corner_normals
    if len(normals) != len(old_normals) or len(normals) != len(mesh.loops):
        raise ValueError('角点法线数量与基准不同，当前改动已保留')
    normal_matrix = actual.to_3x3().inverted().transposed()
    old_normal_matrix = old_matrix.to_3x3().inverted().transposed()
    normal_error = 0.0
    for index, (normal, previous) in enumerate(zip(normals, old_normals)):
        current = transform._unit(normal_matrix @ normal.vector, '当前世界角点法线')
        expected = transform._unit(old_normal_matrix @ previous.vector, '基准世界角点法线')
        error = transform._component_error(current, expected)
        normal_error = max(normal_error, error)
        if error > transform.NORMAL_TOLERANCE:
            raise ValueError('角点 %d 的世界法线与基准误差 %.9g 超过 %.9g；当前改动已保留'
                             % (index, error, transform.NORMAL_TOLERANCE))
    return {
        'vertices': len(mesh.vertices), 'edges': len(mesh.edges),
        'faces': len(mesh.polygons), 'corners': len(mesh.loops),
        'max_position_error': position_error,
        'max_normal_error': normal_error,
        'position_tolerance': transform.POSITION_TOLERANCE,
        'normal_tolerance': transform.NORMAL_TOLERANCE,
        'world_baseline_validated': True,
    }


def capture(obj):
    """Create an internal snapshot; return a JSON-safe persistent reference.

    This does not change obj or discard an earlier baseline.  A caller replacing
    a baseline should persist the new reference first, then drop the old one.
    """
    reasons = transform.check_object(obj)
    if reasons:
        raise ValueError('；'.join(reasons))
    snapshot = None
    try:
        token = uuid.uuid4().hex
        snapshot = obj.data.copy()
        snapshot.name = '.PBG_OriginBaseline_' + token
        snapshot.use_fake_user = True
        snapshot[_TOKEN] = token
        snapshot[_MATRIX] = [float(value) for row in obj.matrix_world for value in row]
        slots = []
        for index, slot in enumerate(obj.material_slots):
            slots.append({'link': slot.link, 'empty': slot.material is None})
            if slot.material is not None:
                # ID pointers, unlike names/addresses, survive saving a blend
                # and follow material renaming without invalidating a baseline.
                snapshot[_MATERIAL_PREFIX + str(index)] = slot.material
        metadata = {'version': VERSION, 'token': token, 'material_slots': slots}
        snapshot[_META] = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        _check_materials(obj, snapshot, metadata)
        _compare_world(obj, snapshot, _read_matrix(snapshot))
        return {'version': VERSION, 'token': token, 'mesh_name': snapshot.name}
    except Exception:
        if snapshot is not None:
            bpy.data.meshes.remove(snapshot)
        raise


def validate(obj, record):
    """Prove current world geometry/data matches a saved manual-edit baseline.

    No local-coordinate digest or old object transform equality is required.
    Failure only raises: the user's current geometry and transforms stay intact.
    """
    reasons = transform.check_object(obj)
    if reasons:
        raise ValueError('；'.join(reasons))
    snapshot, metadata = _resolve(record)
    if snapshot == obj.data:
        raise ValueError('基准网格被用作当前模型，请重新建立独立基准')
    _check_materials(obj, snapshot, metadata)
    return _compare_world(obj, snapshot, _read_matrix(snapshot))


def drop(record):
    """Remove only our unreferenced snapshot; harmless when already absent.

    A datablock that somebody has linked to an object or another ID is retained.
    False means absent/ambiguous/in use, and lets the caller report or retry.
    """
    try:
        snapshot, _metadata = _resolve(record)
    except (ValueError, ReferenceError):
        return False
    if snapshot.library or snapshot.override_library or not snapshot.is_editable:
        return False
    if snapshot.users > int(snapshot.use_fake_user):
        return False
    had_fake_user = snapshot.use_fake_user
    snapshot.use_fake_user = False
    try:
        bpy.data.meshes.remove(snapshot, do_unlink=False)
    except Exception:
        snapshot.use_fake_user = had_fake_user
        raise
    return True


def prepare_current(obj, record=None):
    """Bake positive scale while retaining the current origin and full frame.

    This does not infer a stem or choose a new roll angle.  Return ownership and
    rollback rules are the same as pivot_transform.prepare_rebase.  The caller
    must still validate_committed after assigning the returned mesh/matrix.
    An explicit record supports legacy baseline checks. Current manual
    confirmation omits it and accepts the mesh/frame at the instant of Confirm.
    """
    reasons = transform.check_object(obj)
    if reasons:
        raise ValueError('；'.join(reasons))
    baseline_stats = validate(obj, record) if record is not None else None
    current = obj.matrix_world.copy()
    rotation = current.to_3x3()
    x_axis = transform._unit(rotation.col[0], '当前 X 轴')
    z_hint = transform._unit(rotation.col[2], '当前 Z 轴')
    # Remove numerical non-orthogonality only.  The original Z fixes the roll;
    # unlike automatic preparation there is no world-axis auxiliary choice.
    y_axis = transform._unit(z_hint.cross(x_axis), '当前 Y 轴')
    z_axis = transform._unit(x_axis.cross(y_axis), '当前 Z 轴')
    basis = Matrix((x_axis, y_axis, z_axis)).transposed()
    transform._validate_basis(basis)
    root = current.translation.copy()
    # This point only carries a direction into prepare_rebase.  A sufficiently
    # long carrier avoids root + unit-X rounding back to root at large offsets.
    carrier_length = max(1.0, *(abs(value) for value in root)) * 4.0
    tip = root + x_axis * carrier_length
    prepared = transform.prepare_rebase(obj, root, tip, basis_world=basis)
    if baseline_stats is not None:
        prepared['stats']['baseline_before'] = baseline_stats
    return prepared


def current_frame_is_unit(obj):
    """Whether manual confirmation can retain the exact current mesh/frame.

    The caller still runs check_object, including editable/static mesh and
    positive-scale checks. This is only a no-op normalization decision; it
    does not compare with any historical editing baseline.
    """
    if transform._component_error(obj.scale, (1.0, 1.0, 1.0)) > transform.BASIS_TOLERANCE:
        return False
    try:
        transform._validate_basis(obj.matrix_world.to_3x3())
    except ValueError:
        return False
    return True
