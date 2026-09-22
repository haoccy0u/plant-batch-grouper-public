# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Geometry-only grouping. Blender ships NumPy; no extra packages required."""
import numpy as np


DEFAULTS = dict(up=2, stem_ratio=25.0, min_stem_fraction=0.14,
                root_fraction=0.06, pair_fraction=0.012,
                attach_fraction=0.005, flower_fraction=0.025, ambiguity_ratio=1.8)


def components(record):
    adj = [[] for _ in record['vertices']]
    for a, b in record['edges']:
        adj[a].append(b)
        adj[b].append(a)
    seen, result = set(), []
    for start in range(len(adj)):
        if start in seen:
            continue
        stack, ids = [start], []
        seen.add(start)
        while stack:
            a = stack.pop()
            ids.append(a)
            for b in adj[a]:
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        result.append(ids)
    return result


def shape(vertices, up):
    v = np.asarray(vertices, dtype=float)
    centered = v - v.mean(axis=0)
    eig, axes = np.linalg.eigh(centered.T @ centered / max(len(v), 1))
    axis = axes[:, -1]
    t = centered @ axis
    span = max(float(np.ptp(t)), 1e-12)
    ends = [v[t <= t.min() + span * .12], v[t >= t.max() - span * .12]]
    widths = [float(np.linalg.norm(np.ptp(x, axis=0))) for x in ends]
    return dict(v=v, lo=v.min(axis=0), hi=v.max(axis=0), axis=axis,
                ratio=float(eig[-1] / max(eig[-2], 1e-20)), ends=ends,
                widths=widths, span=span, height=float(np.ptp(v[:, up])))


def centerline(record, up, count=25):
    v = np.asarray(record['vertices'], dtype=float)
    edges = np.asarray(record['edges'], dtype=int).reshape(-1, 2)
    heights = np.linspace(v[:, up].min(), v[:, up].max(), count)
    result = []
    for h in heights:
        a, b = v[edges[:, 0]], v[edges[:, 1]]
        delta = b[:, up] - a[:, up]
        valid = (np.abs(delta) > 1e-12) & (h >= np.minimum(a[:, up], b[:, up]) - 1e-10) & (h <= np.maximum(a[:, up], b[:, up]) + 1e-10)
        if np.any(valid):
            t = (h - a[valid, up]) / delta[valid]
            pts = a[valid] + t[:, None] * (b[valid] - a[valid])
            result.append(np.unique(np.round(pts, 10), axis=0).mean(axis=0))
        else:
            result.append(v[np.argmin(np.abs(v[:, up] - h))])
    return np.asarray(result)


