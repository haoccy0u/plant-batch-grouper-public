# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Persistent work queue. Finished sources never re-enter the review workspace."""
import bpy
import json
import uuid
import hashlib
import colorsys
import copy
import numpy as np
from bpy.app.handlers import persistent
from . import core
from .mesh_ops import record, signature, fingerprint, validate_source, schema

KEY = 'pbg_plan_json'
UID = 'PBG_UID'
VIEW = 'pbg_workspace_view_v2'
DIRTY = set()


def uid(item):
    if not item.get(UID):
        item[UID] = uuid.uuid4().hex
    return item[UID]


def index_objects(scene, plan=None):
    result = {}
    # A user duplicate inherits custom properties. Keep the recorded original ID.
    preferred = {k:v.get('name') for k,v in (plan or {}).get('objects', {}).items()}
    for obj in sorted(scene.objects, key=lambda o: o.name != preferred.get(o.get(UID))):
        key = obj.get(UID)
        if key and key in result:
            if not obj.is_editable or obj.library:
                continue
            obj[UID] = uuid.uuid4().hex
            key = obj[UID]
        if key:
            result[key] = obj
    return result


def digest(rec):
    return hashlib.sha256(repr((rec['vertices'],rec['edges'],rec.get('polygon_count'))).encode()).hexdigest()


def snapshot(obj):
    r = record(obj)
    return dict(name=obj.name, geometry=r, digest=digest(r),
                surface_digest=hashlib.sha256(repr(r['triangles']).encode()).hexdigest())


def load(scene):
    if not scene.get(KEY):
        raise ValueError('请先指定集合并分析')
    return json.loads(scene[KEY])


def save(scene, plan):
    summarize(plan)
    scene[KEY] = json.dumps(plan, ensure_ascii=False)


def group(plan, gid):
    return next(g for g in plan['groups'] if g['id'] == gid)


def pending_groups(plan):
    return [g for g in plan['groups'] if not g.get('done')]


def completed_ids(plan):
    return {i for c in plan.get('completed',[]) for i in c['sources']}


def pending_ids(plan):
    return set(plan['assignments']) - completed_ids(plan) - set(plan.get('ignored_zero_face',{}))


def summarize(plan):
    finished = completed_ids(plan)
    for g in pending_groups(plan):
        g['blockers'] = [i for i,a in plan['assignments'].items() if i not in finished and not a['group'] and
                         g['id'] not in a.get('excluded',[]) and any(c['group']==g['id'] for c in a['candidates'])]
        g['errors'] = [i for i in g['members'] if plan['objects'].get(i,{}).get('error')]
        if g.get('schema_error'):
            g['errors'].append('schema')
        manual_review = any(plan['assignments'][i].get('manual_review') for i in g['members'])
        g['ready'] = bool(not g['errors'] and not g['blockers'] and not g.get('stem_review') and
                          not g.get('stem_uncertain') and not manual_review and len(g['members'])>=len(g['stems'])>0)
        if g['errors'] or g['blockers']:
            g['confirmed'] = False
    pending = pending_groups(plan)
    unassigned = [i for i,a in plan['assignments'].items() if i not in finished and not a['group']]
    plan['summary'] = dict(groups=len(pending), confirmed=sum(g.get('confirmed',False) for g in pending),
                           ready=sum(g['ready'] and not g.get('confirmed') for g in pending),
                           unassigned=len(unassigned), completed=len(plan.get('completed',[])),
                           objects=len(pending_ids(plan)), all_complete=not pending and not unassigned)


def descendants(collection):
    result = {collection}
    for child in collection.children:
        result |= descendants(child)
    return result


