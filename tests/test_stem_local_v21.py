# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Focused regression: safe local stem validation without changing any group."""
import bpy
import json
import sys
from pathlib import Path

from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
import plant_batch_grouper as addon
from plant_batch_grouper import workflow as w, output

addon.register()
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj,do_unlink=True)
source = bpy.data.collections.new('StemLocalSource')
destination = bpy.data.collections.new('StemLocalOutput')
bpy.context.scene.collection.children.link(source)
bpy.context.scene.collection.children.link(destination)
p = bpy.context.scene.pbg_settings
p.analysis_method = 'LEGACY'
p.source, p.output = source, destination

def stem(name,x):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x-.001,0,0),(x+.001,0,0),(x+.001,0,1),(x-.001,0,1)],[],[(0,1,2,3)])
    mesh.update()
    obj = bpy.data.objects.new(name,mesh)
    source.objects.link(obj)
    return obj

a,b,c,d = [stem(name,x) for name,x in [('A',0),('B',1),('C',2),('D',2.004)]]
plan = w.start_analysis(bpy.context)
gids = {o.name:plan['assignments'][w.uid(o)]['group'] for o in [a,b,c,d]}
assert gids['C']==gids['D']
initial = {g['id']:set(g['stems']) for g in plan['groups']}
checks = {}
def check(name,condition):
    assert condition,name
    checks[name] = True
    print('PASS',name,flush=True)

for g in plan['groups']:
    w.confirm(plan,g['id'])
a.location.x = .1
plan = w.sync(bpy.context,plan)
check('translated_bare_stem_ready',w.group(plan,gids['A'])['ready'] and not w.group(plan,gids['A'])['stem_review'])
check('unrelated_confirmation_preserved',w.group(plan,gids['B'])['confirmed'] and w.group(plan,gids['C'])['confirmed'])
c.location.x = d.location.x = .1
plan = w.sync(bpy.context,plan)
check('translated_pair_retains_unique_membership',w.group(plan,gids['C'])['ready'] and not w.group(plan,gids['C'])['stem_review'])
a.location.x = 1.001
plan = w.sync(bpy.context,plan)
check('new_pair_conflict_marks_both_groups',all(w.group(plan,gids[n])['stem_review'] and not w.group(plan,gids[n])['ready'] for n in ['A','B']))
a.location.x = .1
plan = w.sync(bpy.context,plan)
check('separated_pair_conflict_recovers_both_groups',all(w.group(plan,gids[n])['ready'] and not w.group(plan,gids[n])['stem_review'] for n in ['A','B']))
a.location.z = .2
plan = w.sync(bpy.context,plan)
check('root_constraint_not_waived',w.group(plan,gids['A'])['stem_review'] and not w.group(plan,gids['A'])['ready'])
a.location.z = 0
plan = w.sync(bpy.context,plan)
check('root_repair_recovers',w.group(plan,gids['A'])['ready'])
c.location.x = .3
plan = w.sync(bpy.context,plan)
check('split_pair_not_silently_regrouped',w.group(plan,gids['C'])['stem_review'] and not w.group(plan,gids['C'])['ready'])
c.location.x = .1
plan = w.sync(bpy.context,plan)
check('pair_repair_recovers',w.group(plan,gids['C'])['ready'])
g = w.group(plan,gids['A'])
g['stem_uncertain'] = True
a.location.x += .001
plan = w.sync(bpy.context,plan)
check('stale_uncertainty_clears_only_after_safe_revalidation',not g['stem_uncertain'] and not g['stem_review'] and g['ready'])
g['stem_review'] = True
plan = w.sync(bpy.context,plan)
check('legacy_review_flag_recovers_without_new_edit',g['ready'] and not g['stem_review'])

# The minimum-height adjustment must match analysis's reference-component rule.
b.scale.z = .1
plan['reference_components'] = [dict(height=.2,ratio=100,vertices=4)]
plan = w.sync(bpy.context,plan)
check('reference_adjusted_minimum_height_preserved',w.group(plan,gids['B'])['ready'])
records = {w.uid(b):w.record(b)}
plan['reference_components'] = []
check('without_reference_short_stem_rejected',w.uid(b) not in w.stem_pairing(plan,records))
plan['reference_components'] = [dict(height=.2,ratio=100,vertices=4)]
check('group_ids_and_membership_unchanged',initial=={g['id']:set(g['stems']) for g in plan['groups']})
check('fixed_scale_unchanged',plan['height']==1 and plan['ground']==0)

# A vanished paired stem must not authorize output of a leftover single stem.
c.data.clear_geometry()
plan = w.sync(bpy.context,plan)
check('lost_pair_member_does_not_become_safe_single',w.group(plan,gids['C'])['stem_review'] and not w.group(plan,gids['C'])['ready'])
w.confirm(plan,gids['A'])
w.save(bpy.context.scene,plan)
names = output.merge(bpy.context,plan,[gids['A']])
check('translated_bare_stem_still_outputs',len(names)==1)

# Original ambiguous singleton groups can recover when moved clear of every
# pairing candidate. Neighbors that would need a new pair stay blocked.
e,f,h = [stem(name,x) for name,x in [('E',4),('F',4.002),('H',4.004)]]
plan = w.start_analysis(bpy.context)
uncertain_gids = {o.name:plan['assignments'][w.uid(o)]['group'] for o in [e,f,h]}
check('initial_actual_pair_ambiguity',all(w.group(plan,gid)['stem_uncertain'] for gid in uncertain_gids.values()))
e.location.x = -.00001
plan = w.sync(bpy.context,plan)
check('unresolved_actual_ambiguity_not_waived',w.group(plan,uncertain_gids['E'])['stem_uncertain'] and not w.group(plan,uncertain_gids['E'])['ready'])
e.location.x = -.1
plan = w.sync(bpy.context,plan)
check('moving_uncertain_single_clear_recovers',w.group(plan,uncertain_gids['E'])['ready'] and not w.group(plan,uncertain_gids['E'])['stem_uncertain'])
check('new_neighbor_pair_not_silently_regrouped',all(not w.group(plan,uncertain_gids[n])['ready'] for n in ['F','H']))
report = dict(status='PASS',blender=bpy.app.version_string,checks=checks)
(REPORTS/'test_stem_local_report_v21.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('STEM_LOCAL_PASS',len(checks),flush=True)
