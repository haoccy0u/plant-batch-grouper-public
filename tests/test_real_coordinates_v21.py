# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Group 43 real-scene Join regression, including precise identity validation."""
import json
import math
import sys
import traceback
from pathlib import Path

import bpy

from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
import plant_batch_grouper as addon
from plant_batch_grouper import output, workflow as w
from plant_batch_grouper.mesh_ops import signature, schema

RESULTS = {}


def check(name, condition):
    assert condition, name
    RESULTS[name] = True
    print('PASS', name, flush=True)


def marker(mesh, prefix):
    return next(attr for attr in mesh.attributes if attr.name.startswith(prefix))


def world_vertices(objects):
    return [tuple(obj.matrix_world @ vertex.co) for obj in objects for vertex in obj.data.vertices]


def maximum_vertex_delta(expected, actual):
    """Independent one-to-one nearest matching; the product also checks topology."""
    remaining = list(actual)
    assert len(expected) == len(remaining)
    maximum = 0.0
    for point in expected:
        distances = [sum((a-b)**2 for a, b in zip(point, candidate)) for candidate in remaining]
        closest = min(range(len(distances)), key=distances.__getitem__)
        maximum = max(maximum, math.sqrt(distances[closest]))
        remaining.pop(closest)
    return maximum


def main():
    addon.register()
    report = dict(status='RUNNING', checks=RESULTS)
    try:
        path = fixture_path('test_artifacts/v21/group43_validation.blend')
        bpy.ops.wm.open_mainfile(filepath=str(path))
        plan = w.sync(bpy.context, w.migrate(bpy.context))
        target = w.group(plan, 43)
        index = w.index_objects(bpy.context.scene, plan)
        originals = [index[i] for i in target['members']]
        before_each = {o.name: signature([o]) for o in originals}
        expected_vertices = world_vertices(originals)
        original_schema = schema(originals[0])
        expected_counts = tuple(sum(len(getattr(o.data, name)) for o in originals)
                                for name in ('vertices', 'edges', 'polygons', 'loops'))
        w.confirm(plan, 43)
        w.save(bpy.context.scene, plan)
        old_counter = plan['next_number']
        old_objects, old_meshes = set(bpy.data.objects), set(bpy.data.meshes)
        alternate = bpy.data.materials.new('CoordinateRegressionMaterial')
        native_signature = output.signature
        faults = ('vertex_displacement', 'vertex_id_swap', 'corner_id_swap', 'duplicate_corner_id',
                  'edge_topology', 'corner_vertex_mapping', 'face_winding', 'uv', 'material')
        for fault in faults:
            mutated = [False]

            def corrupt_actual_mesh(items):
                if items and items[0].name.startswith('__PBG_TMP') and not mutated[0]:
                    mesh = items[0].data
                    if fault == 'vertex_displacement':
                        mesh.vertices[0].co.x += .001
                    elif fault == 'vertex_id_swap':
                        attr = marker(mesh, '.pbg_v_')
                        first = mesh.vertices[0].co
                        other = max(range(len(mesh.vertices)), key=lambda n: (mesh.vertices[n].co-first).length_squared)
                        a, b = attr.data[0].value, attr.data[other].value
                        attr.data[0].value, attr.data[other].value = b, a
                    elif fault == 'corner_id_swap':
                        attr = marker(mesh, '.pbg_c_')
                        a, b = attr.data[0].value, attr.data[1].value
                        attr.data[0].value, attr.data[1].value = b, a
                    elif fault == 'duplicate_corner_id':
                        attr = marker(mesh, '.pbg_c_')
                        attr.data[1].value = attr.data[0].value
                    elif fault == 'edge_topology':
                        a, b = mesh.edges[0].vertices
                        different = next(n for n in range(len(mesh.vertices)) if n not in (a, b))
                        mesh.edges[0].vertices = (a, different)
                    elif fault == 'corner_vertex_mapping':
                        mesh.loops[0].vertex_index = (mesh.loops[0].vertex_index + 1) % len(mesh.vertices)
                    elif fault == 'face_winding':
                        loops = list(mesh.polygons[0].loop_indices)
                        vertices = [mesh.loops[n].vertex_index for n in loops]
                        for n, vertex in zip(loops, reversed(vertices)):
                            mesh.loops[n].vertex_index = vertex
                    elif fault == 'uv':
                        mesh.uv_layers[0].data[0].uv.x += .02
                    elif fault == 'material':
                        mesh.materials.append(alternate)
                        mesh.polygons[0].material_index = len(mesh.materials)-1
                    mesh.update()
                    mutated[0] = True
                return native_signature(items)

            output.signature = corrupt_actual_mesh
            was_rejected = False
            try:
                output.merge(bpy.context, plan, [43])
            except ValueError:
                was_rejected = True
            finally:
                output.signature = native_signature
            check(fault + '_rejected', mutated[0] and was_rejected)
            check(fault + '_rollback_preserves_scene_and_number',
                  set(bpy.data.objects) == old_objects and set(bpy.data.meshes) == old_meshes and
                  plan['next_number'] == old_counter and not target.get('done'))
        names = output.merge(bpy.context, plan, [43])
        check('group43_outputs_successfully_once', len(names) == 1 and target.get('done'))
        result = bpy.data.objects[names[0]]
        report['maximum_world_vertex_delta'] = maximum_vertex_delta(expected_vertices, world_vertices([result]))
        check('group43_world_vertices_within_1e_minus7', report['maximum_world_vertex_delta'] <= 1e-7)
        check('group43_vertex_edge_face_corner_counts_unchanged', expected_counts ==
              tuple(len(getattr(result.data, name)) for name in ('vertices', 'edges', 'polygons', 'loops')))
        check('group43_output_attribute_schema_unchanged', schema(result) == original_schema)
        check('temporary_markers_absent_from_final_output',
              not any(attr.name.startswith(('.pbg_v_', '.pbg_c_')) for attr in result.data.attributes))
        check('temporary_markers_absent_from_originals', not any(
            attr.name.startswith(('.pbg_v_', '.pbg_c_')) for obj in originals for attr in obj.data.attributes))
        check('group43_source_signature_unchanged', all(signature([bpy.data.objects[name]]) == value
                                                      for name, value in before_each.items()))
        check('group43_output_collection_and_hidden_sources', result.users_collection[:] ==
              (bpy.context.scene.pbg_settings.output,) and all(obj.hide_get() for obj in originals))
        report['status'] = 'PASS'
    except Exception:
        report['status'] = 'FAIL'
        report['error'] = traceback.format_exc()
        raise
    finally:
        report['check_count'] = len(RESULTS)
        (REPORTS / 'test_real_coordinates_report_v21.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('REAL_COORDINATE_REPORT', json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
