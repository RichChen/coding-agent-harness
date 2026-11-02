"""Fixed Sphinx public check: freeze/print input, clean base, or terminal candidate."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.validation.public_target import (PACKAGE, package_manifest, public_task_input, check_target, load_candidate)
from src.workspace.contracts import canonical_json, blob_sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--freeze',action='store_true')
    modes.add_argument('--print-task',action='store_true')
    modes.add_argument('--base',action='store_true')
    modes.add_argument('--session',type=Path)
    parser.add_argument('--export',type=Path,help='Optional export, requires its source --session')
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--task-output',type=Path,help='Write assembled --print-task as new UTF-8 file')
    args=parser.parse_args()
    if args.export and not args.session: parser.error('--export requires source --session')
    if args.task_output and not args.print_task: parser.error('--task-output requires --print-task')
    if args.freeze:
        value=package_manifest()
        with (PACKAGE/'check-manifest.json').open('xb') as stream: stream.write(canonical_json(value))
        print(json.dumps(value));return 0
    if args.print_task:
        text=public_task_input()
        if args.task_output:
            args.task_output.parent.mkdir(parents=True,exist_ok=True)
            raw=text.encode('utf-8')
            with args.task_output.open('xb') as stream: stream.write(raw)
            print(json.dumps(dict(task_sha256=blob_sha256(raw),bytes=len(raw))))
        else: print(text)
        return 0
    if not args.output_dir: parser.error('--output-dir is required for execution')
    capture=reader=source=None
    if args.session: capture,reader,source=load_candidate(args.session,args.export)
    result=asyncio.run(check_target(args.output_dir,capture=capture,reader=reader,source=source))
    print(json.dumps(dict(status=result['status'],candidate_id=result.get('candidate_id'),
                         cleanup_ok=result['cleanup_ok'],output_dir=str(args.output_dir))))
    return 0 if result['status']=='passed' else 1 if result['status']=='behavior_failed' else 2


if __name__=='__main__': raise SystemExit(main())
