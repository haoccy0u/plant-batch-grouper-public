# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Validate Join by temporary, exact element identities, never nearest geometry."""
import math
import uuid
from collections import Counter
from .mesh_ops import signature

POSITION_TOLERANCE = 1e-7
NORMAL_TOLERANCE = 5e-5


def mark_copies(copies):
    """Mark only private copies; IDs follow original object/element order."""
    token = uuid.uuid4().hex
    names = ('.pbg_v_'+token,'.pbg_c_'+token)
    vertex_offset = corner_offset = 0
    for obj in copies:
        mesh = obj.data
        point = mesh.attributes.new(names[0],'INT','POINT')
        corner = mesh.attributes.new(names[1],'INT','CORNER')
        if point.name!=names[0] or corner.name!=names[1]:
            raise ValueError('无法建立临时合并校验标记')
        point.data.foreach_set('value',range(vertex_offset,vertex_offset+len(mesh.vertices)))
        corner.data.foreach_set('value',range(corner_offset,corner_offset+len(mesh.loops)))
        vertex_offset += len(mesh.vertices)
        corner_offset += len(mesh.loops)
    return names


def clean_markers(result, marker_names):
    for name in marker_names:
        attribute = result.data.attributes.get(name)
        if attribute:
            result.data.attributes.remove(attribute)


def _near(a,b,tolerance):
    return (len(a)==len(b) and all(math.isfinite(x) for x in (*a,*b))
            and sum((x-y)**2 for x,y in zip(a,b))<=tolerance**2)


def _cycle(values):
    if not values:
        return ()
    first = values.index(min(values))
    return tuple(values[first:]+values[:first])


def _material(obj, polygon):
    material = obj.material_slots[polygon.material_index].material if polygon.material_index<len(obj.material_slots) else None
    return material.name_full if material else None


def _schema(mesh):
    return (tuple(sorted(a.name for a in mesh.uv_layers)),
            tuple(sorted((a.name,a.domain,a.data_type) for a in mesh.color_attributes)))


def equivalent(originals, result, marker_names, before, after):
    """Prove element correspondence, exact attributes, and bounded float error.

    The existing signatures remain authoritative snapshots. Only their decimal
    bin equality may differ: actual data is independently checked by stable IDs.
    """
    try:
        return _equivalent(originals,result,marker_names,before,after)
    except (AttributeError,IndexError,KeyError,TypeError,ValueError,ReferenceError):
        return False


def _equivalent(originals, result, marker_names, before, after):
    if not originals or len(marker_names)!=2 or len(set(marker_names))!=2:
        return False
    if before!=signature(originals) or after!=signature([result]):
        return False
    mesh = result.data
    counts = tuple(sum(len(getattr(o.data,kind)) for o in originals) for kind in ('vertices','edges','polygons','loops'))
    if counts!=tuple(len(getattr(mesh,kind)) for kind in ('vertices','edges','polygons','loops')):
        return False
    vertex_attr, corner_attr = (mesh.attributes.get(name) for name in marker_names)
    if (not vertex_attr or not corner_attr or vertex_attr.domain!='POINT' or corner_attr.domain!='CORNER'
            or vertex_attr.data_type!='INT' or corner_attr.data_type!='INT'):
        return False
    vertex_ids = [d.value for d in vertex_attr.data]
    corner_ids = [d.value for d in corner_attr.data]
    if sorted(vertex_ids)!=list(range(counts[0])) or sorted(corner_ids)!=list(range(counts[3])):
        return False
    expected_schema = _schema(originals[0].data)
    if _schema(mesh)!=expected_schema or any(_schema(o.data)!=expected_schema for o in originals):
        return False
    if any(domain not in {'POINT','CORNER'} for _,domain,_ in expected_schema[1]):
        return False
    expected_vertices, expected_loops, expected_normals = [], [], []
    expected_uv = {name:[] for name in expected_schema[0]}
    expected_colors = {name:[] for name,_,_ in expected_schema[1]}
    expected_edges, expected_faces = Counter(), Counter()
    vertex_offset = corner_offset = 0
    for obj in originals:
        source = obj.data
        expected_vertices.extend(tuple(obj.matrix_world @ v.co) for v in source.vertices)
        expected_loops.extend(vertex_offset+loop.vertex_index for loop in source.loops)
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        expected_normals.extend(tuple((normal_matrix @ n.vector).normalized()) for n in source.corner_normals)
        for name in expected_uv:
            expected_uv[name].extend(tuple(item.uv) for item in source.uv_layers[name].data)
        for name in expected_colors:
            expected_colors[name].extend(tuple(item.color) for item in source.color_attributes[name].data)
        expected_edges.update((tuple(sorted(vertex_offset+v for v in e.vertices)),e.use_seam,e.use_edge_sharp) for e in source.edges)
        expected_faces.update((_cycle([corner_offset+i for i in p.loop_indices]),_material(obj,p),p.use_smooth) for p in source.polygons)
        vertex_offset += len(source.vertices)
        corner_offset += len(source.loops)
    for vertex, identity in zip(mesh.vertices,vertex_ids):
        if not _near(tuple(result.matrix_world @ vertex.co),expected_vertices[identity],POSITION_TOLERANCE):
            return False
    normal_matrix = result.matrix_world.to_3x3().inverted_safe().transposed()
    normals = mesh.corner_normals
    for loop, identity in zip(mesh.loops,corner_ids):
        if vertex_ids[loop.vertex_index]!=expected_loops[identity]:
            return False
        if not _near(tuple((normal_matrix @ normals[loop.index].vector).normalized()),expected_normals[identity],NORMAL_TOLERANCE):
            return False
        if any(tuple(mesh.uv_layers[name].data[loop.index].uv)!=values[identity] for name,values in expected_uv.items()):
            return False
    for name,domain,_ in expected_schema[1]:
        identities = vertex_ids if domain=='POINT' else corner_ids
        if any(tuple(item.color)!=expected_colors[name][identity] for item,identity in zip(mesh.color_attributes[name].data,identities)):
            return False
    edges = Counter((tuple(sorted(vertex_ids[v] for v in e.vertices)),e.use_seam,e.use_edge_sharp) for e in mesh.edges)
    if edges!=expected_edges:
        return False
    faces = Counter((_cycle([corner_ids[i] for i in p.loop_indices]),_material(result,p),p.use_smooth) for p in mesh.polygons)
    return faces==expected_faces
