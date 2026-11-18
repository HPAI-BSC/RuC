import argparse
import json
import os
import re
import shutil

from evaluation.equivalence_scripts import SYNTHESIS_DEFINES
from .project_manifest import (
    compile_sources, dataset_root, manifest_include_dirs, manifest_sources, read_manifest
)


"""
Turn an emitted agentic benchmark into a rule-completion dataset, so the non-agentic flow
(`--inference`, `--evaluate`, `--evaluate --self`) runs on the same holes, unchanged.

    python3 -m benchmark.export_rule_completion <tasks_path> --dataset_path <dataset> --output_path <export>

"""


# Comments are blanked before looking for directives, strings are kept whole so the name
# of an include survives
LEXEMES = re.compile(r'"(?:\\.|[^"\\\n])*"|//[^\n]*|/\*.*?\*/', re.S)
DIRECTIVES = re.compile(r'`(ifdef|ifndef|endif|include)\b(?:\s*"([^"]+)")?')
GUARD = re.compile(r'\s*`ifndef\s+(\w+)\s+`define\s+(\w+)')
MARKER = re.compile("\0([SE])(\\d+)\0")


def blank_comments(text):
    """ `text` with every comment turned into spaces, so every offset stays where it was """
    def blank(match):
        lexeme = match.group(0)
        return lexeme if lexeme.startswith('"') else re.sub(r"[^\n]", " ", lexeme)

    return LEXEMES.sub(blank, text)


def is_guarded(text):
    guard = GUARD.match(blank_comments(text))
    return guard is not None and guard.group(1) == guard.group(2)


def resolve_include(name, directory, include_dirs):
    """ The file an include names, looked for beside the including file first, like slang """
    for folder in [directory] + include_dirs:
        path = os.path.join(folder, name)

        if os.path.isfile(path):
            return os.path.abspath(path)

    raise FileNotFoundError(f'`include "{name}" not found from {directory}')


def expand_includes(text, directory, include_dirs, pasted, depth=0):
    """
    `text` with every `` `include `` replaced by the file it names, recursively.

    A guarded header already read outside of any `` `ifdef `` would expand to nothing, so
    it is left out instead of pasted again: `prim_assert.sv` alone is included by dozens
    of sources. Everything else is pasted wherever it is included, since a header like
    `prim_util_memload.svh` sits inside a module body and every such module needs it.
    """
    out = []
    last = 0

    for match in DIRECTIVES.finditer(blank_comments(text)):
        directive, name = match.groups()

        if directive in ("ifdef", "ifndef"):
            depth += 1
            continue

        if directive == "endif":
            depth -= 1
            continue

        if name is None:
            continue

        path = resolve_include(name, directory, include_dirs)

        with open(path, encoding="utf-8") as f:
            header = f.read()

        guarded = is_guarded(header)

        if guarded and path in pasted:
            replacement = f'// `include "{name}" is pasted above'
        else:
            if guarded and depth == 0:
                pasted.add(path)

            body = expand_includes(header, os.path.dirname(path), include_dirs, pasted, depth)
            replacement = (
                f'// ===== Begin of `include "{name}" =====\n'
                f"{body}\n"
                f'// ===== End of `include "{name}" ====='
            )

        out.append(text[last:match.start()])
        out.append(replacement)
        last = match.end()

    out.append(text[last:])
    return "".join(out)


def mark_holes(text, holes):
    """ `text` with a marker at both ends of each hole, from the end so the spans stay valid """
    cuts = [(hole["span"][0], f"\0S{number}\0") for number, hole in holes]
    cuts += [(hole["span"][1], f"\0E{number}\0") for number, hole in holes]

    for position, marker in sorted(cuts, key=lambda cut: cut[0], reverse=True):
        text = text[:position] + marker + text[position:]

    return text


def take_markers(text):
    """ `text` without its markers, and where each marker was in what is left """
    positions = {}
    removed = 0

    for match in MARKER.finditer(text):
        key = match.group(1) + match.group(2)

        if key in positions:
            raise SystemExit(f"[ERROR] A hole was pasted twice ({key}), its position is ambiguous")

        positions[key] = match.start() - removed
        removed += len(match.group(0))

    return MARKER.sub("", text), positions