def forget_objects(plan, ids, keep_ignored=False):
    """Remove pending objects and their references without touching completed batches."""
    ids = set(ids) - completed_ids(plan)
    if ids and plan.get('engine') == 'GRAPH':
        plan['graph_dirty'] = True
    affected = set()
    for g in pending_groups(plan):
        g.setdefault('stem_basis',list(g['stems']))
        if ids.intersection(g['members']) or ids.intersection(g['stems']):
            affected.add(g['id'])
        g['members'] = [i for i in g['members'] if i not in ids]
        g['stems'] = [i for i in g['stems'] if i not in ids]
        g['blockers'] = [i for i in g.get('blockers',[]) if i not in ids]
        g['errors'] = [i for i in g.get('errors',[]) if i not in ids]
        if g['id'] in affected:
            g['schema_error'] = False
            g['confirmed'] = False
    removed_groups = {g['id'] for g in pending_groups(plan) if not g['stems']}
    plan['groups'] = [g for g in plan['groups'] if g['id'] not in removed_groups]
    for i in ids:
        plan['assignments'].pop(i,None)
        if not keep_ignored:
            plan['objects'].pop(i,None)
            plan.get('ignored_zero_face',{}).pop(i,None)
    detached = set()
    for i,a in plan['assignments'].items():
        a['candidates'] = [c for c in a.get('candidates',[]) if c['group'] not in removed_groups]
        a['excluded'] = [g for g in a.get('excluded',[]) if g not in removed_groups]
        if a['group'] in removed_groups:
            a.update(group=0,state='UNASSIGNED',reason='原茎没有有效面，请修复后局部刷新',manual_review=True)
            detached.add(i)
    plan['dirty_ids'] = sorted((set(plan.get('dirty_ids',[])) | detached) - ids)
    return affected | removed_groups


def exclude_zero_face(context, plan):
    """Upgrade geometry records and put zero-face sources outside the work queue."""
    ignored = plan.setdefault('ignored_zero_face',{})
    objects = index_objects(context.scene,plan)
    newly_ignored = set()
    for i in pending_ids(plan):
        obj = objects.get(i)
        if not obj or obj.type != 'MESH':
            continue
        cached = plan['objects'].get(i,{})
        geometry = cached.get('geometry')
        # Keep old coordinates: an actual move since the last analysis must still
        # be detected, while adding the field alone must not dirty every group.
        if geometry is not None and 'polygon_count' not in geometry:
            geometry['polygon_count'] = len(obj.data.polygons)
            cached['digest'] = digest(geometry)
        if len(obj.data.polygons):
            continue
        a = plan['assignments'][i]
        ignored[i] = dict(name=obj.name,assignment=copy.deepcopy(a))
        if a.get('kind') == 'STEM' and a.get('group'):
            ignored[i]['group'] = copy.deepcopy(group(plan,a['group']))
        plan['objects'][i] = snapshot(obj)
        newly_ignored.add(i)
    affected = forget_objects(plan,newly_ignored,keep_ignored=True)
    plan['schema_minor'] = 1
    return newly_ignored, affected


def validate_collections(context, plan=None):
    p = context.scene.pbg_settings
    if not p.source or not p.output:
        raise ValueError('必须指定来源集合和输出集合')
    if p.output in descendants(p.source) or p.source in descendants(p.output):
        raise ValueError('来源集合和输出集合不能相同或互相嵌套')
    scene_collections = descendants(context.scene.collection)
    if p.source not in scene_collections or p.output not in scene_collections:
        raise ValueError('两个集合必须属于当前场景')
    if plan and plan.get('source_uid') and uid(p.source) != plan['source_uid']:
        raise ValueError('来源集合已改变，请切回原集合或显式重新分析')
    return p.source, p.output


def collect_outputs(scene, source, plan):
    """Recover earlier batches even when v1 reanalysis discarded their groups."""
    existing = {c['output'] for c in plan.get('completed',[])}
    source_names = set(source.all_objects.keys()) if source else set(scene.objects.keys())
    for obj in scene.objects:
        if not obj.get('PBG_Output') or not obj.get('PBG_SourceObjects'):
            continue
        names = json.loads(obj['PBG_SourceObjects'])
        if not any(n in source_names for n in names):
            continue
        oid = uid(obj)
        if oid in existing:
            continue
        ids = []
        for name in names:
            original = scene.objects.get(name)
            if original:
                ids.append(uid(original))
        plan['completed'].append(dict(output=oid, name=obj.name, sources=ids, source_names=names))
        existing.add(oid)


