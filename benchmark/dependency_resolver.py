import os
import re
import json
import shutil
import argparse
from fnmatch import fnmatch
from collections import defaultdict

from .project_manifest import (
    LIBRARY_DIRNAME, MANIFEST_NAME, is_project_dir, read_manifest
)


"""
Work out what each project needs from outside itself, and put it where RuC can find it.

This resolver walks the closure of every project from its top module, over the project
itself plus whatever search paths it is given, copies what it finds into one shared
library at the dataset root, and records it in each `ruc_project.json`:

    "reference_sources": ["_lib/prim/prim_flop.sv", ...]     compiled, read only, not holed
    "include_dirs":      ["_lib/prim"]                        where `include` resolves

    python3 -m benchmark.dependency_resolver \\
        --dataset_path <dataset> --hdl sv \\
        --search_path <opentitan>/hw \\
        --exclude '*/prim_xilinx/*' --exclude '*/prim_asap7/*' \\
        --write

Without `--write` it resolves and reports, and touches nothing.
"""


PKG_DECL = re.compile(r'^\s*package\s+(\w+)\s*;', re.M)
MOD_DECL = re.compile(r'^\s*(?:module|interface)\s+(\w+)', re.M)
INCLUDE = re.compile(r'`include\s+"([^"]+)"')
PKG_REF = re.compile(r'\b(\w+_pkg)\s*::')
IMPORT = re.compile(r'\bimport\s+(\w+)\s*::')
SCOPE_REF = re.compile(r'\b(\w+)\s*::')
INST_PARAM = re.compile(r'^[ \t]*(\w+)[ \t]*#[ \t]*\(', re.M)
INST_PLAIN = re.compile(r'^[ \t]*(\w+)[ \t]+(\w+)[ \t]*\(', re.M)
INST_NEWLINE = re.compile(r'^[ \t]*(\w+)[ \t]*\n[ \t]*#[ \t]*\(', re.M)
INST_WRAPPED = re.compile(r'^[ \t]*(\w+)[ \t]*\n[ \t]+(\w+)[ \t]*\(', re.M)

# Words that could collide with a real module name, since a candidate only becomes
# an edge if it exists as a module.
KEYWORDS = frozenset({
    "if", "else", "for", "while", "case", "casez", "casex", "always", "always_ff",
    "always_comb", "always_latch", "initial", "final", "assign", "return", "begin",
    "end", "function", "task", "module", "endmodule", "package", "endpackage",
    "import", "export", "typedef", "logic", "wire", "reg", "bit", "int", "integer",
    "input", "output", "inout", "parameter", "localparam", "assert", "assume",
    "cover", "generate", "endgenerate", "unique", "priority", "posedge", "negedge",
    "and", "or", "not", "xor", "nand", "nor", "buf", "signed", "unsigned", "static",
    "automatic", "virtual", "class", "endclass", "extends", "new", "super", "this",
    "foreach", "repeat", "do", "forever", "wait", "fork", "join", "disable", "type",
    "endcase", "endfunction", "endtask", "endinterface", "endpackage",
    "property", "endproperty", "sequence", "endsequence", "covergroup", "endgroup",
})

# OpenTitan specific
DEFAULT_EXCLUDES = (
    "*/dv/*", "*/fpv/*", "*/tb/*", "*/pre_dv/*", "*/sva/*",
    "*/lint/*", "*/syn/*", "*/formal/*", "*/rdc/*", "*/cdc/*",
)
UNINFORMATIVE_DIRS = frozenset({
    "rtl", "autogen", "testing", "ip_autogen", "ip", "hw", "src", "source", "vendor",
})


HEADER_SUFFIXES = (".svh", ".vh")


def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)

    return re.sub(r'//[^\n]*', '', text)


def split_macro_definitions(text):
    """
    Separate the body of every `` `define `` from the rest, keeping the line count of both.
    Returns `(code, macro_bodies)`.
    """
    code = []
    macros = []
    in_macro = False

    for line in text.split("\n"):
        starts = re.match(r'\s*`define\b', line) is not None

        if starts or in_macro:
            in_macro = line.rstrip().endswith("\\")
            code.append("")
            macros.append(line)
            continue

        code.append(line)
        macros.append("")

    return "\n".join(code), "\n".join(macros)


def instantiations(text):
    """ Every module name `text` looks like it instantiates """
    found = set(INST_PARAM.findall(text)) | set(INST_NEWLINE.findall(text))
    found |= {module for module, _instance in INST_PLAIN.findall(text)}
    found |= {module for module, _instance in INST_WRAPPED.findall(text)}

    return found


