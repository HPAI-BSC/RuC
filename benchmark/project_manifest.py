import os
import json
import re


"""
Describe a multi-file project so that the rest of RuC can treat it like the single
self-contained file the rule completion flow expects.

An agentic project keeps its real source tree, so the top module and the source
list have to be written down. That is `ruc_project.json`:

    {
        "top_module": "cve2_top",
        "hdl": "sv",
        "sources": ["rtl/cve2_pkg.sv", "rtl/cve2_alu.sv", "rtl/cve2_top.sv"],
        "reference_sources": ["_lib/prim/prim_flop.sv", "_lib/tlul/tlul_adapter_reg.sv"],
        "include_dirs": ["_lib/prim"]
    }

Three kinds of path, and the difference between them is what each stage is allowed to do:

- `sources`     the design itself, relative to the **project folder**. Parsed for rule
                occurrences, holed, handed to the agent as editable, and scored.
- `reference_sources`
                the dependency closure, relative to the **dataset root**. Compiled into 
                both sides of the equivalence check and copied into the agent's workspace 
                but they are not the task.
- `include_dirs`
                where `` `include `` resolves from, also relative to the dataset root.

A manifest without the last two behaves correctly.

A project folder holds one design. Anything else that lives next to the projects is named with a leading underscore, and no stage treats such a
folder as a project.
"""


MANIFEST_NAME = "ruc_project.json"

# The shared library written by `benchmark.dependency_resolver`, and the reason for the
# underscore rule below.
LIBRARY_DIRNAME = "_lib"

# Where the reference sources are mounted inside the agent's workspace, so that a library
# path never collides with a path of the project itself.
WORKSPACE_LIBRARY_DIRNAME = "_ruc_lib"

# Never copied into a task: the project history, and caches that are not part of the design
SKIP_DIRS = (".git", "__pycache__", "tmp")

# Written by RuC next to the sources of a project. `all_mask_idx.json` holds the span of
# every rule occurrence and `bundle_idx.json` holds the exact holes of every bundle, so
# copying either into a workspace would hand the agent the answer key.
RUC_ARTIFACTS = frozenset({
    MANIFEST_NAME,
    "all_mask_idx.json",
    "mask_idx.json",
    "bundle_idx.json",
})

# Files that declare shared definitions have to be analysed before the files using them
PACKAGE_PATTERNS = {
    "v": re.compile(r"^\s*package\s+(\w+)", re.MULTILINE),
    "sv": re.compile(r"^\s*package\s+(\w+)", re.MULTILINE),
    "vhd": re.compile(r"^\s*package\s+(\w+)\s+is", re.MULTILINE | re.IGNORECASE),
}

# How a file names a package that another file declares.
PACKAGE_REFERENCE_PATTERNS = {
    "v": re.compile(r"\b(\w+)\s*::"),
    "sv": re.compile(r"\b(\w+)\s*::"),
    "vhd": re.compile(r"\buse\s+\w+\.(\w+)", re.IGNORECASE),
}

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"//[^\n]*")
MACRO_DEFINITION = re.compile(r"^[ \t]*`define\b")

TOP_PATTERNS = {
    "v": re.compile(r"^\s*module\s+(\w+)", re.MULTILINE),
    "sv": re.compile(r"^\s*module\s+(\w+)", re.MULTILINE),
    "vhd": re.compile(r"^\s*entity\s+(\w+)\s+is", re.MULTILINE | re.IGNORECASE),
}


def find_hdl_files_recursive(project_dir, hdl):
    """ Collect every HDL source of a project, as paths relative to the project folder """
    sources = []

    for subdir, dirs, files in os.walk(project_dir):
        # Never hand the agent, or the checker, anything from the project history
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for name in files:
            if not name.endswith("." + hdl):
                continue

            full_path = os.path.join(subdir, name)
            sources.append(os.path.relpath(full_path, project_dir))

    return sorted(sources)


def find_extra_files(project_dir, sources):
    """
    Everything else the project ships, as paths relative to the project folder.

    Non HDL files are context only. They are never parsed for rule occurrences, never holed
    and never evaluated; only the manifest `sources` are.
    """
    known = set(sources)
    extra = []

    for subdir, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for name in files:
            if name in RUC_ARTIFACTS:
                continue

            relative = os.path.relpath(os.path.join(subdir, name), project_dir)

            if relative in known:
                continue

            extra.append(relative)

    return sorted(extra)


def read_source(project_dir, source):
    try:
        with open(os.path.join(project_dir, source), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def declares_package(text, hdl):
    pattern = PACKAGE_PATTERNS.get(hdl)

    return bool(pattern and pattern.search(text))


def declared_modules(text, hdl):
    pattern = TOP_PATTERNS.get(hdl)

    if not pattern:
        return []

    return pattern.findall(text)


def scrub(text):
    """
    Strips comments and macro bodies before scanning for package references
    """
    text = LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))

    kept = []
    in_macro = False

    for line in text.split("\n"):
        if MACRO_DEFINITION.match(line) or in_macro:
            in_macro = line.rstrip().endswith("\\")
            kept.append("")
            continue

        kept.append(line)

    return "\n".join(kept)