def migrate(context):
    scene = context.scene
    old = load(scene)
    old.setdefault('engine','LEGACY')
    if old.get('version') == 2:
        if old.get('schema_minor',0) < 1:
            exclude_zero_face(context,old)
            save(scene,old)
        return old
    p = scene.pbg_settings
    # V1's default value was not serialized when the user left it unchanged.
    if not p.is_property_set('start_number'):
        p.start_number = 3
    plan = dict(old, version=2, session=uuid.uuid4().hex, objects={}, assignments={}, completed=[],
                source_uid=uid(p.source) if p.source else None, next_number=p.start_number, dirty_ids=[])
    names = {}
    for name, a in old['assignments'].items():
        obj = scene.objects.get(name)
        key = uid(obj) if obj else 'missing_'+uuid.uuid4().hex
        names[name] = key
        plan['objects'][key] = snapshot(obj) if obj else dict(name=name,error='来源对象不存在')
        plan['assignments'][key] = dict(a, manual=a['reason']=='人工指定', excluded=[])
        if obj and name in old.get('fingerprints',{}):
            if fingerprint(obj) != old['fingerprints'][name]:
                plan['dirty_ids'].append(key)
    for g in plan['groups']:
        g['members'] = [names[n] for n in g['members'] if n in names]
        g['stems'] = [names[n] for n in g['stems'] if n in names]
        g['confirmed'] = bool(g.get('manual_confirmed'))
        g['done'] = bool(g.get('output') and scene.objects.get(g['output']))
    collect_outputs(scene,p.source,plan)
    done = completed_ids(plan)
    for g in plan['groups']:
        if g['stems'] and all(i in done for i in g['stems']):
            g['done'] = True
            # A late leftover leaf must remain in the unassigned pool.
            for i in g['members']:
                if i not in done:
                    plan['assignments'][i].update(group=0,state='UNASSIGNED',candidates=[],reason='原茎已完成，需人工归属')
        elif not g['done']:
            g['members'] = [i for i in g['members'] if i not in done]
    plan.pop('fingerprints',None)
    exclude_zero_face(context,plan)
    save(scene,plan)
    return plan


