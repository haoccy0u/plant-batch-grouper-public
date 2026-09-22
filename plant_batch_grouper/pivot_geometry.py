# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Read-only geometry estimates for herbaceous root / tip preparation.

All coordinates remain in the input record's space (normally world space).
The paths below describe geodesic cross-section centres, not medial skeletons.
No Blender state, grouping decisions, transforms or input arrays are modified.
"""

import heapq

import numpy as np


def _array(value, columns, dtype=float):
    try:
        result = np.asarray(value, dtype=dtype)
        if result.size % columns:
            return np.empty((0, columns), dtype=dtype)
        return result.reshape(-1, columns)
    except (TypeError, ValueError, OverflowError):
        return np.empty((0, columns), dtype=dtype)


def _indices(value, columns, size):
    result = _array(value, columns, int)
    if len(result):
        result = result[((result >= 0) & (result < size)).all(axis=1)]
    return result


def _topology(record, size):
    triangles = _indices(record.get('triangles', []), 3, size)
    edges = _indices(record.get('edges', []), 2, size)
    if len(triangles):
        edges = np.concatenate((edges, triangles[:, [0, 1]],
                                triangles[:, [1, 2]], triangles[:, [2, 0]]))
    if len(edges):
        edges = np.unique(np.sort(edges, axis=1), axis=0)
        edges = edges[edges[:, 0] != edges[:, 1]]
    return edges, triangles


def _up_vector(up):
    if isinstance(up, str):
        label = up.upper().strip()
        sign = -1.0 if label.startswith('-') else 1.0
        if label.lstrip('+-') in ('X', 'Y', 'Z'):
            result = np.zeros(3)
            result['XYZ'.index(label.lstrip('+-'))] = sign
            return result
    if np.isscalar(up):
        try:
            index = int(up)
            if 0 <= index < 3:
                return np.eye(3)[index]
        except (TypeError, ValueError, OverflowError):
            pass
    else:
        result = _array(up, 3)
        if len(result) == 1 and np.isfinite(result).all():
            norm = float(np.linalg.norm(result[0]))
            if norm > 1e-15:
                return result[0] / norm
    return np.array([0.0, 0.0, 1.0])


def split_components(record):
    """Return independently indexed connected pieces, including loose vertices.

    ``vertex_indices`` maps each returned vertex to the input record's indices;
    if the input already has that mapping it is composed, not discarded.
    ``polygon_count`` is a face-presence/count hint: triangles supply the count
    when available because polygon boundaries are not part of this record API.
    """
    vertices = _array(record.get('vertices', []), 3)
    if not len(vertices):
        return []
    edges, triangles = _topology(record, len(vertices))
    adjacency = [[] for _ in vertices]
    for a, b in edges:
        adjacency[a].append(int(b))
        adjacency[b].append(int(a))
    labels = np.full(len(vertices), -1, dtype=int)
    pieces = []
    for first in range(len(vertices)):
        if labels[first] >= 0:
            continue
        label = len(pieces)
        labels[first] = label
        stack, indices = [first], []
        while stack:
            a = stack.pop()
            indices.append(a)
            for b in adjacency[a]:
                if labels[b] < 0:
                    labels[b] = label
                    stack.append(b)
        pieces.append(np.asarray(sorted(indices), dtype=int))
    original_indices = np.asarray(record.get('vertex_indices', np.arange(len(vertices))))
    if original_indices.shape != (len(vertices),):
        original_indices = np.arange(len(vertices))
    result = []
    for index, ids in enumerate(pieces):
        remap = np.full(len(vertices), -1, dtype=int)
        remap[ids] = np.arange(len(ids))
        part_edges = edges[labels[edges[:, 0]] == index] if len(edges) else edges
        part_triangles = triangles[labels[triangles[:, 0]] == index] if len(triangles) else triangles
        # Without faces an edge-only piece is not evidence of a real stem.
        face_count = len(part_triangles)
        if not len(triangles) and len(pieces) == 1:
            face_count = int(record.get('polygon_count', 0) or 0)
        part = {key: value for key, value in record.items()
                if key not in ('vertices', 'edges', 'triangles', 'polygon_count', 'vertex_indices')}
        part.update(vertices=vertices[ids].copy(), edges=remap[part_edges],
                    triangles=remap[part_triangles], polygon_count=face_count,
                    vertex_indices=original_indices[ids].astype(int).tolist(),
                    component_index=index)
        result.append(part)
    return result


def _walk(adjacency, first):
    distances = np.full(len(adjacency), np.inf)
    distances[first] = 0.0
    queue = [(0.0, first)]
    while queue:
        distance, a = heapq.heappop(queue)
        if distance != distances[a]:
            continue
        for b, weight in adjacency[a]:
            proposal = distance + weight
            if proposal < distances[b]:
                distances[b] = proposal
                heapq.heappush(queue, (proposal, b))
    return distances


def _unique_points(points, epsilon):
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    if len(points) < 2:
        return points
    # Translate first to avoid integer overflow on assets far from world origin.
    keys = np.round((points - points[0]) / max(epsilon, 1e-15), decimals=0)
    return points[np.sort(np.unique(keys, axis=0, return_index=True)[1])]


def _section(vertices, edges, coordinate, level, epsilon):
    """Interpolate edge / triangle-edge crossings without vertex-only sampling."""
    if not len(edges):
        return np.empty((0, 3))
    a, b = edges[:, 0], edges[:, 1]
    ca, cb = coordinate[a], coordinate[b]
    denominator = cb - ca
    crossing = ((np.minimum(ca, cb) <= level + epsilon) &
                (np.maximum(ca, cb) >= level - epsilon) &
                (np.abs(denominator) > epsilon))
    points = []
    if np.any(crossing):
        aa, bb = a[crossing], b[crossing]
        t = np.clip((level - ca[crossing]) / denominator[crossing], 0.0, 1.0)
        points.append(vertices[aa] + t[:, None] * (vertices[bb] - vertices[aa]))
    # Include entire coincident edges, e.g. the cap of a four-vertex card.
    coincident = (np.abs(ca - level) <= epsilon) & (np.abs(cb - level) <= epsilon)
    if np.any(coincident):
        points.append(vertices[np.unique(edges[coincident])])
    if not points:
        return np.empty((0, 3))
    return _unique_points(np.concatenate(points), epsilon)


def _section_center(points):
    """Bounding centre in principal section axes; insensitive to triangulation."""
    if not len(points):
        return None, 0.0
    mean = points.mean(axis=0)
    if len(points) == 1:
        return mean, 0.0
    _, _, axes = np.linalg.svd(points - mean, full_matrices=False)
    projections = (points - mean) @ axes.T
    center = mean + ((projections.min(axis=0) + projections.max(axis=0)) * 0.5) @ axes
    width = float(np.max(np.linalg.norm(points - center, axis=1)) * 2.0)
    return center, width


def _resample(points, count=33):
    points = np.asarray(points, dtype=float)
    lengths = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if lengths[-1] <= 1e-15:
        return np.repeat(points[:1], count, axis=0)
    targets = np.linspace(0.0, lengths[-1], count)
    return np.column_stack([np.interp(targets, lengths, points[:, axis]) for axis in range(3)])


def _cap(vertices, edges, coordinate, path, width, length, epsilon, at_start, support_scale=1.0):
    """Estimate a terminal section, using a transverse cap edge when available."""
    work_path = path if at_start else path[::-1]
    parameter = coordinate if at_start else coordinate.max() - coordinate
    parameter = parameter - parameter.min()
    # Interior centres avoid the diagonal bias of geodesic extreme corners.
    window_end = max(5, min(9, int(round(6 * support_scale))))
    local = work_path[2:window_end]
    _, values, axes = np.linalg.svd(local - local.mean(axis=0), full_matrices=False)
    if not len(values) or values[0] <= epsilon:
        return work_path[0], False
    tangent = axes[0]
    if np.dot(tangent, work_path[window_end] - work_path[1]) < 0:
        tangent = -tangent
    stable = len(values) < 2 or values[1] <= values[0] * 0.45
    band = min(length * 0.2, max(length * 0.02, width * 1.5) * support_scale)
    support = parameter <= band + epsilon
    ids = np.flatnonzero(support)
    if not len(ids):
        return work_path[0], False
    projection = (vertices - work_path[0]) @ tangent
    low = float(projection[ids].min())
    delta = vertices[edges[:, 1]] - vertices[edges[:, 0]]
    edge_length = np.linalg.norm(delta, axis=1)
    transverse = (np.abs(delta @ tangent) <= edge_length * 0.45) & (edge_length > epsilon)
    eligible = transverse & support[edges].all(axis=1)
    if np.any(eligible):
        candidate = edges[eligible]
        levels = projection[candidate].mean(axis=1)
        cap_edges = candidate[levels <= levels.min() + max(width * 0.15, epsilon)]
        points = vertices[np.unique(cap_edges)]
        center, cap_width = _section_center(points)
        spread = float(np.ptp((points - center) @ tangent))
        if cap_width > epsilon and spread <= max(width * 0.4, length * 0.005):
            return center, bool(stable)
    # Sparse / closed / pointed ends: use an actual plane-edge section slightly
    # inward and project its centre to the terminal plane, not to an extreme vertex.
    local_edges = edges[support[edges].any(axis=1)]
    inset = max(min(width * 0.2 * support_scale, band * 0.25), length * 1e-5)
    points = _section(vertices, local_edges, projection, low + inset, epsilon)
    center, section_width = _section_center(points)
    if center is None or len(points) < 2 or section_width <= epsilon:
        return work_path[0], False
    center = center - tangent * (float(np.dot(center - work_path[0], tangent)) - low)
    # A point terminal is valid only when the interpolated centre actually
    # converges onto that terminal; otherwise the cap needs human inspection.
    extremes = vertices[ids[np.abs(projection[ids] - low) <= epsilon * 4]]
    if len(extremes) == 1 and np.linalg.norm(center - extremes[0]) > max(width * 0.2, epsilon * 8):
        stable = False
    return center, bool(stable)


def _describe(record, up):
    vertices = _array(record.get('vertices', []), 3)
    if len(vertices) < 3 or not np.isfinite(vertices).all() or not record.get('polygon_count', 0):
        return None
    scale = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    epsilon = max(scale * 1e-9, 1e-12)
    if scale <= epsilon:
        return None
    edges, triangles = _topology(record, len(vertices))
    if not len(edges):
        return None
    if len(triangles):
        p = vertices[triangles]
        areas = np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
        if float(areas.max()) <= scale * scale * 1e-12:
            return None
    adjacency = [[] for _ in vertices]
    for a, b in edges:
        weight = float(np.linalg.norm(vertices[a] - vertices[b]))
        adjacency[a].append((int(b), weight))
        adjacency[b].append((int(a), weight))
    initial = int(np.argmin(vertices @ up))
    first_distances = _walk(adjacency, initial)
    if not np.isfinite(first_distances).all():
        return None
    a = int(np.argmax(first_distances))
    from_a = _walk(adjacency, a)
    b = int(np.argmax(from_a))
    length = float(from_a[b])
    if length <= epsilon:
        return None
    from_b = _walk(adjacency, b)
    coordinate = (from_a - from_b + length) * 0.5
    centers, widths = [], []
    for level in np.linspace(float(coordinate.min()), float(coordinate.max()), 41):
        points = _section(vertices, edges, coordinate, level, epsilon)
        center, width = _section_center(points)
        if center is not None:
            centers.append(center)
            widths.append(width)
    if len(centers) < 8:
        return None
    path = _resample(centers)
    width = float(np.quantile(widths[4:-4], 0.75))
    width = max(width, epsilon)
    estimated_length = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
    estimates = [[_cap(vertices, edges, coordinate, path, width, estimated_length,
                       epsilon, end, factor) for factor in (0.75, 1.0, 1.5)]
                 for end in (True, False)]
    roots = np.asarray([value[0] for value in estimates[0]])
    tips = np.asarray([value[0] for value in estimates[1]])
    root_support = [value[1] for value in estimates[0]]
    tip_support = [value[1] for value in estimates[1]]
    root, tip = roots.mean(axis=0), tips.mean(axis=0)
    path[0], path[-1] = root, tip
    path = _resample(path)
    path_length = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
    vector = tip - root
    if np.dot(vector, up) < 0 or (abs(np.dot(vector, up)) <= epsilon and vector[np.argmax(np.abs(vector))] < 0):
        root, tip = tip, root
        path = path[::-1].copy()
        roots, tips = tips, roots
        root_support, tip_support = tip_support, root_support
    # Sections near each end distinguish a broad midrib leaf from a stem card.
    end_width = max(float(np.median(widths[2:6])), float(np.median(widths[-6:-2])))
    double_taper = end_width < width * 0.5
    return dict(root=root, tip=tip, path=path, width=width, length=path_length,
                aspect=path_length / width, double_taper=double_taper,
                root_samples=roots, tip_samples=tips,
                root_support=root_support, tip_support=tip_support,
                axis_span=float(np.ptp(vertices @ up)), epsilon=epsilon,
                record=record)


def discover_stems(record, up=2):
    """Find conservative stem-like components; return every plausible candidate.

    This deliberately does not choose the lowest / longest leaf here. A tapered
    narrow piece remains a candidate: main-stem selection and root / direction
    reliability are assessed together by ``analyze_stems``.
    """
    direction = _up_vector(up)
    candidates = []
    for component in split_components(record):
        feature = _describe(component, direction)
        if feature is None or feature['aspect'] < 8.0:
            continue
        candidate = dict(component)
        candidate['pivot_candidate_reason'] = '独立细长茎候选；仍需核对与其他候选的沿程对应'
        candidates.append(candidate)
    return candidates


def _result(reason, features=(), root=None, tip=None, root_reliable=False,
            direction_reliable=False, root_reason=None, direction_reason=None):
    return dict(root=None if root is None else np.asarray(root).tolist(),
                tip=None if tip is None else np.asarray(tip).tolist(),
                reason=reason, review=not (root_reliable and direction_reliable),
                root_reliable=bool(root_reliable), direction_reliable=bool(direction_reliable),
                root_reason=root_reason if root_reason is not None else reason,
                direction_reason=direction_reason if direction_reason is not None else reason,
                paths=[f['path'].tolist() for f in features], stem_count=len(features),
                width=float(max((f['width'] for f in features), default=0.0)),
                length=float(np.mean([f['length'] for f in features])) if features else 0.0)


def _pair_distance(first, other):
    """A geometric card match, independent of the number of card vertices."""
    ratio = min(first['length'], other['length']) / max(first['length'], other['length'], 1e-15)
    if ratio < 0.7:
        return None
    tolerance = max(0.5 * max(first['width'], other['width']),
                    0.02 * max(first['length'], other['length']))
    direct = np.linalg.norm(first['path'] - other['path'], axis=1)
    reverse = np.linalg.norm(first['path'] - other['path'][::-1], axis=1)
    distances = direct if direct.mean() <= reverse.mean() else reverse
    # Pairing needs along-path agreement, not an accurately reconstructed tip.
    if max(distances[1], distances[-2], np.quantile(distances[2:-2], 0.9)) > tolerance:
        return None
    return float(np.mean(distances) / max(tolerance, 1e-15))


def _card_clusters(features):
    """Complete-link clusters prevent a chain of nearby cards becoming one stem."""
    groups = [[index] for index in range(len(features))]
    pairs = {}
    for a in range(len(features)):
        for b in range(a + 1, len(features)):
            pairs[a, b] = _pair_distance(features[a], features[b])
    while len(groups) > 1:
        best = None
        for a, group_a in enumerate(groups):
            for b in range(a + 1, len(groups)):
                scores = [pairs[min(i, j), max(i, j)] for i in group_a for j in groups[b]]
                if any(score is None for score in scores):
                    continue
                option = (max(scores), a, b)
                if best is None or option < best:
                    best = option
        if best is None:
            break
        _, a, b = best
        groups[a] += groups.pop(b)
    return [[features[index] for index in group] for group in groups]


def _unit_directions(vectors, epsilon):
    vectors = np.asarray(vectors, dtype=float).reshape(-1, 3)
    lengths = np.linalg.norm(vectors, axis=1)
    if not len(vectors) or not np.isfinite(vectors).all() or np.any(lengths <= epsilon):
        return None
    return vectors / lengths[:, None]


def _angle_degrees(vectors):
    """Largest pairwise angle, without treating opposite signs as equivalent."""
    if vectors is None or not len(vectors):
        return float('inf')
    return float(np.degrees(np.arccos(np.clip(np.min(vectors @ vectors.T), -1.0, 1.0))))


def _cluster_evidence(features, up):
    reference = features[0]['path']
    root_sets, tip_sets, supported = [], [], []
    for feature in features:
        path = feature['path']
        reverse = (np.linalg.norm(reference - path[::-1], axis=1).mean() <
                   np.linalg.norm(reference - path, axis=1).mean())
        root_sets.append(feature['tip_samples'] if reverse else feature['root_samples'])
        tip_sets.append(feature['root_samples'] if reverse else feature['tip_samples'])
        supported.append(feature['tip_support'] if reverse else feature['root_support'])
    roots, tips = np.asarray(root_sets), np.asarray(tip_sets)
    # Keep the direction evidence unchanged: its samples include all the old
    # root / terminal estimates, independently of root-location acceptance.
    direction_root, direction_tip = roots.mean(axis=(0, 1)), tips.mean(axis=(0, 1))
    length = float(np.mean([feature['length'] for feature in features]))
    width = float(max(feature['width'] for feature in features))
    epsilon = float(max(feature['epsilon'] for feature in features))
    root_tolerance = max(width * 0.2, length * 0.005, epsilon * 8)
    card_roots, support_counts, sample_spreads = [], [], []
    insufficient_support, unstable_samples = False, False
    for feature, samples, support in zip(features, roots, supported):
        valid = samples[np.asarray(support, dtype=bool)]
        support_counts.append(len(valid))
        if len(valid) < 2:
            insufficient_support = True
            # Retain only a provisional display point, never an accepted root.
            card_roots.append(samples.mean(axis=0))
            sample_spreads.append(None)
            continue
        spread = float(np.max(np.linalg.norm(valid[:, None] - valid[None, :], axis=2)))
        sample_spreads.append(spread)
        tolerance = max(feature['width'] * 0.2, feature['length'] * 0.005,
                        feature['epsilon'] * 8)
        unstable_samples = unstable_samples or spread > tolerance
        card_roots.append(valid.mean(axis=0))
    # First average supported estimates inside a card, then average cards.
    # A card with three supported bands must not outweigh one with two bands.
    card_roots = np.asarray(card_roots)
    root = card_roots.mean(axis=0)
    root_spread = max((value for value in sample_spreads if value is not None), default=0.0)
    vector = direction_tip - direction_root
    chord = float(np.linalg.norm(vector))
    axis = vector / chord if chord > epsilon else up
    axial_offset, lateral_offset = 0.0, 0.0
    axial_conflict, lateral_conflict = False, False
    for i, first in enumerate(features):
        for j in range(i + 1, len(features)):
            other = features[j]
            delta = card_roots[j] - card_roots[i]
            axial = float(np.dot(delta, axis))
            lateral = float(np.linalg.norm(delta - axial * axis))
            axial_offset = max(axial_offset, abs(axial))
            lateral_offset = max(lateral_offset, lateral)
            pair_tolerance = max(0.5 * max(first['width'], other['width']),
                                 0.02 * max(first['length'], other['length']))
            axial_conflict = axial_conflict or abs(axial) > root_tolerance
            lateral_conflict = lateral_conflict or lateral > pair_tolerance
    root_problems = []
    if insufficient_support:
        root_problems.append('根部截面支持不足，至少一张卡片没有两次有效估计')
    if unstable_samples:
        root_problems.append('同一卡片的有效根部采样不稳定')
    if axial_conflict:
        root_problems.append('卡片根部沿生长方向错位')
    if lateral_conflict:
        root_problems.append('卡片根部横向偏置超出配对范围')
    root_reliable = not root_problems
    root_reason = '；'.join(root_problems) if root_problems else '每张卡片根部稳定，卡间等权中心可靠'
    directions = _unit_directions([tip_sample - root_sample
                                  for card_roots, card_tips in zip(roots, tips)
                                  for root_sample in card_roots for tip_sample in card_tips], epsilon)
    angle = _angle_degrees(directions)
    direction_reliable = angle <= 3.0 and chord >= length * 0.5 and chord > epsilon
    direction_reason = ('总体方向在多个支持带内变化不超过 3°' if direction_reliable else
                        '总体方向不稳定或茎存在明显回弯')
    if abs(float(np.dot(vector, up))) <= length * 0.02:
        root_reliable = direction_reliable = False
        root_reason = '接近水平，无法依据生长轴确定哪一端是根部'
        direction_reason = '根尖朝向不明确，需要确认本地 +X 的正向'
    if any(feature['aspect'] < 4.0 for feature in features):
        root_reliable = direction_reliable = False
        root_reason = direction_reason = '候选不够细长，无法可靠代表主茎'
    # ``tip`` is the orientation endpoint; shifting it with the refined root
    # preserves the original +X exactly, without claiming a new botanical tip.
    tip = root + vector
    return dict(features=features, root=root, tip=tip, length=length, width=width,
                epsilon=epsilon, root_tolerance=root_tolerance,
                root_reliable=root_reliable, direction_reliable=direction_reliable,
                root_reason=root_reason, direction_reason=direction_reason,
                direction_angle=angle, root_spread=root_spread,
                root_support_counts=support_counts, root_sample_spreads=sample_spreads,
                root_axial_offset=axial_offset, root_lateral_offset=lateral_offset,
                axis_span=max(feature['axis_span'] for feature in features),
                base=float(np.dot(root, up)),
                double_taper=all(feature['double_taper'] for feature in features))


def analyze_stems(records, up=2, trusted=False):
    """Estimate root / tip from stem records without touching grouping state.

    ``trusted`` means verified stem identity, not verified root polarity. Without
    it, main-stem candidates must span 70% of the largest candidate's extent on
    the growth axis and reach the low-root band. Flower / leaf bounds do not set
    that denominator. Output coordinates use the same space as input records.

    Root and direction reliability are independent; ``tip`` is an orientation
    endpoint, not a promise of a precisely reconstructed botanical tip. A result
    may retain useful partial evidence while ``review`` remains true.
    """
    if isinstance(records, dict):
        records = [records]
    records = list(records or [])
    direction = _up_vector(up)
    features, ignored = [], 0
    for record in records:
        parts = split_components(record) if trusted else discover_stems(record, direction)
        for part in parts:
            if not part.get('polygon_count', 0):
                continue
            feature = _describe(part, direction)
            if feature is None:
                ignored += 1
            else:
                features.append(feature)
    if not features:
        return _result('没有可靠主茎候选，请使用原生原点编辑确认根部和 +X')
    clusters = [_cluster_evidence(group, direction) for group in _card_clusters(features)]
    candidates = clusters
    if not trusted:
        maximum_span = max(cluster['axis_span'] for cluster in clusters)
        candidates = [cluster for cluster in clusters if cluster['axis_span'] >= maximum_span * 0.7]
        minimum_root = min(cluster['base'] for cluster in candidates)
        candidates = [cluster for cluster in candidates
                      if cluster['base'] - minimum_root <=
                      max(cluster['width'] * 0.5, cluster['length'] * 0.02)]
        # Once a candidate reaches the coverage and low-root requirements,
        # taper alone cannot remove its conflicting root or direction evidence.
    else:
        # Broken or tiny islands inside a trusted source must not veto a useful
        # main stem. Comparable substantial candidates remain explicit rivals.
        maximum_length = max(cluster['length'] for cluster in clusters)
        candidates = [cluster for cluster in clusters if cluster['length'] >= maximum_length * 0.35]
    candidates.sort(key=lambda cluster: (-cluster['axis_span'], -cluster['length'], cluster['base']))
    chosen = candidates[0]
    root, tip = chosen['root'], chosen['tip']
    root_reliable, direction_reliable = chosen['root_reliable'], chosen['direction_reliable']
    root_reason, direction_reason = chosen['root_reason'], chosen['direction_reason']
    if len(candidates) > 1:
        # Retain a shared root or axis even when the other property is ambiguous.
        # Distinct stems are never averaged into an invented root / tip pair.
        root_tolerance = max(cluster['root_tolerance'] for cluster in candidates)
        roots = np.asarray([cluster['root'] for cluster in candidates])
        root_distances = np.linalg.norm(roots[:, None] - roots[None, :], axis=2)
        root_reliable = (all(cluster['root_reliable'] for cluster in candidates) and
                         float(root_distances.max()) <= root_tolerance)
        axes = _unit_directions([cluster['tip'] - cluster['root'] for cluster in candidates],
                                max(cluster['epsilon'] for cluster in candidates))
        direction_reliable = (all(cluster['direction_reliable'] for cluster in candidates) and
                              _angle_degrees(axes) <= 3.0)
        if root_reliable:
            root_reason = '多个主茎候选给出一致根部'
        else:
            root_problems = [cluster['root_reason'] for cluster in candidates
                             if not cluster['root_reliable']]
            if float(root_distances.max()) > root_tolerance:
                root_problems.append('主要候选的根部不一致，请确认原点位置')
            root_reason = '；'.join(dict.fromkeys(root_problems))
        direction_reason = ('多个主茎候选给出一致总体方向' if direction_reliable else
                            '主要候选的总体方向不一致，请确认本地 +X')
    selected = [feature for cluster in candidates for feature in cluster['features']]
    reason = '根部：' + root_reason + '；方向：' + direction_reason
    result = _result(reason, selected, root, tip, root_reliable, direction_reliable,
                     root_reason, direction_reason)
    result.update(candidate_cluster_count=len(clusters), main_cluster_count=len(candidates),
                  ignored_component_count=ignored + len(features) - len(selected))
    return result
