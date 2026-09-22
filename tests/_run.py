# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Isolated worker for scripts/test.py; runs under Python or factory Blender."""
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import time
import traceback
import unittest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0,str(TESTS))
from _paths import ARTIFACTS


def main():
    args = sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    kind,name,report_name = args
    path = TESTS/name
    started = time.perf_counter()
    result = dict(suite=path.stem,run_id=os.environ.get('PBG_TEST_RUN_ID'),status='FAIL',checks=0)
    try:
        sys.argv = [str(path)]
        if kind=='unittest':
            spec = importlib.util.spec_from_file_location(path.stem,path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            outcome = unittest.TextTestRunner(verbosity=2).run(suite)
            result.update(checks=outcome.testsRun-len(outcome.skipped),skipped_checks=len(outcome.skipped),
                          failures=len(outcome.failures),errors=len(outcome.errors))
            if not outcome.wasSuccessful() or not outcome.testsRun:
                raise AssertionError('Unit-test suite did not pass or discovered no tests')
        else:
            runpy.run_path(str(path),run_name='__main__')
            report_path = ARTIFACTS/report_name
            if not report_path.is_file():
                raise AssertionError('Suite did not produce its current-run report: '+report_name)
            report = json.loads(report_path.read_text(encoding='utf-8'))
            checks = report.get('checks',{})
            count = len(checks) if isinstance(checks,(dict,list)) else report.get('check_count',0)
            if report.get('status')!='PASS' or not count:
                raise AssertionError('Suite report does not contain a nonempty PASS result')
            if isinstance(checks,dict) and not all(value is True for value in checks.values()):
                raise AssertionError('Suite report contains a failed check')
            result.update(checks=count,report=report_name)
        result['status'] = 'PASS'
    except BaseException:
        result['error'] = traceback.format_exc()
        print(result['error'],file=sys.stderr,flush=True)
    finally:
        result['elapsed_seconds'] = round(time.perf_counter()-started,3)
        (ARTIFACTS/'suite_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('SUITE_RESULT '+json.dumps(result,ensure_ascii=False),flush=True)
    if result['status']!='PASS':
        raise RuntimeError('Test suite failed: '+name)


if __name__=='__main__':
    main()