def scan(path):
    """ Everything one file declares and everything it asks for """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return None

    text, macros = split_macro_definitions(strip_comments(raw))

    instantiated = instantiations(text) | instantiations(macros)

    return {
        "modules": set(MOD_DECL.findall(text)),
        "packages": set(PKG_DECL.findall(text)),
        "instantiates": instantiated - KEYWORDS,
        "imports": set(PKG_REF.findall(text)) | set(IMPORT.findall(text)),
        # Every `name::`, resolved against the packages that exist and never reported.
        "scopes": (set(SCOPE_REF.findall(text)) | set(IMPORT.findall(text))) - KEYWORDS,
        "includes": {os.path.basename(name) for name in INCLUDE.findall(raw)},
        "is_header": path.endswith(HEADER_SUFFIXES),
    }


class SourceIndex:
    """ Every file the resolver may draw on, and what each one provides """

    def __init__(self, hdl, excludes=(), debug=False):
        self.hdl = hdl
        self.excludes = tuple(excludes)
        self.debug = debug

        self.files = {}
        self.module_owner = defaultdict(list)
        self.package_owner = defaultdict(list)
        self.include_owner = defaultdict(list)

    def excluded(self, path):
        normalized = path.replace(os.sep, "/")

        return any(fnmatch(normalized, pattern) for pattern in self.excludes)

    def add_tree(self, root):
        """ Index every source under `root`, skipping the excluded trees """
        suffixes = ("." + self.hdl,) + HEADER_SUFFIXES
        added = 0

        for subdir, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in (".git", "__pycache__", "tmp"))

            for name in sorted(names):
                if not name.endswith(suffixes):
                    continue

                path = os.path.abspath(os.path.join(subdir, name))

                if path in self.files or self.excluded(path):
                    continue

                info = scan(path)

                if info is None:
                    continue

                self.files[path] = info
                added += 1

                for module in info["modules"]:
                    self.module_owner[module].append(path)
                for package in info["packages"]:
                    self.package_owner[package].append(path)

                self.include_owner[name].append(path)

        if self.debug:
            print(f"[INFO] Indexed {added} files under {root}")

        return added

    def provider(self, candidates, prefer_under):
        """
        Which file provides a name, when more than one does.

        A file of the project being resolved wins, so a project that ships its own copy of
        something is not silently checked against another one. Everything else falls back
        to the first indexed path, which is the search path order the caller gave.
        """
        if not candidates:
            return None

        if prefer_under:
            for path in candidates:
                if path.startswith(prefer_under):
                    return path

        return candidates[0]


def closure(index, project_dir, sources, top_module):
    """
    Every file reachable from the top module, and every name that nothing provides.

    Returns (files, unresolved) or (None, ...) when the top module itself is not declared
    by any source of the project.
    """
    project_dir = os.path.abspath(project_dir)
    prefer = project_dir + os.sep

    start = None
    for source in sources:
        info = index.files.get(os.path.abspath(source))
        if info and top_module in info["modules"]:
            start = os.path.abspath(source)
            break

    if start is None:
        return None, set()

    seen = {start}
    queue = [start]
    unresolved = set()
    # A file pulled in only by an `include` is a header: it is passed with -I and must not
    # also be compiled, which for a file of pure macros would be an empty compilation unit
    include_only = set()

    while queue:
        path = queue.pop()
        info = index.files[path]

        wanted = (
            [(name, index.module_owner, False, False) for name in info["instantiates"]]
            + [(name, index.package_owner, False, True) for name in info["imports"]]
            + [(name, index.package_owner, False, False) for name in info["scopes"]]
            + [(name, index.include_owner, True, True) for name in info["includes"]]
        )

        for name, owner, is_include, report_missing in wanted:
            candidates = owner.get(name)

            if not candidates:
                # An unknown instantiation is usually a macro or a generate label rather
                # than a missing module, and an unknown `::` scope is usually a class or an
                # enum, so only names that look like real dependencies are reported
                if report_missing:
                    unresolved.add(name)
                continue

            found = index.provider(candidates, prefer)

            if is_include:
                include_only.add(found)

            if found not in seen:
                seen.add(found)
                queue.append(found)

    # Reached by an include and declaring nothing: a header, whatever its extension
    headers = {
        path for path in include_only
        if index.files[path]["is_header"]
        or not (index.files[path]["modules"] or index.files[path]["packages"])
    }

    return {"sources": seen - headers, "headers": headers}, unresolved


def stranded_sources(found, sources):
    """
    The sources of a project that the top module does not reach.
    """
    if found is None:
        return []

    reached = found["sources"] | found["headers"]

    return sorted(
        source for source in sources if os.path.abspath(source) not in reached
    )


