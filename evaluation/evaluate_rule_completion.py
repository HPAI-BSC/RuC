import os
from pathlib import Path
from collections import defaultdict
import asyncio
from concurrent.futures import ThreadPoolExecutor


from .equivalence_checker import verify_equivalence, verify_syntax
from .parser import parse_args_eval
from configs.rules import RULES
from .utils import read_path, save_generation_eqy_results, find_hdl_files, RESULTS_FILENAME, SUMMARY_FILENAME

# Concurrent STX/EQV checks; the default executor would cap them at 32
MAX_WORKERS = 76


"""
Evaluate the generated code
"""


async def evaluate_rule_completion_async(generation_path: Path, reference_path: Path, dataset_project_path: Path, args):
    loop = asyncio.get_event_loop()
    stx_result = await loop.run_in_executor(
        None, verify_syntax, generation_path, args, args.debug
    )

    if not stx_result["syntax_valid"]:
        return {
            "stx_passed": False,
            "equiv_passed": False,
            "equiv_method": None,
        }

    eqy_result= await loop.run_in_executor(
        None, verify_equivalence, generation_path, reference_path, dataset_project_path, args, args.debug
    )

    return {
        "stx_passed": True,
        "equiv_passed": eqy_result["equiv_passed"],
        # proven, failed, timeout or error, so the passes that are timeouts stay visible
        "equiv_method": eqy_result["equiv_method"],
    }



def process_evaluation_results(args):
    async def _run_all_eqy():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=MAX_WORKERS))

        flat_tasks = []
        task_meta = []
        generation_paths = []

        # Track number of tasks per rule
        total_tasks_per_rule = defaultdict(int)

        for rule in os.listdir(args.output_path):
            if rule not in RULES:
                continue

            rule_path = os.path.join(args.output_path, rule)

            for project_name in os.listdir(rule_path):

                project, task = read_path(project_name)

                if args.debug:
                    print(f"[INFO] Project: {project}, Task: {task}")

                dataset_project_path = os.path.join(args.dataset_path, project)

                modules = find_hdl_files(os.listdir(dataset_project_path), args.hdl)
                if not modules:
                    if args.debug: print(f"[WARNING] No HDL files found in {dataset_project_path} for HDL {args.hdl}")
                    continue

                if args.debug and len(modules) > 1:
                        print(f"[WARNING] Multiple HDL files found in {dataset_project_path} for HDL {args.hdl}. Using the first one: {modules[0]}")

                reference_path = os.path.join(dataset_project_path, modules[0])

                task_path = os.path.join(rule_path, project_name)

                total_tasks_per_rule[rule] += 1

                for generation in os.listdir(task_path):
                    pattern = generation.split("_", 1)[0]
                    if args.debug:
                            print(f"[INFO] generation: {generation}, pattern: {pattern}")
                    if pattern != "full":
                         continue

                    generation_path = os.path.join(task_path, generation)


                    flat_tasks.append(
                        evaluate_rule_completion_async(
                            Path(generation_path),
                            Path(reference_path),
                            Path(dataset_project_path),
                            args,
                        )
                    )

                    task_meta.append({
                        "rule": rule,
                        "task": task,
                    })

                    generation_paths.append(generation_path)

        if args.debug:
                print(f"[INFO] Running {len(flat_tasks)} evaluations...")
        results = await asyncio.gather(*flat_tasks)

        # Aggregate results per rule
        eqy_passes = defaultdict(int)
        stx_passes = defaultdict(int)

        # Store generation paths and equivalence results
        generation_results = {}

        for meta, gen_path, result in zip(task_meta, generation_paths, results):
            stx_passed = result["stx_passed"]
            equiv_passed = result["equiv_passed"]

            generation_results[gen_path] = {
                "eqy_passed": equiv_passed,
                "stx_passed": stx_passed,
                "equiv_method": result["equiv_method"],
                "rule": meta["rule"],
                "task": meta["task"],
            }

            if stx_passed:
                stx_passes[meta["rule"]] += 1
            if equiv_passed:
                eqy_passes[meta["rule"]] += 1
            

        # Compute pass rates
        eqy_results = {}
        stx_results = {}
        for rule in RULES:
            total = total_tasks_per_rule.get(rule, 0)
            if total > 0:
                stx_results[rule] = (stx_passes[rule] / total) * 100
                eqy_results[rule] = (eqy_passes[rule] / total) * 100
            else:
                eqy_results[rule] = 0.0
                stx_results[rule] = 0.0

        return stx_results, eqy_results, generation_results

    stx_results, eqy_results, generation_results = asyncio.run(_run_all_eqy())

    print(f"[INFO] Results:")
    for rule in RULES:
        print(f"Rule: {rule}")
        print(f"  Syntax Check Pass Rate: {stx_results[rule]:.2f}%")
        print(f"  Equivalence Check Pass Rate: {eqy_results[rule]:.2f}%")
    

    path_save = os.path.join(args.output_path, RESULTS_FILENAME)
    if args.debug: print(f"[INFO] Saving generation equivalence results to {path_save}")
    save_generation_eqy_results(generation_results, path_save)

    path_save_summary = os.path.join(args.output_path, SUMMARY_FILENAME)
    if args.debug: print(f"[INFO] Saving summary results to {path_save_summary}")
    save_generation_eqy_results({
        "syntax_check": stx_results,
        "equivalence_check": eqy_results,
        "timeout_is_pass": getattr(args, "timeout_is_pass", True),
    }, path_save_summary)


if __name__ == "__main__":
    args = parse_args_eval()
    print("[INFO] Args parsed:")
    for arg, value in vars(args).items():
        print(f"[INFO]   {arg}: {value}")
    process_evaluation_results(args)