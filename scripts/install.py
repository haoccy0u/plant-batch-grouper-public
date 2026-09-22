# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Install the built ZIP through Blender, repairing old orphan registrations.

Run in Blender's Text Editor after saving a project copy. This script preserves
saved progress/settings, saves add-on preferences, and never saves the project.
It may also be imported by isolated installation tests.
"""
import ast
import importlib
from pathlib import Path
import sys
import zipfile

import addon_utils
import bpy

MODULE = 'plant_batch_grouper'
ROOT = Path(__file__).resolve().parents[1]


def built_archive():
    tree=ast.parse((ROOT/MODULE/'__init__.py').read_text(encoding='utf-8-sig'))
    info=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
              and any(isinstance(t,ast.Name) and t.id=='bl_info' for t in n.targets))
    return ROOT/'dist'/('plant_batch_grouper_v'+'.'.join(map(str,info['version']))+'.zip')


def remove_orphans():
    """Use actual old module references; never unregister another add-on's types."""
    old_ui=sys.modules.get(MODULE+'.ui')
    old_workflow=sys.modules.get(MODULE+'.workflow')
    old_pivot_ui=sys.modules.get(MODULE+'.pivot_ui')
    if old_pivot_ui:
        old_pivot_ui.stop_runtime()
    for owner,names in ((old_ui,('selection_timer','deferred_resume')),):
        for name in names:
            fn=getattr(owner,name,None)
            if fn and bpy.app.timers.is_registered(fn):
                bpy.app.timers.unregister(fn)
    for callbacks in (bpy.app.handlers.depsgraph_update_post,bpy.app.handlers.load_post,
                      bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):
        for fn in list(callbacks):
            if getattr(fn,'__module__','').startswith(MODULE+'.'):
                callbacks.remove(fn)
    for name in ('pbg_pivot_settings','pbg_settings'):
        if hasattr(bpy.types.Scene,name):
            delattr(bpy.types.Scene,name)
    for cls in reversed(getattr(old_ui,'CLASSES',())):
        if getattr(cls,'is_registered',False):
            bpy.utils.unregister_class(cls)
    if old_workflow:
        old_workflow.DIRTY.clear()


def install(archive=None, *, save_preferences=True):
    archive=Path(archive or built_archive()).resolve()
    if not archive.is_file():
        raise FileNotFoundError('请先运行 scripts/build.py 生成安装包：'+str(archive))
    # Reject invalid packages before changing the running add-on or preferences.
    with zipfile.ZipFile(archive) as package:
        names=package.namelist()
        if MODULE+'/__init__.py' not in names or any(
                not name.startswith(MODULE+'/') or '\\' in name
                or '..' in name.split('/') or ':' in name for name in names):
            raise ValueError('安装包必须只包含 plant_batch_grouper 插件目录')
        damaged=package.testzip()
        if damaged:
            raise ValueError('安装包文件损坏：'+damaged)
        for name in names:
            if name.endswith('.py'):
                compile(package.read(name),name,'exec')
    if bpy.context.mode!='OBJECT':
        raise RuntimeError('请先结束编辑并切换到物体模式')
    snapshots=[]
    for scene in bpy.data.scenes:
        settings={}
        for name in ('pbg_settings','pbg_pivot_settings'):
            if not hasattr(scene,name):
                continue
            p=getattr(scene,name)
            values={}
            for prop in p.bl_rna.properties:
                if prop.identifier!='rna_type' and not prop.is_readonly:
                    values[prop.identifier]=getattr(p,prop.identifier)
            settings[name]=values
        snapshots.append((scene,settings,{key:scene.get(key) for key in (
            'pbg_plan_json','pbg_workspace_view_v2','pbg_pivot_prep_json','pbg_pivot_native_json')}))
    # The old unregister restores preview styling. Keep the exact working view.
    view=[(o,o.hide_get(),tuple(o.color),o.select_get()) for o in bpy.context.view_layer.objects]
    active=bpy.context.view_layer.objects.active
    layers=[]
    def remember_layer(layer):
        layers.append((layer,layer.hide_viewport,layer.exclude))
        for child in layer.children:
            remember_layer(child)
    for scene in bpy.data.scenes:
        for view_layer in scene.view_layers:
            remember_layer(view_layer.layer_collection)
    shading=[(a.spaces.active.shading,a.spaces.active.shading.type,a.spaces.active.shading.color_type)
             for a in bpy.context.screen.areas if a.type=='VIEW_3D'] if bpy.context.screen else []
    old=sys.modules.get(MODULE)
    if old and getattr(old,'__addon_enabled__',False):
        addon_utils.disable(MODULE,default_set=True)
    remove_orphans()
    for name in list(sys.modules):
        if name==MODULE or name.startswith(MODULE+'.'):
            del sys.modules[name]
    # A development checkout must not shadow the installed package on sys.path.
    sys.path[:]=[p for p in sys.path if Path(p or '.').resolve()!=ROOT]
    importlib.invalidate_caches()
    result=bpy.ops.preferences.addon_install(filepath=str(archive),overwrite=True)
    if 'FINISHED' not in result:
        raise RuntimeError('Blender 未完成插件安装')
    addon=addon_utils.enable(MODULE,default_set=True,persistent=True)
    if addon is None:
        raise RuntimeError('插件正式启用失败；请查看 Blender 控制台')
    ui=sys.modules[MODULE+'.ui']
    ui.cancel_deferred_resume()
    for scene,settings,properties in snapshots:
        for name,values in settings.items():
            if hasattr(scene,name):
                for key,value in values.items():
                    if key in getattr(scene,name).bl_rna.properties:
                        setattr(getattr(scene,name),key,value)
        for key,value in properties.items():
            if value is not None:
                scene[key]=value
    # Resume in the now unrestricted context, keeping serialized decisions exact.
    ui.resume(bpy.context)
    for scene,_,properties in snapshots:
        for key,value in properties.items():
            if value is not None:
                scene[key]=value
    for layer,hidden,excluded in layers:
        layer.exclude=excluded
        layer.hide_viewport=hidden
    for obj,hidden,color,selected in view:
        obj.hide_set(hidden)
        obj.color=color
        obj.select_set(selected)
    bpy.context.view_layer.objects.active=active
    for item,kind,color in shading:
        item.type=kind
        item.color_type=color
    expected=Path(bpy.utils.user_resource('SCRIPTS',path='addons'))/MODULE/'__init__.py'
    if Path(addon.__file__).resolve()!=expected.resolve():
        raise RuntimeError('加载位置不是本次安装目录：'+str(addon.__file__))
    if addon_utils.check(MODULE)!=(True,True):
        raise RuntimeError('插件运行状态与偏好设置未一致')
    if save_preferences:
        bpy.ops.wm.save_userpref()
    print('Plant Batch Grouper enabled:',addon.bl_info['version'],addon.__file__)
    return addon


if __name__=='__main__':
    install()
