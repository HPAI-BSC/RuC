import os
import json
from pathlib import Path
import asyncio
import copy

from .equivalence_checker import verify_equivalence, verify_syntax
from .parser import parse_args_eval
from .utils import find_hdl_files


async def evaluate_self_equivalence_async(reference_path: Path, dataset_project_path: Path, args, project_name):
    loop = asyncio.get_event_loop()

    # Create per-project output directory
    project_output_path = os.path.join(args.output_path, project_name)
    os.makedirs(project_output_path, exist_ok=True)

    # Create a local copy of args so each task writes to its own folder
    local_args = copy.copy(args)
    local_args.output_path = project_output_path
    local_args.task_path = project_output_path

    stx_result = await loop.run_in_executor(
        None, verify_syntax, reference_path, local_args, args.debug
    )

    if not stx_result["syntax_valid"]:
        return {
        "project": project_name,
        "reference_path": str(reference_path),
        "stx_passed": False,
        "eqy_passed": False
        }

    # Checking EQY of the design against itself
    eqy_result = await loop.run_in_executor(
        None, verify_equivalence, reference_path, reference_path,  dataset_project_path, local_args, args.debug
    )

    return {
        "project": project_name,
        "reference_path": str(reference_path),
        "stx_passed": stx_result["syntax_valid"],
        "eqy_passed": eqy_result["equiv_passed"]
    }


def process_references(args):
    async def _run_all():
        tasks = []

        for project in os.listdir(args.dataset_path):
            dataset_project_path = os.path.join(args.dataset_path, project)

            if not os.path.isdir(dataset_project_path):
                continue

            modules = find_hdl_files(os.listdir(dataset_project_path), args.hdl)
            if not modules:
                if args.debug: print(f"[WARNING] No HDL files in {dataset_project_path}")
                continue

            if args.debug and len(modules) > 1:
                        print(f"[WARNING] Multiple HDL files in {project}, using {modules[0]}")

            reference_path = os.path.join(dataset_project_path, modules[0])

            tasks.append(
                evaluate_self_equivalence_async(
                    Path(reference_path),
                    Path(dataset_project_path),
                    args,
                    project
                )
            )

        results = await asyncio.gather(*tasks)
        return results

    results = asyncio.run(_run_all())

    # Aggregate results
    total = len(results)
    stx_passed = sum(r["stx_passed"] for r in results)
    eqv_passed = sum(r["eqy_passed"] for r in results)

    print("\n[INFO] Self Results:")
    print(f"[INFO] Total designs: {total}")
    print(f"[INFO] STX Passed: {stx_passed}/{total} ({(stx_passed/total)*100:.2f}%)")
    print(f"[INFO] EQV Passed: {eqv_passed}/{total} ({(eqv_passed/total)*100:.2f}%)")
    
    generation_results = {}
    for r in results:
        generation_results[r["reference_path"]] = {
            "stx_passed": r["stx_passed"], 
            "eqy_passed": r["eqy_passed"],
            "project": r["project"]
        }

    # Save JSON
    os.makedirs(args.output_path, exist_ok=True)
    output_json = os.path.join(args.output_path, "generation_eqy_results.json")

    with open(output_json, "w") as f:
        json.dump(generation_results, f, indent=4)

    if args.debug: print(f"\n[INFO] Saved results to {output_json}")

    if args.debug:
        # Print failures
        print("\n[INFO] Failures:")
        for r in results:
            if not r["eqy_passed"]:
                    print(f"[INFO]   {r['project']} ({r['reference_path']})")


if __name__ == "__main__":
    args = parse_args_eval()
    process_references(args)