def flatten_project(project_dir, manifest, holes):
    """
    The single file of a project, and the span of each hole inside it.

    Every hole is marked in its own source before anything moves, so wherever the includes
    push its code, the markers say where it ended up.
    """
    root = dataset_root(project_dir)
    include_dirs = manifest_include_dirs(project_dir, manifest)

    holes_by_path = {}
    for number, hole in enumerate(holes):
        path = os.path.abspath(os.path.join(project_dir, hole["file"]))
        holes_by_path.setdefault(path, []).append((number, hole))

    parts = [f"`define {define}\n" for define in SYNTHESIS_DEFINES]
    pasted = set()

    for path in compile_sources(project_dir, manifest, manifest_sources(project_dir, manifest)):
        path = os.path.abspath(path)

        with open(path, encoding="utf-8") as f:
            text = mark_holes(f.read(), holes_by_path.get(path, []))

        parts.append(f"\n// ===== File: {os.path.relpath(path, root)} =====\n")
        parts.append(expand_includes(text, os.path.dirname(path), include_dirs, pasted))

    flat, positions = take_markers("".join(parts))

    if len(positions) != 2 * len(holes):
        raise SystemExit(f"[ERROR] {project_dir}: a hole is in a file the design does not compile")

    return flat, [[positions[f"S{n}"], positions[f"E{n}"]] for n in range(len(holes))]


def read_tasks(tasks_path):
    """ The holes of every emitted task by project, each with the code it removed """
    projects = {}

    for task_name in sorted(os.listdir(tasks_path)):
        solution_dir = os.path.join(tasks_path, task_name, "solution")

        if not os.path.exists(os.path.join(solution_dir, "holes.json")):
            continue

        with open(os.path.join(solution_dir, "holes.json")) as f:
            solution = json.load(f)

        for hole in solution["holes"]:
            with open(os.path.join(solution_dir, "golden", hole["file"]), encoding="utf-8") as f:
                code = f.read()[hole["span"][0]:hole["span"][1]]

            projects.setdefault(solution["project"], []).append(
                dict(hole, task=task_name, code=code)
            )

    return projects


def export_project(project_dir, holes, out_dir):
    manifest = read_manifest(project_dir)
    flat, spans = flatten_project(project_dir, manifest, holes)

    mask_idx = {}
    task_map = {}

    for hole, (start, stop) in zip(holes, spans):
        # Also catches a dataset that changed since the tasks were emitted
        if flat[start:stop] != hole["code"]:
            raise SystemExit(
                f"[ERROR] {hole['task']}/{hole['id']}: the code at its new position is not "
                f"the code the task removed"
            )

        mask_idx.setdefault(hole["rule"], []).append([start, stop])
        task_map.setdefault(hole["rule"], []).append(f"{hole['task']}/{hole['id']}")

    # The flow compiles whichever source it finds first, so nothing else may be left there
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)

    with open(os.path.join(out_dir, f"{manifest['top_module']}.{manifest['hdl']}"), "w", encoding="utf-8") as f:
        f.write(flat)

    with open(os.path.join(out_dir, "mask_idx.json"), "w") as f:
        json.dump(mask_idx, f, indent=4)

    with open(os.path.join(out_dir, "task_map.json"), "w") as f:
        json.dump(task_map, f, indent=4)

    return len(flat)


def export(tasks_path, dataset_path, output_path):
    projects = read_tasks(tasks_path)

    if not projects:
        print(f"[WARNING] No emitted task found in {tasks_path}")

    for project, holes in sorted(projects.items()):
        size = export_project(
            os.path.join(dataset_path, project), holes, os.path.join(output_path, project)
        )

        print(f"[INFO] {project}: {len(holes)} holes, {size / 1024:.0f} KB")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Turn an emitted agentic benchmark into a rule-completion dataset"
    )
    parser.add_argument("tasks_path", help="Folder of emitted tasks")
    parser.add_argument("--dataset_path", required=True, help="Dataset the tasks were emitted from")
    parser.add_argument("--output_path", required=True, help="Folder to write the rule-completion dataset into")

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    export(args.tasks_path, args.dataset_path, args.output_path)
