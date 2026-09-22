# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Lifecycle regression in a disposable Blender --factory-startup process.

Use the official addon_utils entry points: calling register() alone cannot
exercise Blender's restricted add-on context or its preference bookkeeping.
No user preferences or .blend files are saved by this test.
"""
import json
from pathlib import Path
import sys
import time
import traceback

import bpy
import addon_utils
from _bpy_restrict_state import RestrictBlend

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ROOT, ARTIFACTS

import plant_batch_grouper
from plant_batch_grouper import ui, workflow as w

MODULE = 'plant_batch_grouper'
RESULTS = {}
EXPECTED_ERRORS = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    RESULTS[name] = True
    print('PASS', name, flush=True)


def all_classes_registered():
    return all(getattr(cls, 'is_registered', False) for cls in ui.CLASSES)


def clean_resources():
    return (not any(getattr(cls, 'is_registered', False) for cls in ui.CLASSES)
            and not hasattr(bpy.types.Scene, 'pbg_settings')
            and ui.on_selection not in bpy.app.handlers.depsgraph_update_post
            and w.track_changes not in bpy.app.handlers.depsgraph_update_post
            and w.loaded not in bpy.app.handlers.load_post
            and not bpy.app.timers.is_registered(ui.selection_timer)
            and not bpy.app.timers.is_registered(ui.deferred_resume))


def unique_handlers():
    return (bpy.app.handlers.depsgraph_update_post.count(ui.on_selection) == 1
            and bpy.app.handlers.depsgraph_update_post.count(w.track_changes) == 1
            and bpy.app.handlers.load_post.count(w.loaded) == 1)


def enable():
    errors = []
    module = addon_utils.enable(MODULE, default_set=True, refresh_handled=True, handle_error=errors.append)
    if errors:
        raise AssertionError('Official enable failed: ' + repr(errors))
    return module


def disable():
    errors = []
    addon_utils.disable(MODULE, default_set=True, refresh_handled=True, handle_error=errors.append)
    if errors:
        raise AssertionError('Official disable failed: ' + repr(errors))


def run_deferred_once():
    # Background --python scripts do not run the GUI timer event loop. Consume
    # the pending callback and invoke exactly what Blender would dispatch next.
    ui.cancel_deferred_resume()
    return ui.deferred_resume()


def scene_geometry():
    return {obj.name: (tuple(tuple(row) for row in obj.matrix_world),
                       tuple(tuple(v.co) for v in obj.data.vertices))
            for obj in bpy.context.scene.objects if obj.type == 'MESH'}


def official_enable_and_resume():
    calls = []
    real_resume = ui.resume
    def observed_resume(context):
        calls.append(hasattr(context, 'scene'))
        return real_resume(context)
    ui.resume = observed_resume
    try:
        module = enable()
        check('official_enable_succeeds_under_restricted_context', module is not None)
        check('official_preference_and_runtime_are_both_enabled', addon_utils.check(MODULE) == (True, True))
        check('enable_uses_expected_package', Path(module.__file__).resolve().parent == ROOT/'plant_batch_grouper')
        check('all_panels_operators_and_scene_property_registered', all_classes_registered() and hasattr(bpy.types.Scene, 'pbg_settings'))
        check('registration_does_not_restore_a_restricted_scene', calls == [])
        check('initial_resume_and_selection_timers_scheduled',
              bpy.app.timers.is_registered(ui.deferred_resume) and bpy.app.timers.is_registered(ui.selection_timer))
        ui.register()
        ui.schedule_resume()
        w.register_handlers()
        check('repeat_registration_keeps_single_handler_instances', unique_handlers())
        with RestrictBlend():
            check('selection_callback_accepts_restricted_context', ui.selection_changed(bpy.context) is None)
            check('direct_resume_defers_without_scene_access', ui.resume(bpy.context) is False)
            check('timer_retries_until_scene_context_exists', ui.deferred_resume() == .1)
        calls.clear()
        check('deferred_restore_completes_after_restrictions_end', run_deferred_once() is None and calls == [True])
        check('one_shot_resume_has_been_consumed', not bpy.app.timers.is_registered(ui.deferred_resume))
        w.loaded(None)
        w.loaded(None)
        check('load_handler_schedules_one_deferred_restore', bpy.app.timers.is_registered(ui.deferred_resume) and unique_handlers())
        run_deferred_once()
    finally:
        ui.resume = real_resume


def keep_scene_progress_and_repeat_toggle():
    scene = bpy.context.scene
    source = bpy.data.collections.new('Lifecycle Source')
    destination = bpy.data.collections.new('Lifecycle Output')
    scene.collection.children.link(source)
    scene.collection.children.link(destination)
    settings = scene.pbg_settings
    settings.source, settings.output = source, destination
    settings.prefix, settings.start_number, settings.digits = 'Keep_', 73, 4
    settings.analysis_method = 'LEGACY'
    plan = dict(version=2, schema_minor=1, engine='LEGACY', session='lifecycle-test',
                source_uid=w.uid(source), next_number=91, groups=[], objects={},
                assignments={}, completed=[], dirty_ids=[])
    scene[w.KEY] = json.dumps(plan, ensure_ascii=False)
    before_plan = scene[w.KEY]
    before_geometry = scene_geometry()
    for cycle in range(3):
        disable()
        check('cycle_%d_disable_updates_preferences' % cycle, addon_utils.check(MODULE) == (False, False))
        check('cycle_%d_disable_removes_every_runtime_resource' % cycle, clean_resources())
        check('cycle_%d_disable_preserves_saved_plan' % cycle, scene[w.KEY] == before_plan)
        check('cycle_%d_repeated_unregister_is_safe' % cycle, ui.unregister() == [] and clean_resources())
        enable()
        run_deferred_once()
        settings = scene.pbg_settings
        check('cycle_%d_reenable_restores_settings' % cycle,
              settings.source == source and settings.output == destination
              and settings.prefix == 'Keep_' and settings.start_number == 73 and settings.digits == 4)
        check('cycle_%d_reenable_preserves_plan_and_unique_handlers' % cycle,
              scene[w.KEY] == before_plan and unique_handlers())
    check('lifecycle_does_not_change_meshes_or_world_transforms', scene_geometry() == before_geometry)


def restore_failure_does_not_leave_partial_registration():
    real_restore = w.restore_view
    calls = []
    def broken_restore(_context):
        calls.append(True)
        raise RuntimeError('injected view restoration failure')
    w.restore_view = broken_restore
    try:
        disable()
        check('failed_view_restore_does_not_prevent_disable', len(calls) == 1 and clean_resources())
        check('failed_view_restore_does_not_keep_preference_enabled', addon_utils.check(MODULE) == (False, False))
    finally:
        w.restore_view = real_restore
    enable()
    real_resume = ui.resume
    def broken_resume(_context):
        raise RuntimeError('injected saved workspace failure')
    ui.resume = broken_resume
    try:
        check('deferred_scene_failure_is_contained', run_deferred_once() is None)
        check('deferred_scene_failure_keeps_complete_enabled_addon', addon_utils.check(MODULE) == (True, True) and all_classes_registered())
        check('deferred_scene_failure_is_visible_in_status', 'injected saved workspace failure' in bpy.context.scene.pbg_settings.status)
    finally:
        ui.resume = real_resume
    disable()


def injected_register_failures_roll_back():
    def fail_enable(label):
        errors = []
        module = addon_utils.enable(MODULE, default_set=True, refresh_handled=True, handle_error=errors.append)
        EXPECTED_ERRORS.extend(str(exc) for exc in errors)
        check(label+'_error_reaches_official_enable', module is None and len(errors) == 1)
        check(label+'_preferences_and_resources_rolled_back', MODULE not in bpy.context.preferences.addons and clean_resources())
        check(label+'_root_module_removed_without_ui_residue', MODULE not in sys.modules and MODULE+'.ui' in sys.modules)

    real_register_class = bpy.utils.register_class
    def broken_register_class(cls):
        real_register_class(cls)
        if cls == ui.CLASSES[3]:
            raise RuntimeError('injected after RNA class registration')
    bpy.utils.register_class = broken_register_class
    try:
        fail_enable('rna_partial_failure')
    finally:
        bpy.utils.register_class = real_register_class

    real_handlers = w.register_handlers
    def broken_handlers():
        real_handlers()
        raise RuntimeError('injected after handler registration')
    w.register_handlers = broken_handlers
    try:
        fail_enable('handler_partial_failure')
    finally:
        w.register_handlers = real_handlers

    real_schedule = ui.schedule_resume
    def broken_schedule():
        real_schedule()
        raise RuntimeError('injected after timer registration')
    ui.schedule_resume = broken_schedule
    try:
        fail_enable('timer_partial_failure')
    finally:
        ui.schedule_resume = real_schedule
    check('all_failure_types_recover_with_normal_enable', enable() is not None and addon_utils.check(MODULE) == (True, True))
    disable()


def orphan_and_partial_teardown():
    # Simulate the old failure's observable state: RNA/handlers remain, while
    # addon_utils has dropped the root package and preference entry.
    ui.register()
    sys.modules.pop(MODULE, None)
    check('orphan_fixture_matches_unchecked_visible_panel', addon_utils.check(MODULE) == (False, False) and all_classes_registered())
    before = bpy.context.scene.get(w.KEY)
    check('orphan_ui_can_be_unregistered_without_root_package', ui.unregister(restore_view=False) == [] and clean_resources())
    check('orphan_teardown_keeps_progress_json', bpy.context.scene.get(w.KEY) == before)
    check('official_enable_recovers_after_orphan_cleanup', enable() is not None and addon_utils.check(MODULE) == (True, True))
    disable()

    bpy.utils.register_class(ui.PBG_Settings)
    bpy.types.Scene.pbg_settings = bpy.props.PointerProperty(type=ui.PBG_Settings)
    bpy.app.handlers.depsgraph_update_post.extend([ui.on_selection, ui.on_selection])
    bpy.app.handlers.load_post.extend([w.loaded, w.loaded])
    with RestrictBlend():
        warnings = ui.unregister()
    check('partial_unregister_handles_missing_context_and_duplicate_callbacks', warnings == [] and clean_resources())
    check('final_unregister_is_idempotent', ui.unregister() == [] and clean_resources())


def main():
    started = time.perf_counter()
    report = dict(status='RUNNING', blender=bpy.app.version_string, checks=RESULTS)
    try:
        official_enable_and_resume()
        keep_scene_progress_and_repeat_toggle()
        restore_failure_does_not_leave_partial_registration()
        injected_register_failures_roll_back()
        orphan_and_partial_teardown()
        report['status'] = 'PASS'
    except Exception:
        report['status'] = 'FAIL'
        report['error'] = traceback.format_exc()
        raise
    finally:
        ui.unregister(restore_view=False)
        report['check_count'] = len(RESULTS)
        report['expected_injected_errors'] = EXPECTED_ERRORS
        report['elapsed_seconds'] = round(time.perf_counter()-started, 3)
        (ARTIFACTS/'test_lifecycle_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PBG_LIFECYCLE_REPORT', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