_SCAN_CACHE = {}


def scan_packages(path, hdl):
    """
    `(packages declared, packages referenced)` of one file.

    Cached on the file's identity rather than its path, because the agentic flow scans a
    generated file that is rewritten between runs under the same name.
    """
    try:
        stat = os.stat(path)
        key = (os.path.abspath(path), hdl, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return frozenset(), frozenset()

    cached = _SCAN_CACHE.get(key)

    if cached is not None:
        return cached

    declares = PACKAGE_PATTERNS.get(hdl)
    references = PACKAGE_REFERENCE_PATTERNS.get(hdl)

    if not declares or not references:
        return frozenset(), frozenset()

    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = scrub(f.read())
    except OSError:
        return frozenset(), frozenset()

    scanned = (frozenset(declares.findall(text)), frozenset(references.findall(text)))
    _SCAN_CACHE[key] = scanned

    return scanned


def order_by_package_dependencies(paths, hdl, debug=False):
    """
    Order a compile list so that a package is declared before the files using it.

    A cycle is left in the incoming order: SystemVerilog does allow packages to use each
    other's types across files of one compilation unit, and no order helps there.
    """
    paths = list(paths)

    if not PACKAGE_PATTERNS.get(hdl) or len(paths) < 2:
        return paths

    scanned = [scan_packages(path, hdl) for path in paths]

    provider = {}
    for index, (declared, _referenced) in enumerate(scanned):
        for package in declared:
            provider.setdefault(package, index)

    needs = []
    for index, (_declared, referenced) in enumerate(scanned):
        needs.append({
            provider[package] for package in referenced
            if provider.get(package, index) != index
        })

    ordered = []
    remaining = set(range(len(paths)))

    while remaining:
        ready = sorted(index for index in remaining if not (needs[index] & remaining))

        if not ready:
            if debug:
                cycle = [os.path.basename(paths[i]) for i in sorted(remaining)]
                print(f"[WARNING] Package dependency cycle, left in place: {cycle}")
            ready = sorted(remaining)

        for index in ready:
            ordered.append(paths[index])

        remaining.difference_update(ready)

    if debug and ordered != paths:
        print(f"[INFO] Compile order changed by package dependencies ({len(paths)} files)")

    return ordered


def sort_sources(project_dir, sources, hdl, debug=False):
    """
    Order the sources of a project for analysis.

    Packages first, since both `read_slang` and `ghdl -a` need the shared definitions
    before the files that import them, then the dependencies between those packages
    resolved. The rest keeps its stable alphabetical order, so regenerating a manifest
    twice gives the same file.
    """
    packages = []
    rest = []

    for source in sources:
        text = read_source(project_dir, source)

        if declares_package(text, hdl):
            packages.append(source)
        else:
            rest.append(source)

    packages = [
        os.path.relpath(path, project_dir)
        for path in order_by_package_dependencies(
            [os.path.join(project_dir, source) for source in packages], hdl, debug
        )
    ]

    if debug and packages:
        print(f"[INFO] Packages analysed first: {packages}")

    return packages + rest


def find_top_module(project_dir, sources, hdl, debug=False):
    """
    Infer the top module of a project.

    The single-file flow names the file after the top module, so the multi-file
    equivalent is to name the project folder after it. That is checked against the
    declarations actually found in the sources, and only falls back to a structural
    guess (the module nobody instantiates) when the folder name does not match.
    """
    project_name = os.path.basename(os.path.normpath(project_dir))

    declarations = {}
    for source in sources:
        for module in declared_modules(read_source(project_dir, source), hdl):
            declarations.setdefault(module, source)

    if not declarations:
        if debug:
            print(f"[WARNING] No module or entity declaration found in {project_dir}")
        return project_name

    if project_name in declarations:
        return project_name

    # Fall back to the module that no other source instantiates by name
    instantiated = set()
    for source in sources:
        text = read_source(project_dir, source)

        for module in declarations:
            if declarations[module] == source:
                continue
            if re.search(rf"\b{re.escape(module)}\b", text):
                instantiated.add(module)

    roots = [m for m in declarations if m not in instantiated]

    if len(roots) == 1:
        if debug:
            print(f"[WARNING] {project_dir} is not named after its top module, using {roots[0]}")
        return roots[0]

    if debug:
        print(
            f"[WARNING] Could not infer the top module of {project_dir} "
            f"(candidates: {sorted(roots) or sorted(declarations)}), using {project_name}. "
            f"Set 'top_module' in {MANIFEST_NAME} to fix it."
        )

    return project_name


def build_manifest(project_dir, hdl, debug=False):
    """ Build the manifest of a single project, or None when it has no sources """
    sources = find_hdl_files_recursive(project_dir, hdl)

    if not sources:
        return None

    sources = sort_sources(project_dir, sources, hdl, debug)

    return {
        "top_module": find_top_module(project_dir, sources, hdl, debug),
        "hdl": hdl,
        "sources": sources,
    }


def read_manifest(project_dir):
    """ Read the manifest of a project, or None when it is a single-file project """
    manifest_path = os.path.join(project_dir, MANIFEST_NAME)

    if not os.path.exists(manifest_path):
        return None

    with open(manifest_path) as f:
        return json.load(f)


def is_project_dir(name):
    """
    Whether a folder of the dataset is a project.

    A leading underscore marks a folder that lives next to the projects without being one
    -- `_lib` above all. Without this the shared library would get a manifest of its own,
    be parsed for rule occurrences, and be emitted as a task.
    """
    return not name.startswith("_")


def dataset_root(project_dir):
    """ The folder holding the projects, which is what the reference paths are relative to """
    return os.path.dirname(os.path.abspath(project_dir))


def manifest_sources(project_dir, manifest):
    """ Absolute paths of the sources of a project, in analysis order """
    return [os.path.join(project_dir, source) for source in manifest["sources"]]


def manifest_reference_sources(project_dir, manifest):
    """
    Absolute paths of the dependency closure, in analysis order.

    Relative to the dataset root rather than the project, so one copy of the library
    serves every project instead of one copy per project.
    """
    root = dataset_root(project_dir)

    return [os.path.join(root, source) for source in manifest.get("reference_sources", [])]


def manifest_reference_headers(project_dir, manifest):
    """
    Absolute paths of the headers the design includes.

    Kept apart from `reference_sources` because they are never compiled: they are found
    through `include_dirs`, and a header of pure macros is not a compilation unit.
    """
    root = dataset_root(project_dir)

    return [os.path.join(root, header) for header in manifest.get("reference_headers", [])]


def manifest_include_dirs(project_dir, manifest):
    """
    Absolute include directories of a project, relative to the dataset root.

    The project's own folder is always one of them, and is not recorded in the manifest: a
    design that `` `include ``s a header it ships itself needs it, `verilator` and
    `iverilog` do not search the including file's directory on their own, and it is the
    same directory for every project.
    """
    root = dataset_root(project_dir)

    return [os.path.abspath(project_dir)] + [
        os.path.join(root, directory) for directory in manifest.get("include_dirs", [])
    ]


def workspace_reference_path(relative_path):
    """
    Where a reference source lands inside the agent's workspace.

    The library prefix is dropped so the agent reads `_ruc_lib/prim/prim_flop.sv` rather
    than `_ruc_lib/_lib/prim/prim_flop.sv`, and everything else keeps its dataset relative
    path, so a reference that is another project's package cannot collide with a source of
    this one.
    """
    parts = relative_path.replace("\\", "/").split("/")

    if parts and parts[0] == LIBRARY_DIRNAME:
        parts = parts[1:]

    return os.path.join(WORKSPACE_LIBRARY_DIRNAME, *parts)


def manifest_hdl(manifest, sources):
    """ The HDL of a project, from the manifest or from what its sources are called """
    hdl = manifest.get("hdl")

    if hdl:
        return hdl

    for source in sources:
        suffix = os.path.splitext(str(source))[1].lstrip(".")

        if suffix in PACKAGE_PATTERNS:
            return suffix

    return None


def compile_sources(project_dir, manifest, sources):
    """
    The full argument list handed to a compiler: the closure first, then the design, with
    package dependencies resolved across both.

    The closure leads because it declares most of what the design imports, and both
    `read_slang` and `ghdl -a` need a declaration before its users. That alone is not
    enough, in either direction: the library imports packages the project declares, and a
    project's own packages import each other. `order_by_package_dependencies` sorts the
    joined list and only moves what has to move, so the library still leads everything it
    does not depend on.

    `sources` is passed in rather than read from the manifest so that a caller checking one
    generated file can swap it in without the library moving.
    """
    sources = list(sources)
    joined = manifest_reference_sources(project_dir, manifest) + sources

    return order_by_package_dependencies(joined, manifest_hdl(manifest, sources))


def generate_manifests(dataset_path, hdl, debug=False):
    """
    Write `ruc_project.json` for every project of the dataset that does not have one.

    Existing manifests are kept untouched, so hand-corrected top modules and analysis
    orders survive a regeneration.
    """
    written = 0
    skipped = 0

    for project in sorted(os.listdir(dataset_path)):
        project_dir = os.path.join(dataset_path, project)

        if not os.path.isdir(project_dir) or not is_project_dir(project):
            continue

        if read_manifest(project_dir) is not None:
            if debug:
                print(f"[INFO] Keeping the existing manifest of {project}")
            skipped += 1
            continue

        manifest = build_manifest(project_dir, hdl, debug)

        if manifest is None:
            if debug:
                print(f"[WARNING] No .{hdl} sources found in {project_dir}")
            continue

        with open(os.path.join(project_dir, MANIFEST_NAME), "w") as f:
            json.dump(manifest, f, indent=4)

        if debug:
            print(
                f"[INFO] {project}: top module {manifest['top_module']}, "
                f"{len(manifest['sources'])} sources"
            )

        written += 1

    print(f"[INFO] Manifests written: {written}, kept: {skipped}")

    return written, skipped
