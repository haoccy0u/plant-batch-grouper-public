# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Portable test inputs and disposable outputs, shared by every test suite."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

_results = os.environ.get('PBG_TEST_RESULTS')
ARTIFACTS = Path(_results).resolve() if _results else Path(tempfile.mkdtemp(prefix='plant-grouper-tests-'))
ARTIFACTS.mkdir(parents=True,exist_ok=True)
REPORTS = ARTIFACTS
_fixtures = os.environ.get('PBG_REAL_FIXTURES')
FIXTURES = Path(_fixtures).resolve() if _fixtures else None


def fixture_path(relative):
    """Read optional archived fixtures; never fall back to a user's open file."""
    if FIXTURES is None:
        raise FileNotFoundError('Real fixtures require --real-fixtures PATH via scripts/test.py')
    path = (FIXTURES/relative).resolve()
    if not path.is_relative_to(FIXTURES):
        raise ValueError('Fixture path must remain inside the supplied fixture directory')
    if not path.is_file():
        raise FileNotFoundError('Missing optional fixture: '+str(path))
    return path
