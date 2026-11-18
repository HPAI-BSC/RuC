import os
import json
import hashlib
import shutil

from configs.agent_prompts import (
    AGENT_INSTRUCTION, EXPORT_SUBDIR, HDL_NAMES, HOLE_EXAMPLE_BODY, HOLE_MARKERS,
    STRAY_REPORT_NAME, TASK_TOML_TEMPLATE, TASK_README_TEMPLATE, TEST_SCRIPT_TEMPLATE,
    VERIFIER_DIR, WORKSPACE_DIR
)
from .bundle_selecter import read_bundles
from .project_manifest import (
    WORKSPACE_LIBRARY_DIRNAME, dataset_root, find_extra_files, is_project_dir,
    read_manifest, workspace_reference_path
)
from .utils import extend_start_to_line_begin


"""
Turn each bundle into a task folder Harbor can run.

    <output_path>/<project>-bundle00/
        task.toml                    what Harbor needs to schedule the task, and the
                                     only place the container image can be named
        instruction.md               the prompt handed to the agent
        environment/                 the project with its holes punched out, uploaded
                                     wholesale into the container workdir (/app)
        environment/RUC_TASK.md
        tests/test.sh                the in-container checks, the workspace export and
                                     the reward file Harbor requires
        solution/                    golden sources and holes.json, never given to Harbor
"""


TASKS_DIRNAME = "tasks"

LINT_COMMANDS = {
    "v": ["iverilog", "-t", "null", "-g2012"],
    "sv": ["verilator", "--lint-only", "-Wno-fatal"],
    "vhd": ["ghdl", "-a", "--std=08"],
}

# Every bundle of a project gets its own copy of the workspace, so anything the project
# ships is written once per bundle. Past this the run is warned.
LARGE_PAYLOAD_BYTES = 64 * 1024 * 1024


def warn_if_payload_large(project_dir, extra_files, bundle_count, library_sources=()):
    """ Flag a project whose reference files will be copied once per bundle """
    sizes = []

    for extra in extra_files:
        try:
            sizes.append((os.path.getsize(os.path.join(project_dir, extra)), extra))
        except OSError:
            continue

    # The dependency closure is copied per bundle just like the rest, and for a design
    # built on a large shared library it is most of the payload
    for source_path, workspace_path in library_sources:
        try:
            sizes.append((os.path.getsize(source_path), workspace_path))
        except OSError:
            continue

    total = sum(size for size, _ in sizes)

    if total * max(bundle_count, 1) < LARGE_PAYLOAD_BYTES:
        return

    biggest = ", ".join(name for _, name in sorted(sizes, reverse=True)[:3])

    print(
        f"[WARNING] {os.path.basename(project_dir)}: reference files are "
        f"{total / 1024 / 1024:.1f} MB and are copied into each of {bundle_count} "
        f"bundles ({total * bundle_count / 1024 / 1024:.1f} MB total). Largest: {biggest}"
    )


def bundle_task_name(project, bundle_id):
    """ Folder name of a bundle, mirroring the <project>-task<i> naming of the flow """
    return f"{project}-bundle{bundle_id.lstrip('b')}"


def build_hole_block(text, hole, hdl):
    """
    The marker block that replaces a hole in the workspace.

    The block is indented like the line the hole starts on, so the sources stay readable
    and the agent sees the region where the code used to be.
    """
    begin, end = HOLE_MARKERS[hdl]

    line_begin = extend_start_to_line_begin(text, hole["span"][0])
    indent = text[line_begin:hole["span"][0]]

    if indent.strip():
        # The hole does not start the line, so do not copy the code before it
        indent = " " * len(indent)

    lines = [
        begin.format(id=hole["id"]),
        end.format(id=hole["id"]),
    ]

    return ("\n" + indent).join(lines)


def punch_holes(text, holes, hdl):
    """
    Replace every hole of one source with its marker block.

    Holes are punched from the end of the file backwards so that the spans of the holes
    still to be punched keep pointing at the right characters.
    """
    for hole in sorted(holes, key=lambda h: h["span"][0], reverse=True):
        start, stop = hole["span"]
        text = text[:start] + build_hole_block(text, hole, hdl) + text[stop:]

    return text