def start_analysis(context):
    source, output = validate_collections(context)
    p = context.scene.pbg_settings
    previous = migrate(context) if context.scene.get(KEY) else None
    if previous and previous.get('source_uid') != uid(source):
        previous = None
    shell = dict(completed=list(previous.get('completed',[])) if previous else [])
    collect_outputs(context.scene,source,shell)
    done = completed_ids(shell)
    selected = set(context.selected_objects)
    source_objects = [o for o in source.all_objects if o.type=='MESH' and not o.get('PBG_Output') and
               uid(o) not in done and o!=p.reference and not (p.protect_numbered and o.name.isdigit()) and
               (not p.selected_only or o in selected)]
    objects = [o for o in source_objects if len(o.data.polygons)]
    ignored_objects = [o for o in source_objects if not len(o.data.polygons)]
    if not objects:
        if previous and not pending_ids(previous):
            return previous
        raise ValueError('来源集合没有可分析的有面未完成部件')
    for obj in objects:
        validate_source(obj)
    cfg = {k:getattr(p,k) for k in core.DEFAULTS}
    cfg['up'] = int(p.up)
    engine = getattr(p,'analysis_method','LEGACY')
    records = [record(o) for o in objects]
    if engine == 'GRAPH':
        from . import connection_graph
        for obj, rec in zip(objects,records):
            rec['name'] = uid(obj)
        raw = connection_graph.analyze(records,record(p.reference) if p.reference else None,cfg)
        mapping = {uid(o):uid(o) for o in objects}
    else:
        raw = core.analyze(records,record(p.reference) if p.reference else None,cfg)
        mapping = {o.name:uid(o) for o in objects}
    raw['engine'] = engine
    if engine == 'GRAPH' and previous:
        # Explicit main branches survive reanalysis, including a missing member
        # which must remain a visible error instead of silently losing its ID.
        manual_targets = {a['group'] for a in previous['assignments'].values() if a.get('manual') and a.get('group')}
        explicit = [g for g in pending_groups(previous) if g.get('anchor_manual') or g['id'] in manual_targets]
        protected = {i for g in explicit for i in g['stems']}
        # A manually assigned leaf/side branch cannot become a new automatic
        # main branch merely because its edited shape looks more elongated.
        manual_parts = {i for i,a in previous['assignments'].items()
                        if a.get('manual') and a.get('kind') != 'STEM'}
        for g in raw['groups']:
            g['stems'] = [i for i in g['stems'] if i not in manual_parts]
        raw['groups'] = [g for g in raw['groups'] if not protected.intersection(g['stems'])]
        raw['groups'] = [g for g in raw['groups'] if g['stems']]
        raw_id = max([g['id'] for g in raw['groups']] + [0]) + 1
        for old in explicit:
            raw['groups'].append(dict(id=raw_id,stems=list(old['stems']),members=list(old['stems']),
                                      stem_uncertain=False,anchor_manual=old.get('anchor_manual',False)))
            for i in old['stems']:
                mapping.setdefault(i,i)
                if i in raw['assignments']:
                    raw['assignments'][i].update(group=raw_id,kind='STEM',candidates=[])
            raw_id += 1
    plan = dict(raw, version=2, schema_minor=1, session=previous['session'] if previous else uuid.uuid4().hex,
                source_uid=uid(source), objects={uid(o):snapshot(o) for o in objects},
                completed=shell['completed'], next_number=previous['next_number'] if previous else p.start_number,
                assignments={}, dirty_ids=[], ignored_zero_face={uid(o):dict(name=o.name) for o in ignored_objects})
    plan['objects'].update({uid(o):snapshot(o) for o in ignored_objects})
    old_by_stems = {frozenset(g['stems']):g for g in pending_groups(previous)} if previous else {}
    next_gid = max((g['id'] for g in previous['groups']),default=0)+1 if previous else 1
    gid_map = {}
    for g in plan['groups']:
        g['stems'] = [mapping[n] for n in g['stems']]
        g['members'] = [mapping[n] for n in g['members']]
        matched = old_by_stems.get(frozenset(g['stems']))
        gid_map[g['id']] = matched['id'] if matched else next_gid
        if not matched:
            next_gid += 1
        g['id'] = gid_map[g['id']]
        if matched and matched.get('anchor_manual') and engine == 'GRAPH':
            g['anchor_manual'] = True
            g['stem_basis'] = list(matched.get('stem_basis',g['stems']))
        g['confirmed'] = False
        g['done'] = False
    for name,a in raw['assignments'].items():
        a['group'] = gid_map.get(a['group'],0)
        a['candidates'] = [c for c in a['candidates'] if c['group'] in gid_map]
        for c in a['candidates']:
            c['group'] = gid_map[c['group']]
        plan['assignments'][mapping[name]] = dict(a,manual=False,excluded=[])
    valid_gids = {g['id'] for g in plan['groups']}
    if previous:
        for i,a in plan['assignments'].items():
            old = previous['assignments'].get(i)
            if not old:
                continue
            a['excluded'] = [g for g in old.get('excluded',[]) if g in valid_gids]
            a['candidates'] = [c for c in a['candidates'] if c['group'] not in a['excluded']]
            if old.get('manual'):
                a.update(manual=True,group=old['group'] if old['group'] in valid_gids else 0,
                         reason='保留人工归属' if old['group'] in valid_gids else '原人工组已改变，请重新指定',manual_review=True)
        # Missing objects are explicit conflicts, never silently dropped.
        for i in pending_ids(previous)-set(plan['assignments'])-set(plan['ignored_zero_face']):
            plan['objects'][i] = previous['objects'][i]
            plan['objects'][i]['error'] = '原来源未包含在本次分析，请恢复对象或来源范围'
            plan['assignments'][i] = dict(previous['assignments'][i],group=0,candidates=[],state='UNASSIGNED')
    rebuild_members(plan)
    if engine == 'GRAPH':
        # Re-evaluate with stable group IDs and preserved manual decisions.
        from . import graph_workflow
        graph_workflow.refresh(context,plan,set(plan['assignments']),set())
    save(context.scene,plan)
    return plan


def rebuild_members(plan):
    for g in pending_groups(plan):
        g['members'] = [i for i,a in plan['assignments'].items() if a['group']==g['id'] and i not in completed_ids(plan)]


def stem_pairing(plan, records):
    """The original stem and pairing rules, with this session's fixed scale."""
    cfg = plan['config']
    up, height = cfg['up'], plan['height']
    minimum = height * cfg['min_stem_fraction']
    reference = [r['height'] for r in plan.get('reference_components',[])
                 if r['ratio'] >= cfg['stem_ratio'] and r['height'] > minimum]
    if reference:
        minimum = min(minimum,max(reference)*.45)
    curves = {}
    for i, rec in records.items():
        if not rec.get('vertices') or rec.get('polygon_count',1) == 0:
            continue
        try:
            shape = core.shape(rec['vertices'],up)
            if (shape['height'] >= minimum and shape['lo'][up] <= plan['ground']+height*cfg['root_fraction']
                    and shape['ratio'] >= cfg['stem_ratio']):
                curves[i] = core.centerline(rec,up)
        except (KeyError,ValueError,IndexError,np.linalg.LinAlgError):
            continue
    pairs = {i:[] for i in curves}
    tolerance = height * cfg['pair_fraction']
    ids = sorted(curves)
    for offset, a in enumerate(ids):
        for b in ids[offset+1:]:
            distance = np.linalg.norm(curves[a]-curves[b],axis=1)
            if max(distance[0],distance[-1]) <= tolerance*1.5 and np.quantile(distance,.9) <= tolerance:
                score = float(distance.mean())
                pairs[a].append((score,b))
                pairs[b].append((score,a))
    for candidates in pairs.values():
        candidates.sort()
    return pairs


