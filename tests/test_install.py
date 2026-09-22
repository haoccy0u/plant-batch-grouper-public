# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Exercise the distributable installer in isolated Blender user profiles.

The outer factory Blender process only builds the ZIP and launches workers.
Every installation, preference file and synthetic .blend belongs to ARTIFACTS.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from zipfile import ZipFile, ZIP_STORED

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import ROOT, ARTIFACTS

MODULE = 'plant_batch_grouper'
CHECKS = {}


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    CHECKS[name] = True
    print('PASS', name, flush=True)


def load_script(name):
    spec = importlib.util.spec_from_file_location('pbg_test_'+name, ROOT/'scripts'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def isolated_paths(profile):
    for resource, subdir in [('CONFIG','config'), ('SCRIPTS','scripts'),
                              ('DATAFILES','datafiles'), ('EXTENSIONS','extensions')]:
        resolved = Path(bpy.utils.user_resource(resource)).resolve()
        check('isolated_'+resource.lower(), resolved == (profile/subdir).resolve())


def layer_state():
    rows = {}
    def visit(layer, parent=''):
        path = parent+'/'+layer.collection.name
        rows[path] = [layer.hide_viewport, layer.exclude]
        for child in layer.children:
            visit(child, path)
    for scene in bpy.data.scenes:
        for view_layer in scene.view_layers:
            visit(view_layer.layer_collection,scene.name+':'+view_layer.name)
    return rows


def snapshot():
    scene = bpy.context.scene
    p = scene.pbg_settings
    settings = {name:getattr(p,name) for name in ('prefix','start_number','digits','analysis_method',
                'selected_only','protect_numbered','up','stem_ratio','report_path')}
    settings.update(source=p.source.name, output=p.output.name,
                    reference=p.reference.name if p.reference else None)
    objects = {}
    for obj in scene.objects:
        objects[obj.name] = dict(matrix=[list(row) for row in obj.matrix_world],
                                color=list(obj.color), hidden=obj.hide_get(), selected=obj.select_get(),
                                collections=sorted(c.name for c in obj.users_collection))
        if obj.type == 'MESH':
            objects[obj.name]['vertices'] = [list(v.co) for v in obj.data.vertices]
            objects[obj.name]['faces'] = [list(p.vertices) for p in obj.data.polygons]
    return dict(settings=settings, plan=scene.get('pbg_plan_json'),
                preview=scene.get('pbg_workspace_view_v2'), objects=objects, layers=layer_state(),
                active=bpy.context.view_layer.objects.active.name if bpy.context.view_layer.objects.active else None)


def check_installed(profile):
    import addon_utils
    module = sys.modules.get(MODULE)
    check('official_enabled_state', addon_utils.check(MODULE) == (True, True))
    check('loaded_from_isolated_install', module is not None and Path(module.__file__).resolve()
          == (profile/'scripts'/'addons'/MODULE/'__init__.py').resolve())
    ui = sys.modules[MODULE+'.ui']
    w = sys.modules[MODULE+'.workflow']
    check('one_copy_of_runtime_resources', all(cls.is_registered for cls in ui.CLASSES)
          and bpy.app.handlers.depsgraph_update_post.count(ui.on_selection) == 1
          and bpy.app.handlers.depsgraph_update_post.count(w.track_changes) == 1
          and bpy.app.handlers.load_post.count(w.loaded) == 1
          and bpy.app.timers.is_registered(ui.selection_timer))
    return ui, w


def fixture(w):
    scene = bpy.context.scene
    source = bpy.data.collections.new('Synthetic Source')
    output = bpy.data.collections.new('Synthetic Output')
    scene.collection.children.link(source)
    scene.collection.children.link(output)
    p = scene.pbg_settings
    p.source, p.output = source, output
    p.prefix, p.start_number, p.digits = 'Flower_', 5, 4
    p.analysis_method = 'LEGACY'
    p.report_path = '//synthetic-report.json'
    def mesh(name, x, collection):
        data = bpy.data.meshes.new(name+'_Mesh')
        data.from_pydata([(x,0,0),(x+.005,0,0),(x+.005,0,1),(x,0,1)],[],[(0,1,2,3)])
        data.update()
        obj = bpy.data.objects.new(name,data)
        collection.objects.link(obj)
        return obj
    original = mesh('completed_source',0,source)
    pending = mesh('pending_source',1,source)
    result = mesh('Flower_0091',0,output)
    i, j, out = w.uid(original), w.uid(pending), w.uid(result)
    result['PBG_Output'] = True
    result['PBG_SourceObjects'] = json.dumps([original.name])
    result['PBG_SourceIDs'] = json.dumps([i])
    group_done = dict(id=3,stems=[i],members=[i],done=True,confirmed=True)
    group_todo = dict(id=21,stems=[j],members=[j],done=False,confirmed=True,
                      stem_uncertain=False,stem_review=False)
    plan = dict(version=2,schema_minor=1,engine='LEGACY',session='synthetic-install',
                source_uid=w.uid(source),next_number=92,objects={i:w.snapshot(original),j:w.snapshot(pending)},
                config=dict(w.core.DEFAULTS),height=1.,ground=0.,
                groups=[group_done,group_todo],dirty_ids=[],assignments={},
                completed=[dict(output=out,name=result.name,sources=[i],source_names=[original.name],group=3)])
    for key,gid in ((i,3),(j,21)):
        plan['assignments'][key] = dict(group=gid,kind='STEM',state='ASSIGNED',candidates=[],
                                        manual=True,manual_review=False,excluded=[],reason='人工指定')
    w.save(scene,plan)
    original.hide_set(True)
    w.remember_view(bpy.context,plan)
    pending.color = (.17,.31,.57,1)
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    pending.select_set(True)
    bpy.context.view_layer.objects.active = pending
    w.output_visibility(bpy.context,False)
    alternate = scene.view_layers.new('Alternate Visibility')
    for layer in w.layer_collections(alternate.layer_collection):
        if layer.collection == output:
            layer.hide_viewport = True
            layer.exclude = True
    other_scene = bpy.data.scenes.new('Another Scene')
    other_collection = bpy.data.collections.new('Other Scene Hidden Collection')
    other_scene.collection.children.link(other_collection)
    other_scene.view_layers[0].layer_collection.children[other_collection.name].hide_viewport = True
    bpy.context.view_layer.update()
    return pending


def setup_child(profile, archive):
    import addon_utils
    isolated_paths(profile)
    check('clean_profile_has_no_addon', MODULE not in bpy.context.preferences.addons and MODULE not in sys.modules)
    installer = load_script('install')
    before_path = bpy.data.filepath
    installer.install(archive)
    ui, w = check_installed(profile)
    check('first_install_does_not_save_a_project', bpy.data.filepath == before_path)
    pending = fixture(w)
    before = snapshot()
    for run in range(2):
        installer.install(archive)
        ui, w = check_installed(profile)
        after = snapshot()
        check('upgrade_%d_preserves_settings_and_progress' % run,
              after['settings'] == before['settings'] and after['plan'] == before['plan'])
        check('upgrade_%d_preserves_source_meshes_colors_and_collections' % run, after['objects'] == before['objects'])
        check('upgrade_%d_preserves_collection_visibility_and_selection' % run,
              after['layers'] == before['layers'] and after['active'] == before['active'])
        check('upgrade_%d_preserves_preview_restore_record' % run, after['preview'] == before['preview'])

    corrupt = ARTIFACTS/'invalid-addon.zip'
    corrupt.write_bytes(b'not a zip archive')
    rejected = False
    try:
        installer.install(corrupt)
    except Exception:
        rejected = True
    check('invalid_archive_rejected_before_working_addon_is_removed', rejected and addon_utils.check(MODULE) == (True,True))
    check('invalid_archive_keeps_workspace_unchanged', snapshot() == before)
    invalid_members = {
        'wrong_structure': {'wrong_package/__init__.py': '# wrong root'},
        'invalid_python': {MODULE+'/__init__.py': 'def register(:\n    pass\n'},
        'parent_path': {MODULE+'/__init__.py': '# valid', '../escape.py': '# unsafe path'},
        'bad_crc': {MODULE+'/__init__.py': '# payload_checked_by_crc'},
    }
    for label,members in invalid_members.items():
        path = ARTIFACTS/(label+'.zip')
        with ZipFile(path,'w',compression=ZIP_STORED) as package:
            for name,contents in members.items():
                package.writestr(name,contents)
        if label == 'bad_crc':
            path.write_bytes(path.read_bytes().replace(b'payload_checked_by_crc',b'payload_changed_by_crc',1))
        rejected = False
        try:
            installer.install(path)
        except Exception:
            rejected = True
        check(label+'_rejected_without_losing_enabled_addon',rejected and addon_utils.check(MODULE) == (True,True))
        check(label+'_keeps_workspace_unchanged',snapshot() == before)

    # An old failed official enable left submodules/classes but dropped the
    # package and preference item. Recreate that observable state only here.
    addon_utils.disable(MODULE,default_set=True,refresh_handled=True)
    ui.register()
    ui.cancel_deferred_resume()
    sys.modules.pop(MODULE,None)
    check('orphan_fixture_is_unchecked_but_has_panel', addon_utils.check(MODULE) == (False,False)
          and hasattr(bpy.types,'PBG_PT_automatic'))
    before_orphan = snapshot()
    def unrelated_handler(_):
        pass
    unrelated_handler.__module__ = 'unrelated_fixture'
    bpy.app.handlers.load_post.append(unrelated_handler)
    installer.install(archive)
    ui, w = check_installed(profile)
    check('orphan_repair_preserves_scene_and_progress', snapshot() == before_orphan)
    check('orphan_repair_does_not_remove_other_addon_callbacks', unrelated_handler in bpy.app.handlers.load_post)
    bpy.app.handlers.load_post.remove(unrelated_handler)
    check('preferences_written_only_to_isolated_profile', (profile/'config'/'userpref.blend').is_file())
    check('installer_never_changes_original_project_path', bpy.data.filepath == before_path)
    # Save a synthetic normal working preview, then verify in a NEW process.
    ui.resume(bpy.context)
    pending.select_set(True)
    bpy.context.view_layer.objects.active = pending
    scene_path = ARTIFACTS/'synthetic_92.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path),check_existing=False)
    (ARTIFACTS/'expected_after_reopen.json').write_text(json.dumps(snapshot(),ensure_ascii=False),encoding='utf-8')
    check('synthetic_reopen_fixture_exists', scene_path.is_file())


def restart_child(profile):
    isolated_paths(profile)
    ui, w = check_installed(profile)
    check('new_process_loaded_synthetic_project', Path(bpy.data.filepath).resolve() == (ARTIFACTS/'synthetic_92.blend').resolve())
    ui.cancel_deferred_resume()
    ui.deferred_resume()
    expected = json.loads((ARTIFACTS/'expected_after_reopen.json').read_text(encoding='utf-8'))
    actual = snapshot()
    check('reopen_keeps_collection_and_naming_settings', actual['settings'] == expected['settings'])
    check('reopen_keeps_progress_json_and_next_number', actual['plan'] == expected['plan'] and json.loads(actual['plan'])['next_number'] == 92)
    check('reopen_keeps_sources_outputs_and_world_geometry', actual['objects'] == expected['objects'])
    check('reopen_keeps_completed_original_hidden', bpy.data.objects['completed_source'].hide_get())
    check('reopen_has_single_output_without_reanalysis', len(w.load(bpy.context.scene)['completed']) == 1 and 'Flower_0091' in bpy.data.objects)


def child_main(args):
    phase,profile,archive = args
    report = dict(status='RUNNING',checks=CHECKS)
    try:
        if phase == 'setup':
            setup_child(Path(profile),Path(archive))
        else:
            restart_child(Path(profile))
        report['status'] = 'PASS'
    except Exception:
        report.update(status='FAIL',error=traceback.format_exc())
        raise
    finally:
        (ARTIFACTS/('install_'+phase+'_report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('INSTALL_CHILD_REPORT',json.dumps(report,ensure_ascii=False),flush=True)


def main():
    started = time.perf_counter()
    report = dict(status='RUNNING',checks=CHECKS,blender=bpy.app.version_string)
    try:
        archive = load_script('build').build(ARTIFACTS/'packages')
        profile = ARTIFACTS/'isolated_profile'
        env = os.environ.copy()
        env['PBG_TEST_RESULTS'] = str(ARTIFACTS)
        for key,subdir in [('CONFIG','config'),('SCRIPTS','scripts'),('DATAFILES','datafiles'),('EXTENSIONS','extensions')]:
            path = profile/subdir
            path.mkdir(parents=True,exist_ok=True)
            env['BLENDER_USER_'+key] = str(path)
        for phase in ('setup','restart'):
            startup = ['--factory-startup'] if phase == 'setup' else [str(ARTIFACTS/'synthetic_92.blend')]
            command = [bpy.app.binary_path,'--background',*startup,'--python-exit-code','1',
                       '--python',str(Path(__file__).resolve()),'--','--install-child',phase,str(profile),str(archive)]
            result = subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
            (ARTIFACTS/('install_'+phase+'.log')).write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
            path = ARTIFACTS/('install_'+phase+'_report.json')
            if path.is_file():
                child = json.loads(path.read_text(encoding='utf-8'))
                CHECKS.update({phase+'_'+name:value for name,value in child.get('checks',{}).items()})
            else:
                child = {}
            if result.returncode or child.get('status') != 'PASS':
                raise AssertionError(phase+' installation process failed: '+child.get('error',result.stdout[-4000:]))
            check(phase+'_process_exited_successfully',result.returncode == 0)
        report['status'] = 'PASS'
    except Exception:
        report.update(status='FAIL',error=traceback.format_exc())
        raise
    finally:
        report.update(check_count=len(CHECKS),elapsed_seconds=round(time.perf_counter()-started,3))
        (ARTIFACTS/'test_install_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PBG_INSTALL_REPORT',json.dumps(report,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    arguments = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    if arguments and arguments[0] == '--install-child':
        child_main(arguments[1:])
    else:
        main()