def library_layout(project_dir, manifest):
    """
    Where each reference source and include directory of a project sits, on the host and
    inside the workspace.

    Sources are compiled, headers are only copied and reached through `-I`, and both are
    written into the workspace.

    Returns ([(path, workspace_path)] sources, [(path, workspace_path)] headers,
    [workspace_include_dir]).
    """
    root = dataset_root(project_dir)

    def placed(key):
        return [
            (os.path.join(root, relative), workspace_reference_path(relative))
            for relative in manifest.get(key, [])
        ]

    include_dirs = ["."] + [
        workspace_reference_path(relative)
        for relative in manifest.get("include_dirs", [])
    ]

    return placed("reference_sources"), placed("reference_headers"), include_dirs


def write_library(workspace, library_sources, debug=False):
    """
    Copy the dependency closure into the workspace.

    Byte for byte and never holed.
    """
    written = 0

    for source_path, workspace_path in library_sources:
        destination = os.path.join(workspace, workspace_path)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(source_path, destination)
        written += 1

    if debug and written:
        print(f"[INFO] {written} reference sources copied into {WORKSPACE_LIBRARY_DIRNAME}/")

    return written


def file_sha256(path):
    digest = hashlib.sha256()

    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            digest.update(block)

    return digest.hexdigest()


def blank_hole_bodies(text):
    """
    The source with everything between its hole markers removed.
    Blanking those regions compares everything that is not its answer.
    """
    kept = []
    inside = False

    for line in text.split("\n"):
        if "RUC_HOLE_BEGIN" in line:
            inside = True
        elif "RUC_HOLE_END" in line:
            inside = False
        elif inside:
            continue

        kept.append(line)

    return "\n".join(kept)


def normalized_sha256(text):
    """ 
    sha256 of `text` with exactly one trailing newline, which is what awk emits 
    """
    return hashlib.sha256((text.rstrip("\n") + "\n").encode("utf-8")).hexdigest()


def workspace_digests(workspace, sources):
    """
    What every file of the emitted workspace hashes to, so a stray edit can be named later.
    """
    source_set = set(sources)
    source_sha = []
    other_sha = []

    for root, _, names in os.walk(workspace):
        for name in sorted(names):
            path = os.path.join(root, name)
            relative = os.path.relpath(path, workspace)

            if relative in source_set:
                with open(path, encoding="utf-8") as f:
                    text = blank_hole_bodies(f.read())

                source_sha.append(f"{normalized_sha256(text)} {relative}")
            else:
                other_sha.append(f"{file_sha256(path)} {relative}")

    return sorted(source_sha), sorted(other_sha)


def write_workspace(task_dir, project_dir, manifest, holes, hdl, extra_files):
    """
    Copy the project into the task environment, with the holes punched out.

    The manifest sources are rewritten, since they are what carries the holes. Everything
    else the project ships is copied byte for byte.

    Extra files (non HDL) are copied rather than read as text, because a project may ship
    something that is not UTF-8 -- a PDF spec, a memory image, a waveform.
    """
    # Straight into `environment/`, not a subdirectory of it: Harbor uploads the contents
    # of that folder into the container's workdir, so the project root ends up being the
    # directory the agent already starts in.
    workspace = os.path.join(task_dir, "environment")

    holes_by_file = {}
    for hole in holes:
        holes_by_file.setdefault(hole["file"], []).append(hole)

    for source in manifest["sources"]:
        with open(os.path.join(project_dir, source), encoding="utf-8") as f:
            text = f.read()

        if source in holes_by_file:
            text = punch_holes(text, holes_by_file[source], hdl)

        destination = os.path.join(workspace, source)
        os.makedirs(os.path.dirname(destination), exist_ok=True)

        with open(destination, "w", encoding="utf-8") as f:
            f.write(text)

    for extra in extra_files:
        destination = os.path.join(workspace, extra)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(os.path.join(project_dir, extra), destination)

    return workspace