def order_sources(index, paths):
    """
    Analysis order for the library: a file declaring a package before the files using it.
    """
    paths = list(paths)
    provider = {}

    for path in paths:
        for name in index.files[path]["modules"] | index.files[path]["packages"]:
            provider.setdefault(name, path)

    needs = {path: set() for path in paths}

    for path in paths:
        info = index.files[path]
        for name in info["instantiates"] | info["imports"] | info["scopes"]:
            other = provider.get(name)
            if other is not None and other != path:
                needs[path].add(other)

    def rank(path):
        info = index.files[path]

        return (0 if info["packages"] else 1, os.path.basename(path))

    ordered = []
    remaining = set(paths)

    while remaining:
        ready = sorted(
            (path for path in remaining if not (needs[path] & remaining)), key=rank
        )

        if not ready:
            # A dependency cycle, which SystemVerilog packages do allow across files that
            # only use each other's types. Order cannot help there, so fall back to the
            # heuristic and let the compiler resolve it in one compilation unit.
            ready = sorted(remaining, key=rank)

        for path in ready:
            ordered.append(path)
            remaining.discard(path)

    return ordered


def library_group(path):
    """ The folder of `_lib` a file goes into, taken from the block it belongs to """
    parts = os.path.dirname(os.path.abspath(path)).split(os.sep)

    while parts and parts[-1] in UNINFORMATIVE_DIRS:
        parts.pop()

    # A source drawn from a folder the dataset excludes from being a project keeps the
    # block's name, without the underscore that marked it as not-a-project
    return parts[-1].lstrip("_") if parts else "lib"


class Library:
    """ The one copy of the shared sources, at the dataset root """

    def __init__(self, dataset_path, library_dirname=LIBRARY_DIRNAME):
        self.root = os.path.join(dataset_path, library_dirname)
        self.dirname = library_dirname
        self.placed = {}
        self.taken = {}

    def place(self, path):
        """ Where a file lives in the library, as a dataset relative path """
        path = os.path.abspath(path)

        if path in self.placed:
            return self.placed[path]

        group = library_group(path)
        name = os.path.basename(path)
        relative = f"{self.dirname}/{group}/{name}"

        # Only fires on a real collision.
        if relative in self.taken and self.taken[relative] != path:
            stem, extension = os.path.splitext(name)
            parent = os.path.basename(os.path.dirname(path))
            relative = f"{self.dirname}/{group}/{stem}__{parent}{extension}"

        self.taken[relative] = path
        self.placed[path] = relative

        return relative

    def materialize(self):
        """
        Copy every placed file into the dataset, byte for byte, then drop what is stale.

        Copy first and prune second, rather than emptying the library up front: a re-run
        resolves against the dataset it is rewriting, so a source can already be inside the
        library, and deleting it before reading it would lose it. Pruning still happens,
        because a file left behind by an earlier run is referenced by no manifest and would
        quietly become the answer to "where did this come from" for whoever looks next.
        """

        if not self.placed:
            print("[WARNING] Nothing resolved, so the library was left exactly as it was")
            return 0

        dataset = os.path.dirname(self.root)
        written = set()

        for path, relative in sorted(self.placed.items(), key=lambda item: item[1]):
            destination = os.path.join(dataset, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)

            if os.path.abspath(destination) != path:
                shutil.copyfile(path, destination)

            written.add(os.path.abspath(destination))

        stale = 0

        for subdir, _, names in os.walk(self.root):
            for name in names:
                existing = os.path.abspath(os.path.join(subdir, name))

                if existing not in written:
                    os.remove(existing)
                    stale += 1

        if stale:
            print(f"[INFO] Removed {stale} file(s) no manifest references any more")

        return len(self.placed)


