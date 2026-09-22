# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Independent geometry contracts for the connection-graph engine.

Run with any Python that has NumPy (Blender's bundled python.exe is sufficient).
No bpy, scene, renderer, or artist asset is used.  Fixtures express geometry
with known connectivity; assertions intentionally avoid internal graph scores,
diagnostic formatting, candidate ordering, and generated group numbering.
"""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest


from _paths import ROOT, ARTIFACTS, REPORTS, fixture_path
PACKAGE = ROOT / "plant_batch_grouper"


def load_engine():
    # Import the geometry module without executing the add-on's bpy-based UI.
    name = "pbg_connection_graph_tests"
    package = types.ModuleType(name)
    package.__path__ = [str(PACKAGE)]
    sys.modules[name] = package
    spec = importlib.util.spec_from_file_location(
        name + ".connection_graph", PACKAGE / "connection_graph.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def record(name, vertices, triangles):
    edges = set()
    for face in triangles:
        for i, start in enumerate(face):
            edges.add(tuple(sorted((start, face[(i + 1) % len(face)]))))
    return dict(name=name, vertices=[tuple(v) for v in vertices],
                edges=sorted(edges), triangles=[tuple(t) for t in triangles],
                polygon_count=len(triangles))


def ribbon(name, points, width=.004, offset=(0, 0, 0)):
    """A thin, faced strip following a path in the XZ plane.

    Constant Y width keeps a genuine surface even when the path bends sharply
    or is horizontal.  This represents a plant card, not a zero-face polyline.
    """
    vertices = []
    for x, y, z in points:
        for sign in (-1, 1):
            vertices.append((x + offset[0], y + offset[1] + sign*width/2,
                             z + offset[2]))
    faces = []
    for i in range(len(points)-1):
        a = 2*i
        faces.extend(((a, a+1, a+3), (a, a+3, a+2)))
    return record(name, vertices, faces)


def trunk(name, x=0, height=1, width=.004):
    return ribbon(name, [(x, 0, height*i/8) for i in range(9)], width)


def leaf(name, root=(0, 0, .5), length=.18, width=.09, rise=.06):
    """A broad tapered leaf with an unambiguous pointed attachment end."""
    x, y, z = root
    return record(name, [(x, y, z),
                         (x+length*.58, y-width/2, z+rise*.58),
                         (x+length, y, z+rise),
                         (x+length*.58, y+width/2, z+rise*.58)],
                  [(0, 1, 2), (0, 2, 3)])


def isolated_blob(name="isolated", center=(3, 0, .5), width=.07):
    x, y, z = center
    # A tetrahedron has no long axis and cannot be mistaken for a stem card.
    return record(name, [(x-width, y-width, z-width),
                         (x+width, y-width, z+width),
                         (x-width, y+width, z+width),
                         (x+width, y+width, z-width)],
                  [(0, 1, 2), (0, 3, 1), (0, 2, 3), (1, 3, 2)])


def scaled(records, factor):
    out = copy.deepcopy(records)
    for item in out:
        item["vertices"] = [tuple(factor*x for x in v) for v in item["vertices"]]
    return out


def assignment(result, name):
    return result["assignments"][name]


def candidates(result, name):
    return {entry["group"] for entry in assignment(result, name).get("candidates", [])}


def owners(result):
    return {name: value["group"] for name, value in result["assignments"].items()}


class ConnectionGraphContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = load_engine()

    def analyze(self, records, **kwargs):
        before = copy.deepcopy((records, kwargs))
        result = self.engine.analyze(records, **kwargs)
        self.assertEqual((records, kwargs), before, "analysis must not mutate inputs")
        self.assertEqual(result.get("engine"), "GRAPH")
        self.assertIsInstance(result.get("graph_report"), dict)
        ids = [group["id"] for group in result["groups"]]
        self.assertEqual(len(ids), len(set(ids)), "group IDs must be unique")
        self.assertEqual(set(result["assignments"]), {r["name"] for r in records})
        for group in result["groups"]:
            self.assertTrue(set(group["stems"]) <= set(group["members"]))
            self.assertTrue(set(group["members"]) <= set(result["assignments"]))
            for name in group["members"]:
                self.assertEqual(assignment(result, name)["group"], group["id"])
        return result

    def test_curved_stem_keeps_leaf_at_its_far_end(self):
        path = [(0, 0, 0), (.01, 0, .2), (.1, 0, .4), (.32, 0, .5),
                (.57, 0, .5), (.8, 0, .4)]
        records = [ribbon("curved", path), leaf("tip_leaf", path[-1])]
        result = self.analyze(records, anchors={7: ["curved"]})
        self.assertEqual(assignment(result, "tip_leaf")["group"], 7)

    def test_horizontal_stem_is_not_discarded_for_zero_vertical_height(self):
        path = [(i/8, 0, 0) for i in range(9)]
        result = self.analyze([ribbon("horizontal", path)])
        self.assertEqual(len(result["groups"]), 1)
        self.assertNotEqual(assignment(result, "horizontal")["group"], 0)
        self.assertGreater(result["height"], 0)

    def test_close_parallel_cards_pair_as_one_stem(self):
        path = [(0, 0, i/8) for i in range(9)]
        records = [ribbon("card_a", path),
                   ribbon("card_b", path, offset=(.001, .001, 0))]
        result = self.analyze(records)
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(set(result["groups"][0]["stems"]), {"card_a", "card_b"})
        self.assertFalse(result["groups"][0].get("stem_uncertain", False))

    def test_crossing_in_the_middle_does_not_pair_two_stems(self):
        a = [(-.3+.6*i/8, 0, i/8) for i in range(9)]
        b = [(.3-.6*i/8, 0, i/8) for i in range(9)]
        result = self.analyze([ribbon("cross_a", a), ribbon("cross_b", b)])
        self.assertEqual(len(result["groups"]), 2)
        self.assertNotEqual(assignment(result, "cross_a")["group"],
                            assignment(result, "cross_b")["group"])

    def test_leaf_connects_through_a_real_side_branch(self):
        branch_path = [(0, 0, .5), (.12, 0, .58), (.24, 0, .66), (.36, 0, .74)]
        records = [trunk("main"), ribbon("side_branch", branch_path),
                   leaf("branch_leaf", branch_path[-1])]
        result = self.analyze(records, anchors={21: ["main"]})
        self.assertEqual(assignment(result, "side_branch")["group"], 21)
        self.assertEqual(assignment(result, "branch_leaf")["group"], 21)

    def test_disconnected_piece_stays_unassigned(self):
        result = self.analyze([trunk("main"), isolated_blob()], anchors={7: ["main"]})
        orphan = assignment(result, "isolated")
        self.assertEqual(orphan["group"], 0)
        self.assertNotEqual(orphan["state"], "ASSIGNED")
        self.assertEqual(candidates(result, "isolated"), set())

    def test_more_than_four_plausible_candidates_are_retained(self):
        records, anchors = [], {}
        for index in range(6):
            name = "neighbor_%d" % index
            # All roots fit inside the leaf's local contact neighborhood; the
            # property under test is preserving all choices, not a tolerance.
            records.append(trunk(name, -.002 + .0008*index))
            anchors[10+index] = [name]
        records.append(leaf("ambiguous"))
        result = self.analyze(records, anchors=anchors)
        self.assertEqual(candidates(result, "ambiguous"), set(anchors))
        self.assertEqual(assignment(result, "ambiguous")["group"], 0)

    def test_manual_assignment_survives_a_better_geometric_candidate(self):
        records = [trunk("old", 0), trunk("new", 1), leaf("moved_leaf", (1, 0, .5))]
        result = self.analyze(records, anchors={7: ["old"], 21: ["new"]},
                              decisions={"moved_leaf": {"group": 7, "manual": True, "excluded": []}})
        moved = assignment(result, "moved_leaf")
        self.assertEqual(moved["group"], 7)
        self.assertTrue(moved.get("manual_review"), "lost manual contact needs review")
        self.assertIn(21, candidates(result, "moved_leaf"))

    def test_manually_excluded_candidate_is_not_reintroduced(self):
        records = [trunk("left", -.002), trunk("right", .002), leaf("choice")]
        result = self.analyze(records, anchors={7: ["left"], 21: ["right"]},
                              decisions={"choice": {"group": 0, "manual": True, "excluded": [7]}})
        self.assertNotIn(7, candidates(result, "choice"))
        self.assertIn(21, candidates(result, "choice"))

    def test_anchor_ids_do_not_follow_name_sorting_or_input_order(self):
        records = [trunk("z_last", 0), trunk("a_first", 1),
                   leaf("left_leaf"), leaf("right_leaf", (1, 0, .5))]
        anchors = {41: ["z_last"], 3: ["a_first"]}
        first = self.analyze(records, anchors=anchors)
        second = self.analyze(list(reversed(records)), anchors=anchors)
        self.assertEqual(owners(first), owners(second))
        self.assertEqual(assignment(first, "left_leaf")["group"], 41)
        self.assertEqual(assignment(first, "right_leaf")["group"], 3)

    def test_uniform_scale_does_not_change_connectivity(self):
        records = [trunk("a", 0), trunk("b", 1),
                   leaf("attached"), isolated_blob("unknown")]
        anchors = {7: ["a"], 21: ["b"]}
        baseline = self.analyze(records, anchors=anchors)
        for factor in (.01, 100):
            with self.subTest(factor=factor):
                result = self.analyze(scaled(records, factor), anchors=anchors)
                self.assertEqual(owners(result), owners(baseline))
                self.assertEqual(candidates(result, "attached"), candidates(baseline, "attached"))

    def test_local_refresh_uses_frozen_scale_when_an_orphan_moves_far_away(self):
        records = [trunk("a"), leaf("attached"), isolated_blob("orphan")]
        baseline = self.analyze(records, anchors={7: ["a"]})
        changed = [records[0], records[1], isolated_blob("orphan", (3, 0, 100))]
        fixed = {key: baseline[key] for key in ("height", "ground", "config")}
        result = self.analyze(changed, anchors={7: ["a"]}, fixed_scale=fixed)
        self.assertEqual(result["height"], baseline["height"])
        self.assertEqual(result["ground"], baseline["ground"])
        self.assertEqual(assignment(result, "attached")["group"], 7)
        self.assertEqual(candidates(result, "attached"), candidates(baseline, "attached"))

    def test_single_valid_stem_has_no_self_pairing_or_phantom_members(self):
        result = self.analyze([trunk("only")])
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["groups"][0]["members"], ["only"])
        self.assertEqual(result["groups"][0]["stems"], ["only"])
        self.assertFalse(result["groups"][0].get("stem_uncertain", False))

    def test_empty_input_is_a_valid_empty_work_queue(self):
        result = self.analyze([])
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["assignments"], {})

    def test_broad_leaf_cannot_bridge_a_disconnected_twig_to_a_stem(self):
        # Main -- broad leaf -- detached twig -- terminal leaf.  Only a leaf
        # touches both structural pieces, so this is not one connected branch.
        twig = [( .4, 0, .5), (.5, 0, .63), (.6, 0, .76)]
        records = [trunk("main"),
                   leaf("bridging_leaf", (0, 0, .5), length=.4, width=.24, rise=0),
                   ribbon("detached_twig", twig), leaf("twig_leaf", twig[-1])]
        result = self.analyze(records, anchors={7: ["main"]})
        self.assertNotEqual(assignment(result, "detached_twig")["group"], 7)
        self.assertNotEqual(assignment(result, "twig_leaf")["group"], 7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