def write_solution(task_dir, project_dir, manifest, holes, bundle_id, project,
                   library_sources, library_headers, include_dirs):
    """ Golden sources plus the hole descriptions, held back from the harness """
    solution = os.path.join(task_dir, "solution")

    for source in manifest["sources"]:
        destination = os.path.join(solution, "golden", source)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(os.path.join(project_dir, source), destination)

    os.makedirs(solution, exist_ok=True)
    with open(os.path.join(solution, "holes.json"), "w") as f:
        json.dump(
            {
                "project": project,
                "bundle": bundle_id,
                "top_module": manifest["top_module"],
                "hdl": manifest["hdl"],
                "sources": manifest["sources"],
                # Where the library landed in the workspace, so `refresh_tasks` can rebuild
                # the lint command and the task readme without the dataset at hand.
                "reference_workspace_paths": [
                    workspace_path for _, workspace_path in library_sources
                ],
                "reference_header_workspace_paths": [
                    workspace_path for _, workspace_path in library_headers
                ],
                "include_dirs": list(include_dirs),
                "holes": holes,
            },
            f,
            indent=4,
        )


def write_instruction(task_dir, hdl):
    """ instruction.md, which is what Harbor gives the agent as its prompt """
    begin, end = HOLE_MARKERS[hdl]

    instruction = AGENT_INSTRUCTION.format(
        hdl_name=HDL_NAMES.get(hdl, hdl),
        begin_marker=begin.format(id="<hole id>"),
        end_marker=end.format(id="<hole id>"),
        begin_example=begin.format(id="h1"),
        end_example=end.format(id="h1"),
        example_body=HOLE_EXAMPLE_BODY[hdl],
        library_dir=WORKSPACE_LIBRARY_DIRNAME,
    )

    with open(os.path.join(task_dir, "instruction.md"), "w") as f:
        f.write(instruction.strip() + "\n")


def write_task_readme(workspace, project, bundle_id, manifest, holes, hdl, extra_files,
                      library_sources):
    begin, end = HOLE_MARKERS[hdl]

    # hole_rows = "\n".join(
    #     f"| {h['id']} | {h['file']} |" for h in holes
    # )
    source_list = "\n".join(f"- `{source}`" for source in manifest["sources"])

    if extra_files:
        reference_list = "\n".join(f"- `{extra}`" for extra in extra_files)
    else:
        reference_list = "_This project ships no files besides its sources._"

    if library_sources:
        # Listing 90 paths would bury the sources above it, so the library is described by
        # where it is rather than enumerated
        groups = sorted({
            workspace_path.split(os.sep)[1]
            for _, workspace_path in library_sources
            if len(workspace_path.split(os.sep)) > 1
        })
        library_list = (
            f"{len(library_sources)} files, in: "
            + ", ".join(f"`{WORKSPACE_LIBRARY_DIRNAME}/{group}/`" for group in groups)
        )
    else:
        library_list = "_This design needs no shared library._"

    readme = TASK_README_TEMPLATE.format(
        bundle_id=bundle_id,
        project=project,
        hdl_name=HDL_NAMES.get(hdl, hdl),
        hole_count=len(holes),
        file_count=len({h["file"] for h in holes}),
        top_module=manifest["top_module"],
        source_list=source_list,
        library_dir=WORKSPACE_LIBRARY_DIRNAME,
        library_list=library_list,
        reference_list=reference_list,
        # hole_rows=hole_rows,
        begin_example=begin.format(id="h1"),
        end_example=end.format(id="h1"),
    )

    with open(os.path.join(workspace, "RUC_TASK.md"), "w") as f:
        f.write(readme)


def quote_all(values):
    """ A bash array body, so a path with a space cannot split into two arguments """
    return " ".join(json.dumps(str(value)) for value in values)


def write_tests(task_dir, task_name, holes, manifest, hdl, library_sources, include_dirs):
    """
    The in-container checks.

    The workspace is fingerprinted as emitted rather than re-derived in the container:
    `tests/` is uploaded after the agent phase has ended, so the digests cannot be read or
    edited from inside the container while the agent is running.
    """
    tests_dir = os.path.join(task_dir, "tests")
    os.makedirs(tests_dir, exist_ok=True)

    source_sha, other_sha = workspace_digests(
        os.path.join(task_dir, "environment"), manifest["sources"]
    )

    content = TEST_SCRIPT_TEMPLATE.format(
        workspace_dir=WORKSPACE_DIR,
        verifier_dir=VERIFIER_DIR,
        export_subdir=EXPORT_SUBDIR,
        task_name=task_name,
        hole_ids=quote_all(sorted(h["id"] for h in holes)),
        hole_files=quote_all(sorted({h["file"] for h in holes})),
        sources=quote_all(manifest["sources"]),
        references=quote_all(workspace_path for _, workspace_path in library_sources),
        include_dirs=quote_all(include_dirs),
        source_sha=quote_all(source_sha),
        other_sha=quote_all(other_sha),
        stray_report=STRAY_REPORT_NAME,
        lint=quote_all(LINT_COMMANDS[hdl]),
    )

    test_path = os.path.join(tests_dir, "test.sh")

    with open(test_path, "w") as f:
        f.write(content)

    os.chmod(test_path, 0o755)


