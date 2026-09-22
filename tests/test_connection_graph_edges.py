# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Additional graph-engine safety contracts, using synthetic geometry only.

Run with Blender's bundled python.exe (NumPy is the only dependency).
Artist assets and live Blender sessions are deliberately not accessed.
"""
import copy
import unittest

from test_connection_graph import load_engine, trunk, ribbon, leaf, record, assignment, owners


class ConnectionGraphEdgeContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = load_engine()

    def keyed_records(self):
        a, b = trunk('a'), trunk('b', x=1)
        a['_geometry_key'], b['_geometry_key'] = 'a-original', 'b-original'
        return [a, b]

    def test_unchanged_geometry_reuses_features_without_reusing_assignments(self):
        records, cache = self.keyed_records(), {}
        self.engine.analyze(records, anchors={7:['a'], 21:['b']}, geometry_cache=cache)
        first_a, first_b = cache['a']['feature'], cache['b']['feature']
        result = self.engine.analyze(records, anchors={41:['a'], 81:['b']}, geometry_cache=cache)
        self.assertIs(cache['a']['feature'], first_a)
        self.assertIs(cache['b']['feature'], first_b)
        self.assertEqual(owners(result), {'a':41, 'b':81})

    def test_changed_digest_rebuilds_only_the_changed_object(self):
        records, cache = self.keyed_records(), {}
        self.engine.analyze(records, geometry_cache=cache)
        first_a, first_b = cache['a']['feature'], cache['b']['feature']
        records[1] = trunk('b', x=1.5)
        records[1]['_geometry_key'] = 'b-moved'
        self.engine.analyze(records, geometry_cache=cache)
        self.assertIs(cache['a']['feature'], first_a)
        self.assertIsNot(cache['b']['feature'], first_b)
        self.assertGreater(float(cache['b']['feature']['lo'][0]), 1.4)

    def test_growth_axis_change_rebuilds_cached_paths(self):
        records, cache = self.keyed_records(), {}
        self.engine.analyze(records, config={'up':2}, geometry_cache=cache)
        original = {name:value['feature'] for name,value in cache.items()}
        result = self.engine.analyze(records, config={'up':0}, geometry_cache=cache)
        self.assertEqual(result['config']['up'], 0)
        for name in original:
            self.assertIsNot(cache[name]['feature'], original[name])

    def test_removed_or_completed_objects_leave_the_cache(self):
        records, cache = self.keyed_records(), {}
        self.engine.analyze(records, geometry_cache=cache)
        self.engine.analyze(records[:1], geometry_cache=cache)
        self.assertEqual(set(cache), {'a'})
        result = self.engine.analyze([], geometry_cache=cache)
        self.assertEqual(cache, {})
        self.assertEqual(result['assignments'], {})

    def test_records_without_digest_do_not_trust_previous_cache(self):
        records, cache = self.keyed_records(), {}
        self.engine.analyze(records, geometry_cache=cache)
        records[0] = trunk('a', x=3)
        result = self.engine.analyze(records, anchors={7:['a'],21:['b']}, geometry_cache=cache)
        self.assertNotIn('a', cache)
        self.assertIn('b', cache)
        self.assertEqual(assignment(result, 'a')['group'], 7)

    def test_one_trunk_with_two_primary_branches_keeps_three_subtrees(self):
        left = [(0,0,.5),(-.3,0,.6),(-.6,0,.8)]
        right = [(0,0,.7),(.3,0,.9),(.6,0,1.1)]
        result = self.engine.analyze([trunk('trunk'), ribbon('left',left), ribbon('right',right),
                                      leaf('left_leaf',left[-1]), leaf('right_leaf',right[-1])])
        self.assertEqual(len(result['groups']), 3)
        trunk_id = assignment(result,'trunk')['group']
        left_id = assignment(result,'left')['group']
        right_id = assignment(result,'right')['group']
        self.assertEqual(len({trunk_id,left_id,right_id}),3)
        self.assertEqual(assignment(result,'left_leaf')['group'],left_id)
        self.assertEqual(assignment(result,'right_leaf')['group'],right_id)
        trunk_group = next(g for g in result['groups'] if g['id']==trunk_id)
        self.assertEqual(trunk_group['members'],['trunk'])

    def test_multiple_roots_keep_side_branches_under_their_own_root(self):
        side = [(0,0,.5),(.2,0,.6),(.35,0,.75)]
        result = self.engine.analyze([trunk('a'),trunk('b',x=2),ribbon('side',side),leaf('tip',side[-1])])
        self.assertEqual(len(result['groups']),2)
        self.assertNotEqual(assignment(result,'a')['group'],assignment(result,'b')['group'])
        self.assertEqual(assignment(result,'side')['group'],assignment(result,'a')['group'])
        self.assertEqual(assignment(result,'side')['kind'],'BRANCH')
        self.assertEqual(assignment(result,'tip')['group'],assignment(result,'a')['group'])

    def test_manual_branch_moved_far_away_does_not_prove_its_own_support(self):
        twig = ribbon('twig',[(4,0,.5),(4.4,0,.8)])
        result = self.engine.analyze([trunk('root'),twig], anchors={7:['root']},
                                     decisions={'twig':{'group':7,'manual':True}})
        a = assignment(result,'twig')
        self.assertEqual(a['group'],7)
        self.assertTrue(a['manual'])
        self.assertTrue(a['manual_review'])
        self.assertEqual(a['state'],'REVIEW')
        self.assertFalse(result['groups'][0]['ready'])
        self.assertIn('twig',result['groups'][0]['blockers'])

    def test_new_pair_between_fixed_groups_requires_review_without_regrouping(self):
        anchors = {7:['a'],21:['b'],31:['unrelated']}
        result = self.engine.analyze([trunk('a'),trunk('b',x=.001),trunk('unrelated',x=2)],anchors=anchors)
        groups = {g['id']:g for g in result['groups']}
        self.assertEqual({gid:g['stems'] for gid,g in groups.items()},anchors)
        for gid in (7,21):
            self.assertTrue(groups[gid]['stem_uncertain'])
            self.assertFalse(groups[gid]['ready'])
        self.assertFalse(groups[31]['stem_uncertain'])
        self.assertTrue(groups[31]['ready'])

    def test_fixed_groups_recover_when_the_unwanted_pair_is_separated(self):
        anchors = {7:['a'],21:['b']}
        overlapping = self.engine.analyze([trunk('a'),trunk('b',x=.001)],anchors=anchors)
        self.assertTrue(all(g['stem_uncertain'] for g in overlapping['groups']))
        separate = self.engine.analyze([trunk('a'),trunk('b',x=1)],anchors=anchors,
                                       fixed_scale={k:overlapping[k] for k in ('height','ground','config')})
        self.assertEqual([g['id'] for g in separate['groups']],[7,21])
        self.assertTrue(all(not g['stem_uncertain'] and g['ready'] for g in separate['groups']))

    def test_manual_anchor_exempts_only_the_explicitly_marked_group(self):
        result = self.engine.analyze([trunk('a'),trunk('b',x=.001)],anchors={7:['a'],21:['b']},
                                     decisions={'a':{'anchor_manual':True}})
        groups = {g['id']:g for g in result['groups']}
        self.assertFalse(groups[7]['stem_uncertain'])
        self.assertTrue(groups[21]['stem_uncertain'])
        self.assertFalse(groups[21]['ready'])

    def test_cards_of_one_automatic_anchor_moved_apart_require_review(self):
        records = [trunk('a'),trunk('b',x=1)]
        result = self.engine.analyze(records,anchors={7:['a','b']})
        self.assertTrue(result['groups'][0]['stem_uncertain'])
        self.assertFalse(result['groups'][0]['ready'])
        manual = self.engine.analyze(records,anchors={7:['a','b']},
                                     decisions={'a':{'anchor_manual':True}})
        self.assertFalse(manual['groups'][0]['stem_uncertain'])
        self.assertEqual(manual['groups'][0]['stems'],['a','b'])

    def test_initial_multiple_pair_ambiguity_survives_fixed_anchor_refresh(self):
        records = [trunk('a'),trunk('b',x=.001),trunk('c',x=-.001)]
        initial = self.engine.analyze(records)
        self.assertTrue(any(g['stem_uncertain'] for g in initial['groups']))
        anchors = {g['id']:copy.copy(g['stems']) for g in initial['groups']}
        refreshed = self.engine.analyze(records,anchors=anchors)
        self.assertEqual([g['id'] for g in refreshed['groups']],list(anchors))
        self.assertTrue(all(g['stem_uncertain'] and not g['ready'] for g in refreshed['groups']))

    def test_zero_area_faces_are_retained_as_unknown_instead_of_auto_output(self):
        degenerate = record('degenerate',[(0,0,0),(0,0,.5),(0,0,1)],[(0,1,2)])
        result = self.engine.analyze([degenerate])
        self.assertEqual(result['groups'],[])
        self.assertIn('degenerate',result['assignments'])
        self.assertEqual(assignment(result,'degenerate')['group'],0)
        self.assertEqual(assignment(result,'degenerate')['state'],'UNASSIGNED')
        self.assertEqual(assignment(result,'degenerate')['kind'],'UNKNOWN')


if __name__ == '__main__':
    unittest.main(verbosity=2)
