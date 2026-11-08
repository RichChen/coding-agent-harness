"""Portable stdlib-only complete tree codec, also executed in pinned images.

Unlike a5.snapshot.v1 this schema includes upstream symlinks and executable
modes. Hashes are intentionally not interchangeable with the v1 byte tree.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import sys
import tarfile

LIMIT = 8 * 1024 * 1024
MAX_FILES = 2048
SCHEMA = 'a5.typed-tree.v1'
DELTA_SCHEMA = 'a5.typed-tree.delta.v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(name):
    if not isinstance(name, str) or not name:
        raise ValueError('invalid tree path')
    p = PurePosixPath(name)
    if (str(p) != name or p.is_absolute() or '..' in p.parts or
            '\\' in name or ':' in name or '\x00' in name or not p.parts):
        raise ValueError('unsafe tree path: ' + repr(name))
    return p


def validate_entries(entries):
    if not isinstance(entries, dict) or not entries:
        raise ValueError('empty or invalid tree')
    for name, meta in entries.items():
        path = safe_path(name)
        if not isinstance(meta, dict) or meta.get('kind') not in ('file', 'dir', 'symlink'):
            raise ValueError('unsupported tree member')
        mode = meta.get('mode')
        if type(mode) is not int or mode < 0 or mode > 0o777:
            raise ValueError('unsafe tree member mode')
        for parent in path.parents:
            if str(parent) == '.':
                continue
            if entries.get(str(parent), {}).get('kind') != 'dir':
                raise ValueError('missing or non-directory ancestor')
        if meta['kind'] == 'file':
            if (set(meta) != {'kind', 'mode', 'size', 'sha256'} or
                    type(meta['size']) is not int or meta['size'] < 0 or
                    not isinstance(meta['sha256'], str) or len(meta['sha256']) != 64 or
                    any(c not in '0123456789abcdef' for c in meta['sha256'])):
                raise ValueError('invalid file metadata')
        elif meta['kind'] == 'dir':
            if set(meta) != {'kind', 'mode'}:
                raise ValueError('invalid directory metadata')
        else:
            if set(meta) != {'kind', 'mode', 'target'}:
                raise ValueError('invalid symlink metadata')
            target = meta['target']
            if (not isinstance(target, str) or not target or target.startswith('/') or
                    '\\' in target or ':' in target or '\x00' in target):
                raise ValueError('unsafe link target')
            resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
            if resolved == '..' or resolved.startswith('../'):
                raise ValueError('escaping link target')
            # Bind only links naming a real internal file or directory (never a link,
            # never an ancestor of the link). This rejects cycles and traversal
            # through links; nothing is recorded below a linked directory.
            kind = entries.get(resolved, {}).get('kind')
            if kind == 'dir' and (str(path) + '/').startswith(resolved + '/'):
                raise ValueError('symlink names an ancestor directory')
            if kind not in ('file', 'dir'):
                raise ValueError('symlink does not name an internal regular file or directory')
    return entries


def tree_hash(entries):
    return sha(canonical({'schema': SCHEMA, 'entries': validate_entries(entries)}))


def archive_manifest(archive_path, repo, revision):
    prefix = repo.split('/')[1] + '-' + revision
    entries = {}
    with tarfile.open(str(archive_path), 'r:gz') as archive:
        for member in archive.getmembers():
            root, sep, name = member.name.partition('/')
            if root != prefix:
                raise ValueError('archive root/commit mismatch')
            if not sep and member.isdir():
                continue
            safe_path(name)
            if name in entries:
                raise ValueError('duplicate archive member')
            meta = {'mode': member.mode}
            if member.isdir():
                meta['kind'] = 'dir'
            elif member.isfile():
                data = archive.extractfile(member).read()
                if len(data) != member.size:
                    raise ValueError('short member')
                meta.update(kind='file', size=len(data), sha256=sha(data))
            elif member.issym():
                meta.update(kind='symlink', target=member.linkname)
            else:
                raise ValueError('unsupported archive member type')
            entries[name] = meta
        if archive.pax_headers.get('comment') != revision:
            raise ValueError('archive git commit mismatch')
    return {'schema': SCHEMA, 'repo': repo, 'base_revision': revision,
            'archive_sha256': sha(Path(archive_path).read_bytes()),
            'archive_bytes': Path(archive_path).stat().st_size,
            'entries': entries, 'source_tree_hash': tree_hash(entries),
            'file_count': sum(m['kind'] != 'dir' for m in entries.values()),
            'directory_count': sum(m['kind'] == 'dir' for m in entries.values()),
            'total_bytes': sum(m.get('size', 0) for m in entries.values())}


def disk_entries(root):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('tree root must be a real directory')
    entries = {}
    for parent, dirs, names in os.walk(str(root), followlinks=False):
        for name in dirs + names:
            p = Path(parent) / name
            mode = p.lstat().st_mode
            meta = {'mode': stat.S_IMODE(mode)}
            if stat.S_ISLNK(mode):
                meta.update(kind='symlink', target=os.readlink(str(p)))
            elif stat.S_ISDIR(mode):
                meta['kind'] = 'dir'
            elif stat.S_ISREG(mode):
                meta.update(kind='file', size=p.stat().st_size, sha256=sha(p.read_bytes()))
            else:
                raise ValueError('unsupported filesystem member')
            entries[p.relative_to(root).as_posix()] = meta
    return validate_entries(entries)


def extract(archive_path, manifest, destination, *, verify_modes=True):
    actual = archive_manifest(archive_path, manifest['repo'], manifest['base_revision'])
    if any(manifest.get(key) != value for key, value in actual.items()):
        raise ValueError('complete archive manifest mismatch')
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('extraction destination must be empty')
    # Validation precedes all filesystem writes; do not use tar.extractall.
    entries = manifest['entries']
    for name in sorted(entries, key=lambda n: (len(PurePosixPath(n).parts), n)):
        if entries[name]['kind'] == 'dir':
            (root / name).mkdir()
    with tarfile.open(str(archive_path), 'r:gz') as archive:
        prefix = manifest['repo'].split('/')[1] + '-' + manifest['base_revision'] + '/'
        copied_links = []
        for name, meta in entries.items():
            p = root / name
            if meta['kind'] == 'file':
                with p.open('xb') as stream:
                    stream.write(archive.extractfile(prefix + name).read())
                p.chmod(meta['mode'])
            elif meta['kind'] == 'symlink':
                try:
                    p.symlink_to(meta['target'])
                except OSError as exc:
                    if getattr(exc, 'winerror', None) not in (5, 1314):
                        raise
                    resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), meta['target']))
                    target = entries.get(resolved) or {}
                    if target.get('kind') != 'file':
                        raise
                    data = archive.extractfile(prefix + resolved).read()
                    if sha(data) != target['sha256']:
                        raise ValueError('symlink target hash mismatch')
                    p.write_bytes(data)
                    copied_links.append(name)
    for name, meta in entries.items():
        if meta['kind'] == 'dir':
            (root / name).chmod(meta['mode'])
    actual = disk_entries(root)
    for name in copied_links:
        resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent), entries[name]['target']))
        if actual.get(name, {}).get('sha256') != entries[resolved]['sha256']:
            raise ValueError('materialized symlink mismatch')
        actual[name] = dict(entries[name])
    if not verify_modes:
        actual = {name: {key: value for key, value in meta.items() if key != 'mode'} for name, meta in actual.items()}
        expected = {name: {key: value for key, value in meta.items() if key != 'mode'} for name, meta in entries.items()}
    else:
        expected = entries
    if actual != expected:
        raise ValueError('extracted complete tree mismatch')
    return manifest['source_tree_hash']


def export_delta(root, manifest, allowed_paths):
    base = validate_entries(manifest['entries'])
    if tree_hash(base) != manifest['source_tree_hash']:
        raise ValueError('base tree hash mismatch')
    allowed = set(allowed_paths)
    for name in allowed:
        safe_path(name)
    candidate = disk_entries(root)
    changed = sorted(n for n in base.keys() | candidate.keys() if base.get(n) != candidate.get(n))
    if set(changed) - allowed:
        raise ValueError('candidate changed unassigned paths: ' + repr(sorted(set(changed) - allowed)[:10]))
    if len(changed) > MAX_FILES:
        raise ValueError('candidate delta file limit')
    data = {}
    deleted = []
    for name in changed:
        old, new = base.get(name), candidate.get(name)
        if ((old and old['kind'] != 'file') or (new and new['kind'] != 'file') or
                (old and new and old['mode'] != new['mode'])):
            raise ValueError('candidate changed link/type/mode')
        if new is None:
            deleted.append(name)
        else:
            if new['size'] > LIMIT:
                raise ValueError('candidate delta byte limit')
            data[name] = {'metadata': new, 'content': base64.b64encode((Path(root) / name).read_bytes()).decode('ascii')}
    delta = {'schema': DELTA_SCHEMA, 'base_tree_hash': manifest['source_tree_hash'],
             'candidate_tree_hash': tree_hash(candidate), 'changed': data, 'deleted': deleted}
    validate_delta(manifest, delta, allowed)
    return delta


def validate_delta(manifest, delta, allowed_paths):
    if not isinstance(delta, dict) or delta.get('schema') != DELTA_SCHEMA or len(canonical(delta)) > LIMIT:
        raise ValueError('invalid delta schema or byte limit')
    base = validate_entries(manifest['entries'])
    if delta.get('base_tree_hash') != tree_hash(base) or manifest['source_tree_hash'] != tree_hash(base):
        raise ValueError('delta base tree hash mismatch')
    changed, deleted = delta.get('changed'), delta.get('deleted')
    if (not isinstance(changed, dict) or not isinstance(deleted, list) or
            any(not isinstance(n, str) for n in deleted) or len(set(deleted)) != len(deleted) or
            set(changed) & set(deleted) or len(changed) + len(deleted) > MAX_FILES):
        raise ValueError('invalid delta changes or file limit')
    if (set(changed) | set(deleted)) - set(allowed_paths):
        raise ValueError('delta contains unassigned path')
    result = dict(base)
    for name in deleted:
        safe_path(name)
        if result.get(name, {}).get('kind') != 'file':
            raise ValueError('delta deletes non-file or missing path')
        del result[name]
    for name, change in changed.items():
        safe_path(name)
        if not isinstance(change, dict) or set(change) != {'metadata', 'content'}:
            raise ValueError('invalid delta entry')
        meta = change['metadata']
        content = base64.b64decode(change['content'], validate=True)
        if (meta.get('kind') != 'file' or meta.get('size') != len(content) or meta.get('sha256') != sha(content)):
            raise ValueError('delta file content mismatch')
        old = base.get(name)
        if old and (old['kind'] != 'file' or old['mode'] != meta['mode']):
            raise ValueError('delta changes type or mode')
        result[name] = meta
    if tree_hash(result) != delta.get('candidate_tree_hash'):
        raise ValueError('delta candidate tree hash mismatch')
    return result


def apply_delta(root, manifest, delta, allowed_paths):
    expected = validate_delta(manifest, delta, allowed_paths)
    if disk_entries(root) != manifest['entries']:
        raise ValueError('workspace base drift')
    for name in delta['deleted']:
        (Path(root) / name).unlink()
    for name, change in delta['changed'].items():
        p = Path(root) / name
        p.write_bytes(base64.b64decode(change['content'], validate=True))
        p.chmod(change['metadata']['mode'])
    if disk_entries(root) != expected:
        raise ValueError('delta reconstructed tree mismatch')
    return delta['candidate_tree_hash']


def main():
    action = sys.argv[1]
    manifest = json.loads(Path('/opt/a5-tree.json').read_text())
    if action == 'extract':
        result = extract('/opt/source.tar.gz', manifest, '/opt/a5-base')
    elif action == 'seed':
        if any(Path('/workspace').iterdir()):
            raise ValueError('workspace must be empty')
        if disk_entries('/opt/a5-base') != manifest['entries']:
            raise ValueError('image base drift')
        for child in Path('/opt/a5-base').iterdir():
            dest = Path('/workspace') / child.name
            if child.is_symlink():
                dest.symlink_to(os.readlink(str(child)))
            elif child.is_dir():
                shutil.copytree(str(child), str(dest), symlinks=True)
            else:
                shutil.copy2(str(child), str(dest))
        result = tree_hash(disk_entries('/workspace'))
        if result != manifest['source_tree_hash']:
            raise ValueError('live source tree mismatch')
    elif action == 'export':
        result = export_delta('/workspace', manifest, json.load(sys.stdin)['allowed_paths'])
    elif action == 'apply':
        payload = json.load(sys.stdin)
        result = apply_delta('/workspace', manifest, payload['delta'], payload['allowed_paths'])
    else:
        raise ValueError('unknown tree operation')
    sys.stdout.buffer.write(canonical(result))


if __name__ == '__main__':
    main()
