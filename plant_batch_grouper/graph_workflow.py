# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Adapt the geometry graph to persistent Blender work queues.

Graph reachability can change beyond an edited object. Recompute its forest when
dirty, but invalidate only decisions whose geometry or graph evidence changed.
Stable anchor IDs, completed sources, thresholds and human choices are retained.
"""
import copy
from . import connection_graph as graph
from . import workflow as w
from .mesh_ops import schema

_GEOMETRY_CACHE = {}


def evidence(assignment):
    return (assignment.get('group',0), assignment.get('kind'),
            assignment.get('manual_review',False), assignment.get('candidates',[]))


def related(assignment):
    return {assignment.get('group',0)} | {c['group'] for c in assignment.get('candidates',[])}


def refresh(context, plan, changed, affected):
    changed, affected = set(changed), set(affected)
    active = w.pending_groups(plan)
    anchors = {g['id']:list(g['stems']) for g in active}
    anchor_key = repr(sorted((gid, sorted(ids)) for gid,ids in anchors.items()))
    dirty = plan.pop('graph_dirty',False) or anchor_key != plan.get('graph_anchor_key')
    decision_ids = set(plan.pop('graph_decision_ids',[]))
    if not changed and not dirty and not decision_ids:
        plan['last_refresh'] = dict(changed=0,evaluated=0,affected_groups=sorted(affected))
        return
    pending = w.pending_ids(plan)
    records = [dict(plan['objects'][i]['geometry'],name=i,
                    _geometry_key=plan['objects'][i].get('digest','')+':'+plan['objects'][i].get('surface_digest','')) for i in sorted(pending)
               if 'geometry' in plan['objects'][i] and not plan['objects'][i].get('error')]
    previous = {i:copy.deepcopy(plan['assignments'][i]) for i in pending}
    decisions = {i:dict(group=a.get('group',0),manual=a.get('manual',False),excluded=a.get('excluded',[]))
                 for i,a in previous.items()}
    for g in active:
        if g.get('anchor_manual'):
            for i in g['stems']:
                decisions.setdefault(i,{})['anchor_manual'] = True
    # Keep one session's derived paths in memory. Saved progress is self-contained
    # and undo is safe because every cached entry is keyed by geometry content.
    session = plan['session']
    if session not in _GEOMETRY_CACHE:
        _GEOMETRY_CACHE.clear()
        _GEOMETRY_CACHE[session] = {}
    result = graph.analyze(records,config=plan['config'],anchors=anchors,decisions=decisions,
                           fixed_scale={key:plan[key] for key in ('height','ground','config')},
                           geometry_cache=_GEOMETRY_CACHE[session])
    for i, fresh in result['assignments'].items():
        old = previous[i]
        fresh.setdefault('manual',old.get('manual',False))
        fresh['excluded'] = list(old.get('excluded',[]))
        # An explicit confirmation is valid until geometry/connection evidence
        # changes. Merely recomputing another branch must not revoke it.
        if (old.get('manual') and not old.get('manual_review') and i not in changed
                and old.get('group') == fresh.get('group')
                and old.get('candidates',[]) == fresh.get('candidates',[])):
            fresh['manual_review'] = False
        if evidence(old) != evidence(fresh) or i in changed or i in decision_ids:
            affected |= related(old) | related(fresh)
        plan['assignments'][i] = fresh
    for i in changed:
        if i in previous:
            affected |= related(previous[i])
    result_groups = {g['id']:g for g in result['groups']}
    objects = w.index_objects(context.scene,plan)
    w.rebuild_members(plan)
    for g in active:
        now = result_groups[g['id']]
        missing = any(plan['objects'].get(i,{}).get('error') for i in g['stems'])
        uncertain = bool(now.get('stem_uncertain') or missing)
        # An incomplete/removed card is not a valid original anchor set.
        incomplete = set(g['stems']) != set(g.get('stem_basis',g['stems']))
        review = uncertain or incomplete
        if bool(g.get('stem_review')) != review or bool(g.get('stem_uncertain')) != uncertain:
            affected.add(g['id'])
        g.update(stem_review=review,stem_uncertain=uncertain)
        if g['id'] in affected:
            g['confirmed'] = False
        schemas = [schema(objects[i]) for i in g['members'] if i in objects and objects[i].type=='MESH']
        g['schema_error'] = bool(schemas and any(s!=schemas[0] for s in schemas[1:]))
    plan['graph_anchor_key'] = anchor_key
    plan['graph_report'] = result['graph_report']
    plan['last_refresh'] = dict(changed=len(changed),evaluated=len(records),
                                affected_groups=sorted(affected-{0}),mode='graph_evidence_diff')
    w.summarize(plan)


def set_anchor(context, plan):
    """Explicitly seed one whole object, or reaffirm its existing paired seed."""
    if plan.get('engine') != 'GRAPH':
        raise ValueError('请先使用连接图方式分析')
    w.sync(context,plan)
    obj = context.view_layer.objects.active
    key = obj.get(w.UID) if obj and obj.select_get() else None
    if key not in w.pending_ids(plan):
        raise ValueError('请在视口选择未完成的枝干对象')
    if plan['objects'][key].get('error') or not len(obj.data.polygons):
        raise ValueError('请选择有有效面的可处理枝干')
    a = plan['assignments'][key]
    old_gid = a['group']
    if a['kind'] == 'STEM' and old_gid:
        target = w.group(plan,old_gid)
    else:
        if old_gid:
            w.group(plan,old_gid)['confirmed'] = False
        gid = max([g['id'] for g in plan['groups']] +
                  [c.get('group',0) for c in plan.get('completed',[])] + [0]) + 1
        target = dict(id=gid,stems=[key],members=[key],confirmed=False,done=False)
        plan['groups'].append(target)
    target.update(anchor_manual=True,stem_review=False,stem_uncertain=False,
                  stem_basis=list(target['stems']),confirmed=False)
    for i in target['stems']:
        plan['assignments'][i].update(group=target['id'],kind='STEM',state='ASSIGNED',
                                      manual=True,manual_review=False,excluded=[],candidates=[],reason='人工指定主枝')
    plan['graph_dirty'] = True
    plan['graph_decision_ids'] = list(target['stems'])
    refresh(context,plan,set(),{target['id'],old_gid}-{0})
    w.save(context.scene,plan)
    return target['id']
