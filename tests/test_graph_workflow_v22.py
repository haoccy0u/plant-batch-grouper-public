# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Synthetic Blender integration tests for the GRAPH work queue.

Run through scripts/test.py, which launches a disposable factory Blender.
No artist file or live Blender connection is opened by this test.
"""
import copy
import json
import sys
import traceback
from pathlib import Path

import bpy

from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
import plant_batch_grouper as addon
from plant_batch_grouper import workflow as w, output, interaction
from plant_batch_grouper import graph_workflow
from plant_batch_grouper.mesh_ops import signature, record

RESULTS = {}


def check(name,condition):
    assert condition,name
    RESULTS[name] = True
    print('PASS',name,flush=True)


def rejects(action):
    try:
        action()
    except ValueError:
        return True
    return False


def fresh(name):
    if bpy.context.mode!='OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj,do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    for key in (w.KEY,w.VIEW):
        if key in bpy.context.scene:
            del bpy.context.scene[key]
    w.DIRTY.clear()
    p = bpy.context.scene.pbg_settings
    for key in list(p.keys()):
        p.property_unset(key)
    p.source = bpy.data.collections.new(name+'_Source')
    p.output = bpy.data.collections.new(name+'_Output')
    bpy.context.scene.collection.children.link(p.source)
    bpy.context.scene.collection.children.link(p.output)
    p.prefix = 'Graph_'
    p.start_number = 1
    p.digits = 3
    p.selected_only = False
    p.reference = None
    interaction.clear_preview(bpy.context)
    return p


def mesh(name,vertices,faces,collection):
    data = bpy.data.meshes.new(name+'_Mesh')
    data.from_pydata(vertices,[],faces)
    data.update()
    obj = bpy.data.objects.new(name,data)
    collection.objects.link(obj)
    uv = data.uv_layers.new(name='UVMap')
    for i,item in enumerate(uv.data):
        item.uv = ((i%3)/3,(i%5)/5)
    color = data.color_attributes.new(name='Color',type='FLOAT_COLOR',domain='CORNER')
    for i,item in enumerate(color.data):
        item.color = (.25+(i%3)*.1,.5,.75,1)
    return obj


def ribbon(name,points,collection,width=.004):
    vertices = [(x,y+sign*width/2,z) for x,y,z in points for sign in (-1,1)]
    faces = []
    for i in range(len(points)-1):
        a = 2*i
        faces.extend([(a,a+1,a+3),(a,a+3,a+2)])
    return mesh(name,vertices,faces,collection)


def stem(name,x,collection):
    return ribbon(name,[(x,0,i/8) for i in range(9)],collection)


def leaf_vertices(x,z=.5):
    return [(x,0,z),(x+.18*.58,-.045,z+.06*.58),(x+.18,0,z+.06),
            (x+.18*.58,.045,z+.06*.58)]


def leaf(name,x,collection,z=.5):
    return mesh(name,leaf_vertices(x,z),[(0,1,2),(0,2,3)],collection)


def select(obj):
    for old in bpy.context.selected_objects:
        old.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()


def analyze():
    bpy.context.view_layer.update()
    return w.start_analysis(bpy.context)


def group_id(plan,obj):
    return plan['assignments'][w.uid(obj)]['group']


def test_graph_start_and_local_repair():
    p = fresh('Local')
    check('new_settings_default_to_graph',p.analysis_method=='GRAPH')
    trunks = [stem('trunk_'+str(i),i,p.source) for i in range(3)]
    leaves = [leaf('leaf_'+str(i),i,p.source) for i in range(3)]
    dot = mesh('ignored_dot',[(2,0,.75)],[],p.source)
    source_before = signature([*trunks,*leaves,dot])
    plan = analyze()
    check('analysis_dispatches_graph_engine',plan.get('engine')=='GRAPH')
    check('graph_report_is_persisted',isinstance(plan.get('graph_report'),dict))
    check('graph_geometry_records_include_surface_triangles',bool(record(trunks[0]).get('triangles')))
    gids = [group_id(plan,o) for o in trunks]
    check('separate_trunks_keep_separate_groups',len(set(gids))==3 and all(gids))
    check('attached_leaves_follow_correct_trunks',[group_id(plan,o) for o in leaves]==gids)
    check('graph_analysis_preserves_source_data',signature([*trunks,*leaves,dot])==source_before)
    dot_id = w.uid(dot)
    check('graph_ignores_zero_face_objects',dot_id not in plan['assignments'] and dot_id in plan['ignored_zero_face'])
    basis = {g['id']:set(g['stems']) for g in plan['groups']}
    frozen = copy.deepcopy({key:plan[key] for key in ('height','ground','config')})
    for gid in gids:
        w.confirm(plan,gid)
    leaves[0].location.x = .001
    plan = w.sync(bpy.context,plan)
    check('local_move_only_invalidates_related_confirmation',
          not w.group(plan,gids[0])['confirmed'] and all(w.group(plan,gid)['confirmed'] for gid in gids[1:]))
    check('local_graph_refresh_preserves_anchors',basis=={g['id']:set(g['stems']) for g in plan['groups']})
    check('local_graph_refresh_preserves_scale_and_parameters',frozen=={key:plan[key] for key in frozen})
    leaves[1].name = 'Renamed_leaf'
    plan = w.sync(bpy.context,plan)
    check('rename_preserves_identity_and_unrelated_confirmation',group_id(plan,leaves[1])==gids[1] and w.group(plan,gids[1])['confirmed'])
    select(leaves[0])
    w.edit_members(bpy.context,plan,gids[0],'ADD')
    leaves[0].location.x = 1
    plan = w.sync(bpy.context,plan)
    a = plan['assignments'][w.uid(leaves[0])]
    check('manual_assignment_survives_better_graph_candidate',a['manual'] and a['group']==gids[0] and a.get('manual_review'))
    check('moved_manual_leaf_exposes_new_graph_candidate',gids[1] in {c['group'] for c in a['candidates']})
    check('manual_repair_does_not_reset_distant_confirmation',w.group(plan,gids[2])['confirmed'])
    dot.data.clear_geometry()
    dot.data.from_pydata(leaf_vertices(2,.75),[],[(0,1,2),(0,2,3)])
    dot.data.update()
    plan = w.sync(bpy.context,plan)
    check('zero_face_object_reenters_graph_after_receiving_faces',dot_id in plan['assignments'] and dot_id not in plan['ignored_zero_face'])
    check('restored_faced_part_attaches_without_reanalysis',plan['assignments'][dot_id]['group']==gids[2])
    check('reentry_keeps_group_anchors',basis=={g['id']:set(g['stems']) for g in plan['groups']})


def test_single_object_numbering_rollback_and_reopen():
    p = fresh('Output')
    trunks = [stem('output_trunk_'+str(i),i,p.source) for i in range(2)]
    before = signature(trunks)
    plan = analyze()
    check('graph_bare_stem_groups_are_ready',len(plan['groups'])==2 and all(g['ready'] for g in plan['groups']))
    occupied = bpy.data.objects.new('Graph_001',None)
    p.output.objects.link(occupied)
    old_objects,old_meshes = set(bpy.data.objects),set(bpy.data.meshes)
    old_number = plan['next_number']
    native_equivalent = output.equivalent
    def corrupt_result(originals,result,markers,before,after):
        result.data.vertices[0].co.x += .1
        return native_equivalent(originals,result,markers,before,after)
    output.equivalent = corrupt_result
    try:
        failed = rejects(lambda:interaction.auto_merge(bpy.context,plan))
    finally:
        output.equivalent = native_equivalent
    check('graph_output_rejects_actual_geometry_corruption',failed)
    check('graph_failed_output_rolls_back_all_staging_objects',set(bpy.data.objects)==old_objects and set(bpy.data.meshes)==old_meshes)
    check('graph_failed_output_preserves_progress_and_number',not plan['completed'] and plan['next_number']==old_number and all(g['confirmed'] for g in plan['groups']))
    check('graph_failed_output_preserves_source_data',signature(trunks)==before)
    gids = [group_id(plan,o) for o in trunks]
    names = output.merge(bpy.context,plan,[gids[0]])
    check('graph_single_object_output_skips_collision',names==['Graph_002'])
    check('graph_single_object_output_preserves_data',signature([bpy.data.objects[names[0]]])==signature([trunks[0]]))
    names += interaction.auto_merge(bpy.context,plan)
    check('graph_batches_use_continuous_numbers',names==['Graph_002','Graph_003'] and plan['next_number']==4)
    check('graph_outputs_leave_no_validation_markers',all(not any(a.name.startswith('.pbg_') for a in bpy.data.objects[name].data.attributes) for name in names))
    check('graph_finished_sources_stay_hidden',all(o.hide_get() for o in trunks))
    check('graph_completion_and_output_collection_are_correct',plan['summary']['all_complete'] and len(plan['completed'])==2 and all(bpy.data.objects[name].users_collection[:]==(p.output,) for name in names))
    w.save(bpy.context.scene,plan)
    path = ARTIFACTS/'graph_resume.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    plan = w.load(bpy.context.scene)
    check('graph_engine_and_progress_survive_save_reopen',plan.get('engine')=='GRAPH' and len(plan['completed'])==2 and plan['next_number']==4 and plan['summary']['all_complete'])
    check('graph_reopen_keeps_finished_sources_hidden',all(bpy.data.objects['output_trunk_'+str(i)].hide_get() for i in range(2)))
    check('graph_reopen_does_not_duplicate_outputs',interaction.auto_merge(bpy.context,plan)==[])


def test_viewport_candidates_and_binding():
    p = fresh('Candidates')
    trunks = [ribbon('cross_left',[(-.3+.6*i/8,0,i/8) for i in range(9)],p.source),
              ribbon('cross_right',[(.3-.6*i/8,0,i/8) for i in range(9)],p.source)]
    leaves = [leaf('choice_'+str(i),0,p.source) for i in range(2)]
    plan = analyze()
    gids = [group_id(plan,o) for o in trunks]
    ids = [w.uid(o) for o in leaves]
    check('graph_crossing_stems_remain_distinct',len(set(gids))==2)
    check('graph_crossing_leaf_has_both_candidates',all(not plan['assignments'][i]['group'] and {c['group'] for c in plan['assignments'][i]['candidates']}==set(gids) for i in ids))
    expected_assignments = copy.deepcopy(plan['assignments'])
    select(leaves[0])
    for gid in gids:
        interaction.preview_candidate(bpy.context,plan,ids[0],gid)
        check('graph_preview_keeps_leaf_active_'+str(gid),bpy.context.view_layer.objects.active==leaves[0])
        check('graph_preview_binds_explicit_candidate_'+str(gid),p.preview_leaf_uid==ids[0] and p.preview_group_id==gid)
    check('graph_candidate_preview_never_assigns',plan['assignments']==expected_assignments)
    select(leaves[1])
    check('graph_changed_selection_rejects_stale_confirmation',rejects(lambda:interaction.confirm_selected(bpy.context,plan)))
    select(leaves[0])
    interaction.preview_candidate(bpy.context,plan,ids[0],gids[0])
    names = interaction.confirm_selected(bpy.context,plan)
    check('graph_first_manual_leaf_records_without_early_output',names==[] and plan['assignments'][ids[0]]['manual'] and plan['assignments'][ids[0]]['group']==gids[0] and not w.group(plan,gids[0]).get('done'))
    w.save(bpy.context.scene,plan)
    path = ARTIFACTS/'graph_manual_resume.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    p = bpy.context.scene.pbg_settings
    plan = w.load(bpy.context.scene)
    leaves = [bpy.data.objects['choice_'+str(i)] for i in range(2)]
    check('graph_pending_manual_progress_survives_reopen',plan.get('engine')=='GRAPH' and not plan['completed']
          and plan['assignments'][ids[0]]['manual'] and plan['assignments'][ids[0]]['group']==gids[0])
    check('graph_reopen_retains_remaining_question',ids[1] in w.group(plan,gids[0])['blockers'])
    check('graph_reopen_discards_transient_candidate_binding',not p.preview_leaf_uid and not p.preview_group_id)
    select(leaves[1])
    interaction.preview_candidate(bpy.context,plan,ids[1],gids[0])
    names = interaction.confirm_selected(bpy.context,plan)
    check('graph_last_manual_leaf_merges_selected_branch',len(names)==1 and w.group(plan,gids[0]).get('done'))
    check('graph_manual_output_keeps_other_branch_pending',not w.group(plan,gids[1]).get('done'))
    check('graph_manual_confirmation_clears_preview_binding',not p.preview_leaf_uid and not p.preview_group_id)


def test_manual_anchor_and_legacy_compatibility():
    p = fresh('Anchor')
    trunk = stem('existing_anchor',0,p.source)
    orphan = mesh('manual_anchor',[(2.9,-.1,.4),(3.1,-.1,.6),(2.9,.1,.6),(3.1,.1,.4)],
                  [(0,1,2),(0,3,1),(0,2,3),(1,3,2)],p.source)
    before = signature([trunk,orphan])
    plan = analyze()
    original_gid = group_id(plan,trunk)
    check('compact_disconnected_mesh_starts_unassigned',group_id(plan,orphan)==0)
    orphan_assignment = plan['assignments'][w.uid(orphan)]
    check('graph_no_candidate_has_explicit_review_reason',orphan_assignment['candidates']==[] and bool(orphan_assignment['reason']))
    select(orphan)
    graph_workflow.set_anchor(bpy.context,plan)
    plan = w.load(bpy.context.scene)
    new_gid = group_id(plan,orphan)
    check('manual_main_branch_adds_stable_anchor',new_gid and new_gid!=original_gid and w.uid(orphan) in w.group(plan,new_gid)['stems'])
    check('manual_anchor_keeps_existing_group_identity',group_id(plan,trunk)==original_gid)
    check('manual_anchor_does_not_split_or_change_mesh',signature([trunk,orphan])==before and set(p.source.objects.keys())=={trunk.name,orphan.name})
    select(trunk)
    graph_workflow.set_anchor(bpy.context,plan)
    plan = w.load(bpy.context.scene)
    check('reasserting_existing_stem_does_not_duplicate_groups',len(plan['groups'])==2 and group_id(plan,trunk)==original_gid)

    p = fresh('Legacy')
    p.analysis_method = 'LEGACY'
    stem('legacy_stem',0,p.source)
    plan = analyze()
    plan.pop('engine',None)
    prior = copy.deepcopy(plan['assignments'])
    w.save(bpy.context.scene,plan)
    p.analysis_method = 'GRAPH'
    plan = w.migrate(bpy.context)
    check('legacy_missing_engine_is_not_silently_reanalyzed',plan.get('engine','LEGACY')=='LEGACY' and plan['assignments']==prior)
    plan = w.sync(bpy.context,plan)
    check('legacy_refresh_keeps_legacy_method',plan.get('engine','LEGACY')=='LEGACY')


def test_reanalysis_preserves_manual_anchors_and_members():
    p = fresh('Reanalysis')
    trunk = stem('automatic_main',0,p.source)
    orphan = mesh('explicit_isolated_main',[(2.9,-.1,.4),(3.1,-.1,.6),(2.9,.1,.6),(3.1,.1,.4)],
                  [(0,1,2),(0,3,1),(0,2,3),(1,3,2)],p.source)
    part = leaf('manually_assigned_leaf',0,p.source)
    plan = analyze()
    trunk_gid = group_id(plan,trunk)
    select(orphan)
    graph_workflow.set_anchor(bpy.context,plan)
    manual_gid = group_id(plan,orphan)
    select(part)
    w.edit_members(bpy.context,plan,manual_gid,'ADD')
    expected = signature([trunk,orphan,part])
    for iteration in range(2):
        plan = analyze()
        target = w.group(plan,manual_gid)
        a = plan['assignments'][w.uid(part)]
        check('reanalysis_keeps_manual_anchor_id_'+str(iteration),group_id(plan,orphan)==manual_gid and target.get('anchor_manual') and target['stems']==[w.uid(orphan)])
        check('reanalysis_keeps_other_manual_member_'+str(iteration),a['manual'] and a['group']==manual_gid and w.uid(part) in target['members'])
        check('reanalysis_keeps_unrelated_automatic_anchor_'+str(iteration),group_id(plan,trunk)==trunk_gid)
    check('repeated_graph_analysis_never_changes_source_meshes',signature([trunk,orphan,part])==expected)
    # The former broad leaf now satisfies automatic trunk shape heuristics.
    # Its explicit membership must still prevent it becoming a new main branch.
    vertices = [(5,y,z/8) for z in range(9) for y in (-.002,.002)]
    faces = []
    for i in range(8):
        j = 2*i
        faces.extend([(j,j+1,j+3),(j,j+3,j+2)])
    part.data.clear_geometry()
    part.data.from_pydata(vertices,[],faces)
    part.data.update()
    for iteration in range(2):
        plan = analyze()
        a = plan['assignments'][w.uid(part)]
        check('reshaped_manual_leaf_keeps_membership_'+str(iteration),a.get('manual') and a['group']==manual_gid)
        check('reshaped_manual_leaf_cannot_become_auto_anchor_'+str(iteration),
              a['kind']!='STEM' and all(w.uid(part) not in g['stems'] for g in plan['groups']) and len(plan['groups'])==2)


def test_local_pair_conflict_preserves_fixed_groups():
    p = fresh('PairChange')
    trunks = [stem('pair_change_'+str(i),i,p.source) for i in range(3)]
    plan = analyze()
    gids = [group_id(plan,o) for o in trunks]
    anchors = {g['id']:tuple(g['stems']) for g in plan['groups']}
    check('pair_conflict_fixture_starts_as_clear_singletons',len(set(gids))==3 and all(g['ready'] for g in plan['groups']))
    for gid in gids:
        w.confirm(plan,gid)
    trunks[1].location.x = -.999
    plan = w.sync(bpy.context,plan)
    check('new_cross_group_pair_blocks_both_original_groups',all(w.group(plan,gid).get('stem_review') and w.group(plan,gid).get('stem_uncertain') and not w.group(plan,gid)['ready'] for gid in gids[:2]))
    check('new_pair_conflict_preserves_fixed_group_ids_and_anchors',anchors=={g['id']:tuple(g['stems']) for g in plan['groups']})
    check('new_pair_conflict_preserves_unrelated_confirmation',w.group(plan,gids[2])['confirmed'])
    trunks[1].location.x = 0
    plan = w.sync(bpy.context,plan)
    check('separating_new_pair_recovers_both_groups',all(w.group(plan,gid)['ready'] and not w.group(plan,gid).get('stem_review') and not w.group(plan,gid).get('stem_uncertain') for gid in gids[:2]))
    check('pair_repair_still_preserves_unrelated_confirmation',w.group(plan,gids[2])['confirmed'])
    check('pair_repair_never_rebuilds_group_identity',anchors=={g['id']:tuple(g['stems']) for g in plan['groups']})


def test_legacy_manual_target_survives_engine_switch():
    p = fresh('LegacyManualSwitch')
    p.analysis_method = 'LEGACY'
    stem('scale_reference_tall',2,p.source)
    shorts = [mesh('legacy_pair_'+str(i),[(x-.002,0,0),(x+.002,0,0),
                                        (x+.002,0,.2),(x-.002,0,.2)],[(0,1,2,3)],p.source)
              for i,x in enumerate((0,.006))]
    part = leaf('user_leaf',0,p.source,.1)
    plan = analyze()
    gid = group_id(plan,shorts[0])
    anchor_ids = {w.uid(obj) for obj in shorts}
    check('legacy_switch_fixture_has_old_paired_anchor',gid and group_id(plan,shorts[1])==gid
          and set(w.group(plan,gid)['stems'])==anchor_ids)
    select(part)
    w.edit_members(bpy.context,plan,gid,'ADD')
    p.analysis_method = 'GRAPH'
    plan = analyze()
    assignment = plan['assignments'][w.uid(part)]
    target = w.group(plan,gid)
    check('switching_to_graph_retains_old_manual_destination',plan.get('engine')=='GRAPH'
          and assignment.get('manual') and assignment['group']==gid)
    check('switching_to_graph_preserves_old_target_stem_set',set(target['stems'])==anchor_ids
          and w.uid(part) in target['members'])
    check('old_manual_target_is_reviewed_not_promoted_to_manual_anchor',target.get('stem_uncertain')
          and target.get('stem_review') and not target['ready'] and not target.get('anchor_manual'))


def test_manual_anchor_still_requires_usable_faces():
    p = fresh('DegenerateAnchor')
    obj = mesh('explicit_anchor_with_faces',[(2.9,-.1,.4),(3.1,-.1,.6),(2.9,.1,.6),(3.1,.1,.4)],
               [(0,1,2),(0,3,1),(0,2,3),(1,3,2)],p.source)
    plan = analyze()
    select(obj)
    graph_workflow.set_anchor(bpy.context,plan)
    gid = group_id(plan,obj)
    check('manual_anchor_with_usable_faces_can_be_ready',w.group(plan,gid).get('anchor_manual') and w.group(plan,gid)['ready'])
    for index,vertex in enumerate(obj.data.vertices):
        vertex.co = (3,0,index*.1)
    obj.data.update()
    plan = w.sync(bpy.context,plan)
    target = w.group(plan,gid)
    check('manual_anchor_degenerate_faces_fixture_keeps_polygons',len(obj.data.polygons)==4)
    check('manual_anchor_cannot_waive_degenerate_surface',target.get('anchor_manual') and target.get('stem_uncertain')
          and target.get('stem_review') and not target['ready'])


def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    addon.register()
    status,error = 'PASS',None
    try:
        test_graph_start_and_local_repair()
        test_single_object_numbering_rollback_and_reopen()
        test_viewport_candidates_and_binding()
        test_manual_anchor_and_legacy_compatibility()
        test_reanalysis_preserves_manual_anchors_and_members()
        test_local_pair_conflict_preserves_fixed_groups()
        test_legacy_manual_target_survives_engine_switch()
        test_manual_anchor_still_requires_usable_faces()
    except Exception:
        status,error = 'FAIL',traceback.format_exc()
    report = dict(status=status,blender=bpy.app.version_string,checks=RESULTS,error=error,
                  note='Synthetic disposable background scenes only; no artist files or live session.')
    (ARTIFACTS/'test_graph_workflow_report_v22.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(status=status,checks=len(RESULTS),error=error),ensure_ascii=False),flush=True)
    if error:
        raise AssertionError(error)


if __name__=='__main__':
    main()
