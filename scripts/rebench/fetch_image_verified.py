"""Fetch Docker Hub images over a direct CDN connection with resume, verify every blob, and docker-load them.

The resulting local image must carry the same RepoDigest as `docker pull` would give.
"""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "runs/rebench/image-cache"
PROXY = "http://127.0.0.1:7890"
WORKERS = 2
PROXY_BLOBS = False
SMALL = 32 << 20
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
lock = threading.Lock()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def opener(proxy, follow=True):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})]
    if not follow:
        handlers.append(NoRedirect())
    return urllib.request.build_opener(*handlers)


def registry_token(repo):
    url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    with opener(PROXY).open(url, timeout=10) as response:
        return json.load(response)["token"]


def blob_location(repo, digest, token):
    request = urllib.request.Request(
        f"https://registry-1.docker.io/v2/{repo}/blobs/{digest}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with opener(PROXY, follow=False).open(request, timeout=10):
            pass
    except urllib.error.HTTPError as error:
        if error.code in (301, 302, 303, 307, 308):
            return error.headers["Location"]
        raise
    raise RuntimeError("official registry did not redirect blob request")


def log(*a):
    with lock:
        print(time.strftime("%H:%M:%S"), *a, flush=True)


class Repo:
    def __init__(self, repo):
        self.repo = repo
        self._tok = None
        self._t = 0

    def token(self):
        if time.time() - self._t > 200:
            self._tok, self._t = registry_token(self.repo), time.time()
        return self._tok

    def manifest(self, ref):
        req = urllib.request.Request(
            f"https://registry-1.docker.io/v2/{self.repo}/manifests/{ref}",
            headers={"Authorization": f"Bearer {self.token()}", "Accept": ACCEPT},
        )
        with opener(PROXY).open(req, timeout=10) as r:
            body = r.read()
            return body, r.headers.get("Content-Type").split(";")[0], r.headers.get("Docker-Content-Digest")

    def location(self, digest):
        return blob_location(self.repo, digest, self.token())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def fetch_blob(repo, digest, size, dest, force_tls12=False):
    if dest.exists() and dest.stat().st_size == size and sha256_file(dest) == digest:
        log("cached", digest[7:19], size)
        return
    if not dest.exists():
        for cached in ROOT.glob("*/blobs/sha256/" + digest[7:]):
            if cached.stat().st_size == size and sha256_file(cached) == digest:
                dest.hardlink_to(cached)
                log("shared cached", digest[7:19], size)
                return
    part = dest.with_suffix(".part")
    for attempt in range(1, 4):
        have = part.stat().st_size if part.exists() else 0
        if have == size:
            if sha256_file(part) != digest:
                raise RuntimeError("digest mismatch, partial retained " + digest)
            part.replace(dest)
            return
        if have > size:
            raise RuntimeError("oversized partial, retained " + digest)
        url = repo.location(digest)
        if not url.startswith("https://"):
            raise RuntimeError("non-HTTPS CDN redirect")
        proxy = PROXY if PROXY_BLOBS or size <= SMALL else ""
        config = "url = " + json.dumps(url) + "\nproxy = " + json.dumps(proxy) + "\n"
        transfer_timeout = 1800 if size > SMALL else 90
        cmd = ["curl.exe", "--config", "-", "--silent", "--show-error", "--location", "--fail",
               "--proto", "=https", "--proto-redir", "=https",
               "--connect-timeout", "10", "--max-time", str(transfer_timeout), "--continue-at", "-",
               "--max-filesize", str(size), "--output", str(part)]
        if size > SMALL:
            cmd += ["--speed-limit", str(1 << 20), "--speed-time", "30"]
        if force_tls12:
            cmd += ["--tlsv1.2", "--tls-max", "1.2"]
        start = time.monotonic()
        proc = subprocess.run(cmd, input=config, text=True, capture_output=True, timeout=transfer_timeout + 30)
        have = part.stat().st_size if part.exists() else 0
        log(digest[7:19], f"attempt={attempt} rc={proc.returncode} bytes={have}/{size}",
            f"seconds={time.monotonic()-start:.1f}")
        if proc.returncode in (28, 35):
            force_tls12 = True
        if proc.returncode == 0 and have == size:
            if sha256_file(part) != digest:
                raise RuntimeError("digest mismatch, partial retained " + digest)
            part.replace(dest)
            return
        if attempt < 3:
            time.sleep(2)
    raise RuntimeError("bounded download failed, partial retained " + digest)


def fetch_image(image):
    ref = image if ":" in image.split("/")[-1] else image + ":latest"
    name, tag = ref.rsplit(":", 1)
    repo = Repo(name)
    out = ROOT / name.split("/")[-1]
    blobs = out / "blobs" / "sha256"
    blobs.mkdir(parents=True, exist_ok=True)

    top, top_type, top_digest = repo.manifest(tag)
    if "sha256:" + hashlib.sha256(top).hexdigest() != top_digest:
        raise RuntimeError("official top manifest digest mismatch")
    manifest_digests = {top_digest}
    (blobs / top_digest[7:]).write_bytes(top)
    manifests = [json.loads(top)]
    if top_type in INDEX_TYPES:
        manifests = []
        for m in json.loads(top)["manifests"]:
            body, _, d = repo.manifest(m["digest"])
            if "sha256:" + hashlib.sha256(body).hexdigest() != m["digest"]:
                raise RuntimeError("official child manifest digest mismatch")
            (blobs / m["digest"][7:]).write_bytes(body)
            manifest_digests.add(m["digest"])
            manifests.append(json.loads(body))
    todo = {}
    for m in manifests:
        for b in [m["config"], *m["layers"]]:
            todo[b["digest"]] = b["size"]
    total = sum(todo.values())
    log(f"{name.split('.')[-1]}: {len(todo)} blobs {total/1e9:.2f} GB, top {top_digest}")
    t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        for fut in [ex.submit(fetch_blob, repo, d, s, blobs / d[7:]) for d, s in
                    sorted(todo.items(), key=lambda x: -x[1])]:
            fut.result()
    log(f"downloaded+verified in {time.time()-t0:.0f}s (verified cache hits included)")

    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "mediaType": top_type, "digest": top_digest, "size": len(top),
            "annotations": {"io.containerd.image.name": f"docker.io/{name}:{tag}",
                            "org.opencontainers.image.ref.name": tag},
        }],
    }
    t1 = time.time()
    log("docker load starting", ref)
    proc = subprocess.Popen(["docker", "--context", "desktop-linux", "load"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    with tarfile.open(fileobj=proc.stdin, mode="w|") as tar:
        for fname, data in (("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
                            ("index.json", json.dumps(index).encode())):
            info = tarfile.TarInfo(fname)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for digest in sorted(manifest_digests | todo.keys()):
            f = blobs / digest[7:]
            tar.add(f, arcname=f"blobs/sha256/{f.name}")
    proc.stdin.close()
    msg = proc.stdout.read().decode(errors="replace").strip()
    rc = proc.wait()
    log(f"docker load rc={rc} in {time.time()-t1:.0f}s: {msg[-300:]}")
    if rc != 0:
        raise RuntimeError("docker load failed, verified blobs retained")
    insp = json.loads(subprocess.run(["docker", "--context", "desktop-linux", "image", "inspect", ref],
                                     capture_output=True, text=True, check=True, timeout=30).stdout)[0]
    ok = f"{name}@{top_digest}" in (insp.get("RepoDigests") or [])
    log(f"RepoDigests={insp.get('RepoDigests')} match_official={ok}")
    if not ok:
        raise SystemExit("RepoDigest mismatch for " + ref)
    log("verified blobs retained", str(out))
    return ok


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", help="Docker Hub repository:tag references")
    parser.add_argument("--cache", type=Path, default=ROOT, help="Retained verified OCI blob cache")
    parser.add_argument("--proxy", default=PROXY, help="Proxy for official registry and small blobs")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4), default=WORKERS)
    parser.add_argument("--proxy-blobs", action="store_true", help="Use the configured proxy for large blobs too")
    args = parser.parse_args()
    ROOT, PROXY, WORKERS = args.cache.resolve(), args.proxy, args.workers
    PROXY_BLOBS = args.proxy_blobs
    for img in args.images:
        fetch_image(img)
