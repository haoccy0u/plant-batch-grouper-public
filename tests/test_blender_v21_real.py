# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

import bpy, sys, json, copy, time, traceback
from pathlib import Path
from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
bpy.ops.wm.open_mainfile(filepath=str(fixture_path('backups/before_v21_20260921_184608.blend')))
old=json.loads(bpy.context.scene['pbg_plan_json'])
import plant_batch_grouper as addon
from plant_batch_grouper import workflow as w, interaction, output
from plant_batch_grouper.mesh_ops import signature
meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']
before=signature(meshes)
counts=len(bpy.context.scene.objects)
outputs={c['name']:signature([bpy.data.objects[c['name']]]) for c in old['completed']}
checks={}
def check(name, cond):
    assert cond,name
    checks[name]=True
    print('PASS',name,flush=True)
addon.register()
plan=w.migrate(bpy.context)
check('real_migration_keeps_48_results',len(plan['completed'])==48 and plan['completed']==old['completed'])
check('real_migration_keeps_all_objects',len(bpy.context.scene.objects)==counts)
check('real_migration_excludes_134_zero_face',len(plan['ignored_zero_face'])==134)
check('real_migration_keeps_20_real_unassigned',plan['summary']['unassigned']==20)
check('real_migration_preserves_pending_group_ids',{g['id'] for g in w.pending_groups(plan)}=={g['id'] for g in w.pending_groups(old)})
check('real_migration_preserves_unaffected_confirmation',all(w.group(plan,g['id'])['confirmed']==g['confirmed'] for g in w.pending_groups(old) if not set(g['members'])&set(plan['ignored_zero_face'])))
check('real_migration_removes_misassigned_dot',all(not set(g['members'])&set(plan['ignored_zero_face']) for g in w.pending_groups(plan)))
check('real_migration_keeps_source_attributes',signature(meshes)==before)
w.show_remaining(bpy.context,plan)
index=w.index_objects(bpy.context.scene,plan)
check('real_preview_hides_ignored_objects',all(index[i].hide_get() for i in plan['ignored_zero_face']))
check('real_preview_hides_completed_sources',all(index[i].hide_get() for i in w.completed_ids(plan)))
plan=w.sync(bpy.context,plan)
check('real_upgrade_does_not_refresh_unchanged_geometry',plan['last_refresh']['changed']==0)
ready=[g['id'] for g in w.pending_groups(plan) if g['ready']]
newnames=interaction.auto_merge(bpy.context,plan)
check('real_auto_batch_completes_ready_groups',len(newnames)==len(ready)>0)
check('real_auto_batch_does_not_duplicate_existing_outputs',all(signature([bpy.data.objects[name]])==s for name,s in outputs.items()))
check('real_auto_batch_keeps_20_unassigned',plan['summary']['unassigned']==20)
check('real_auto_batch_original_geometry_preserved',signature(meshes)==before)
check('real_references_001_002_stay_outside_output',all(bpy.context.scene.pbg_settings.output not in bpy.data.objects[n].users_collection for n in ['001','002']))
(REPORTS/'test_report_v21_real_model.json').write_text(json.dumps(dict(status='PASS',checks=checks,check_count=len(checks),new_outputs_on_test_copy=newnames,ready_groups=ready,live_scene_modified=False),ensure_ascii=False,indent=2),encoding='utf-8')
print('REAL_MODEL_PASS',len(checks),newnames,flush=True)
