"""Content-hash cache-busting for /static assets -- no manual `?v=N` bumps.

`static_url('style.css')` -> `/static/style.css?v=<hash of file contents>`, and
`js_import_map()` renders an ES-module import map that does the same for every
`import ... from './x.js'` inside the JS, so the whole module graph is
versioned automatically. `CachedStaticFiles` (main.py) serves any `?v=` URL
with an immutable 1-year Cache-Control, so a deploy that changes a file
changes its URL and browsers re-fetch exactly what changed.

Import maps key on the *resolved* URL, so a versioned specifier in JS source
(`./api.js?v=3`) would bypass the map and create a second module instance --
JS imports must stay unversioned. Browsers without import-map support (Safari
< 16.4) just load unversioned URLs, which `CachedStaticFiles` serves as
`no-cache` (revalidated), so they stay correct, only a little slower.
"""
import hashlib
import json
import os
import pathlib

from markupsafe import Markup

STATIC_PATH = pathlib.Path(os.environ.get("STATIC_PATH", "static"))

# path -> (mtime_ns, size, hash). Re-hashes only when the file changes on disk,
# so it is computed once per file in prod (static files are baked into the image).
_HASHES: dict = {}


def _file_hash(path: pathlib.Path) -> str:
    st = path.stat()
    cached = _HASHES.get(path)
    if cached and cached[0] == (st.st_mtime_ns, st.st_size):
        return cached[1]
    digest = hashlib.md5(path.read_bytes()).hexdigest()[:10]
    _HASHES[path] = ((st.st_mtime_ns, st.st_size), digest)
    return digest


def static_url(rel_path: str) -> str:
    """`/static/<rel_path>?v=<hash>`; unversioned if the file doesn't exist."""
    url = f"/static/{rel_path}"
    try:
        return f"{url}?v={_file_hash(STATIC_PATH / rel_path)}"
    except OSError:
        return url


def js_import_map() -> Markup:
    """`<script type="importmap">` versioning every module under static/js/.
    Must be emitted before the first `<script type="module">` on the page."""
    imports = {
        f"/static/js/{p.name}": static_url(f"js/{p.name}")
        for p in sorted((STATIC_PATH / "js").glob("*.js"))
    }
    return Markup('<script type="importmap">' + json.dumps({"imports": imports}) + "</script>")


def register(env) -> None:
    env.globals["static_url"] = static_url
    env.globals["js_import_map"] = js_import_map
