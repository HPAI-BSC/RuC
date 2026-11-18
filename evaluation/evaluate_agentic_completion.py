import os
import json
from pathlib import Path
from collections import defaultdict
import asyncio
import copy

from .equivalence_checker import verify_equivalence, verify_syntax
from .equivalence_scripts import SYNTHESIS_DEFINES
from .parser import parse_args_agentic_eval
from configs.rules import RULES
from .utils import save_generation_eqy_results, RESULTS_FILENAME, SUMMARY_FILENAME
from benchmark.project_manifest import (
    compile_sources, manifest_include_dirs, manifest_sources, read_manifest
)


"""
Evaluate the code an agent wrote for a multi-file project.

An agent fills several holes spread over several sources, so each hole is checked on 
its own.

Every folder holding a `hole.json` is one such check. That makes this walk work both for
the empty outputs of the functional validation step and for real agent generations,
whatever folder layout produced them.
"""


def find_hole_dirs(output_path):
    """ Every folder describing one hole, wherever it sits in the output tree """
    for subdir, _, files in os.walk(output_path):
        if "hole.json" in files:
            yield subdir


def find_generation(hole_dir):
    """ The rebuilt source of a hole, following the `full_gen` naming of the flow """
    for name in sorted(os.listdir(hole_dir)):
        if name.startswith("full_gen"):
            return os.path.join(hole_dir, name)

    return None


def build_source_list(project_dir, manifest, hole_file, generation_path):
    """
    The sources of the design with only the generated file swapped in.

    Returns None when the hole names a source the manifest does not list, which means
    the manifest and the benchmark have drifted apart.
    """
    if hole_file not in manifest["sources"]:
        return None

    return [
        generation_path if source == hole_file else os.path.join(project_dir, source)
        for source in manifest["sources"]
    ]


async def evaluate_hole_async(hole_dir, args):
    """ Syntax and equivalence of a single hole, isolated from the other holes """
    loop = asyncio.get_event_loop()

    with open(os.path.join(hole_dir, "hole.json")) as f:
        hole = json.load(f)

    result = {
        "rule": hole.get("rule"),
        "project": hole.get("project"),
        "file": hole.get("file"),
        "index": hole.get("index"),
        "bundle": hole.get("bundle"),
        "hole_id": hole.get("hole_id"),
        "markers_intact": hole.get("markers_intact", True),
        "stx_passed": False,
        "eqy_passed": False,
        "error_message": "",
    }

    generation_path = find_generation(hole_dir)

    if generation_path is None:
        result["error_message"] = f"No full_gen file in {hole_dir}"
        return result

    project_dir = os.path.join(args.dataset_path, hole["project"])
    manifest = read_manifest(project_dir)

    if manifest is None:
        result["error_message"] = f"No project manifest in {project_dir}"
        return result

    generated_sources = build_source_list(
        project_dir, manifest, hole["file"], generation_path
    )

    if generated_sources is None:
        result["error_message"] = f"{hole['file']} is not listed in the manifest of {hole['project']}"
        return result

    # The dependency closure goes in front of both lists
    generated_sources = compile_sources(project_dir, manifest, generated_sources)
    reference_sources = compile_sources(
        project_dir, manifest, manifest_sources(project_dir, manifest)
    )

    # Each hole reports into its own folder and against its own top module
    local_args = copy.copy(args)
    local_args.top_module = manifest["top_module"]
    local_args.task_path = hole_dir
    local_args.include_dirs = manifest_include_dirs(project_dir, manifest)
    # A design built on a shared library is read as synthesizable on both sides. 
    local_args.defines = SYNTHESIS_DEFINES

    stx_result = await loop.run_in_executor(
        None, verify_syntax, generated_sources, local_args, args.debug
    )

    if not stx_result["syntax_valid"]:
        result["error_message"] = stx_result["error_message"]
        return result

    result["stx_passed"] = True

    eqy_result = await loop.run_in_executor(
        None, verify_equivalence, generated_sources, reference_sources,
        Path(project_dir), local_args, args.debug
    )

    result["eqy_passed"] = eqy_result["equiv_passed"]
    result["equiv_method"] = eqy_result["equiv_method"]
    result["error_message"] = eqy_result["error_message"]

    return result