def point_edges(points, vertices, edges):
    points = np.asarray(points)
    if not len(edges):
        return np.linalg.norm(points[:, None] - vertices[None], axis=2).min(axis=1)
    a = vertices[edges[:, 0]]
    d = vertices[edges[:, 1]] - a
    result = []
    for chunk in np.array_split(points, max(1, (len(points) + 63) // 64)):
        q = chunk[:, None] - a
        t = np.clip(np.einsum('pei,ei->pe', q, d) / np.maximum((d*d).sum(axis=1), 1e-24), 0, 1)
        result.extend(np.sqrt(((q - t[:, :, None]*d)**2).sum(axis=2).min(axis=1)))
    return np.asarray(result)


def group_geometry(records, stems, up):
    vv, ee, tips, offset = [], [], [], 0
    for name in stems:
        r = records[name]
        v = np.asarray(r['vertices'], dtype=float)
        if not len(v):
            raise ValueError('茎网格没有顶点')
        e = np.asarray(r['edges'], dtype=int).reshape(-1, 2)
        vv.append(v)
        ee.append(e + offset)
        tips.append(centerline(r, up)[-1])
        offset += len(v)
    v, e = np.concatenate(vv), np.concatenate(ee)
    return v, e, np.mean(tips, axis=0), v.min(axis=0), v.max(axis=0)


def classify(record, geometries, cfg, height, ground, excluded=()):
    if not record['vertices']:
        return dict(group=0, kind='UNKNOWN', state='UNASSIGNED', candidates=[], reason='空网格')
    up = cfg['up']
    f = shape(record['vertices'], up)
    flower = f['ratio'] < 6 and f['span'] < height * .12
    attach_tol = height * cfg['attach_fraction']
    tol = height * cfg['flower_fraction'] if flower else attach_tol
    candidates = []
    for gid, (v, e, tip, lo, hi) in geometries.items():
        if gid in excluded or np.any(f['hi'] < lo - tol) or np.any(f['lo'] > hi + tol):
            continue
        if flower:
            bottom = f['v'][f['v'][:, up] <= f['lo'][up] + f['height'] * .35 + 1e-12]
            distances = np.linalg.norm(bottom-tip, axis=1)
            distance = float(distances.min())
            contact = bottom[int(distances.argmin())]
            uncertain_end = False
        else:
            ds = [point_edges(pts, v, e) for pts in f['ends']]
            side = int(ds[1].min() < ds[0].min())
            distance = float(ds[side].min())
            contact = f['ends'][side][int(ds[side].argmin())]
            uncertain_end = f['widths'][side] > max(f['widths'][1-side] * 1.8, attach_tol * 2)
        if distance <= tol * cfg['ambiguity_ratio']:
            candidates.append(dict(group=gid, distance=distance, score=distance/tol,
                                   uncertain_end=bool(uncertain_end), root_contact=bool(contact[up] < ground+height*.07)))
    candidates.sort(key=lambda c:(c['score'], c['group']))
    state, group, reason = 'UNASSIGNED', 0, '连接端不在容差范围内'
    if candidates and candidates[0]['score'] <= 1:
        best = candidates[0]
        ambiguous = len(candidates)>1 and candidates[1]['score'] < max(best['score']*cfg['ambiguity_ratio'], .35)
        if ambiguous or best['uncertain_end'] or best['root_contact']:
            state, reason = 'REVIEW', '多个候选距离接近' if ambiguous else ('疑似叶尖接触' if best['uncertain_end'] else '根部密集区接触')
        else:
            state, group, reason = 'ASSIGNED', best['group'], '花头底部接近茎尖' if flower else '叶片端部接近茎边缘'
    return dict(group=group, kind='FLOWER' if flower else 'LEAF', state=state, reason=reason, candidates=candidates[:4])


def analyze(records, reference=None, config=None):
    cfg = dict(DEFAULTS, **(config or {}))
    up = cfg['up']
    records = sorted(records, key=lambda r: r['name'])
    if not records:
        raise ValueError('没有可分析的网格对象')
    feats = {r['name']: shape(r['vertices'], up) for r in records if r['vertices']}
    if not feats:
        raise ValueError('输入对象没有顶点')
    height = max(f['hi'][up] for f in feats.values()) - min(f['lo'][up] for f in feats.values())
    if height < 1e-9:
        raise ValueError('竖直轴上没有高度，请更换生长轴')
    ground = float(np.quantile([f['lo'][up] for f in feats.values()], .02))
    min_height = height * cfg['min_stem_fraction']
    ref_info = []
    if reference:
        for ids in components(reference):
            if len(ids) < 3:
                continue
            f = shape(np.asarray(reference['vertices'])[ids], up)
            ref_info.append(dict(vertices=len(ids), height=f['height'], ratio=f['ratio']))
        long = [f['height'] for f in ref_info if f['ratio'] >= cfg['stem_ratio'] and f['height'] > min_height]
        if long:
            min_height = min(min_height, max(long) * .45)
    stem_names = {n for n, f in feats.items() if f['height'] >= min_height and
                  f['lo'][up] <= ground + height * cfg['root_fraction'] and f['ratio'] >= cfg['stem_ratio']}
    if not stem_names:
        raise ValueError('没有识别到茎，请调整生长轴、最小茎长度或细长度')
    byname = {r['name']: r for r in records}
    curves = {n: centerline(byname[n], up) for n in stem_names}
    pair_tol = height * cfg['pair_fraction']
    pairs = {n: [] for n in stem_names}
    for i, a in enumerate(sorted(stem_names)):
        for b in sorted(stem_names)[i+1:]:
            d = np.linalg.norm(curves[a] - curves[b], axis=1)
            if max(d[0], d[-1]) <= pair_tol * 1.5 and np.quantile(d, .9) <= pair_tol:
                score = float(d.mean())
                pairs[a].append((score, b))
                pairs[b].append((score, a))
    for candidates in pairs.values():
        candidates.sort()

    def unique_partner(n):
        p = pairs[n]
        if not p:
            return None
        if len(p) > 1 and p[1][0] < max(p[0][0] * cfg['ambiguity_ratio'], pair_tol * .3):
            return None
        return p[0][1]

    groups, used = [], set()
    for name in sorted(stem_names):
        if name in used:
            continue
        partner = unique_partner(name)
        names = [name]
        pair_uncertain = bool(pairs[name])
        if partner and partner not in used and unique_partner(partner) == name:
            names.append(partner)
            pair_uncertain = False
        used.update(names)
        groups.append(dict(id=len(groups)+1, stems=names, members=list(names),
                           stem_uncertain=pair_uncertain, manual_confirmed=False))
    stem_group = {n: g['id'] for g in groups for n in g['stems']}
    geometries = {g['id']: group_geometry(byname, g['stems'], up) for g in groups}
    assignments = {n: dict(group=stem_group[n], kind='STEM', state='ASSIGNED', candidates=[], reason='茎：根部、细长度与全长曲线') for n in sorted(stem_names)}
    for name in feats:
        if name in stem_names:
            continue
        assignment = classify(byname[name], geometries, cfg, height, ground)
        assignments[name] = assignment
        if assignment['group']:
            groups[assignment['group']-1]['members'].append(name)
    # Empty/degenerate records remain visible in the review queue.
    for r in records:
        if r['name'] not in assignments:
            assignments[r['name']] = dict(group=0, kind='UNKNOWN', state='UNASSIGNED', candidates=[], reason='空网格')
    result = dict(version=1, config=cfg, height=height, ground=ground, reference_components=ref_info,
                  groups=groups, assignments=assignments)
    refresh(result)
    return result


def refresh(plan):
    for g in plan['groups']:
        blockers = [n for n, a in plan['assignments'].items() if not a['group'] and
                    any(c['group'] == g['id'] for c in a['candidates'])]
        g['blockers'] = blockers
        g['ready'] = bool(g.get('manual_confirmed') or (not g['stem_uncertain'] and not blockers and len(g['members']) > len(g['stems'])))
    plan['summary'] = dict(objects=len(plan['assignments']), stems=sum(len(g['stems']) for g in plan['groups']),
                           groups=len(plan['groups']), ready=sum(g['ready'] for g in plan['groups']),
                           review=sum(a['state'] != 'ASSIGNED' for a in plan['assignments'].values()))
