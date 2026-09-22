# SPDX-License-Identifier: GPL-3.0-or-later
# Project: Plant Batch Grouper (haoccy0u / Codex). See LICENSE.

"""Run fresh synthetic regressions; real plant snapshots are explicit and optional."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT/'tests'
SYNTHETIC = [
    ('test_connection_graph','unittest',None),
    ('test_connection_graph_edges','unittest',None),
    ('test_graph_workflow_v22','blender','test_graph_workflow_report_v22.json'),
    ('test_blender_v21','blender','test_report_v21.json'),
    ('test_stem_local_v21','blender','test_stem_local_report_v21.json'),
    ('test_lifecycle','blender','test_lifecycle_report.json'),
    ('test_install','blender','test_install_report.json'),
]
REAL = [
    ('test_blender_v21_real','blender','test_report_v21_real_model.json','backups/before_v21_20260921_184608.blend'),
    ('test_real_coordinates_v21','blender','test_real_coordinates_report_v21.json','test_artifacts/v21/group43_validation.blend'),
    ('test_real_normals_v21','blender','test_real_normals_report_v21.json','test_artifacts/v21/live_validation_failure.blend'),
    ('test_last_group_v211','blender','test_last_group_report_v211.json','test_artifacts/last_group/before_fix.blend'),
]


def executable(value):
    if not value:
        return None
    found = shutil.which(value)
    path = Path(found or value).expanduser()
    return path.resolve() if path.is_file() else None


def numpy_python(explicit,blender):
    candidates = [executable(explicit)] if explicit else [Path(sys.executable)]
    if blender and not explicit:
        for pattern in ('*/python/bin/python.exe','*/python/bin/python3.*','../Resources/*/python/bin/python3.*'):
            candidates.extend(sorted(blender.parent.glob(pattern),reverse=True))
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            check = subprocess.run([str(candidate),'-c','import numpy'],capture_output=True,timeout=30)
        except (OSError,subprocess.TimeoutExpired):
            continue
        if check.returncode==0:
            return candidate
    return None


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender',help='Blender executable; otherwise find blender on PATH')
    parser.add_argument('--python',help='Optional Python with NumPy for geometry-only tests')
    parser.add_argument('--real-fixtures',type=Path,help='Optional archived root containing backups/ and test_artifacts/')
    parser.add_argument('--results-dir',type=Path,default=ROOT/'.test-results',help='Parent for a new isolated run directory')
    parser.add_argument('--only',nargs='+',help='Run only named suites, without the .py suffix')
    parser.add_argument('--timeout',type=int,default=600,help='Maximum seconds per suite (default 600)')
    parser.add_argument('--list',action='store_true',help='List suites and exit without launching Blender')
    return parser.parse_args()


def main():
    args = arguments()
    all_suites = [(name,kind,report,None) for name,kind,report in SYNTHETIC]+REAL
    if args.list:
        for name,kind,_,fixture in all_suites:
            print(name+' ['+kind+(' / optional fixture: '+fixture if fixture else ' / synthetic')+']')
        return 0
    selected = set(name.removesuffix('.py') for name in args.only) if args.only else None
    known = {suite[0] for suite in all_suites}
    if selected and selected-known:
        raise SystemExit('Unknown suites: '+', '.join(sorted(selected-known)))
    if args.timeout<=0:
        raise SystemExit('--timeout must be greater than zero')
    blender = executable(args.blender or 'blender')
    python = numpy_python(args.python,blender)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    run_dir = args.results_dir.expanduser().resolve()/run_id
    run_dir.mkdir(parents=True,exist_ok=False)
    summary = dict(run_id=run_id,started_utc=datetime.now(timezone.utc).isoformat(),
                   blender=str(blender) if blender else None,python=str(python) if python else None,
                   status='RUNNING',suites=[])
    print('Results: '+str(run_dir),flush=True)
    started = time.perf_counter()
    for name,kind,report,fixture in all_suites:
        if selected and name not in selected:
            continue
        result = dict(suite=name,checks=0)
        reason = None
        if fixture and not args.real_fixtures:
            reason = 'Real fixtures not requested (--real-fixtures PATH)'
        elif fixture and not (args.real_fixtures.expanduser().resolve()/fixture).is_file():
            reason = 'Optional fixture missing: '+fixture
        if reason:
            result.update(status='SKIP',reason=reason)
            summary['suites'].append(result)
            print('SKIP '+name+': '+reason,flush=True)
            continue
        suite_dir = run_dir/name
        suite_dir.mkdir()
        if not (TESTS/(name+'.py')).is_file():
            result.update(status='FAIL',error='Required test script missing: '+name+'.py')
        elif kind=='blender' and not blender:
            result.update(status='FAIL',error='Blender not found; use --blender PATH or add blender to PATH')
        elif kind=='unittest' and not python and not blender:
            result.update(status='FAIL',error='NumPy Python and Blender not found; use --python PATH or --blender PATH')
        else:
            worker_args = [kind,name+'.py',report or '-']
            if kind=='unittest' and python:
                command = [str(python),str(TESTS/'_run.py'),*worker_args]
            else:
                command = [str(blender),'--background','--factory-startup','--python-exit-code','1',
                           '--python',str(TESTS/'_run.py'),'--',*worker_args]
            env = dict(os.environ,PBG_TEST_RESULTS=str(suite_dir),PBG_TEST_RUN_ID=run_id,PYTHONUTF8='1')
            if args.real_fixtures:
                env['PBG_REAL_FIXTURES'] = str(args.real_fixtures.expanduser().resolve())
            else:
                env.pop('PBG_REAL_FIXTURES',None)
            print('RUN  '+name,flush=True)
            try:
                process = subprocess.run(command,cwd=str(ROOT),env=env,capture_output=True,
                                         text=True,encoding='utf-8',errors='replace',timeout=args.timeout)
                (suite_dir/'output.log').write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
                result_path = suite_dir/'suite_result.json'
                if result_path.is_file():
                    recorded = json.loads(result_path.read_text(encoding='utf-8'))
                    if recorded.get('run_id')!=run_id or recorded.get('suite')!=name:
                        raise ValueError('Worker result does not belong to this run')
                    result.update(recorded)
                else:
                    result.update(status='FAIL',error='Worker did not produce a fresh suite_result.json')
                if process.returncode!=0:
                    result.update(status='FAIL',exit_code=process.returncode)
                result['log'] = str(Path(name)/'output.log')
            except subprocess.TimeoutExpired as exc:
                def as_bytes(value):
                    return value.encode('utf-8') if isinstance(value,str) else (value or b'')
                (suite_dir/'output.log').write_bytes(as_bytes(exc.stdout)+as_bytes(exc.stderr))
                result.update(status='FAIL',error='Suite timed out after '+str(args.timeout)+' seconds')
            except (OSError,ValueError) as exc:
                result.update(status='FAIL',error=str(exc))
        summary['suites'].append(result)
        print(result['status']+' '+name+' ('+str(result.get('checks',0))+' checks)',flush=True)
        if result['status']=='FAIL' and result.get('error'):
            print(result['error'],flush=True)
    statuses = [item['status'] for item in summary['suites']]
    summary.update(status='FAIL' if 'FAIL' in statuses else ('PASS' if 'PASS' in statuses else 'SKIP'),
                   passed_suites=statuses.count('PASS'),failed_suites=statuses.count('FAIL'),
                   skipped_suites=statuses.count('SKIP'),
                   passed_checks=sum(item.get('checks',0) for item in summary['suites'] if item['status']=='PASS'),
                   elapsed_seconds=round(time.perf_counter()-started,3),finished_utc=datetime.now(timezone.utc).isoformat())
    (run_dir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('{status}: {passed_suites} suites / {passed_checks} checks; {failed_suites} failed; {skipped_suites} skipped'.format(**summary),flush=True)
    print('Summary: '+str(run_dir/'summary.json'),flush=True)
    return 1 if summary['status']=='FAIL' else 0


if __name__=='__main__':
    raise SystemExit(main())
