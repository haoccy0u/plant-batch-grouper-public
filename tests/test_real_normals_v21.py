# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Exact saved real-model group-20 regression; isolated background Blender only."""
import json
import math
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path

import bpy

from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
import plant_batch_grouper as addon
from plant_batch_grouper import output, workflow as w
from plant_batch_grouper.mesh_ops import signature

RESULTS = {}


def check(name, condition):
    assert condition, name
    RESULTS[name] = True
    print('PASS', name, flush=True)


def without_normals(value):
    clean = dict(value)
    faces = Counter()
    for (material, smooth, corners), count in value['faces'].items():
        static_corners = [corner[:-1] for corner in corners]
        rotations = [tuple(static_corners[n:] + static_corners[:n]) for n in range(len(static_corners))]
        faces[(material, smooth, min(rotations))] += count
    clean['faces'] = faces
    return clean


def raw_normals_by_face(objects):
    """Independent corner-level comparison, including UV/material/color binding."""
    by_face = defaultdict(list)
    rounded = lambda vector, digits: tuple(round(float(v), digits) for v in vector)
    for obj in objects:
        mesh = obj.data
        coords = [rounded(obj.matrix_world @ vert.co, 7) for vert in mesh.vertices]
        uv = sorted(mesh.uv_layers, key=lambda a: a.name)
        color = sorted((a for a in mesh.color_attributes if a.domain == 'CORNER'), key=lambda a: a.name)
        normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
        normals = mesh.corner_normals
        for face in mesh.polygons:
            static, raw = [], []
            for index in face.loop_indices:
                static.append((coords[mesh.loops[index].vertex_index],
                               tuple((a.name, rounded(a.data[index].uv, 6)) for a in uv),
                               tuple((a.name, a.data_type, rounded(a.data[index].color, 6)) for a in color)))
                raw.append(tuple((normal_matrix @ normals[index].vector).normalized()))
            rotation = min(range(len(static)), key=lambda n: tuple(static[n:] + static[:n]))
            material = obj.material_slots[face.material_index].material if face.material_index < len(obj.material_slots) else None
            key = (material.name if material else '', face.use_smooth,
                   tuple(static[rotation:] + static[:rotation]))
            by_face[key].append(tuple(raw[rotation:] + raw[:rotation]))
    return dict(by_face)


def raw_maximum_delta(before, after):
    assert before.keys() == after.keys(), 'face identity changed'
    maximum = 0.0
    for key, expected in before.items():
        candidates = list(after[key])
        assert len(candidates) == len(expected), 'face multiplicity changed'
        for normals in expected:
            errors = [max(math.sqrt(sum((a-b)**2 for a, b in zip(n, m)))
                          for n, m in zip(normals, candidate))
                      for candidate in candidates]
            chosen = min(range(len(errors)), key=errors.__getitem__)
            maximum = max(maximum, errors[chosen])
            candidates.pop(chosen)
    return maximum


def main():
    addon.register()
    report = dict(status='RUNNING', checks=RESULTS)
    try:
        fixture = fixture_path('test_artifacts/v21/live_validation_failure.blend')
        bpy.ops.wm.open_mainfile(filepath=str(fixture))
        plan = w.sync(bpy.context, w.migrate(bpy.context))
        target = w.group(plan, 20)
        objects = w.index_objects(bpy.context.scene, plan)
        originals = [objects[i] for i in target['members']]
        expected = signature(originals)
        expected_raw = raw_normals_by_face(originals)
        original_each = {o.name: signature([o]) for o in originals}
        report['members'] = [dict(name=o.name, custom_normals=o.data.has_custom_normals,
                                  loops=len(o.data.loops), matrix=[list(r) for r in o.matrix_world])
                             for o in originals]
        w.confirm(plan, 20)
        w.save(bpy.context.scene, plan)
        old_counter = plan['next_number']
        old_objects, old_meshes = set(bpy.data.objects), set(bpy.data.meshes)
        native_signature = output.signature

        alternate_material = bpy.data.materials.new('RegressionCorruptMaterial')
        for corruption in ('normal', 'uv', 'color', 'material'):
            mutated = [False]

            def corrupt_actual_mesh(items):
                if items and items[0].name.startswith('__PBG_TMP') and not mutated[0]:
                    mesh = items[0].data
                    if corruption == 'normal':
                        normals = [tuple(n.vector) for n in mesh.corner_normals]
                        normals[0] = tuple(-v for v in normals[0])
                        attr = mesh.attributes.get('custom_normal')
                        if attr and attr.data_type == 'FLOAT_VECTOR':
                            # The legacy tangent-space setter does not alter
                            # Blender's free-normal attribute. Mutate real data.
                            attr.data[0].vector = normals[0]
                        else:
                            mesh.normals_split_custom_set(normals)
                        mesh.update()
                        assert sum(a*b for a,b in zip(mesh.corner_normals[0].vector,normals[0])) > .99, 'normal fault was not applied'
                    elif corruption == 'uv':
                        mesh.uv_layers[0].data[0].uv.x += .02
                    elif corruption == 'color':
                        if mesh.color_attributes:
                            attr = mesh.color_attributes[0]
                        else:
                            attr = mesh.color_attributes.new(name='InjectedColor', type='FLOAT_COLOR', domain='CORNER')
                        attr.data[0].color = (1.0, .25, .75, 1.0)
                    elif corruption == 'material':
                        mesh.materials.append(alternate_material)
                        mesh.polygons[0].material_index = len(mesh.materials) - 1
                    mesh.update()
                    mutated[0] = True
                return native_signature(items)

            output.signature = corrupt_actual_mesh
            was_rejected = False
            try:
                output.merge(bpy.context, plan, [20])
            except ValueError:
                was_rejected = True
            finally:
                output.signature = native_signature
            check(corruption + '_corruption_is_still_rejected', mutated[0] and was_rejected)
            check(corruption + '_failure_keeps_scene_and_number',
                  set(bpy.data.objects) == old_objects and set(bpy.data.meshes) == old_meshes and
                  plan['next_number'] == old_counter and not target.get('done'))
        names = output.merge(bpy.context, plan, [20])
        check('real_group_20_outputs_once', len(names) == 1 and target.get('done'))
        result = bpy.data.objects[names[0]]
        check('real_group_20_all_non_normal_signature_fields_exact',
              without_normals(signature([result])) == without_normals(expected))
        report['raw_normal_max_vector_delta'] = raw_maximum_delta(expected_raw, raw_normals_by_face([result]))
        check('real_group_20_raw_normals_preserved_within_float_error',
              report['raw_normal_max_vector_delta'] <= 5e-5)
        check('real_group_20_sources_unchanged', all(signature([bpy.data.objects[name]]) == value
                                                   for name, value in original_each.items()))
        check('real_group_20_uses_configured_output', result.users_collection[:] ==
              (bpy.context.scene.pbg_settings.output,))
        check('real_group_20_sources_hide_after_success', all(obj.hide_get() for obj in originals))
        report['status'] = 'PASS'
    except Exception:
        report['status'] = 'FAIL'
        report['error'] = traceback.format_exc()
        raise
    finally:
        report['check_count'] = len(RESULTS)
        (REPORTS / 'test_real_normals_report_v21.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REAL_NORMAL_REPORT', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
