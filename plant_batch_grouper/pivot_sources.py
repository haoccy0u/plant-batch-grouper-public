# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Read-only stem provenance, plus optional metadata on validated outputs.

Source objects and grouping records are never repaired or assigned IDs here.
Coordinates persisted on an output are always in that output's mesh space.
"""
import hashlib
import json
import math
from collections import Counter, defaultdict
from itertools import product

from mathutils import Matrix, Vector

from .mesh_ops import record
from . import pivot_geometry


KEY = 'PBG_PivotSource'
VERSION = 1
POSITION_TOLERANCE = 1e-7
_PLAN_CACHE = {}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _read(raw, default=None):
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return default


def _local_record(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    return dict(name=obj.name, vertices=[list(v.co) for v in mesh.vertices],
                edges=[list(e.vertices) for e in mesh.edges],
                polygons=[list(p.vertices) for p in mesh.polygons],
                triangles=[list(t.vertices) for t in mesh.loop_triangles],
                polygon_count=len(mesh.polygons))


def _topology(rec):
    return dict(vertex_count=len(rec['vertices']), edges=rec['edges'],
                polygons=rec.get('polygons', []),
                polygon_count=rec.get('polygon_count', 0))


def _hash(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def geometry_digest(obj_or_record):
    """Exact local vertices and polygon topology; independent of object matrix.

    Accept an object, or a local record from this module. Material, visibility,
    name and derived triangulation do not determine a stem's spatial identity.
    """
    rec = obj_or_record if isinstance(obj_or_record, dict) else _local_record(obj_or_record)
    return _hash(dict(vertices=rec['vertices'], topology=_topology(rec)))


def _cycle(values):
    values = tuple(values)
    return min(values[i:] + values[:i] for i in range(len(values))) if values else ()


def _transform(rec, matrix):
    result = dict(rec)
    result['vertices'] = [list(matrix @ Vector(v)) for v in rec['vertices']]
    return result


def _near(a, b):
    return (len(a) == len(b) == 3 and
            all(math.isfinite(float(x)) for x in (*a, *b)) and
            max(abs(x - y) for x, y in zip(a, b)) <= POSITION_TOLERANCE)


def _piece(rec, indices, source_id):
    """Extract a complete source piece; new connections invalidate provenance.

    Taking only fully-contained faces would silently omit a newly welded leaf
    or a face crossing two old source pieces. Check every incident edge and
    polygon first, even when the recorded stem coordinates are unchanged.
    """
    indices = list(indices)
    if (not indices or any(type(i) is not int for i in indices) or
            len(set(indices)) != len(indices) or
            min(indices) < 0 or max(indices) >= len(rec['vertices'])):
        raise ValueError('茎的输出顶点对应无效')
    lookup = {v: i for i, v in enumerate(indices)}
    for elements in (rec['edges'], rec.get('polygons', [])):
        for element in elements:
            inside = [v in lookup for v in element]
            if any(inside) and not all(inside):
                raise ValueError('茎来源已与其他部件新增连接，旧对应不再可靠')
    def subset(elements):
        return [[lookup[v] for v in element] for element in elements
                if all(v in lookup for v in element)]
    polygons = subset(rec.get('polygons', []))
    if not polygons:
        raise ValueError('茎来源不含有效面')
    return dict(name=str(source_id), source_id=str(source_id),
                vertices=[list(rec['vertices'][v]) for v in indices],
                vertex_indices=indices, edges=subset(rec['edges']),
                triangles=subset(rec['triangles']), polygons=polygons,
                polygon_count=len(polygons))


def _record_evidence(rec):
    # Triangulation can change when a planar polygon is rebased; its original
    # polygon boundaries are the durable topology evidence instead.
    return dict(source_id=rec['source_id'], vertex_indices=rec['vertex_indices'],
                vertices=rec['vertices'], topology=_topology(rec))


def _seal(payload, output_rec):
    payload['geometry_digest'] = geometry_digest(output_rec)
    payload['topology_digest'] = _hash(_topology(output_rec))
    payload['records_digest'] = _hash([_record_evidence(r) for r in payload['records']])
    return payload


def _payload(raw):
    value = _read(raw)
    if not isinstance(value, dict) or value.get('version') != VERSION:
        raise ValueError('没有受支持的茎来源记录')
    ids = value.get('stem_ids', [])
    records = value.get('records', [])
    if (not ids or len(set(ids)) != len(ids) or not records or
            [r.get('source_id') for r in records] != ids or
            value.get('records_digest') != _hash([_record_evidence(r) for r in records])):
        raise ValueError('茎来源记录不完整或已改变')
    return value


def _same_piece_topology(expected, actual):
    """Compare local vertex correspondence, not unrelated mesh element order."""
    return (len(expected['vertices']) == len(actual['vertices']) and
            expected.get('polygon_count') == actual.get('polygon_count') and
            Counter(tuple(sorted(e)) for e in expected['edges']) ==
            Counter(tuple(sorted(e)) for e in actual['edges']) and
            Counter(_cycle(p) for p in expected.get('polygons', [])) ==
            Counter(_cycle(p) for p in actual.get('polygons', [])))


def _affine_matrix(value):
    matrix = Matrix(value)
    if (len(matrix) != 4 or any(len(row) != 4 for row in matrix) or
            not all(math.isfinite(x) for row in matrix for x in row) or
            tuple(matrix[3]) != (0.0, 0.0, 0.0, 1.0) or
            abs(matrix.determinant()) <= 1e-15):
        raise ValueError('旧局部坐标到新局部坐标的变换无效')
    return matrix


def _unique_piece_mapping(points, current, transformed=False):
    """Recover shifted indices only when all coordinate matches are unique.

    Ordinary mesh edits require exact recorded coordinates. A caller-supplied
    origin rebase permits only the established coordinate tolerance. Neither
    path picks the nearest point when duplicated/nearby vertices are ambiguous.
    """
    buckets = defaultdict(list)
    if not transformed:
        for index, point in enumerate(current['vertices']):
            buckets[tuple(point)].append(index)
        choices = [buckets.get(tuple(point), []) for point in points]
    else:
        def cell(point):
            return tuple(math.floor(float(x) / POSITION_TOLERANCE) for x in point)
        for index, point in enumerate(current['vertices']):
            buckets[cell(point)].append(index)
        choices = []
        for point in points:
            center = cell(point)
            choices.append([index for delta in product((-1, 0, 1), repeat=3)
                            for index in buckets.get(tuple(a + b for a, b in zip(center, delta)), [])
                            if _near(point, current['vertices'][index])])
    if any(len(options) != 1 for options in choices):
        raise ValueError('茎顶点索引已变且坐标对应不唯一')
    mapping = [options[0] for options in choices]
    if len(set(mapping)) != len(mapping):
        raise ValueError('茎来源不再具有一对一顶点对应')
    return mapping


def _verified_snapshot(raw, current, new_from_old=None):
    """Validate only saved stems and their incidence boundary, not whole leaves.

    Whole-output digests remain optional historical information. Keeping them
    as a gate would invalidate sound stem evidence after any independent leaf
    edit. Coordinates, complete stem topology and absence of crossing elements
    are instead checked directly, with a bijection across all retained stems.
    """
    value = _payload(raw)
    matrix = _affine_matrix(new_from_old) if new_from_old is not None else None
    used, recorded = set(), set()
    actual = []
    for rec in value['records']:
        indices = rec['vertex_indices']
        if (len(indices) != len(rec['vertices']) or recorded.intersection(indices) or
                len(set(indices)) != len(indices)):
            raise ValueError('茎来源顶点对应重叠或不完整')
        recorded.update(indices)
        points = [list(matrix @ Vector(v)) for v in rec['vertices']] if matrix is not None else rec['vertices']
        if not all(len(point) == 3 and all(math.isfinite(x) for x in point) for point in points):
            raise ValueError('茎来源包含无效坐标')
        def matches(candidate):
            return (_same_piece_topology(rec, candidate) and
                    all(_near(a, b) if matrix is not None else tuple(a) == tuple(b)
                        for a, b in zip(points, candidate['vertices'])))
        try:
            sampled = _piece(current, indices, rec['source_id'])
            if not matches(sampled):
                sampled = None
        except ValueError:
            sampled = None
        if sampled is None:
            indices = _unique_piece_mapping(points, current, matrix is not None)
            sampled = _piece(current, indices, rec['source_id'])
            if not matches(sampled):
                raise ValueError('茎快照与当前茎的坐标或拓扑不符')
        if used.intersection(indices):
            raise ValueError('多个茎来源映射到同一顶点')
        used.update(indices)
        actual.append(sampled)
    return value, actual


def _all_topology_matches(sources, target, mapping):
    if sorted(mapping) != list(range(len(target['vertices']))):
        return False
    edges, faces = Counter(), Counter()
    offset = 0
    for rec in sources:
        edges.update(tuple(sorted(mapping[offset + v] for v in edge)) for edge in rec['edges'])
        faces.update(_cycle([mapping[offset + v] for v in face]) for face in rec['polygons'])
        offset += len(rec['vertices'])
    return (edges == Counter(tuple(sorted(e)) for e in target['edges']) and
            faces == Counter(_cycle(p) for p in target['polygons']))


def _match_all(sources, target):
    """Prove a full vertex bijection and polygon topology, or decline recovery.

    Try the historical concatenation order first. Otherwise every coordinate
    must have one unique match; duplicates are not guessed by nearest distance.
    """
    points = [v for rec in sources for v in rec['vertices']]
    if (len(points) != len(target['vertices']) or
            sum(len(r['edges']) for r in sources) != len(target['edges']) or
            sum(len(r['polygons']) for r in sources) != len(target['polygons'])):
        return None
    ordered = list(range(len(points)))
    if (all(_near(a, b) for a, b in zip(points, target['vertices'])) and
            _all_topology_matches(sources, target, ordered)):
        return ordered
    def cell(point):
        return tuple(math.floor(float(x) / POSITION_TOLERANCE) for x in point)
    buckets = defaultdict(list)
    for i, point in enumerate(target['vertices']):
        buckets[cell(point)].append(i)
    mapping = []
    for point in points:
        center = cell(point)
        candidates = [i for delta in product((-1, 0, 1), repeat=3)
                      for i in buckets.get(tuple(a + b for a, b in zip(center, delta)), [])
                      if _near(point, target['vertices'][i])]
        if len(candidates) != 1:
            return None
        mapping.append(candidates[0])
    return mapping if _all_topology_matches(sources, target, mapping) else None


def capture_sources(output, originals, group, plan, marker_names=None):
    """Attach optional provenance after successful Join validation.

    Pass the still-present validation markers for an exact source/output map.
    No source or plan is modified. The returned small dict belongs in the new
    completion record; callers must treat metadata failure as nonfatal.
    """
    if KEY in output:
        del output[KEY]  # An object copy may carry unrelated inherited metadata.
    stem_ids = list(group.get('stems', []))
    source_ids = [obj.get('PBG_UID') for obj in originals]
    if (not stem_ids or len(set(source_ids)) != len(source_ids) or
            any(not i for i in source_ids) or not set(stem_ids).issubset(source_ids)):
        raise ValueError('没有完整的茎来源标识')
    current = _local_record(output)
    count = sum(len(o.data.vertices) for o in originals)
    if marker_names:
        attribute = output.data.attributes.get(marker_names[0])
        if not attribute or attribute.domain != 'POINT' or attribute.data_type != 'INT':
            raise ValueError('合并顶点对应标记不存在')
        identities = [d.value for d in attribute.data]
        if sorted(identities) != list(range(count)):
            raise ValueError('合并顶点对应标记不完整')
        by_identity = {identity: i for i, identity in enumerate(identities)}
        mapping = [by_identity[i] for i in range(count)]
    else:
        sources = [_local_record(o) for o in originals]
        source_world = [_transform(r, o.matrix_world) for r, o in zip(sources, originals)]
        mapping = _match_all(source_world, _transform(current, output.matrix_world))
        if mapping is None:
            raise ValueError('无法验证茎来源与合并结果的顶点对应')
    pieces, offset = {}, 0
    for obj, source_id in zip(originals, source_ids):
        size = len(obj.data.vertices)
        if source_id in stem_ids:
            pieces[source_id] = _piece(current, mapping[offset:offset + size], source_id)
        offset += size
    value = _seal(dict(version=VERSION, stem_ids=stem_ids,
                       records=[pieces[i] for i in stem_ids],
                       source_group=group.get('id'), source_session=plan.get('session', '')),
                  current)
    output[KEY] = _json(value)
    return dict(version=VERSION, stem_ids=stem_ids)


def rebase_source(obj, new_from_old, previous_geometry_digest=None):
    """Refresh provenance after a separately validated, committed mesh rebase.

    ``new_from_old`` maps saved mesh-local points to current mesh-local points.
    The optional third argument remains accepted for API compatibility, but a
    whole-output digest mismatch no longer rejects unchanged stem evidence.
    Actual transformed stem coordinates, topology and boundary are mandatory.
    Never use this as a substitute for validating the complete mesh rebase.

    Success returns True. Missing or invalid provenance returns False; stale
    metadata is removed, so it cannot retain the old local coordinate system.
    """
    if not obj.get(KEY):
        return False
    try:
        current = _local_record(obj)
        value, actual = _verified_snapshot(obj[KEY], current, new_from_old)
        value['records'] = actual
        obj[KEY] = _json(_seal(value, current))
        return True
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError, ReferenceError):
        if KEY in obj:
            del obj[KEY]
        return False


def _legacy_scope(context, obj):
    raw = context.scene.get('pbg_plan_json')
    key = context.scene.as_pointer()
    cached = _PLAN_CACHE.get(key)
    if cached and cached[0] == raw:
        plan = cached[1]
    else:
        plan = _read(raw, {})
        _PLAN_CACHE[key] = (raw, plan)
    if not isinstance(plan, dict):
        plan = {}
    identity = obj.get('PBG_UID')
    completed = [entry for entry in plan.get('completed', [])
                 if identity and entry.get('output') == identity]
    entry = completed[0] if len(completed) == 1 else {}
    ids = _read(obj.get('PBG_SourceIDs'), [])
    if not isinstance(ids, list) or not ids:
        ids = entry.get('sources', [])
    if (not ids or any(not isinstance(i, str) or not i for i in ids) or
            len(set(ids)) != len(ids)):
        raise ValueError('缺少完整的旧来源标识')
    if entry and set(entry.get('sources', [])) != set(ids):
        raise ValueError('旧完成记录与输出来源范围不一致')
    stem_ids = entry.get('pivot_source', {}).get('stem_ids', [])
    if not stem_ids:
        matching_groups = [g for g in plan.get('groups', [])
                           if g.get('done') and g.get('output') == identity and
                           set(g.get('members', [])) == set(ids)]
        if len(matching_groups) == 1:
            stem_ids = matching_groups[0].get('stems', [])
    if not set(stem_ids).issubset(ids):
        raise ValueError('旧茎标识不属于输出来源范围')
    # A duplicate object's inherited ID is ambiguous; do not repair user data.
    index = defaultdict(list)
    for source in context.scene.objects:
        key = source.get('PBG_UID')
        if key in ids and source.type == 'MESH' and source != obj:
            index[key].append(source)
    if any(len(index[i]) != 1 for i in ids):
        raise ValueError('旧来源缺失或对象标识不唯一')
    return ids, list(stem_ids), [index[i][0] for i in ids]


def _recover_legacy(context, obj, local, up):
    ids, stem_ids, sources = _legacy_scope(context, obj)
    records = [_local_record(source) for source in sources]
    world_sources = [_transform(r, source.matrix_world) for r, source in zip(records, sources)]
    mapping = _match_all(world_sources, _transform(local, obj.matrix_world))
    method = 'VERIFIED_SOURCE_WORLD'
    if mapping is None:
        # The old Join active object used the first source's local coordinate
        # frame. Merely knowing that frame is not proof: every source vertex,
        # edge and face must still match the complete current output mesh.
        world_to_first = sources[0].matrix_world.inverted()
        in_old_frame = [_transform(r, world_to_first @ source.matrix_world)
                        for r, source in zip(records, sources)]
        mapping = _match_all(in_old_frame, local)
        method = 'VERIFIED_SOURCE_LOCAL'
    if mapping is None:
        raise ValueError('旧来源与结果位置无法可靠对应')
    pieces, offset = {}, 0
    for source_id, source in zip(ids, sources):
        size = len(source.data.vertices)
        if source.data.polygons:
            pieces[source_id] = _piece(local, mapping[offset:offset + size], source_id)
        offset += size
    if stem_ids:
        if any(i not in pieces for i in stem_ids):
            raise ValueError('旧茎来源已没有有效面')
        world = [_transform(pieces[i], obj.matrix_world) for i in stem_ids]
        return dict(records=world, stem_ids=stem_ids, provenance=method,
                    reason='已验证旧来源与结果的完整顶点及拓扑对应', trusted=True)
    candidates, candidate_ids = [], []
    for source_id, piece in pieces.items():
        found = pivot_geometry.discover_stems(_transform(piece, obj.matrix_world), up)
        if found:
            candidate_ids.append(source_id)
            candidates.extend(found)
    return dict(records=candidates, stem_ids=candidate_ids,
                provenance='VERIFIED_SOURCE_CANDIDATES',
                reason='已验证旧来源位置；缺少原茎标识，使用来源范围中的细长候选', trusted=False)


def resolve_stems(context, obj, up=2, new_from_old=None):
    """Return verified output-space evidence as world records, without writes.

    Optional ``new_from_old`` permits read-only recovery after native origin-only
    editing. It must map the snapshot's old local coordinates to the current
    local coordinates. Persist that recovery explicitly via ``rebase_source``;
    this function never changes stored provenance or grouping progress.
    """
    if getattr(obj, 'type', None) != 'MESH':
        return dict(records=[], stem_ids=[], provenance='OUTPUT_GEOMETRY',
                    reason='当前对象不是网格，无法取得茎来源', trusted=False)
    reasons = []
    try:
        local = _local_record(obj)
        if obj.get(KEY):
            try:
                try:
                    value, pieces = _verified_snapshot(obj[KEY], local)
                except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
                    if new_from_old is None:
                        raise
                    value, pieces = _verified_snapshot(obj[KEY], local, new_from_old)
                return dict(records=[_transform(r, obj.matrix_world) for r in pieces],
                            stem_ids=list(value['stem_ids']), provenance='LOCAL_SNAPSHOT',
                            reason='使用已验证的输出局部茎记录，位置随当前结果变换', trusted=True)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
                reasons.append('旧茎快照已失效')
        try:
            return _recover_legacy(context, obj, local, up)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
            reasons.append('旧来源与结果位置无法可靠对应')
        candidates = pivot_geometry.discover_stems(record(obj), up)
        reason = '；'.join(reasons + ['使用结果自身的独立细长候选' if candidates else
                                      '没有可靠茎候选，请手动指定根尖'])
        return dict(records=candidates, stem_ids=[], provenance='OUTPUT_GEOMETRY',
                    reason=reason, trusted=False)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError, ReferenceError):
        return dict(records=[], stem_ids=[], provenance='OUTPUT_GEOMETRY',
                    reason='无法可靠读取茎几何，请检查网格或手动指定根尖', trusted=False)