def revalidate_stems(plan, old_records, changed_groups):
    """Validate the existing sets; never repair a pairing by changing membership."""
    active = pending_groups(plan)
    if not changed_groups and not any(g.get('stem_review') for g in active):
        return set()
    ids = {i for g in active for i in g['stems']}
    records = {i:v['geometry'] for i,v in plan['objects'].items()
               if i in ids and 'geometry' in v and not v.get('error')}
    pairs = stem_pairing(plan,records)
    old_pairs = stem_pairing(plan,old_records) if changed_groups else pairs
    affected = set(changed_groups)
    for g in active:
        if g.get('stem_review') or any(old_pairs.get(i) != pairs.get(i) for i in g['stems']):
            affected.add(g['id'])
    tolerance = plan['height'] * plan['config']['pair_fraction']
    def unique_partner(i):
        candidates = pairs.get(i,[])
        if not candidates:
            return None
        if len(candidates)>1 and candidates[1][0] < max(candidates[0][0]*plan['config']['ambiguity_ratio'],tolerance*.3):
            return None
        return candidates[0][1]
    for g in active:
        if g['id'] not in affected:
            continue
        stems = g['stems']
        valid = (set(stems)==set(g.get('stem_basis',stems))
                 and all(i in pairs for i in stems))
        if len(stems)==1:
            valid = valid and not pairs.get(stems[0])
        elif len(stems)==2:
            valid = valid and unique_partner(stems[0])==stems[1] and unique_partner(stems[1])==stems[0]
        else:
            valid = False
        g['stem_review'] = not valid
        if valid:
            # A formerly uncertain single can become unambiguous after moving
            # away. This is fresh geometric proof, never a manual waiver.
            g['stem_uncertain'] = False
        g['confirmed'] = False
    return affected