def process_agentic_evaluation_results(args):
    """ Run every hole under `output_path` and aggregate per rule and per bundle """

    async def _run_all_eqy():
        hole_dirs = sorted(find_hole_dirs(args.output_path))

        if args.debug:
            print(f"[INFO] Running {len(hole_dirs)} hole evaluations...")

        results = await asyncio.gather(
            *(evaluate_hole_async(hole_dir, args) for hole_dir in hole_dirs)
        )

        return hole_dirs, results

    hole_dirs, results = asyncio.run(_run_all_eqy())

    eqy_passes = defaultdict(int)
    stx_passes = defaultdict(int)
    total_per_rule = defaultdict(int)

    # A bundle only counts as solved when every one of its holes is correct
    bundle_holes = defaultdict(list)

    generation_results = {}

    for hole_dir, result in zip(hole_dirs, results):
        generation_results[hole_dir] = result

        rule = result["rule"]
        total_per_rule[rule] += 1

        if result["stx_passed"]:
            stx_passes[rule] += 1
        if result["eqy_passed"]:
            eqy_passes[rule] += 1

        if result["bundle"]:
            bundle_holes[result["bundle"]].append(result["eqy_passed"])

    stx_results = {}
    eqy_results = {}

    stx_weighted_sum = 0.0
    eqy_weighted_sum = 0.0
    total_weight = 0

    for rule in RULES:
        total = total_per_rule.get(rule, 0)

        if total > 0:
            stx_results[rule] = (stx_passes[rule] / total) * 100
            stx_weighted_sum += stx_results[rule] * total

            eqy_results[rule] = (eqy_passes[rule] / total) * 100
            eqy_weighted_sum += eqy_results[rule] * total

            total_weight += total
        else:
            stx_results[rule] = 0.0
            eqy_results[rule] = 0.0

    stx_weighted_avg = (stx_weighted_sum / total_weight) if total_weight > 0 else 0.0
    eqy_weighted_avg = (eqy_weighted_sum / total_weight) if total_weight > 0 else 0.0

    solved_bundles = sum(1 for passed in bundle_holes.values() if all(passed))
    bundle_results = {
        "total_bundles": len(bundle_holes),
        "fully_solved_bundles": solved_bundles,
        "fully_solved_rate": (solved_bundles / len(bundle_holes) * 100) if bundle_holes else 0.0,
    }

    print(f"[INFO] Results:")
    for rule in RULES:
        if total_per_rule.get(rule, 0) == 0:
            continue
        print(f"Rule: {rule}")
        print(f"  Syntax Check Pass Rate: {stx_results[rule]:.2f}%")
        print(f"  Equivalence Check Pass Rate: {eqy_results[rule]:.2f}%")

    print(f"Syntax Check Weighted Avg: {stx_weighted_sum}")
    print(f"Equivalence Check Weighted Avg: {eqy_weighted_sum}")

    if bundle_holes:
        print(f"[INFO] Bundles fully solved: {solved_bundles}/{len(bundle_holes)} "
              f"({bundle_results['fully_solved_rate']:.2f}%)")

    path_save = os.path.join(args.output_path, RESULTS_FILENAME)
    if args.debug: print(f"[INFO] Saving generation equivalence results to {path_save}")
    save_generation_eqy_results(generation_results, path_save)

    path_save_summary = os.path.join(args.output_path, SUMMARY_FILENAME)
    if args.debug: print(f"[INFO] Saving summary results to {path_save_summary}")
    save_generation_eqy_results({
        "syntax_check": stx_results,
        "equivalence_check": eqy_results,
        "bundle_check": bundle_results,
    }, path_save_summary)


if __name__ == "__main__":
    args = parse_args_agentic_eval()
    print("[INFO] Args parsed:")
    for arg, value in vars(args).items():
        print(f"[INFO]   {arg}: {value}")
    process_agentic_evaluation_results(args)
