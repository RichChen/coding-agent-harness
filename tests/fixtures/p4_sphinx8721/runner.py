"""Public, stdlib-only driver. Candidate Sphinx runs in fresh child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

CASES = ('fresh_default', 'html_epub_default', 'html_epub_false', 'epub_true')
LIMIT = 4 * 1024 * 1024
BUILD = r'''
import json,sys,traceback
from pathlib import Path
sys.path[:0]=['/workspace','/workspace/src']
request=json.loads(Path(sys.argv[1]).read_text())
facts={}
try:
 import sphinx
 from sphinx.cmd.build import build_main
 facts={'sphinx_file':str(Path(sphinx.__file__).resolve()),'python_version':'.'.join(map(str,sys.version_info[:3]))}
 code=int(build_main(request['argv']))
except BaseException as exc:
 traceback.print_exc(); code=3; facts['error_type']=type(exc).__name__
facts['exit_code']=code
Path(sys.argv[2]).write_text(json.dumps(facts))
raise SystemExit(code)
'''


def module_pages(paths):
    return sorted(p for p in paths if '_modules' in p.split('/') and p.endswith(('.html', '.xhtml', '.htm')))


def inspect_epub(out):
    archives = list(out.glob('*.epub'))
    if len(archives) != 1:
        raise ValueError('expected exactly one EPUB archive')
    with zipfile.ZipFile(archives[0]) as archive:
        names = archive.namelist()
        if archive.testzip() is not None or archive.read('mimetype') != b'application/epub+zip':
            raise ValueError('invalid EPUB archive')
        if ('META-INF/container.xml' not in names or not any(p.endswith('.opf') for p in names)
                or not any(p.endswith('index.xhtml') and archive.getinfo(p).file_size > 0 for p in names)):
            raise ValueError('empty or incomplete EPUB archive')
        pages = module_pages(names)
        if any(archive.getinfo(p).file_size == 0 for p in pages):
            raise ValueError('empty module page in archive')
    disk = [p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()]
    if not (out / 'index.xhtml').is_file() or (out / 'index.xhtml').stat().st_size == 0:
        raise ValueError('missing EPUB output index')
    if any((out / p).stat().st_size == 0 for p in module_pages(disk)):
        raise ValueError('empty module page on disk')
    return dict(archive=archives[0].name, archive_bytes=archives[0].stat().st_size,
                disk_module_pages=module_pages(disk), archive_module_pages=pages)


def run(root, remaining_ms):
    deadline = time.monotonic() + remaining_ms / 1000
    records, total_output = [], 0
    materials = {name: (root / name).read_bytes() for name in ('conf.py', 'index.rst', 'p4_example.py')}
    for name in CASES:
        case = dict(name=name, status='inconclusive', builds=[])
        records.append(case)
        try:
            if time.monotonic() >= deadline: raise TimeoutError('target build deadline')
            directory = root / name
            source = directory / 'source'
            source.mkdir(parents=True)
            for path, data in materials.items():
                (source / path).write_bytes(data)
            if name == 'html_epub_false':
                with (source / 'conf.py').open('a') as stream: stream.write('\nviewcode_enable_epub = False\n')
            if name == 'epub_true':
                with (source / 'conf.py').open('a') as stream: stream.write('\nviewcode_enable_epub = True\n')
            for builder in (('html', 'epub') if name.startswith('html_') else ('epub',)):
                output = directory / builder
                argv = ['-b', builder, '-d', str(directory / 'doctrees'), str(source), str(output)]
                request, facts = directory / (builder + '-request.json'), directory / (builder + '-facts.json')
                request.write_text(json.dumps(dict(argv=argv)))
                stdout, stderr = directory / (builder + '-stdout'), directory / (builder + '-stderr')
                started = time.monotonic()
                if started >= deadline: raise TimeoutError('target build deadline')
                with stdout.open('wb') as out, stderr.open('wb') as err:
                    proc = subprocess.Popen([sys.executable, '-I', '-B', '-c', BUILD, str(request), str(facts)],
                                            stdout=out, stderr=err)
                    try:
                        while proc.poll() is None:
                            if time.monotonic() >= deadline: raise TimeoutError('target build deadline')
                            if total_output + stdout.stat().st_size + stderr.stat().st_size > LIMIT:
                                raise ValueError('output_limit')
                            time.sleep(0.02)
                    finally:
                        if proc.poll() is None: proc.kill()
                        proc.wait(timeout=2)
                raw_out, raw_err = stdout.read_bytes(), stderr.read_bytes()
                total_output += len(raw_out) + len(raw_err)
                evidence = dict(builder=builder, exit_code=proc.returncode,
                    elapsed_ms=int((time.monotonic()-started)*1000),
                    stdout=raw_out[:LIMIT].decode('utf-8', 'replace'), stderr=raw_err[:LIMIT].decode('utf-8', 'replace'))
                case['builds'].append(evidence)
                if total_output > LIMIT: raise ValueError('output_limit')
                observed = json.loads(facts.read_text())
                evidence['facts'] = observed
                if (proc.returncode != 0 or observed.get('exit_code') != 0
                        or observed.get('sphinx_file') != '/workspace/sphinx/__init__.py'
                        or observed.get('python_version') != '3.9.25' or observed.get('error_type')):
                    raise ValueError('build_or_import_failed')
            if name.startswith('html_'):
                html = directory / 'html/_modules/p4_example.html'
                if not html.is_file() or html.stat().st_size == 0 or 'add' not in html.read_text():
                    raise ValueError('missing_HTML_positive_control')
                case['html_source_page'] = True
            observed = inspect_epub(directory / 'epub')
            case.update(observed)
            if name == 'epub_true':
                ok = (any(p.endswith('p4_example.xhtml') for p in observed['disk_module_pages'])
                      and any(p.endswith('p4_example.xhtml') for p in observed['archive_module_pages']))
            else:
                ok = not observed['disk_module_pages'] and not observed['archive_module_pages']
            case.update(status='passed' if ok else 'behavior_failed', reason='expected_public_behavior' if ok else 'module_page_behavior')
        except Exception as exc:
            case.update(status='inconclusive', reason=str(exc), error_type=type(exc).__name__)
    return dict(protocol='sphinx8721-public-target-v1', cases=records,
                status='inconclusive' if any(r['status']=='inconclusive' for r in records) else
                       'passed' if all(r['status']=='passed' for r in records) else 'behavior_failed')


if __name__ == '__main__':
    root = Path(sys.argv[1])
    result = run(root, int(sys.argv[2]))
    raw = json.dumps(result, ensure_ascii=True).encode()
    if len(raw) > LIMIT:
        result = dict(protocol='sphinx8721-public-target-v1', status='inconclusive', cases=[], reason='report_output_limit')
        raw = json.dumps(result).encode()
    (root / 'report.json').write_bytes(raw)
    raise SystemExit(0 if result['status']=='passed' else 1 if result['status']=='behavior_failed' else 3)