def sync(context, plan):
    """Scan cheap geometry digests; recompute attachment distances only locally."""
    context.view_layer.update()
    old_stem_groups = {g['id']:set(g['stems']) for g in pending_groups(plan)}
    old_stem_ids = set().union(*old_stem_groups.values()) if old_stem_groups else set()
    old_stem_records = {i:v['geometry'] for i,v in plan['objects'].items()
                        if i in old_stem_ids and 'geometry' in v and not v.get('error')}
    for g in pending_groups(plan):
        g.setdefault('stem_basis',list(g['stems']))
    ignored_now, excluded_affected = exclude_zero_face(context,plan)
    objects = index_objects(context.scene,plan)
    changed = set(plan.pop('dirty_ids',[])) | ignored_now
    for i, ignored in list(plan.get('ignored_zero_face',{}).items()):
        obj = objects.get(i)
        if not obj or obj.type != 'MESH' or not len(obj.data.polygons):
            if obj:
                ignored['name'] = obj.name
            continue
        # Restoring faces is a local edit. Restore a known stem to its stable
        # group, otherwise classify the new part against existing stems.
        a = copy.deepcopy(ignored.get('assignment',{}))
        if a.get('kind') == 'STEM' and ignored.get('group'):
            template = ignored['group']
            target = next((g for g in pending_groups(plan) if g['id']==template['id']),None)
            if target is None:
                target = dict(template,stems=[],members=[],confirmed=False,done=False,stem_review=True)
                plan['groups'].append(target)
            if i not in target['stems']:
                target['stems'].append(i)
            a.update(group=target['id'],state='ASSIGNED')
        else:
            valid_gids = {g['id'] for g in pending_groups(plan)}
            original_gid = a.get('group',0)
            if original_gid not in valid_gids:
                original_gid = 0
            a = dict(a,group=original_gid,kind='UNKNOWN',state='UNASSIGNED',candidates=[],
                     reason='对象新增有效面，正在刷新归属',manual=a.get('manual',False),excluded=a.get('excluded',[]))
        plan['assignments'][i] = a
        plan['objects'][i] = snapshot(obj)
        del plan['ignored_zero_face'][i]
        changed.add(i)
    for i in pending_ids(plan):
        cached = plan['objects'][i]
        obj = objects.get(i)
        if not obj or obj.type!='MESH':
            if not cached.get('error'):
                changed.add(i)
            cached['error'] = '来源对象不存在或不再是网格'
            continue
        now = snapshot(obj)
        try:
            validate_source(obj)
        except ValueError as exc:
            now['error'] = str(exc)
        surface_changed = (plan.get('engine') == 'GRAPH' and cached.get('surface_digest') != now['surface_digest'])
        if cached.get('digest') != now['digest'] or cached.get('error') != now.get('error') or surface_changed:
            changed.add(i)
        plan['objects'][i] = now
    rebuild_members(plan)
    active = pending_groups(plan)
    for g in active:
        schemas = [schema(objects[i]) for i in g['members'] if i in objects and objects[i].type=='MESH']
        g['schema_error'] = bool(schemas and any(s!=schemas[0] for s in schemas[1:]))
    if plan.get('engine') == 'GRAPH':
        from . import graph_workflow
        graph_workflow.refresh(context,plan,changed,excluded_affected)
        DIRTY.clear()
        save(context.scene,plan)
        enforce_completed(context,plan)
        return plan
    changed_stems = {gid for gid,ids in old_stem_groups.items() if ids & changed}
    changed_stems |= {g['id'] for g in active if set(g['stems']) & changed}
    stem_affected = revalidate_stems(plan,old_stem_records,changed_stems)
    if not changed:
        plan['last_refresh'] = dict(changed=0,evaluated=0,affected_groups=sorted(stem_affected))
        DIRTY.clear()
        save(context.scene,plan)
        enforce_completed(context,plan)
        return plan
    geometries = {}
    for g in active:
        try:
            geometries[g['id']] = core.group_geometry({i:v['geometry'] for i,v in plan['objects'].items() if 'geometry' in v and not v.get('error')},g['stems'],plan['config']['up'])
        except (KeyError,ValueError,IndexError):
            g['stem_review'] = True
    affected = set(changed_stems) | excluded_affected | stem_affected
    evaluated = []
    for i in pending_ids(plan):
        a = plan['assignments'][i]
        if a['kind']=='STEM':
            continue
        if i not in changed and not changed_stems:
            continue
        old_gid = a['group']
        old_candidates = {c['group'] for c in a['candidates']}
        cached = plan['objects'][i]
        if cached.get('error'):
            if old_gid:
                affected.add(old_gid)
            continue
        fresh = core.classify(cached['geometry'],geometries,plan['config'],plan['height'],plan['ground'],a.get('excluded',[]))
        new_candidates = {c['group'] for c in fresh['candidates']}
        if i not in changed and not ((old_candidates|new_candidates|{old_gid}) & changed_stems):
            continue
        affected |= old_candidates | new_candidates | {old_gid,fresh['group']}
        if a.get('manual'):
            contact_ok = any(c['group']==old_gid and c['score']<=1 for c in fresh['candidates'])
            fresh.update(group=old_gid,state='ASSIGNED' if old_gid else 'REVIEW',
                         reason='保留人工归属；连接需复查' if not contact_ok else '人工归属；连接已刷新',
                         manual_review=not contact_ok)
        a.update(fresh)
        evaluated.append(i)
    for i in changed:
        a = plan['assignments'].get(i)
        if a and a['group']:
            affected.add(a['group'])
    for g in active:
        if g['id'] in affected:
            g['confirmed'] = False
    rebuild_members(plan)
    for g in active:
        schemas = [schema(objects[i]) for i in g['members'] if i in objects and objects[i].type=='MESH']
        g['schema_error'] = bool(schemas and any(s!=schemas[0] for s in schemas[1:]))
    plan['last_refresh'] = dict(changed=len(changed),evaluated=len(evaluated),affected_groups=sorted(affected-{0}))
    DIRTY.clear()
    save(context.scene,plan)
    enforce_completed(context,plan)
    return plan


def confirm(plan, gid):
    summarize(plan)
    g = group(plan,gid)
    if g.get('done'):
        raise ValueError('该组已经完成')
    if g['errors']:
        raise ValueError('本组有缺失来源、不支持的数据或 UV/颜色结构差异，请先修复')
    if g['blockers']:
        raise ValueError(f"本组还有 {len(g['blockers'])} 个待定部件，请加入或标记不属于本组")
    if not g['stems'] or not g['members']:
        raise ValueError('本组没有有效茎或成员')
    if plan.get('engine') == 'GRAPH' and (g.get('stem_review') or g.get('stem_uncertain')):
        raise ValueError('主枝连接仍有疑问，请检查后将主枝设为锚点，或修正位置并刷新')
    g.update(confirmed=True,stem_review=False,stem_uncertain=False)
    for i in g['members']:
        plan['assignments'][i]['manual_review'] = False


