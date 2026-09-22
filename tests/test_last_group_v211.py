# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Real last-group regression; never operates on the interactive scene."""
import bpy,sys,json,traceback,math
from pathlib import Path
from types import SimpleNamespace
from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
import plant_batch_grouper as addon
from plant_batch_grouper import workflow as w,output,interaction,validation,ui
from plant_batch_grouper.mesh_ops import signature
from test_blender_v21 import RecordingLayout
checks={}
report={'checks':checks,'status':'RUNNING','blender':bpy.app.version_string}
def check(name,condition):
    assert condition,name
    checks[name]=True
    print('PASS',name,flush=True)
try:
    addon.register()
    bpy.ops.wm.open_mainfile(filepath=str(fixture_path('test_artifacts/last_group/before_fix.blend')))
    plan=w.load(bpy.context.scene)
    target=w.pending_groups(plan)[0]
    check('exact_last_group_fixture',target['id']==36 and len(w.pending_groups(plan))==1 and len(plan['completed'])==91)
    index=w.index_objects(bpy.context.scene,plan)
    originals=[index[i] for i in target['members']]
    source_before={o.name:signature([o]) for o in originals}
    previous_results={r['output']:signature([index[r['output']]]) for r in plan['completed']}
    old_names=set(bpy.data.objects.keys())
    old_number=plan['next_number']
    original_prepare=output.prepare_join_normals
    output.prepare_join_normals=lambda copies:None
    try:
        interaction.auto_merge(bpy.context,plan)
        rejected=False
    except ValueError:
        rejected=True
    finally:
        output.prepare_join_normals=original_prepare
    check('reproduces_original_precision_failure',rejected)
    check('failed_last_group_keeps_counter_and_results',plan['next_number']==old_number and len(plan['completed'])==91 and set(bpy.data.objects.keys())==old_names)
    check('normal_tolerance_not_relaxed',validation.NORMAL_TOLERANCE==5e-5)
    original_signature=output.signature
    injected=[False]
    def corrupt(items):
        if len(items)==1 and items[0].name.startswith('__PBG_TMP'):
            mesh=items[0].data
            attr=mesh.attributes['custom_normal']
            attr.data[0].vector=-attr.data[0].vector
            mesh.update()
            injected[0]=True
        return original_signature(items)
    output.signature=corrupt
    try:
        interaction.auto_merge(bpy.context,plan)
        rejected=False
    except ValueError:
        rejected=True
    finally:
        output.signature=original_signature
    check('real_normal_damage_still_rejected',injected[0] and rejected)
    check('normal_damage_rollback_preserves_progress',plan['next_number']==old_number and not target['done'] and target['confirmed'] and set(bpy.data.objects.keys())==old_names)
    native=output.equivalent
    def measured(source,result,markers,before,after):
        expected=[]
        for obj in source:
            matrix=obj.matrix_world.to_3x3().inverted_safe().transposed()
            expected.extend((matrix@n.vector).normalized() for n in obj.data.corner_normals)
        ids=[d.value for d in result.data.attributes[markers[1]].data]
        matrix=result.matrix_world.to_3x3().inverted_safe().transposed()
        report['max_world_normal_delta']=max(((matrix@result.data.corner_normals[i].vector).normalized()-expected[j]).length for i,j in enumerate(ids))
        return native(source,result,markers,before,after)
    output.equivalent=measured
    try: names=interaction.auto_merge(bpy.context,plan)
    finally: output.equivalent=native
    check('last_group_outputs_expected_name',names==['SM_092'])
    check('all_complete_state_reached',plan['summary']['all_complete'] and not w.pending_groups(plan) and not w.pending_ids(plan))
    check('completion_record_added_once',len(plan['completed'])==92 and plan['next_number']==93)
    result=bpy.data.objects[names[0]]
    check('result_uses_full_precision_normals',result.data.attributes['custom_normal'].data_type=='FLOAT_VECTOR')
    check('normal_precision_improved',report['max_world_normal_delta']<1e-6)
    check('sources_and_original_normal_storage_unchanged',all(signature([o])==source_before[o.name] and o.data.attributes['custom_normal'].data_type=='INT16_2D' for o in originals))
    check('all_91_previous_outputs_unchanged',all(signature([index[i]])==value for i,value in previous_results.items()))
    check('sources_and_result_hidden',all(o.hide_get() for o in originals) and not result.visible_get())
    check('temporary_markers_removed',not any(a.name.startswith('.pbg_') for a in result.data.attributes))
    after_names=set(bpy.data.objects.keys())
    check('repeat_completion_creates_nothing',interaction.auto_merge(bpy.context,plan)==[] and set(bpy.data.objects.keys())==after_names)
    check('repeat_completion_has_correct_message',ui.PBG_OT_AutoMerge.run(None,bpy.context)=='全部处理完成')
    layout=RecordingLayout()
    ui.PBG_PT_Manual.draw(SimpleNamespace(layout=layout),bpy.context)
    check('manual_panel_shows_all_complete','全部处理完成' in layout.labels)
    saved=ARTIFACTS/'after_fix.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved))
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    loaded=w.load(bpy.context.scene)
    check('completed_state_survives_reopen',loaded['summary']['all_complete'] and len(loaded['completed'])==92 and loaded['next_number']==93)
    check('no_duplicate_after_reopen',interaction.auto_merge(bpy.context,loaded)==[])
    bpy.ops.wm.open_mainfile(filepath=str(fixture_path('test_artifacts/last_group/before_fix.blend')))
    plan=w.load(bpy.context.scene)
    leaf=bpy.data.objects['SM_Everlasting_01a_NoAlpha.166']
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    leaf.hide_set(False)
    leaf.select_set(True)
    bpy.context.view_layer.objects.active=leaf
    interaction.preview_candidate(bpy.context,plan,leaf[w.UID],36)
    check('manual_last_group_completes',interaction.confirm_selected(bpy.context,plan)==['SM_092'] and plan['summary']['all_complete'])
    check('manual_last_group_clears_binding',not bpy.context.scene.pbg_settings.preview_leaf_uid and not bpy.context.scene.pbg_settings.preview_group_id)
    bpy.ops.wm.open_mainfile(filepath=str(fixture_path('test_artifacts/last_group/before_fix.blend')))
    plan=w.load(bpy.context.scene)
    leaf=bpy.data.objects['SM_Everlasting_01a_NoAlpha.166']
    leaf.rotation_euler=(.2,.1,.05)
    leaf.scale=(.011,.009,.0105)
    w.sync(bpy.context,plan)
    w.confirm(plan,36)
    w.save(bpy.context.scene,plan)
    transformed_before=signature([leaf])
    check('rotated_nonuniform_scaled_leaf_passes_strict_validation',output.merge(bpy.context,plan,[36])==['SM_092'])
    check('transformed_source_preserved',signature([leaf])==transformed_before)
    report['status']='PASS'
except Exception:
    report['status']='FAIL'; report['error']=traceback.format_exc()
    raise
finally:
    report['check_count']=len(checks)
    (REPORTS/'test_last_group_report_v211.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
