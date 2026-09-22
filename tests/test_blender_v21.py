# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Run with Blender --background --factory-startup --python this_file.

Every fixture lives in this disposable Blender process. Never connect this test
to the artist's active session. Reports/artifacts use the isolated test directory.
"""
import copy
import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
import plant_batch_grouper as addon
from plant_batch_grouper import cleanup, interaction, output, ui, workflow as w
from plant_batch_grouper.mesh_ops import signature

RESULTS = {}
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    RESULTS[name] = True
    print('PASS', name, flush=True)


def rejected(call):
    try:
        call()
    except ValueError:
        return True
    return False


def collection(name, parent=None):
    item = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(item)
    return item


def mesh_object(name, verts, faces, parent, edges=()):
    mesh = bpy.data.meshes.new(name + '_Mesh')
    mesh.from_pydata(verts, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    parent.objects.link(obj)
    uv = mesh.uv_layers.new(name='UVMap')
    for i, item in enumerate(uv.data):
        item.uv = (i % 2, (i // 2) % 2)
    attr = mesh.color_attributes.new(name='TestColor', type='FLOAT_COLOR', domain='CORNER')
    for i, item in enumerate(attr.data):
        item.color = (.2 + .1 * (i % 4), .6, .3, 1)
    return obj


def stem(name, x, parent):
    return mesh_object(name, [(x-.002, 0, 0), (x+.002, 0, 0),
                              (x+.002, 0, 1), (x-.002, 0, 1)], [(0, 1, 2, 3)], parent)


def leaf(name, x, parent, z=.5):
    return mesh_object(name, [(x, 0, z), (x+.25, 0, z+.2),
                              (x+.25, .02, z+.2)], [(0, 1, 2)], parent)


def select(obj):
    for old in bpy.context.selected_objects:
        old.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()


def fresh(tag, has_output=True):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for scene in list(bpy.data.scenes):
        if scene != bpy.context.scene:
            bpy.data.scenes.remove(scene)
    for item in list(bpy.data.collections):
        bpy.data.collections.remove(item)
    for key in (w.KEY, w.VIEW):
        if key in bpy.context.scene:
            del bpy.context.scene[key]
    w.DIRTY.clear()
    p = bpy.context.scene.pbg_settings
    for prop in list(p.keys()):
        p.property_unset(prop)
    p.source = collection(tag + '_Source')
    p.output = collection(tag + '_Output') if has_output else None
    p.reference = None
    p.prefix = 'Plant_'
    p.start_number = 1
    p.digits = 3
    p.selected_only = False
    p.analysis_method = 'LEGACY'
    p.protect_numbered = True
    interaction.clear_preview(bpy.context)
    return p


def manual_fixture():
    p = fresh('Manual')
    stems = [stem('stem_' + str(n), n, p.source) for n in range(4)]
    leaves = [leaf('ambiguous_' + str(n), 1, p.source, .35 + .25*n) for n in range(2)]
    bpy.context.view_layer.update()
    plan = w.start_analysis(bpy.context)
    gids = [plan['assignments'][w.uid(obj)]['group'] for obj in stems]
    # Deterministic four-candidate decision fixture. The genuine initial analysis
    # still supplies geometry, object fingerprints, fixed scale and valid stems.
    for obj in leaves:
        a = plan['assignments'][w.uid(obj)]
        a.update(group=0, state='REVIEW', kind='LEAF', reason='多个候选距离接近',
                 manual=False, manual_review=False, excluded=[],
                 candidates=[dict(group=gid, distance=.001*(n+1), score=.2*(n+1),
                                  uncertain_end=False, root_contact=False)
                             for n, gid in enumerate(gids)])
    w.rebuild_members(plan)
    w.save(bpy.context.scene, plan)
    w.show_remaining(bpy.context, plan)
    return p, plan, stems, leaves, gids


def test_manual_flow():
    p, plan, stems, leaves, gids = manual_fixture()
    first_id, second_id = map(w.uid, leaves)
    target = gids[1]
    # Legacy list state must never select the branch receiving this leaf.
    p.current_group = gids[0]
    plan['current_group'] = gids[0]
    select(leaves[0])
    before = copy.deepcopy(plan['assignments'])
    for gid in gids:
        interaction.preview_candidate(bpy.context, plan, first_id, gid)
        check('candidate_%s_keeps_leaf_active' % gid,
              bpy.context.view_layer.objects.active == leaves[0])
        check('candidate_%s_binds_explicit_target' % gid,
              p.preview_leaf_uid == first_id and p.preview_group_id == gid)
        check('candidate_%s_does_not_assign' % gid, plan['assignments'] == before)
    interaction.preview_candidate(bpy.context, plan, first_id, target)
    select(leaves[1])
    check('changed_active_leaf_rejects_stale_target',
          rejected(lambda: interaction.confirm_selected(bpy.context, plan)))
    check('stale_confirmation_does_not_assign', plan['assignments'] == before)
    select(leaves[0])
    interaction.preview_candidate(bpy.context, plan, first_id, target)
    names = interaction.confirm_selected(bpy.context, plan)
    check('first_leaf_records_target_without_output', names == [] and
          plan['assignments'][first_id]['group'] == target and
          plan['assignments'][first_id]['manual'] and not plan['completed'])
    check('old_current_group_does_not_receive_leaf',
          first_id not in w.group(plan, gids[0])['members'])
    check('other_red_leaf_blocks_early_merge', second_id in w.group(plan, target)['blockers'] and
          not w.group(plan, target).get('done') and not leaves[0].hide_get())
    check('confirmation_clears_preview_target', not p.preview_leaf_uid and not p.preview_group_id)
    select(leaves[1])
    interaction.preview_candidate(bpy.context, plan, second_id, target)
    expected = signature([stems[1], *leaves])
    names = interaction.confirm_selected(bpy.context, plan)
    check('last_ambiguous_leaf_merges_whole_branch', len(names) == 1 and
          w.group(plan, target).get('done') and
          signature([bpy.data.objects[names[0]]]) == expected)
    check('manual_output_sources_hidden', all(obj.hide_get() for obj in [stems[1], *leaves]))
    check('manual_output_uses_configured_collection',
          bpy.data.objects[names[0]].users_collection[:] == (p.output,))
    count = len(plan['completed'])
    w.show_remaining(bpy.context, plan)
    check('return_remaining_does_not_resurrect_done_sources',
          all(obj.hide_get() for obj in [stems[1], *leaves]) and len(plan['completed']) == count)
    others = interaction.auto_merge(bpy.context, plan)
    check('remaining_stem_only_groups_auto_merge', len(others) == 3)
    check('manual_then_auto_has_continuous_numbers', names + others ==
          ['Plant_001', 'Plant_002', 'Plant_003', 'Plant_004'])
    check('manual_and_auto_complete_without_double_output',
          len(plan['completed']) == 4 and plan['summary']['all_complete'])


def test_single_object_and_rollback():
    p = fresh('Single')
    obj = stem('single_stem', 0, p.source)
    before = signature([obj])
    bpy.context.view_layer.update()
    plan = w.start_analysis(bpy.context)
    check('stem_only_group_is_ready', len(plan['groups']) == 1 and plan['groups'][0]['ready'])
    occupied = bpy.data.objects.new('Plant_001', None)
    p.output.objects.link(occupied)
    old_objects, old_meshes = set(bpy.data.objects), set(bpy.data.meshes)
    old_counter = plan['next_number']
    native_signature = output.signature

    def bad_signature(objects):
        result = native_signature(objects)
        if objects and objects[0].name.startswith('__PBG_TMP'):
            result['vertices'][('failure_injection',)] += 1
        return result

    output.signature = bad_signature
    try:
        failed = rejected(lambda: interaction.auto_merge(bpy.context, plan))
    finally:
        output.signature = native_signature
    check('single_object_validation_failure_rolls_back', failed and
          set(bpy.data.objects) == old_objects and set(bpy.data.meshes) == old_meshes)
    check('failure_retains_sources_and_number', not obj.hide_get() and
          plan['next_number'] == old_counter and not plan['completed'])
    names = interaction.auto_merge(bpy.context, plan)
    check('single_object_output_skips_name_collision', names == ['Plant_002'])
    check('single_object_data_preserved', signature([bpy.data.objects[names[0]]]) == before and
          signature([obj]) == before)
    check('unrelated_output_object_unchanged', occupied.name in p.output.objects)
    w.save(bpy.context.scene, plan)
    path = ARTIFACTS / 'resume_v21.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    plan = w.load(bpy.context.scene)
    check('save_reopen_preserves_finished_state', len(plan['completed']) == 1 and
          plan['summary']['all_complete'] and plan['next_number'] == 3)
    check('save_reopen_finished_source_stays_hidden', bpy.data.objects['single_stem'].hide_get())


def test_zero_face_migration_and_recovery():
    p = fresh('Zero')
    trunk = stem('stem', 0, p.source)
    live_leaf = leaf('normal_leaf_with_loose_vertex', 0, p.source)
    live_leaf.data.vertices.add(1)
    live_leaf.data.vertices[-1].co = (0, 0, .6)
    dots = mesh_object('dots', [(0, 0, .5)], [], p.source)
    wires = mesh_object('wires', [(3, 0, .4), (4, 0, .5)], [], p.source, [(0, 1)])
    empty = mesh_object('empty', [], [], p.source)
    bpy.context.view_layer.update()
    plan = w.start_analysis(bpy.context)
    zero_ids = {w.uid(obj) for obj in (dots, wires, empty)}
    check('initial_analysis_excludes_all_zero_face_meshes',
          not (zero_ids & set(plan['assignments'])) and w.uid(live_leaf) in plan['assignments'])
    check('zero_face_meshes_do_not_inflate_pending_count', plan['summary']['objects'] == 2)
    w.show_remaining(bpy.context, plan)
    check('ignored_zero_face_meshes_hidden_in_preview', all(obj.hide_get() for obj in (dots, wires, empty)))
    w.restore_view(bpy.context)
    check('exit_preview_restores_zero_face_visibility', all(not obj.hide_get() for obj in (dots, wires, empty)))
    # Simulate a v2 file where the detector incorrectly assigned a loose vertex.
    gid = plan['assignments'][w.uid(trunk)]['group']
    dot_id = w.uid(dots)
    plan.pop('schema_minor', None)
    plan.get('ignored_zero_face', {}).pop(dot_id, None)
    plan['objects'][dot_id] = w.snapshot(dots)
    plan['objects'][dot_id]['geometry'].pop('polygon_count', None)
    plan['assignments'][dot_id] = dict(group=gid, kind='FLOWER', state='ASSIGNED',
                                     candidates=[], reason='legacy bad assignment', manual=False, excluded=[])
    w.group(plan, gid)['members'].append(dot_id)
    bpy.context.scene[w.KEY] = json.dumps(plan)
    plan = w.migrate(bpy.context)
    check('migration_removes_zero_face_member', dot_id not in plan['assignments'] and
          dot_id not in w.group(plan, gid)['members'])
    dots.data.clear_geometry()
    dots.data.from_pydata([(0, 0, .5), (.25, 0, .7), (.25, .02, .7)], [], [(0, 1, 2)])
    dots.data.update()
    plan = w.sync(bpy.context, plan)
    check('new_faces_restore_previously_ignored_part', dot_id in plan['assignments'] and
          plan['assignments'][dot_id]['group'] == gid and dot_id in w.group(plan, gid)['members'])
    before = signature([live_leaf])
    result = cleanup.cleanup(bpy.context)
    check('cleanup_preserves_faces_and_internal_loose_vertex',
          live_leaf.name in bpy.data.objects and signature([live_leaf]) == before)
    check('cleanup_deletes_remaining_wire_and_empty_meshes', result['deleted'] == 2 and
          'wires' not in bpy.data.objects and 'empty' not in bpy.data.objects)


def test_cleanup_scope_and_protections():
    p = fresh('Cleanup', has_output=False)
    child = collection('Cleanup_Child', p.source)
    outside = collection('Outside_Source')
    mesh_object('delete_point', [(0, 0, 0)], [], p.source)
    mesh_object('delete_child_wire', [(0, 0, 0), (1, 0, 0)], [], child, [(0, 1)])
    shared = mesh_object('shared_external_collection', [(0, 0, 0)], [], p.source)
    outside.objects.link(shared)
    other_scene = bpy.data.scenes.new('Another_Scene')
    shared_scene = mesh_object('shared_other_scene', [(0, 0, 0)], [], p.source)
    other_scene.collection.objects.link(shared_scene)
    reference = mesh_object('protected_reference', [(0, 0, 0)], [], p.source)
    p.reference = reference
    existing_output = mesh_object('protected_output', [(0, 0, 0)], [], p.source)
    existing_output['PBG_Output'] = True
    regular = leaf('normal_leaf', 0, p.source)
    original_sig = signature([regular])
    # Make a genuine linked object without relying on the artist's files.
    library_file = ARTIFACTS / 'readonly_zero_face.blend'
    link_source = mesh_object('linked_zero', [(0, 0, 0)], [], outside)
    bpy.data.libraries.write(str(library_file), {link_source})
    bpy.data.objects.remove(link_source, do_unlink=True)
    with bpy.data.libraries.load(str(library_file), link=True) as (_, data_to):
        data_to.objects = ['linked_zero']
    linked = data_to.objects[0]
    p.source.objects.link(linked)
    protected_names = {obj.name for obj in (shared, shared_scene, reference, existing_output, linked, regular)}
    bpy.context.view_layer.update()
    result = cleanup.cleanup(bpy.context)
    check('cleanup_requires_source_only', p.output is None and result['deleted'] == 2)
    check('cleanup_recurses_into_source_children', 'delete_child_wire' not in bpy.data.objects)
    check('cleanup_respects_readonly_reference_output_and_shared_objects',
          protected_names <= set(bpy.data.objects.keys()))
    check('cleanup_reports_protected_objects', result['skipped'] >= 3 and bool(result['details']))
    check('cleanup_does_not_change_real_mesh', signature([regular]) == original_sig)
    # A completed source needs protection even when its mesh is later zeroed.
    p = fresh('Completed')
    completed_source = stem('completed_original', 0, p.source)
    bpy.context.view_layer.update()
    plan = w.start_analysis(bpy.context)
    interaction.auto_merge(bpy.context, plan)
    completed_source.data.clear_geometry()
    result = cleanup.cleanup(bpy.context)
    check('cleanup_protects_completed_source_backups',
          completed_source.name in bpy.data.objects and result['deleted'] == 0)


def test_local_repair():
    p = fresh('Repair')
    trunks = [stem('repair_stem_' + str(n), n, p.source) for n in range(3)]
    leaves = [leaf('repair_leaf_' + str(n), n, p.source) for n in range(3)]
    floating = leaf('floating_leaf', 5, p.source)
    bpy.context.view_layer.update()
    plan = w.start_analysis(bpy.context)
    gids = [plan['assignments'][w.uid(obj)]['group'] for obj in trunks]
    for gid in gids:
        w.confirm(plan, gid)
    w.save(bpy.context.scene, plan)
    floating.location.x = -3
    plan = w.sync(bpy.context, plan)
    check('local_repair_finds_new_attachment', plan['assignments'][w.uid(floating)]['group'] == gids[2])
    check('local_repair_keeps_unrelated_confirmation',
          all(w.group(plan, gid)['confirmed'] for gid in gids[:2]))
    check('local_repair_keeps_fixed_scale', plan['height'] == 1)
    leaves[0].location.z += .01
    plan = w.sync(bpy.context, plan)
    check('moved_leaf_only_refreshes_local_part', plan['last_refresh']['evaluated'] == 1 and
          w.group(plan, gids[1])['confirmed'])
    expected = signature([trunks[0], leaves[0]])
    names = interaction.auto_merge(bpy.context, plan)
    first = next(bpy.data.objects[name] for name in names if bpy.data.objects[name]['PBG_Group'] == gids[0])
    check('output_uses_repaired_world_position', signature([first]) == expected)


def test_cleanup_undo_redo():
    p = fresh('Undo')
    stem('undo_stem', 0, p.source)
    leaf('undo_leaf', 0, p.source)
    dot = mesh_object('undo_dot', [(0, 0, .5)], [], p.source)
    bpy.context.view_layer.update()
    plan = w.start_analysis(bpy.context)
    dot_id = w.uid(dot)
    before_json = bpy.context.scene[w.KEY]
    check('undo_fixture_tracks_ignored_object', dot_id in plan['ignored_zero_face'])
    bpy.context.preferences.edit.use_global_undo = True
    # Direct Python calls have no UI event loop to push operator history. These
    # explicit boundaries exercise Blender's actual global undo/redo mechanism.
    bpy.ops.ed.undo_push(message='Before PBG cleanup')
    check('cleanup_for_undo_succeeds', bpy.ops.pbg.cleanup() == {'FINISHED'})
    after_json = bpy.context.scene[w.KEY]
    check('cleanup_removes_object_and_saved_state', 'undo_dot' not in bpy.data.objects and
          dot_id not in w.load(bpy.context.scene)['ignored_zero_face'])
    bpy.ops.ed.undo_push(message='After PBG cleanup')
    check('blender_cleanup_undo_runs', bpy.ops.ed.undo() == {'FINISHED'})
    restored = bpy.data.objects.get('undo_dot')
    check('cleanup_undo_restores_object_and_uid', restored is not None and
          restored.get(w.UID) == dot_id and len(restored.data.polygons) == 0)
    check('cleanup_undo_restores_exact_plan_json', bpy.context.scene[w.KEY] == before_json)
    check('blender_cleanup_redo_runs', bpy.ops.ed.redo() == {'FINISHED'})
    check('cleanup_redo_removes_object_and_restores_exact_cleaned_json',
          'undo_dot' not in bpy.data.objects and bpy.context.scene[w.KEY] == after_json)


class RecordingLayout:
    """Capture a real panel draw without requiring a visible Blender window."""
    def __init__(self):
        self.operations = []
        self.labels = []
        self.properties = []
        self.lists = []

    def row(self, **_):
        return self

    column = row
    box = row
    split = row

    def operator(self, identifier, **kwargs):
        item = SimpleNamespace(identifier=identifier, text=kwargs.get('text', ''))
        self.operations.append(item)
        return item

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text', ''))

    def prop(self, data, name, **_):
        self.properties.append(name)

    def separator(self, **_):
        pass

    def template_list(self, *args, **kwargs):
        self.lists.append(args)


def test_operator_and_panel_surface():
    p = fresh('Operator', has_output=False)
    mesh_object('operator_dot', [(0, 0, 0)], [], p.source)
    check('cleanup_operator_works_without_output', bpy.ops.pbg.cleanup() == {'FINISHED'} and
          'operator_dot' not in bpy.data.objects)
    p.output = collection('Operator_Output')
    stem('operator_stem', 0, p.source)
    check('analysis_operator_runs', bpy.ops.pbg.analyze() == {'FINISHED'})
    check('one_click_auto_merge_operator_runs', bpy.ops.pbg.auto_merge() == {'FINISHED'} and
          w.load(bpy.context.scene)['summary']['all_complete'])
    p, plan, stems, leaves, gids = manual_fixture()
    select(leaves[0])
    check('candidate_operator_uses_requested_group',
          bpy.ops.pbg.preview_candidate(group_id=gids[3]) == {'FINISHED'} and
          p.preview_group_id == gids[3])
    check('local_refresh_operator_runs', bpy.ops.pbg.refresh_local() == {'FINISHED'})
    # Refresh is allowed to clear a preview, so select the explicit target again.
    bpy.ops.pbg.preview_candidate(group_id=gids[1])
    check('manual_confirm_operator_assigns_leaf', bpy.ops.pbg.confirm_selected() == {'FINISHED'} and
          w.load(bpy.context.scene)['assignments'][w.uid(leaves[0])]['group'] == gids[1])
    select(leaves[1])
    bpy.ops.pbg.preview_candidate(group_id=gids[1])
    check('return_remaining_operator_clears_binding', bpy.ops.pbg.return_remaining() == {'FINISHED'} and
          not p.preview_leaf_uid and not p.preview_group_id)

    panels = [cls for cls in ui.CLASSES if issubclass(cls, bpy.types.Panel)]
    by_label = {cls.bl_label: cls for cls in panels}
    check('four_native_sibling_panels', set(by_label) == {'Set Up', '自动分组', '手动修复', '高级设置'} and
          all(not getattr(cls, 'bl_parent_id', '') for cls in panels))
    check('advanced_panel_closed_by_default', 'DEFAULT_CLOSED' in by_label['高级设置'].bl_options)
    auto = RecordingLayout()
    by_label['自动分组'].draw(SimpleNamespace(layout=auto), bpy.context)
    ids = [item.identifier for item in auto.operations]
    check('cleanup_analysis_auto_merge_button_order',
          ids.index('pbg.cleanup') < ids.index('pbg.analyze') < ids.index('pbg.auto_merge'))
    manual = RecordingLayout()
    by_label['手动修复'].draw(SimpleNamespace(layout=manual), bpy.context)
    candidate_buttons = [item for item in manual.operations if item.identifier == 'pbg.preview_candidate']
    check('manual_panel_exposes_four_candidates', len(candidate_buttons) == 4 and
          {item.group_id for item in candidate_buttons} == set(gids))
    ids = [item.identifier for item in manual.operations]
    check('local_refresh_before_confirmation_in_manual_panel',
          ids.index('pbg.refresh_local') < ids.index('pbg.confirm_selected'))
    check('manual_panel_has_no_legacy_group_or_orphan_lists', not manual.lists and
          not any('下一组' in item.text or '暂跳' in item.text for item in manual.operations))


def main():
    addon.register()
    started = time.perf_counter()
    report = dict(status='RUNNING', blender=bpy.app.version_string, checks=RESULTS)
    try:
        test_manual_flow()
        test_single_object_and_rollback()
        test_zero_face_migration_and_recovery()
        test_cleanup_scope_and_protections()
        test_local_repair()
        test_operator_and_panel_surface()
        test_cleanup_undo_redo()
        report['status'] = 'PASS'
    except Exception:
        report['status'] = 'FAIL'
        report['error'] = traceback.format_exc()
        raise
    finally:
        report['elapsed_seconds'] = round(time.perf_counter() - started, 3)
        report['check_count'] = len(RESULTS)
        report['limitations'] = ['Visual panel layout still requires a separate GUI inspection.']
        (REPORTS / 'test_report_v21.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PBG_V21_REPORT', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