def edit_members(context, plan, gid, action):
    target = group(plan,gid)
    if target.get('done'):
        raise ValueError('该组已经完成')
    ids = [o.get(UID) for o in context.selected_objects if o.get(UID) in pending_ids(plan)]
    if not ids:
        raise ValueError('请选择未完成的叶片或花头')
    if any(plan['assignments'][i]['kind']=='STEM' for i in ids):
        raise ValueError('本轮只支持手动调整叶片和花头；茎片需调整识别参数')
    for i in ids:
        a = plan['assignments'][i]
        old = a['group']
        if action=='REMOVE' and old!=gid:
            raise ValueError('移出操作只能用于当前组成员')
        if action=='EXCLUDE' and old:
            raise ValueError('已有归属的部件请使用移出或加入本组')
    for i in ids:
        a = plan['assignments'][i]
        old = a['group']
        if old:
            group(plan,old)['confirmed'] = False
        target['confirmed'] = False
        if action=='ADD':
            a.update(group=gid,manual=True,manual_review=False,state='ASSIGNED',reason='人工指定')
            a['excluded'] = [x for x in a.get('excluded',[]) if x!=gid]
        else:
            a.update(group=0,manual=True,manual_review=False,state='REVIEW',reason='已从当前组排除')
            a['excluded'] = sorted(set(a.get('excluded',[]))|{gid})
            a['candidates'] = [c for c in a['candidates'] if c['group']!=gid]
    rebuild_members(plan)
    if plan.get('engine') == 'GRAPH':
        plan['graph_dirty'] = True
        plan['graph_decision_ids'] = sorted(set(plan.get('graph_decision_ids',[])) | set(ids))
    save(context.scene,plan)


def layer_collections(root):
    yield root
    for child in root.children:
        yield from layer_collections(child)


def output_visibility(context, hidden):
    output = context.scene.pbg_settings.output
    if not output:
        return
    for layer in layer_collections(context.view_layer.layer_collection):
        if layer.collection==output:
            layer.hide_viewport = hidden


def enforce_completed(context, plan):
    objects = index_objects(context.scene,plan)
    for i in completed_ids(plan):
        obj = objects.get(i)
        if obj and obj.name in context.view_layer.objects:
            obj.hide_set(True)
    from . import pivot
    pivot.enforce_completed(context)


def gather_results(context, plan):
    from . import pivot
    _, output = validate_collections(context,plan)
    objects = index_objects(context.scene,plan)
    pivot_entries = pivot.load(context.scene)['entries']
    for c in plan['completed']:
        obj = objects.get(c['output'])
        if not obj or not obj.get('PBG_Output'):
            continue
        # Completed origin preparation owns the result's new collection.
        if pivot.is_completed(context.scene, obj, pivot_entries):
            continue
        if obj.name not in output.objects:
            output.objects.link(obj)
        for collection in list(obj.users_collection):
            if collection!=output:
                collection.objects.unlink(obj)
    output_visibility(context,True)
    enforce_completed(context,plan)


def remember_view(context, plan):
    if context.scene.get(VIEW):
        return
    state = dict(objects={},views=[])
    for obj in context.view_layer.objects:
        if not obj.get(UID) and (obj.library or not obj.is_editable):
            continue
        state['objects'][uid(obj)] = dict(hidden=obj.hide_get(),color=list(obj.color),selected=obj.select_get())
    for area in context.screen.areas if context.screen else []:
        if area.type=='VIEW_3D':
            state['views'].append(dict(pointer=area.as_pointer(),type=area.spaces.active.shading.type,
                                      color=area.spaces.active.shading.color_type))
    context.scene[VIEW] = json.dumps(state)


