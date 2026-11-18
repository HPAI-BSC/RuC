import os
import copy
import asyncio
from pathlib import Path

from .equivalence_checker import verify_equivalence, verify_syntax
from .equivalence_scripts import SYNTHESIS_DEFINES
from .parser import parse_args_agentic_self
from .utils import save_generation_eqy_results, RESULTS_FILENAME, SUMMARY_FILENAME
from benchmark.project_manifest import (
    compile_sources, is_project_dir, manifest_include_dirs, manifest_sources, read_manifest
)


"""
Check that a multi-file project can be evaluated at all, before it becomes a benchmark.

Report only: nothing is skipped, dropped or rewritten. Fix or remove the projects it
names before building the benchmark.
"""


def project_result(project, manifest):
    """ The per-project record, filled in as far as the checks get """
    return {
        "project": project,
        "top_module": manifest["top_module"],
        "hdl": manifest.get("hdl"),
        "sources": manifest["sources"],
        "stage": "manifest",
        "stx_passed": False,
        "eqy_passed": False,
        "equiv_method": "",
        "eqy_return_code": None,
        "cells_coverage": None,
        "yosys_time": None,
        "error_message": "",
    }


async def evaluate_project_self_async(project, project_dir, manifest, args, semaphore):
    """ Syntax and self-equivalence of one project, reported rather than raised """
    loop = asyncio.get_event_loop()

    result = project_result(project, manifest)

    if manifest.get("hdl") and manifest["hdl"] != args.hdl:
        result["error_message"] = (
            f"Manifest says hdl '{manifest['hdl']}' but the run is checking '{args.hdl}'"
        )
        return result

    sources = compile_sources(project_dir, manifest, manifest_sources(project_dir, manifest))

    missing = [source for source in sources if not os.path.exists(source)]

    if missing:
        result["error_message"] = (
            f"{len(missing)} manifest source(s) do not exist, first: {missing[0]}"
        )
        return result

    # Yosys writes its raw log next to the report, never into the dataset: a stray file
    # in a project folder is not a RuC artefact by name, so `find_extra_files` would copy
    # it into every workspace emitted from that project
    project_output_path = os.path.join(args.output_path, project)
    os.makedirs(project_output_path, exist_ok=True)

    local_args = copy.copy(args)
    local_args.output_path = project_output_path
    local_args.task_path = project_output_path
    local_args.top_module = manifest["top_module"]
    local_args.include_dirs = manifest_include_dirs(project_dir, manifest)
    # Checked as synthesizable, exactly as step 8 will check a generation of this project
    local_args.defines = SYNTHESIS_DEFINES

    async with semaphore:
        result["stage"] = "syntax"

        stx_result = await loop.run_in_executor(
            None, verify_syntax, sources, local_args, args.debug
        )

        if not stx_result["syntax_valid"]:
            result["error_message"] = stx_result["error_message"]
            return result

        result["stx_passed"] = True
        result["stage"] = "equivalence"

        eqy_result = await loop.run_in_executor(
            None, verify_equivalence, sources, sources,
            Path(project_dir), local_args, args.debug
        )

    result["eqy_passed"] = eqy_result["equiv_passed"]
    result["equiv_method"] = eqy_result["equiv_method"]
    result["eqy_return_code"] = eqy_result["eqy_return_code"]
    result["cells_coverage"] = eqy_result["cells_coverage"]
    result["yosys_time"] = eqy_result["yosys_time"]
    result["error_message"] = eqy_result["error_message"]

    return result


def unreadable_result(project, message):
    """ A project whose manifest could not even be read, shaped like every other record """
    result = project_result(project, {"top_module": None, "hdl": None, "sources": []})
    result["error_message"] = message

    return result


def collect_projects(args):
    """
    Split the dataset into what can be checked, what has no manifest, and what has one
    that cannot be read.

    A manifest is hand-editable by design, so a truncated or invalid one is a normal
    finding of this check rather than a reason to abort the whole run.
    """
    projects = []
    skipped = []
    unreadable = []

    for project in sorted(os.listdir(args.dataset_path)):
        project_dir = os.path.join(args.dataset_path, project)

        # `_lib` and anything else underscored sits next to the projects without being one
        if not os.path.isdir(project_dir) or not is_project_dir(project):
            continue

        try:
            manifest = read_manifest(project_dir)
        except Exception as e:
            unreadable.append(unreadable_result(project, f"Cannot read the manifest: {e}"))
            continue

        # Same rule as every other agentic step: no manifest, no project. Reported here
        # rather than passed over, since a project silently missing from the benchmark is
        # the thing this check is meant to surface.
        if manifest is None:
            skipped.append(project)
            continue

        if not manifest.get("sources"):
            unreadable.append(unreadable_result(project, "The manifest lists no sources"))
            continue

        if not manifest.get("top_module"):
            unreadable.append(unreadable_result(project, "The manifest names no top module"))
            continue

        projects.append((project, project_dir, manifest))

    return projects, skipped, unreadable


