# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Prepare a private mesh with a new origin/basis, without changing its object.

The caller owns the returned mesh and must either commit it or remove it.  No
object, collection, selection, scene unit, or saved progress is changed here.
"""
import hashlib
import math

import bpy
from mathutils import Matrix, Vector

from .validation import NORMAL_TOLERANCE, POSITION_TOLERANCE


BASIS_TOLERANCE = 1e-6
_MIN_LENGTH = 1e-12
_TRANSFORMED_ATTRIBUTES = {'position', 'custom_normal'}
_TOPOLOGY_ATTRIBUTES = {'.corner_vert', '.corner_edge', '.edge_verts'}
_VIEW_ATTRIBUTE_PREFIXES = ('.select_', '.hide_', '.vs.', '.es.')


def _finite(values):
    return all(math.isfinite(float(value)) for value in values)


def _point(value, label):
    try:
        point = Vector(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(label + '必须是三维坐标') from exc
    if len(point) != 3 or not _finite(point):
        raise ValueError(label + '包含无效坐标')
    return point


def _matrix_values(matrix):
    return tuple(tuple(float(value) for value in row) for row in matrix)


def _unit(vector, label):
    if (not _finite(vector) or not math.isfinite(vector.length)
            or vector.length <= _MIN_LENGTH):
        raise ValueError(label + '无效或长度为零')
    result = vector.normalized()
    if not _finite(result) or abs(result.length - 1.0) > BASIS_TOLERANCE:
        raise ValueError(label + '无法归一化')
    return result


def _component_error(a, b):
    if len(a) != len(b) or not _finite((*a, *b)):
        return math.inf
    return max((abs(x - y) for x, y in zip(a, b)), default=0.0)


def _validate_basis(basis, direction=None):
    if len(basis) != 3 or any(len(row) != 3 for row in basis):
        raise ValueError('目标方向必须是 3 × 3 矩阵')
    if not all(_finite(row) for row in basis):
        raise ValueError('目标方向包含无效数值')
    columns = [basis.col[i] for i in range(3)]
    if (any(abs(axis.length - 1.0) > BASIS_TOLERANCE for axis in columns)
            or any(abs(columns[i].dot(columns[j])) > BASIS_TOLERANCE
                   for i in range(3) for j in range(i))
            or abs(basis.determinant() - 1.0) > BASIS_TOLERANCE):
        raise ValueError('目标方向不是右手正交单位坐标基')
    if direction is not None and _component_error(columns[0], direction) > BASIS_TOLERANCE:
        raise ValueError('目标 +X 与根到尖的方向不一致')


def make_basis(root_world, tip_world, previous=None):
    """Return columns [X, Y, Z], reusing a still-valid prior world basis.

    Roll is deterministic: choose the least parallel auxiliary axis in Z/Y/X
    order, project it to Z, and complete a right-handed orthonormal frame.
    """
    root = _point(root_world, '根点')
    tip = _point(tip_world, '尖点')
    direction = _unit(tip - root, '根尖方向')
    if previous is not None:
        try:
            saved = Matrix(previous)
            _validate_basis(saved, direction)
            return saved.copy()
        except (TypeError, ValueError, IndexError):
            pass
    helpers = (Vector((0.0, 0.0, 1.0)), Vector((0.0, 1.0, 0.0)),
               Vector((1.0, 0.0, 0.0)))
    helper = min(helpers, key=lambda axis: abs(axis.dot(direction)))
    z_axis = _unit(helper - helper.dot(direction) * direction, '辅助 Z 轴')
    y_axis = _unit(z_axis.cross(direction), '辅助 Y 轴')
    z_axis = _unit(direction.cross(y_axis), '辅助 Z 轴')
    basis = Matrix((direction, y_axis, z_axis)).transposed()
    _validate_basis(basis, direction)
    return basis


def _material_key(material):
    # Memory addresses and session_uid do not survive opening a .blend again.
    # Object/mesh names are deliberately absent from both public digests.
    if material is None:
        return None
    library = material.library
    return (library.filepath if library else '', material.name_full)


def _freeze(value):
    if isinstance(value, (bool, int, float, str, bytes)) or value is None:
        return value
    return tuple(_freeze(item) for item in value)


def _attribute_values(attribute):
    if not len(attribute.data):
        return ()
    first = attribute.data[0]
    for field in ('value', 'vector', 'color'):
        if hasattr(first, field):
            return tuple(_freeze(getattr(item, field)) for item in attribute.data)
    raise ValueError('无法安全读取网格属性：' + attribute.name)


def _layer_state(mesh):
    colors = mesh.color_attributes
    active_attribute = getattr(mesh.attributes, 'active', None)
    return {
        'uv_active_index': mesh.uv_layers.active_index,
        'uv_flags': tuple((layer.name, layer.active_render, layer.active_clone)
                          for layer in mesh.uv_layers),
        'color_active_index': getattr(colors, 'active_color_index', -1),
        'color_render_index': getattr(colors, 'render_color_index', -1),
        'color_active_name': getattr(colors, 'active_color_name', ''),
        'attribute_active': active_attribute.name if active_attribute else None,
    }


def _static_mesh_data(mesh):
    """Data that rebasing must leave exact, in original element order."""
    skipped = _TRANSFORMED_ATTRIBUTES | _TOPOLOGY_ATTRIBUTES
    return {
        'counts': tuple(len(getattr(mesh, kind))
                        for kind in ('vertices', 'edges', 'polygons', 'loops')),
        'edges': tuple((tuple(edge.vertices), edge.use_seam, edge.use_edge_sharp)
                       for edge in mesh.edges),
        'loops': tuple((loop.vertex_index, loop.edge_index) for loop in mesh.loops),
        'faces': tuple((tuple(face.vertices), face.loop_start, face.loop_total,
                        face.material_index, face.use_smooth) for face in mesh.polygons),
        'weights': tuple(tuple((group.group, group.weight) for group in vertex.groups)
                         for vertex in mesh.vertices),
        'uv': tuple((layer.name, tuple(tuple(item.uv) for item in layer.data))
                    for layer in mesh.uv_layers),
        'colors': tuple((layer.name, layer.domain, layer.data_type,
                         tuple(tuple(item.color) for item in layer.data))
                        for layer in mesh.color_attributes),
        'attributes': tuple(sorted((attribute.name, attribute.domain, attribute.data_type,
                                    _attribute_values(attribute))
                                   for attribute in mesh.attributes
                                   if attribute.name not in skipped
                                   and not attribute.name.startswith(_VIEW_ATTRIBUTE_PREFIXES))),
        'materials': tuple(_material_key(material) for material in mesh.materials),
        'layers': _layer_state(mesh),
    }


def geometry_digest(obj):
    """Hash local geometry and stored data; ignore object name and visibility.

    Layer names and persistent material references are part of the data.  A
    material rename conservatively invalidates the digest, unlike object names.
    """
    mesh = obj.data
    payload = (
        _static_mesh_data(mesh),
        tuple(tuple(vertex.co) for vertex in mesh.vertices),
        tuple(tuple(normal.vector) for normal in mesh.corner_normals),
        tuple((slot.link, _material_key(slot.material)) for slot in obj.material_slots),
    )
    return hashlib.sha256(repr(payload).encode('utf-8')).hexdigest()


def matrix_digest(obj):
    """Hash the effective world transform without any selection/name state."""
    return hashlib.sha256(repr(_matrix_values(obj.matrix_world)).encode('ascii')).hexdigest()


def check_object(obj):
    """Return blocking reasons without changing the object or shared mesh."""
    reasons = []
    try:
        if obj is None or obj.type != 'MESH' or obj.data is None:
            return ['只支持网格对象']
        mesh = obj.data
        if (obj.library or mesh.library or obj.override_library or mesh.override_library
                or not getattr(obj, 'is_editable', True)
                or not getattr(mesh, 'is_editable', True)):
            reasons.append('只支持可编辑的本地网格，不处理链接或库覆盖数据')
        if obj.mode != 'OBJECT' or mesh.is_editmode:
            reasons.append('请先退出编辑或雕刻模式')
        if obj.parent is not None or len(obj.children):
            reasons.append('对象存在父级或子对象，请先处理父子关系')
        if len(obj.constraints):
            reasons.append('对象存在约束')
        if len(obj.modifiers):
            reasons.append('对象存在修改器，请先自行处理')
        if obj.animation_data is not None or mesh.animation_data is not None:
            reasons.append('对象或网格存在动画或驱动')
        if mesh.shape_keys is not None:
            reasons.append('网格存在形态键')
        if not len(mesh.vertices) or not len(mesh.polygons):
            reasons.append('网格没有可处理的面')
        matrix = obj.matrix_world.copy()
        if not all(_finite(row) for row in matrix):
            reasons.append('世界矩阵包含非有限数值')
        else:
            columns = [matrix.to_3x3().col[i].copy() for i in range(3)]
            if any(not math.isfinite(axis.length) or axis.length <= _MIN_LENGTH for axis in columns):
                reasons.append('对象存在零缩放或不可逆矩阵')
            elif matrix.to_3x3().determinant() <= 0.0:
                reasons.append('对象存在负缩放或不可逆矩阵')
            else:
                axes = [axis.normalized() for axis in columns]
                if any(abs(axes[i].dot(axes[j])) > BASIS_TOLERANCE
                       for i in range(3) for j in range(i)):
                    reasons.append('世界矩阵包含剪切')
                try:
                    inverse = matrix.inverted()
                    if not all(_finite(row) for row in inverse):
                        reasons.append('世界矩阵不能安全求逆')
                except ValueError:
                    reasons.append('世界矩阵不可逆')
            if _component_error(matrix[3], (0.0, 0.0, 0.0, 1.0)) > BASIS_TOLERANCE:
                reasons.append('世界矩阵不是有效仿射变换')
        # An even number of negative channels has a positive determinant, but
        # this first version deliberately does not reinterpret mirrored inputs.
        channels = tuple(obj.scale) + tuple(obj.delta_scale)
        if not _finite(channels) or any(value <= 0.0 for value in channels):
            reasons.append('缩放通道包含负值、零或非有限数值')
        if _component_error(obj.delta_scale, (1.0, 1.0, 1.0)) > BASIS_TOLERANCE:
            reasons.append('附加缩放（Delta Scale）不是 1，请先自行处理')
        if any(not _finite(vertex.co) for vertex in mesh.vertices):
            reasons.append('网格顶点包含非有限坐标')
        normals = mesh.corner_normals
        if (len(normals) != len(mesh.loops)
                or any(not _finite(normal.vector) or normal.vector.length <= _MIN_LENGTH
                       for normal in normals)):
            reasons.append('无法取得全部有效角点法线')
    except (AttributeError, ReferenceError, TypeError, ValueError, RuntimeError):
        reasons.append('对象数据不可读取或已失效')
    return list(dict.fromkeys(reasons))


def _write_normals(mesh, normals):
    node = getattr(bpy.types, 'GeometryNodeSetMeshNormal', None)
    mode = node.bl_rna.properties.get('mode') if node else None
    if mode and 'FREE' in mode.enum_items.keys():
        old = mesh.attributes.get('custom_normal')
        if old is not None:
            mesh.attributes.remove(old)
        attribute = mesh.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
        if (attribute.name != 'custom_normal' or attribute.domain != 'CORNER'
                or attribute.data_type != 'FLOAT_VECTOR'):
            raise ValueError('无法建立浮点角点法线')
        attribute.data.foreach_set('vector', [value for normal in normals for value in normal])
        method = 'FREE'
    else:
        setter = getattr(mesh, 'normals_split_custom_set', None)
        if setter is None:
            raise ValueError('此 Blender 版本不支持保留角点法线')
        setter(normals)
        method = 'CUSTOM_CHECKED'
    mesh.update()
    return method


def _restore_active_attribute(mesh, name):
    if name is None:
        # Blender may automatically select a newly created custom_normal layer.
        mesh.attributes.active_index = -1
        return
    for index, attribute in enumerate(mesh.attributes):
        if attribute.name == name:
            mesh.attributes.active_index = index
            return
    raise ValueError('原活动属性在准备后丢失')


def prepare_rebase(obj, root_world, tip_world, basis_world=None):
    """Return a validated private mesh and target matrix; never modify obj.

    Runtime validation uses corresponding vertex/corner indices and the fixed
    tolerances.  Failure removes the private mesh before propagating the error.
    """
    reasons = check_object(obj)
    if reasons:
        raise ValueError('；'.join(reasons))
    root = _point(root_world, '根点')
    tip = _point(tip_world, '尖点')
    direction = _unit(tip - root, '根尖方向')
    basis = make_basis(root, tip) if basis_world is None else Matrix(basis_world)
    _validate_basis(basis, direction)
    target = basis.to_4x4()
    target.translation = root
    _validate_basis(target.to_3x3(), direction)
    if _component_error(target.translation, root) > POSITION_TOLERANCE:
        raise ValueError('目标原点不能满足位置精度')

    source = obj.data
    original_matrix = obj.matrix_world.copy()
    before = _static_mesh_data(source)
    material_refs = tuple(source.materials)
    positions = [tuple(original_matrix @ vertex.co) for vertex in source.vertices]
    normal_matrix = original_matrix.to_3x3().inverted().transposed()
    world_normals = [_unit(normal_matrix @ normal.vector, '世界角点法线')
                     for normal in source.corner_normals]
    local_normals = [tuple(_unit(basis.transposed() @ normal, '目标角点法线'))
                     for normal in world_normals]
    transform = target.inverted() @ original_matrix
    private_mesh = None
    try:
        private_mesh = source.copy()
        private_mesh.transform(transform, shape_keys=False)
        normal_storage = _write_normals(private_mesh, local_normals)
        _restore_active_attribute(private_mesh, before['layers']['attribute_active'])
        if _static_mesh_data(private_mesh) != before or tuple(private_mesh.materials) != material_refs:
            raise ValueError('准备后的拓扑、图层、UV、颜色、权重或材质未通过逐元素校验')

        position_error = 0.0
        for index, (vertex, expected) in enumerate(zip(private_mesh.vertices, positions)):
            error = _component_error(tuple(target @ vertex.co), expected)
            position_error = max(position_error, error)
            if error > POSITION_TOLERANCE:
                raise ValueError('顶点 %d 世界位置误差 %.9g 超过 %.9g，已保留原件'
                                 % (index, error, POSITION_TOLERANCE))
        normals = private_mesh.corner_normals
        if len(normals) != len(world_normals):
            raise ValueError('准备后的角点法线数量发生变化')
        final_normal_matrix = target.to_3x3().inverted().transposed()
        normal_error = 0.0
        for index, (normal, expected) in enumerate(zip(normals, world_normals)):
            actual = _unit(final_normal_matrix @ normal.vector, '准备后的世界角点法线')
            error = _component_error(actual, expected)
            normal_error = max(normal_error, error)
            if error > NORMAL_TOLERANCE:
                raise ValueError('角点 %d 世界法线误差 %.9g 超过 %.9g；此网格或 Blender '
                                 '法线存储未通过校验，已保留原件'
                                 % (index, error, NORMAL_TOLERANCE))
        return {
            'mesh': private_mesh,
            'matrix': target,
            'stats': {
                'vertices': len(private_mesh.vertices),
                'edges': len(private_mesh.edges),
                'faces': len(private_mesh.polygons),
                'corners': len(private_mesh.loops),
                'max_position_error': position_error,
                'max_normal_error': normal_error,
                'position_tolerance': POSITION_TOLERANCE,
                'normal_tolerance': NORMAL_TOLERANCE,
                'normal_storage': normal_storage,
            },
        }
    except Exception:
        if private_mesh is not None:
            bpy.data.meshes.remove(private_mesh)
        raise


def validate_committed(obj, old_mesh, old_matrix, target_matrix):
    """Validate the actual object after assigning its mesh and world matrix.

    Blender may decompose an assigned matrix into transform channels.  The
    private-mesh check against the intended matrix is therefore insufficient:
    verify actual world vertices/normals again before the caller saves progress.
    This helper only reads data; any exception must trigger the caller's rollback.
    Object-linked material slots must additionally be checked by that caller.
    """
    reasons = check_object(obj)
    if reasons:
        raise ValueError('；'.join(reasons))
    actual = obj.matrix_world.copy()
    old = Matrix(old_matrix)
    target = Matrix(target_matrix)
    for matrix, label in ((actual, '实际世界矩阵'), (old, '原世界矩阵'), (target, '目标世界矩阵')):
        if (len(matrix) != 4 or any(len(row) != 4 or not _finite(row) for row in matrix)
                or _component_error(matrix[3], (0.0, 0.0, 0.0, 1.0)) > BASIS_TOLERANCE):
            raise ValueError(label + '不是有效仿射变换')
    target_basis = target.to_3x3()
    _validate_basis(target_basis)
    _validate_basis(actual.to_3x3(), target_basis.col[0])
    if _component_error(obj.scale, (1.0, 1.0, 1.0)) > BASIS_TOLERANCE:
        raise ValueError('对象实际缩放不是 1')
    origin_error = _component_error(actual.translation, target.translation)
    if origin_error > POSITION_TOLERANCE:
        raise ValueError('实际原点与目标根点误差 %.9g 超过 %.9g'
                         % (origin_error, POSITION_TOLERANCE))
    mesh = obj.data
    if (mesh == old_mesh or _static_mesh_data(mesh) != _static_mesh_data(old_mesh)
            or tuple(mesh.materials) != tuple(old_mesh.materials)):
        raise ValueError('实际网格的独立性、拓扑、图层或逐元素属性未通过校验')

    position_error = 0.0
    for index, (vertex, previous) in enumerate(zip(mesh.vertices, old_mesh.vertices)):
        error = _component_error(tuple(actual @ vertex.co), tuple(old @ previous.co))
        position_error = max(position_error, error)
        if error > POSITION_TOLERANCE:
            raise ValueError('实际顶点 %d 世界位置误差 %.9g 超过 %.9g'
                             % (index, error, POSITION_TOLERANCE))
    old_normals, normals = old_mesh.corner_normals, mesh.corner_normals
    if len(normals) != len(old_normals) or len(normals) != len(mesh.loops):
        raise ValueError('实际角点法线数量发生变化')
    old_normal_matrix = old.to_3x3().inverted().transposed()
    actual_normal_matrix = actual.to_3x3().inverted().transposed()
    normal_error = 0.0
    for index, (normal, previous) in enumerate(zip(normals, old_normals)):
        expected = _unit(old_normal_matrix @ previous.vector, '原世界角点法线')
        current = _unit(actual_normal_matrix @ normal.vector, '实际世界角点法线')
        error = _component_error(current, expected)
        normal_error = max(normal_error, error)
        if error > NORMAL_TOLERANCE:
            raise ValueError('实际角点 %d 世界法线误差 %.9g 超过 %.9g'
                             % (index, error, NORMAL_TOLERANCE))
    return {
        'vertices': len(mesh.vertices),
        'edges': len(mesh.edges),
        'faces': len(mesh.polygons),
        'corners': len(mesh.loops),
        'max_position_error': position_error,
        'max_normal_error': normal_error,
        'position_tolerance': POSITION_TOLERANCE,
        'normal_tolerance': NORMAL_TOLERANCE,
        'origin_error': origin_error,
        'basis_error': max(_component_error(actual.to_3x3()[row], target_basis[row])
                           for row in range(3)),
        'actual_transform_validated': True,
    }