def resolve_dataset(dataset_path, hdl, search_paths, excludes=(), library_dirname=LIBRARY_DIRNAME,
                    write=False, debug=False):
    """ Resolve every project of the dataset, and report what each one needed """
    index = SourceIndex(hdl, tuple(excludes) + DEFAULT_EXCLUDES, debug)

    # The dataset first, so a project's own copy of a shared file wins over the search
    # paths, then the search paths in the order they were given
    index.add_tree(dataset_path)

    for search_path in search_paths:
        index.add_tree(search_path)

    library = Library(dataset_path, library_dirname)
    report = {}

    for project in sorted(os.listdir(dataset_path)):
        project_dir = os.path.join(dataset_path, project)

        if not os.path.isdir(project_dir) or not is_project_dir(project):
            continue

        manifest = read_manifest(project_dir)

        if manifest is None:
            report[project] = {"status": "no manifest"}
            continue

        sources = [os.path.join(project_dir, source) for source in manifest["sources"]]
        found, unresolved = closure(index, project_dir, sources, manifest["top_module"])

        if found is None:
            report[project] = {
                "status": "no top module",
                "detail": f"no source of the project declares {manifest['top_module']}",
            }
            continue

        project_prefix = os.path.abspath(project_dir) + os.sep

        external = [p for p in found["sources"] if not p.startswith(project_prefix)]
        headers = [p for p in found["headers"] if not p.startswith(project_prefix)]

        reference_sources = [library.place(p) for p in order_sources(index, external)]
        reference_headers = sorted(library.place(p) for p in headers)
        include_dirs = sorted({os.path.dirname(p) for p in reference_headers})

        report[project] = {
            "status": "resolved",
            "reference_sources": len(reference_sources),
            "reference_headers": len(reference_headers),
            "include_dirs": include_dirs,
            "unresolved": sorted(unresolved),
            "stranded": [
                os.path.relpath(source, project_dir)
                for source in stranded_sources(found, sources)
            ],
        }

        if write:
            manifest["reference_sources"] = reference_sources
            # Copied and passed with -I, never compiled: a header of pure macros is not a
            # compilation unit, and the ones that are guarded would be defined twice
            manifest["reference_headers"] = reference_headers
            manifest["include_dirs"] = include_dirs

            with open(os.path.join(project_dir, MANIFEST_NAME), "w") as f:
                json.dump(manifest, f, indent=4)

    resolved = [name for name, entry in report.items() if entry["status"] == "resolved"]

    if write and not resolved:
        # Almost always a dataset without manifests yet: `generate_manifests` runs as step
        # 1 of the benchmark build, and this resolver reads what it wrote
        print(
            "[ERROR] No project resolved, so nothing was written. Every folder either has "
            f"no {MANIFEST_NAME} or no source declaring its top module -- generate the "
            "manifests first."
        )
    elif write:
        copied = library.materialize()
        print(f"[INFO] Library written: {copied} files under {library.root}")
    else:
        print(f"[INFO] Dry run: {len(library.placed)} files would be written to {library.root}")

    return report


def print_report(report):
    resolved = [p for p, r in report.items() if r["status"] == "resolved"]
    blocked = {p: r for p, r in report.items() if r["status"] != "resolved"}

    print(f"\n[INFO] Projects resolved: {len(resolved)}")

    for project in sorted(resolved):
        entry = report[project]
        note = f", unresolved: {entry['unresolved']}" if entry["unresolved"] else ""
        print(
            f"[INFO]   {project:<24} {entry['reference_sources']:>3} reference sources, "
            f"{entry['reference_headers']:>2} headers, "
            f"{len(entry['include_dirs'])} include dir(s){note}"
        )

    if blocked:
        print(f"\n[WARNING] {len(blocked)} folder(s) not resolved:")
        for project, entry in sorted(blocked.items()):
            print(f"[WARNING]   {project:<24} {entry['status']}: {entry.get('detail', '')}")

    still_missing = sorted({
        name for entry in report.values() for name in entry.get("unresolved", [])
    })

    if still_missing:
        print(
            f"\n[WARNING] {len(still_missing)} name(s) no search path provides. Add the "
            f"tree holding them, or drop the projects needing them:"
        )
        for name in still_missing:
            users = sorted(p for p, r in report.items() if name in r.get("unresolved", []))
            print(f"[WARNING]   {name:<28} needed by {users}")

    stranded = {p: r["stranded"] for p, r in report.items() if r.get("stranded")}

    if stranded:
        count = sum(len(files) for files in stranded.values())
        print(
            f"\n[WARNING] {count} source(s) in {len(stranded)} project(s) are compiled but "
            f"not reachable from the top module. `hierarchy -top` deletes them before the "
            f"miter, so a hole in one is syntax checked and then passes the equivalence "
            f"check whatever the agent wrote. Confirm against Yosys, then delete them:"
        )
        for project, files in sorted(stranded.items()):
            print(f"[WARNING]   {project:<24} {files}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve each project's dependency closure into a shared library"
    )
    parser.add_argument("--dataset_path", required=True, help="Dataset root, holding the projects")
    parser.add_argument("--hdl", required=True, help="v, sv or vhd")
    parser.add_argument(
        "--search_path", action="append", default=[],
        help="Tree to resolve dependencies from. Repeatable, and searched in order.",
    )
    parser.add_argument(
        "--exclude", action="append", default=[],
        help="Glob of paths never drawn from, on top of the verification trees that are "
             "always excluded. Use it to pick one technology library when several "
             "implement the same modules.",
    )
    parser.add_argument("--library", default=LIBRARY_DIRNAME, help="Name of the library folder")
    parser.add_argument(
        "--write", action="store_true",
        help="Copy the library and update every manifest. Without it, nothing is touched.",
    )
    parser.add_argument("--debug", action="store_true", help="Print more information")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    report = resolve_dataset(
        args.dataset_path,
        args.hdl,
        args.search_path,
        args.exclude,
        args.library,
        args.write,
        args.debug,
    )

    print_report(report)