def write_task_config(task_dir, project, bundle_id, holes, manifest, hdl, agentic):
    """
    task.toml, which is the only place the image can be named.
    """
    agent_timeout_s = int(agentic.get("agent_timeout_s", 1800))

    base_image = agentic.get("base_container") or agentic.get("docker_image")

    if not base_image:
        raise SystemExit(
            "[ERROR] No base container configured: set agentic.base_container to the .sif "
            "(or a docker image) every task boots from. Harbor cannot run a task without it."
        )

    task_toml = TASK_TOML_TEMPLATE.format(
        tags=", ".join(json.dumps(rule) for rule in sorted({h["rule"] for h in holes})),
        project=project,
        bundle=bundle_id,
        top_module=manifest["top_module"],
        hdl=hdl,
        hole_count=len(holes),
        file_count=len({h["file"] for h in holes}),
        agent_timeout_s=agent_timeout_s,
        # The verifier only lints and copies, so it needs a small fraction of the budget
        verifier_timeout_s=int(agentic.get("verifier_timeout_s", 600)),
        base_image=base_image,
        workdir=WORKSPACE_DIR,
    )

    with open(os.path.join(task_dir, "task.toml"), "w") as f:
        f.write(task_toml)


def emit_tasks(dataset_path, output_path, hdl, agentic, debug=False):
    """ Write one task folder per bundle of the dataset """
    tasks_root = output_path

    # Every folder here is run as a task, so none may survive from a previous build: a
    # project that got no bundle this time would otherwise still be run with its old one
    shutil.rmtree(tasks_root, ignore_errors=True)
    os.makedirs(tasks_root, exist_ok=True)

    emitted = 0

    for project in sorted(os.listdir(dataset_path)):
        project_dir = os.path.join(dataset_path, project)

        if not os.path.isdir(project_dir):
            continue

        if not is_project_dir(project):
            continue

        manifest = read_manifest(project_dir)
        bundles = read_bundles(project_dir)

        if manifest is None or bundles is None:
            continue

        # The same for every bundle of the project, so it is resolved once
        extra_files = find_extra_files(project_dir, manifest["sources"])
        library_sources, library_headers, library_include_dirs = library_layout(
            project_dir, manifest
        )

        warn_if_payload_large(project_dir, extra_files, len(bundles), library_sources)

        if debug and extra_files:
            print(f"[INFO] {project}: {len(extra_files)} reference files besides the sources")

        for bundle_id, holes in sorted(bundles.items()):
            task_name = bundle_task_name(project, bundle_id)
            task_dir = os.path.join(tasks_root, task_name)

            os.makedirs(task_dir, exist_ok=True)

            workspace = write_workspace(
                task_dir, project_dir, manifest, holes, hdl, extra_files
            )
            write_library(workspace, library_sources + library_headers, debug)
            write_task_readme(
                workspace, project, bundle_id, manifest, holes, hdl, extra_files,
                library_sources + library_headers
            )
            write_instruction(task_dir, hdl)
            write_solution(
                task_dir, project_dir, manifest, holes, bundle_id, project,
                library_sources, library_headers, library_include_dirs
            )
            write_tests(
                task_dir, task_name, holes, manifest, hdl, library_sources,
                library_include_dirs
            )
            write_task_config(
                task_dir, project, bundle_id, holes, manifest, hdl, agentic
            )

            if debug:
                print(f"[INFO] Emitted {task_dir} with {len(holes)} holes")

            emitted += 1

    print(f"[INFO] Harbor tasks emitted: {emitted}")

    return emitted