def preview(context, plan):
    remember_view(context,plan)
    objects = index_objects(context.scene,plan)
    for i in pending_ids(plan):
        obj = objects.get(i)
        if not obj:
            continue
        a = plan['assignments'][i]
        anchor_problem = (plan.get('engine')=='GRAPH' and a['kind']=='STEM' and a['group'] and
                          any(g['id']==a['group'] and (g.get('stem_review') or g.get('stem_uncertain')) for g in pending_groups(plan)))
        rgb = colorsys.hsv_to_rgb(.07+((a['group']*.61803398875)%1)*.82,.72,.9) if a['group'] and not a.get('manual_review') and not anchor_problem else (1,.12,.025)
        obj.color = (*rgb,1)
    p=context.scene.pbg_settings
    for i in plan.get('ignored_zero_face',{}):
        obj = objects.get(i)
        if obj and obj.name in context.view_layer.objects:
            obj.hide_set(True)
    # References are outside the queue; temporarily hide them in the work view.
    for obj in p.source.all_objects if p.source else []:
        if obj==p.reference or (p.protect_numbered and obj.name.isdigit() and obj.get(UID) not in pending_ids(plan)):
            if obj.name in context.view_layer.objects:
                obj.hide_set(True)
    for area in context.screen.areas if context.screen else []:
        if area.type=='VIEW_3D':
            shading = area.spaces.active.shading
            if 'SOLID' in shading.bl_rna.properties['type'].enum_items.keys() and 'OBJECT' in shading.bl_rna.properties['color_type'].enum_items.keys():
                shading.type='SOLID'
                shading.color_type='OBJECT'
    enforce_completed(context,plan)


def inspect_group(context,plan,gid,extra=(),frame=True):
    g = group(plan,gid)
    if g.get('done'):
        raise ValueError('该组已完成')
    remember_view(context,plan)
    ids = set(g['members'])|set(g.get('blockers',[]))|set(extra)
    objects = index_objects(context.scene,plan)
    for i in pending_ids(plan):
        obj=objects.get(i)
        if obj and obj.name in context.view_layer.objects:
            obj.hide_set(i not in ids)
            obj.select_set(i in g['members'])
    for i in g['stems']:
        if i in objects:
            context.view_layer.objects.active=objects[i]
            break
    plan['current_group']=gid
    save(context.scene,plan)
    context.scene.pbg_settings.current_group=gid
    preview(context,plan)
    output_visibility(context,True)
    if frame and context.screen:
        for area in context.screen.areas:
            if area.type=='VIEW_3D':
                region=next((r for r in area.regions if r.type=='WINDOW'),None)
                if region:
                    with context.temp_override(area=area,region=region):
                        bpy.ops.view3d.view_selected(use_all_regions=False)
                break


def show_remaining(context,plan):
    remember_view(context,plan)
    objects=index_objects(context.scene,plan)
    for i in pending_ids(plan):
        obj=objects.get(i)
        if obj and obj.name in context.view_layer.objects:
            obj.hide_set(False)
    preview(context,plan)
    output_visibility(context,True)


def restore_view(context):
    if not context.scene.get(VIEW):
        return
    state=json.loads(context.scene[VIEW])
    objects=index_objects(context.scene)
    for i,s in state['objects'].items():
        obj=objects.get(i)
        if obj:
            obj.color=s['color']
            if obj.name in context.view_layer.objects:
                obj.hide_set(s['hidden'])
                obj.select_set(s['selected'])
    for area in context.screen.areas if context.screen else []:
        for item in state['views']:
            if area.type=='VIEW_3D' and area.as_pointer()==item['pointer']:
                area.spaces.active.shading.type=item['type']
                area.spaces.active.shading.color_type=item['color']
    del context.scene[VIEW]
    if context.scene.get(KEY):
        plan=load(context.scene)
        if plan.get('version')==2:
            enforce_completed(context,plan)


@persistent
def track_changes(scene,depsgraph):
    for update in depsgraph.updates:
        item=update.id
        if isinstance(item,bpy.types.Object) and item.get(UID) and (update.is_updated_geometry or update.is_updated_transform):
            DIRTY.add(item[UID])


@persistent
def loaded(_):
    DIRTY.clear()
    if hasattr(bpy.types.Scene,'pbg_settings'):
        from . import ui
        ui.schedule_resume()


def register_handlers():
    for collection,fn in [(bpy.app.handlers.depsgraph_update_post,track_changes),(bpy.app.handlers.load_post,loaded)]:
        if fn not in collection:
            collection.append(fn)


def unregister_handlers():
    for collection,fn in [(bpy.app.handlers.depsgraph_update_post,track_changes),(bpy.app.handlers.load_post,loaded)]:
        while fn in collection:
            collection.remove(fn)
