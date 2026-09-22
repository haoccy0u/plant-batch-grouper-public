# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

import bpy
import hashlib
from collections import Counter

def record(obj):
    obj.data.calc_loop_triangles()
    return dict(name=obj.name, vertices=[list(obj.matrix_world @ v.co) for v in obj.data.vertices],
                edges=[list(e.vertices) for e in obj.data.edges], polygon_count=len(obj.data.polygons),
                triangles=[list(t.vertices) for t in obj.data.loop_triangles])


def prepare_join_normals(copies):
    """Keep decoded normals on private copies without another lossy encoding.

    Blender versions exposing FREE mesh normals can join float vectors directly.
    Preserve the old path on earlier versions; validation is never relaxed.
    A single-object output needs no conversion because it does not use Join.
    """
    if len(copies)<2:
        return
    node=getattr(bpy.types,'GeometryNodeSetMeshNormal',None)
    mode=node.bl_rna.properties.get('mode') if node else None
    if not mode or 'FREE' not in mode.enum_items.keys():
        return
    # Join copies FREE vectors as attributes, without transforming them. Express
    # each source normal in the active object's local space before concatenation.
    target_to_local=copies[0].matrix_world.to_3x3().transposed()
    for obj in copies:
        mesh=obj.data
        normal_matrix=target_to_local @ obj.matrix_world.to_3x3().inverted_safe().transposed()
        normals=[component for normal in mesh.corner_normals
                 for component in (normal_matrix @ normal.vector).normalized()]
        old=mesh.attributes.get('custom_normal')
        if old:
            mesh.attributes.remove(old)
        attribute=mesh.attributes.new('custom_normal','FLOAT_VECTOR','CORNER')
        attribute.data.foreach_set('vector',normals)
        mesh.update()


def signature(objects):
    """Compare per-face corner data, not just independent UV/color histograms."""
    vertices, edges, faces, point_colors = Counter(), Counter(), Counter(), Counter()
    def rounded(value, digits=6):
        return tuple(round(float(x), digits) for x in value)
    for obj in objects:
        mesh = obj.data
        coords = [rounded(obj.matrix_world @ v.co, 7) for v in mesh.vertices]
        vertices.update(coords)
        edges.update((tuple(sorted((coords[e.vertices[0]], coords[e.vertices[1]]))), e.use_seam, e.use_edge_sharp) for e in mesh.edges)
        uv = sorted(mesh.uv_layers, key=lambda a: a.name)
        corner_colors = sorted([a for a in mesh.color_attributes if a.domain == 'CORNER'], key=lambda a: a.name)
        for attr in mesh.color_attributes:
            if attr.domain == 'POINT':
                point_colors.update((attr.name, attr.data_type, coords[i], rounded(d.color)) for i, d in enumerate(attr.data))
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        normals = mesh.corner_normals
        for face in mesh.polygons:
            material = obj.material_slots[face.material_index].material if face.material_index < len(obj.material_slots) else None
            corners = []
            for i in face.loop_indices:
                corners.append((coords[mesh.loops[i].vertex_index],
                                tuple((a.name, rounded(a.data[i].uv)) for a in uv),
                                tuple((a.name, a.data_type, rounded(a.data[i].color)) for a in corner_colors),
                                rounded((normal_matrix @ normals[i].vector).normalized(), 4)))
            # Join preserves polygon corner order; rotate to a canonical first corner.
            rotations = [tuple(corners[j:] + corners[:j]) for j in range(len(corners))]
            faces[(material.name if material else '', face.use_smooth, min(rotations))] += 1
    return dict(vertices=vertices, edges=edges, faces=faces, point_colors=point_colors)


def fingerprint(obj):
    sig = signature([obj])
    payload = [(k, sorted(v.items())) for k, v in sig.items()]
    payload.append(('matrix', [list(row) for row in obj.matrix_world]))
    payload.append(('schema', schema(obj)))
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def schema(obj):
    return (sorted(a.name for a in obj.data.uv_layers),
            sorted((a.name, a.domain, a.data_type) for a in obj.data.color_attributes))


def validate_source(obj):
    if obj.type != 'MESH' or obj.library or obj.data.library:
        raise ValueError(obj.name + '：只支持本地网格')
    if obj.modifiers or obj.data.shape_keys or obj.vertex_groups or obj.constraints or obj.animation_data:
        raise ValueError(obj.name + '：请先处理修改器、形态键、顶点组、约束或动画')
    if obj.matrix_world.determinant() <= 1e-12:
        raise ValueError(obj.name + '：请先应用负缩放或修复零缩放')
    known = {a.name for a in obj.data.uv_layers} | {a.name for a in obj.data.color_attributes}
    known |= {'position', 'custom_normal', 'material_index', 'sharp_edge', 'sharp_face'}
    unknown = [a.name for a in obj.data.attributes if not a.name.startswith('.') and a.name not in known]
    if unknown:
        raise ValueError(obj.name + '：存在尚未支持的属性 ' + ', '.join(unknown))