def process_self_references(args):
    """ Run every project of the dataset and aggregate what can be evaluated """

    if not args.dataset_path or not os.path.isdir(args.dataset_path):
        print(f"[ERROR] --dataset_path is not a directory: {args.dataset_path}")
        return

    async def _run_all():
        projects, skipped, unreadable = collect_projects(args)

        # One Yosys proof of a whole source tree is far heavier than one of a single
        # file, so the fan-out is bounded here rather than left to the default executor
        semaphore = asyncio.Semaphore(max(1, args.max_parallel))

        if args.debug:
            print(f"[INFO] Running {len(projects)} project self-checks, {args.max_parallel} at a time...")

        results = await asyncio.gather(
            *(
                evaluate_project_self_async(project, project_dir, manifest, args, semaphore)
                for project, project_dir, manifest in projects
            )
        )

        # A project whose manifest could not be read is still a project that cannot be
        # evaluated, so it is counted rather than left out of the rates
        return sorted(unreadable + list(results), key=lambda r: r["project"]), skipped

    results, skipped = asyncio.run(_run_all())

    total = len(results)
    stx_passed = sum(r["stx_passed"] for r in results)
    eqy_passed = sum(r["eqy_passed"] for r in results)

    # How a project failed says what to do about it: `timeout` is a budget problem,
    # `error` a toolchain or top module problem, `failed` a genuinely non-deterministic
    # design that no equivalence proof will ever accept
    outcomes = {}
    for r in results:
        if r["stage"] != "equivalence":
            bucket = r["stage"]
        else:
            bucket = r["equiv_method"] or "error"

        outcomes[bucket] = outcomes.get(bucket, 0) + 1

    print("\n[INFO] Agentic Self Results:")
    print(f"[INFO] Total projects: {total}")

    if skipped:
        print(f"[WARNING] {len(skipped)} project(s) without a manifest, not checked: {skipped}")

    if total:
        print(f"[INFO] STX Passed: {stx_passed}/{total} ({(stx_passed / total) * 100:.2f}%)")
        print(f"[INFO] EQV Passed: {eqy_passed}/{total} ({(eqy_passed / total) * 100:.2f}%)")
        print(f"[INFO] Outcomes: {outcomes}")
    else:
        print("[WARNING] No project with a manifest found, nothing was checked")

    timeouts = [r for r in results if r["equiv_method"] == "timeout"]

    if timeouts:
        counted = "as evaluable" if args.timeout_is_pass else "as unevaluable"

        print(
            f"\n[WARNING] {len(timeouts)} project(s) timed out after 900s and are counted "
            f"{counted} (--timeout_is_pass {args.timeout_is_pass}). A proof that does not "
            f"finish for the golden design against itself will not finish for a generation "
            f"either:"
        )

        for r in timeouts:
            print(f"[WARNING]   {r['project']} (top {r['top_module']}, {len(r['sources'])} source(s))")


    failures = [r for r in results if not r["eqy_passed"]]

    if failures:
        print("\n[WARNING] Projects that cannot be evaluated:")
        for r in failures:
            if r["stage"] == "manifest":
                stage = "MANIFEST"
            elif r["stage"] == "syntax":
                stage = "STX"
            else:
                stage = f"EQV/{r['equiv_method'] or 'error'}"

            print(f"[WARNING]   {r['project']} [{stage}] {r['error_message']}")

    generation_results = {r["project"]: r for r in results}

    os.makedirs(args.output_path, exist_ok=True)

    path_save = os.path.join(args.output_path, RESULTS_FILENAME)
    if args.debug: print(f"\n[INFO] Saving per-project results to {path_save}")
    save_generation_eqy_results(generation_results, path_save)

    path_save_summary = os.path.join(args.output_path, SUMMARY_FILENAME)
    if args.debug: print(f"[INFO] Saving summary results to {path_save_summary}")
    save_generation_eqy_results({
        "total_projects": total,
        "syntax_check": (stx_passed / total) * 100 if total else 0.0,
        "equivalence_check": (eqy_passed / total) * 100 if total else 0.0,
        "outcomes": outcomes,
        "unevaluable_projects": [r["project"] for r in failures],
        "timed_out_projects": [r["project"] for r in timeouts],
        "timeout_is_pass": args.timeout_is_pass,
        "projects_without_manifest": skipped,
    }, path_save_summary)


if __name__ == "__main__":
    args = parse_args_agentic_self()
    print("[INFO] Args parsed:")
    for arg, value in vars(args).items():
        print(f"[INFO]   {arg}: {value}")
    process_self_references(args)
