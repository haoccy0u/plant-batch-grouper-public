# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Conservative, topology-first grouping of existing plant mesh objects.

Paths are estimates from graph geodesic bands, not exact medial skeletons.
Nothing here changes input meshes or depends on Blender / third-party solvers.
"""
import heapq
import math

import numpy as np

from .core import DEFAULTS


def _walk(adjacency, start):
    distance = np.full(len(adjacency), np.inf)
    distance[start] = 0.0
    queue = [(0.0, start)]
    while queue:
        cost, a = heapq.heappop(queue)
        if cost != distance[a]:
            continue
        for b, weight in adjacency[a]:
            proposed = cost + weight
            if proposed < distance[b]:
                distance[b] = proposed
                heapq.heappush(queue, (proposed, b))
    return distance


def _resample(points, count=25):
    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        return np.repeat(points[:1], count, axis=0)
    s = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    if s[-1] <= 1e-15:
        return np.repeat(points[:1], count, axis=0)
    targets = np.linspace(0, s[-1], count)
    return np.column_stack([np.interp(targets, s, points[:, i]) for i in range(3)])


def _segment_distances(points, a, b):
    """Distances from each query to all segments, with no vertex-only shortcut."""
    d = b - a
    q = points[:, None, :] - a[None, :, :]
    t = np.clip(np.einsum('pei,ei->pe', q, d) /
                np.maximum(np.einsum('ei,ei->e', d, d), 1e-30), 0, 1)
    return np.linalg.norm(q - t[:, :, None] * d[None, :, :], axis=2)


def _surface_distance(points, feature):
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    v, edges, triangles = feature['v'], feature['edges'], feature['triangles']
    out = np.full(len(points), np.inf)
    # Chunk both queries and primitives to keep large meshes bounded in memory.
    for first in range(0, len(points), 32):
        p = points[first:first + 32]
        best = np.full(len(p), np.inf)
        for begin in range(0, len(triangles), 512):
            tri = v[triangles[begin:begin + 512]]
            a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
            ab, ac = b-a, c-a
            normal = np.cross(ab, ac)
            nn = np.einsum('ei,ei->e', normal, normal)
            delta = p[:, None, :] - a[None, :, :]
            signed = np.einsum('pei,ei->pe', delta, normal)
            projected = delta - (signed / np.maximum(nn, 1e-30))[:, :, None] * normal
            aa = np.einsum('ei,ei->e', ab, ab)
            bb = np.einsum('ei,ei->e', ab, ac)
            cc = np.einsum('ei,ei->e', ac, ac)
            ap = np.einsum('pei,ei->pe', projected, ab)
            cp = np.einsum('pei,ei->pe', projected, ac)
            determinant = aa*cc-bb*bb
            u = (cc*ap-bb*cp) / np.maximum(determinant, 1e-30)
            w = (aa*cp-bb*ap) / np.maximum(determinant, 1e-30)
            inside = (u >= -1e-9) & (w >= -1e-9) & (u+w <= 1+1e-9) & (nn > 1e-30)
            ds = np.minimum(_segment_distances(p, a, b),
                            np.minimum(_segment_distances(p, b, c), _segment_distances(p, c, a)))
            ds = np.minimum(ds, np.where(inside, np.abs(signed)/np.sqrt(np.maximum(nn, 1e-30)), np.inf))
            best = np.minimum(best, ds.min(axis=1))
        if not len(triangles):
            for begin in range(0, len(edges), 512):
                e = edges[begin:begin + 512]
                best = np.minimum(best, _segment_distances(p, v[e[:, 0]], v[e[:, 1]]).min(axis=1))
            if not len(edges) and len(v):
                for begin in range(0, len(v), 512):
                    best = np.minimum(best, np.linalg.norm(p[:, None] - v[None, begin:begin+512], axis=2).min(axis=1))
        out[first:first + len(p)] = best
    return out


def _feature(record, up):
    v = np.asarray(record.get('vertices', []), dtype=float).reshape(-1, 3)
    if len(v) < 2 or not np.isfinite(v).all():
        return None
    edges = np.asarray(record.get('edges', []), dtype=int).reshape(-1, 2)
    if len(edges):
        edges = edges[((edges >= 0) & (edges < len(v))).all(axis=1)]
    triangles = np.asarray(record.get('triangles', []), dtype=int).reshape(-1, 3)
    if len(triangles):
        triangles = triangles[((triangles >= 0) & (triangles < len(v))).all(axis=1)]
        if len(triangles):
            triangle_points = v[triangles]
            area = np.linalg.norm(np.cross(triangle_points[:, 1]-triangle_points[:, 0],
                                           triangle_points[:, 2]-triangle_points[:, 0]), axis=1)
            if float(area.max()) <= max(float(np.linalg.norm(np.ptp(v, axis=0)))**2*1e-14, 1e-30):
                return None
    adjacency = [[] for _ in v]
    for a, b in edges:
        length = float(np.linalg.norm(v[a]-v[b]))
        adjacency[a].append((int(b), length))
        adjacency[b].append((int(a), length))
    remaining, components = set(range(len(v))), []
    while remaining:
        start = min(remaining)
        ids, queue = [], [start]
        remaining.remove(start)
        while queue:
            a = queue.pop()
            ids.append(a)
            for b, _ in adjacency[a]:
                if b in remaining:
                    remaining.remove(b)
                    queue.append(b)
        components.append(ids)
    # Isolated vertices in an otherwise valid mesh must not determine the path.
    substantial = [ids for ids in components if len(ids) >= 3]
    ids = max(components, key=lambda x: (len(x), -min(x)))
    if len(ids) < 2:
        return None
    first = min(ids, key=lambda i: (float(v[i, up]), i))
    d0 = _walk(adjacency, first)
    end_a = max(ids, key=lambda i: (d0[i], -i))
    da = _walk(adjacency, end_a)
    end_b = max(ids, key=lambda i: (da[i], -i))
    if v[end_a, up] > v[end_b, up]:
        end_a, end_b = end_b, end_a
        da = _walk(adjacency, end_a)
    length = float(da[end_b])
    if length <= 1e-12:
        return None
    db = _walk(adjacency, end_b)
    # Symmetric geodesic coordinate removes much of a quad's diagonal bias.
    selected = np.asarray(ids, dtype=int)
    coordinate = np.full(len(v), np.inf)
    coordinate[selected] = (da[selected] - db[selected] + length)*0.5
    d = coordinate[selected]
    sample_count = min(41, max(9, int(math.sqrt(len(ids))*2)))
    centers, widths = [], []
    valid_edges = edges[np.isfinite(coordinate[edges]).all(axis=1)] if len(edges) else edges
    for t in np.linspace(float(d.min()), float(d.max()), sample_count):
        a, b = valid_edges[:, 0], valid_edges[:, 1]
        denominator = coordinate[b]-coordinate[a]
        crossing = ((t >= np.minimum(coordinate[a], coordinate[b])-1e-10) &
                    (t <= np.maximum(coordinate[a], coordinate[b])+1e-10) & (np.abs(denominator) > 1e-12))
        if np.any(crossing):
            a, b, denominator = a[crossing], b[crossing], denominator[crossing]
            pts = v[a] + ((t-coordinate[a])/denominator)[:, None]*(v[b]-v[a])
            pts = np.unique(np.round(pts, 12), axis=0)
        else:
            pts = v[selected[np.argsort(np.abs(d-t))[:min(2, len(ids))]]]
        center = pts.mean(axis=0)
        centers.append(center)
        widths.append(float(2*np.max(np.linalg.norm(pts-center, axis=1))))
    path = _resample(centers)
    path_length = float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())
    # Width also includes transverse PCA spread for undersampled strips.
    centered = v[selected] - v[selected].mean(axis=0)
    eigenvalues = np.linalg.eigvalsh(centered.T @ centered / len(selected))
    band_width = float(np.quantile(widths[1:-1] or widths, .6))
    width = max(band_width, length*.002, 1e-12)
    end_masks = [d <= d.min()+length*.14, d >= d.max()-length*.14]
    ends = [v[selected[m]] for m in end_masks]
    return dict(v=v, edges=edges, triangles=triangles, path=path,
                length=max(path_length, length*.7), width=width,
                aspect=max(path_length, length*.7)/width, eigenvalues=eigenvalues,
                double_taper=bool(max(widths[1], widths[-2]) < width*.6),
                ends=ends, end_widths=[float(np.median(widths[1:4])), float(np.median(widths[-4:-1]))],
                lo=v.min(axis=0), hi=v.max(axis=0),
                disconnected=len(substantial)>1, component_count=len(substantial),
                has_faces=record.get('polygon_count', max(len(triangles), 1))>0)


def _pair(a, b, cfg):
    if min(a['length'], b['length']) / max(a['length'], b['length']) < .7:
        return None
    tolerance = max(min(a['length'], b['length'])*cfg['pair_fraction'],
                    min(a['width'], b['width'])*.35, 1e-12)
    direct = np.linalg.norm(a['path']-b['path'], axis=1)
    reverse = np.linalg.norm(a['path']-b['path'][::-1], axis=1)
    distances = direct if direct.mean() <= reverse.mean() else reverse
    if max(distances[0], distances[-1]) > tolerance*1.5 or np.quantile(distances, .9) > tolerance:
        return None
    return float(distances.mean()/tolerance)


def _local_tolerance(a, b, cfg):
    return max(min(a['length'], b['length'])*cfg['attach_fraction']*2,
               min(a['width'], b['width'])*.4, b['width']*.2, 1e-12)


def _contact(a, b, cfg):
    tolerance = _local_tolerance(a, b, cfg)
    if np.any(a['hi'] < b['lo']-tolerance*cfg['ambiguity_ratio']) or np.any(a['lo'] > b['hi']+tolerance*cfg['ambiguity_ratio']):
        return None
    best = None
    for end, points in enumerate(a['ends']):
        ds = _surface_distance(points, b)
        at = int(ds.argmin())
        distance = float(ds[at])
        if best is None or distance < best['distance']:
            best = dict(distance=distance, score=distance/tolerance, end=end,
                        point=points[at], tolerance=tolerance)
    if best and best['score'] <= cfg['ambiguity_ratio']:
        point = best['point']
        starts, delta = b['path'][:-1], np.diff(b['path'], axis=0)
        t = np.clip(np.einsum('ei,ei->e', point-starts, delta) /
                    np.maximum(np.einsum('ei,ei->e', delta, delta), 1e-30), 0, 1)
        projections = starts + t[:, None]*delta
        target = projections[np.argmin(np.linalg.norm(projections-point, axis=1))]
        towards = target-point
        outward = a['path'][0]-a['path'][1] if best['end']==0 else a['path'][-1]-a['path'][-2]
        cosine = float(np.dot(towards, outward) / max(float(np.linalg.norm(towards)*np.linalg.norm(outward)), 1e-30))
        # Extrapolation is evidence only across a gap. Existing exact contacts
        # and normal side branches must not be rejected by an angle heuristic.
        best['direction_conflict'] = bool(best['distance'] > tolerance*.2 and
                                          np.linalg.norm(towards) > b['width']*.75 and cosine < -.25)
        return best
    return None


def _blank(reason, kind='UNKNOWN'):
    return dict(group=0, kind=kind, state='UNASSIGNED', reason=reason, candidates=[])


def _chosen(candidates, cfg):
    if not candidates or candidates[0]['score'] > 1:
        return False
    if len(candidates) == 1:
        return True
    return candidates[1]['score'] >= max(candidates[0]['score']*cfg['ambiguity_ratio'], .35)


def analyze(records, reference=None, config=None, anchors=None, decisions=None, fixed_scale=None, geometry_cache=None):
    """Return a legacy-compatible grouping plan; unknowns are never force-connected.

    ``anchors`` maps stable group ids to source object ids. Supplying it freezes
    group identity, including missing anchors; no new group is invented locally.
    ``decisions`` binds manual owners and exclusions by object id.
    ``geometry_cache`` optionally holds immutable feature descriptions keyed by
    the caller's geometric digest; assignments and contact graphs are not cached.
    """
    cfg = dict(DEFAULTS, **(config or {}))
    if fixed_scale and fixed_scale.get('config'):
        cfg.update(fixed_scale['config'])
    cfg['up'] = int(cfg['up'])
    if cfg['up'] not in (0, 1, 2):
        raise ValueError('生长轴必须是 X、Y 或 Z')
    decisions = decisions or {}
    byname = {r['name']: r for r in records}
    names = sorted(byname)
    if geometry_cache is not None:
        for old_name in list(geometry_cache):
            if old_name not in byname:
                del geometry_cache[old_name]
    features, invalid = {}, {}
    for name in names:
        digest = byname[name].get('_geometry_key')
        signature = (str(digest), cfg['up']) if digest is not None else None
        cached = geometry_cache.get(name) if geometry_cache is not None and signature is not None else None
        if cached is not None and cached.get('signature') == signature:
            f = cached['feature']
        else:
            try:
                f = _feature(byname[name], cfg['up'])
            except (ValueError, IndexError, TypeError, np.linalg.LinAlgError):
                f = None
            if geometry_cache is not None:
                if signature is None:
                    geometry_cache.pop(name, None)
                else:
                    geometry_cache[name] = dict(signature=signature, feature=f)
        if f is None or not f['has_faces']:
            invalid[name] = '网格缺少有效面或可用连接路径'
        else:
            features[name] = f
    if features:
        low = np.min([f['lo'] for f in features.values()], axis=0)
        high = np.max([f['hi'] for f in features.values()], axis=0)
        height = max(float(high[cfg['up']]-low[cfg['up']]), float(np.linalg.norm(high-low))*.1, 1e-9)
        ground = float(np.quantile([f['lo'][cfg['up']] for f in features.values()], .02))
    else:
        height, ground = 1.0, 0.0
    if fixed_scale:
        height = float(fixed_scale.get('height', height))
        ground = float(fixed_scale.get('ground', ground))
    height = max(height, 1e-12)
    warnings = []
    ref_info = []
    if reference:
        # Reference is descriptive only: one positive example cannot calibrate
        # separation from neighbouring branches without negative examples.
        try:
            rf = _feature(reference, cfg['up'])
            if rf:
                ref_info.append(dict(length=rf['length'], width=rf['width'], ratio=rf['aspect']**2))
        except (ValueError, IndexError, TypeError, np.linalg.LinAlgError):
            pass
        warnings.append('参考枝条仅记录形状；连接图使用局部尺度，未自动改写参数')
    ratio = max(math.sqrt(max(float(cfg['stem_ratio']), 1.0)), 3.0)
    root_limit = ground + height*float(cfg['root_fraction'])
    branch_names = {n for n, f in features.items() if f['aspect'] >= ratio and
                    f['length'] >= height*float(cfg['min_stem_fraction'])*.18 and not f['disconnected'] and
                    (not f['double_taper'] or (f['lo'][cfg['up']] <= root_limit and
                                              f['length'] >= height*float(cfg['min_stem_fraction'])))}
    frozen = anchors is not None
    groups = []
    if frozen:
        for gid, stems in anchors.items():
            valid = [n for n in stems if n in features]
            manual_anchor = any(decisions.get(n, {}).get('anchor_manual') for n in stems)
            uncertain = len(valid)!=len(stems) or not valid
            if not manual_anchor:
                uncertain |= any(features[n]['disconnected'] or features[n]['aspect'] < ratio for n in valid)
                if len(valid)>1:
                    # An automatic multi-card anchor must remain one matching
                    # along-path component; a moved card cannot silently retain
                    # its old role just because its object id is unchanged.
                    reached, queue = {valid[0]}, [valid[0]]
                    while queue:
                        a = queue.pop()
                        for b in valid:
                            if b not in reached and _pair(features[a], features[b], cfg) is not None:
                                reached.add(b)
                                queue.append(b)
                    uncertain |= len(reached)!=len(valid)
            groups.append(dict(id=int(gid), stems=list(stems), members=[],
                               stem_uncertain=bool(uncertain), manual_confirmed=False))
            branch_names.update(n for n in stems if n in features)
    branch_names = sorted(branch_names)
    # Mutual unique path matches combine crossing cards into one atomic anchor.
    pair_candidates = {n: [] for n in branch_names}
    for i, a in enumerate(branch_names):
        for b in branch_names[i+1:]:
            score = _pair(features[a], features[b], cfg)
            if score is not None:
                pair_candidates[a].append((score, b))
                pair_candidates[b].append((score, a))
    for value in pair_candidates.values():
        value.sort()
    if frozen:
        for group in groups:
            stems = set(group['stems'])
            if any(decisions.get(n, {}).get('anchor_manual') for n in stems):
                continue
            # Local refresh must not silently combine two stable groups. A new
            # along-path match crossing their boundary is a review condition,
            # including the multiple-match ambiguity from the first analysis.
            if any(partner_name not in stems for n in stems
                   for _, partner_name in pair_candidates.get(n, [])):
                group['stem_uncertain'] = True
    def partner(n):
        choices = pair_candidates[n]
        if not choices or (len(choices)>1 and choices[1][0] < max(choices[0][0]*cfg['ambiguity_ratio'], .3)):
            return None
        return choices[0][1]
    pairs, paired = [], set()
    for n in branch_names:
        if n in paired:
            continue
        p = partner(n)
        members = [n]
        if p and p not in paired and partner(p) == n:
            members.append(p)
        paired.update(members)
        pairs.append(members)
    contacts, adjacency = [], {n: [] for n in branch_names}
    root_objects = {n for n in branch_names if features[n]['lo'][cfg['up']] <= root_limit and
                    features[n]['length'] >= height*float(cfg['min_stem_fraction'])}
    for i, a in enumerate(branch_names):
        for b in branch_names[i+1:]:
            pairing = _pair(features[a], features[b], cfg)
            ca, cb = _contact(features[a], features[b], cfg), _contact(features[b], features[a], cfg)
            available = [c for c in (ca, cb) if c is not None]
            if not available and pairing is None:
                continue
            contact = min(available, key=lambda c:c['score']) if available else None
            score = contact['score'] if contact else pairing
            # Independent roots touching at the soil are not parent-child links.
            if pairing is None and a in root_objects and b in root_objects and contact['point'][cfg['up']] <= root_limit:
                continue
            if (score > 1 or (contact and contact.get('direction_conflict'))) and pairing is None:
                # Weak contacts remain diagnostics, never propagation bridges.
                contacts.append(dict(a=a, b=b, distance=float(contact['distance']), score=float(score), accepted=False))
                continue
            cost = .05+float(pairing) if pairing is not None else .5+float(score)
            distance = float(contact['distance']) if contact else float(pairing)*min(features[a]['width'], features[b]['width'])
            adjacency[a].append((b, cost, distance))
            adjacency[b].append((a, cost, distance))
            contacts.append(dict(a=a, b=b, distance=distance, score=float(score), accepted=True))
    if not frozen:
        for members in pairs:
            if any(n in root_objects for n in members):
                uncertainty = len(members)==1 and bool(pair_candidates[members[0]])
                groups.append(dict(id=len(groups)+1, stems=list(members), members=[],
                                   stem_uncertain=uncertainty, manual_confirmed=False))
        # With one trunk, its immediate branch subtrees become output roots.
        if len(groups)==1:
            trunk = set(groups[0]['stems'])
            direct = {b for a in trunk for b, _, _ in adjacency[a] if b not in trunk}
            children = [p for p in pairs if any(n in direct for n in p) and not set(p)&trunk]
            if len(children)>=2:
                for members in children:
                    groups.append(dict(id=len(groups)+1, stems=list(members), members=[],
                                       stem_uncertain=False, manual_confirmed=False))
        if not groups:
            warnings.append('未找到可靠主枝起点；请在视口指定主枝，未强制连接孤立部件')
    group_ids = {g['id'] for g in groups}
    anchor_owner = {n:g['id'] for g in groups for n in g['stems']}
    # Manual branch owners are propagation seeds, but cannot change root identity.
    seeds = {gid:[] for gid in group_ids}
    for n, gid in anchor_owner.items():
        if n in adjacency:
            seeds[gid].append(n)
    for n, decision in decisions.items():
        gid = int(decision.get('group') or 0)
        if decision.get('manual') and gid in group_ids and n in adjacency and n not in anchor_owner:
            seeds[gid].append(n)
    costs, predecessors, gap_distances, root_reachable = {}, {}, {}, {}
    for gid in sorted(group_ids):
        # Geometric support comes only from real anchors. Manual assignments are
        # later propagation seeds, but must not prove their own validity.
        reached = {n for n in anchor_owner if anchor_owner[n]==gid and n in adjacency}
        stack = list(reached)
        while stack:
            n = stack.pop()
            for nxt, _, _ in adjacency[n]:
                decision = decisions.get(nxt, {})
                if (nxt in reached or (nxt in anchor_owner and anchor_owner[nxt]!=gid) or
                        gid in {int(x) for x in decision.get('excluded', [])} or
                        (decision.get('manual') and decision.get('group') and int(decision['group'])!=gid)):
                    continue
                reached.add(nxt)
                stack.append(nxt)
        root_reachable[gid] = reached
        distance, previous, gaps, queue = {}, {}, {}, []
        for name in seeds[gid]:
            distance[name], gaps[name] = 0.0, 0.0
            heapq.heappush(queue, (0.0, name))
        while queue:
            score, name = heapq.heappop(queue)
            if score != distance[name]:
                continue
            for nxt, weight, gap in adjacency[name]:
                if nxt in anchor_owner and anchor_owner[nxt] != gid:
                    continue
                decision = decisions.get(nxt, {})
                excluded = {int(x) for x in decision.get('excluded', [])}
                manual_group = int(decision.get('group') or 0)
                if gid in excluded or (decision.get('manual') and manual_group and manual_group!=gid):
                    continue
                proposal = score + weight
                if proposal < distance.get(nxt, math.inf):
                    distance[nxt], previous[nxt] = proposal, name
                    gaps[nxt] = max(gaps[name], gap)
                    heapq.heappush(queue, (proposal, nxt))
        costs[gid], predecessors[gid], gap_distances[gid] = distance, previous, gaps
    assignments = {}
    for name in branch_names:
        if name in anchor_owner:
            assignments[name] = dict(group=anchor_owner[name], kind='STEM', state='ASSIGNED', candidates=[], reason='主枝起点：沿网格连接路径识别')
            continue
        candidates = [dict(group=gid, distance=float(gap_distances[gid][name]), score=float(costs[gid][name]),
                           distance_kind='max_connection_gap', manual_support=name not in root_reachable[gid])
                      for gid in sorted(group_ids) if name in costs[gid]]
        candidates.sort(key=lambda c:(c['score'], c['group']))
        # Path cost counts supported joints, so unlike attachment score it has no
        # absolute <=1 cutoff. Competition margin is still enforced.
        clear = bool(candidates) and (len(candidates)==1 or candidates[1]['score'] >= max(candidates[0]['score']*cfg['ambiguity_ratio'], .35))
        assignments[name] = dict(group=candidates[0]['group'] if clear else 0, kind='BRANCH',
                                 state='ASSIGNED' if clear else ('REVIEW' if candidates else 'UNASSIGNED'),
                                 manual_support=bool(clear and candidates[0].get('manual_support')),
                                 candidates=candidates, reason='枝条连接路径明确' if clear else ('枝条有多个主枝候选' if candidates else '枝条未连接可靠主枝'))
    # Attach leaves only to branch graph nodes, never through another leaf.
    for name, f in features.items():
        if name in assignments:
            continue
        compact = f['eigenvalues'][-1] / max(f['eigenvalues'][-2], 1e-20) < 6
        flower = compact and f['length'] < height*.12
        candidates_by_group = {}
        excluded = {int(x) for x in decisions.get(name, {}).get('excluded', [])}
        for branch in branch_names:
            owner = assignments[branch]
            owners = [owner['group']] if owner['group'] else [c['group'] for c in owner['candidates']]
            owners = [gid for gid in owners if gid not in excluded]
            if not owners:
                continue
            bf = features[branch]
            contact = _contact(f, bf, cfg)
            if flower:
                points = f['v']
                tip_distances = np.linalg.norm(points[:, None] - bf['path'][[0, -1]][None], axis=2)
                tip_distance = float(tip_distances.min())
                tolerance = max(bf['width'], f['length']*.25, bf['length']*cfg['flower_fraction'])
                if tip_distance <= tolerance*cfg['ambiguity_ratio']:
                    contact = dict(distance=tip_distance, score=tip_distance/tolerance, end=0, tolerance=tolerance)
            if not contact:
                continue
            wide_end = bool(contact.get('direction_conflict')) or (not flower and f['end_widths'][contact['end']] > max(f['end_widths'][1-contact['end']]*2.5, f['width']*.8))
            for gid in owners:
                candidate = dict(group=gid, distance=float(contact['distance']), score=float(contact['score']),
                                 uncertain_end=bool(wide_end), via=branch,
                                 uncertain_branch=not bool(owner['group']) or bool(owner.get('manual_support')))
                if gid not in candidates_by_group or candidate['score'] < candidates_by_group[gid]['score']:
                    candidates_by_group[gid] = candidate
        candidates = sorted(candidates_by_group.values(), key=lambda c:(c['score'],c['group']))
        clear = _chosen(candidates, cfg) and not candidates[0]['uncertain_end'] and not candidates[0]['uncertain_branch'] and not f['disconnected']
        reason = ('附属部件连接端明确' if clear else '多个候选或连接端需检查') if candidates else '未找到连接端；可修正位置后局部刷新'
        if f['disconnected']:
            reason = '对象含多个独立片段；保留整个对象并检查归属'
        assignments[name] = dict(group=candidates[0]['group'] if clear else 0, kind='FLOWER' if flower else 'LEAF',
                                 state='ASSIGNED' if clear else ('REVIEW' if candidates else 'UNASSIGNED'), candidates=candidates, reason=reason)
    for name, reason in invalid.items():
        assignments[name] = _blank(reason)
    # Never overwrite a human choice, even if its supporting geometry changed.
    for name, decision in decisions.items():
        if name not in assignments or not decision.get('manual'):
            continue
        a = assignments[name]
        gid = int(decision.get('group') or 0)
        if name in anchor_owner:
            gid = anchor_owner[name]
        supported = gid in group_ids and (name in anchor_owner or any(c['group']==gid and not c.get('uncertain_branch') for c in a['candidates']))
        if name in branch_names and name not in anchor_owner:
            supported = gid in group_ids and name in root_reachable[gid]
        a['group'], a['manual'] = gid, True
        a['manual_review'] = not supported
        a['state'] = 'ASSIGNED' if supported else 'REVIEW'
        a['reason'] = '保留人工归属' if supported else '保留人工归属；连接变化需复查'
    for g in groups:
        gid = g['id']
        g['members'] = sorted(n for n,a in assignments.items() if a['group']==gid)
        g['blockers'] = sorted(n for n,a in assignments.items()
                               if (not a['group'] and any(c['group']==gid for c in a['candidates'])) or
                               (a['group']==gid and (a['state']!='ASSIGNED' or a.get('manual_review'))))
        g['ready'] = bool(g['members'] and not g['stem_uncertain'] and not g['blockers'])
    edge_only = sum(not len(f['triangles']) for f in features.values())
    if edge_only:
        warnings.append('部分记录没有三角面索引，连接距离回退到网格边')
    return dict(version=1, engine='GRAPH', config=cfg, height=height, ground=ground,
                reference_components=ref_info, groups=groups, assignments=assignments,
                summary=dict(objects=len(assignments), stems=sum(len(g['stems']) for g in groups),
                             groups=len(groups), ready=sum(g['ready'] for g in groups),
                             review=sum(a['state']!='ASSIGNED' for a in assignments.values())),
                graph_report=dict(method='mesh_geodesic_bands_and_rooted_contact_forest',
                                  branch_objects=len(branch_names), connections=contacts,
                                  edge_distance_fallback=edge_only, warnings=warnings,
                                  paths={n:dict(length=f['length'], width=f['width'], aspect=f['aspect'],
                                                components=f['component_count']) for n,f in features.items()}))
