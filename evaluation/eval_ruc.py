import os
from pathlib import Path
from collections import defaultdict
import asyncio


from evaluation.eval_notsotiny import run_equivalence_check, run_syntax_check
from .parser import parse_args_eval
from configs.rules import RULES
from .utils import read_path, save_generation_eqy_results, find_hdl_files


"""
Evaluate the generated code
"""


async def eval_ruc_async(generation_path: Path, reference_path: Path, dataset_project_path: Path, args):
    loop = asyncio.get_event_loop()
    stx_result = await loop.run_in_executor(
        None, run_syntax_check, generation_path, args, args.debug
    )

    if not stx_result["syntax_valid"]:
        return {
            "stx_passed": False,
            "equiv_passed": False,
        }

    eqy_result= await loop.run_in_executor(
        None, run_equivalence_check, generation_path, reference_path, dataset_project_path, args, args.debug
    )

    return {
        "stx_passed": True,
        "equiv_passed": eqy_result["equiv_passed"]
    }



def process_results(args):
    async def _run_all_eqy():
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
                    print(f"Project: {project}, Task: {task}")

                dataset_project_path = os.path.join(args.dataset_path, project)

                modules = find_hdl_files(os.listdir(dataset_project_path), args.hdl)
                if not modules:
                    print(f"No HDL files found in {dataset_project_path} for HDL {args.hdl}")
                    continue

                if len(modules) > 1:
                    print(f"Multiple HDL files found in {dataset_project_path} for HDL {args.hdl}. Using the first one: {modules[0]}")

                reference_path = os.path.join(dataset_project_path, modules[0])

                task_path = os.path.join(rule_path, project_name)

                total_tasks_per_rule[rule] += 1

                for generation in os.listdir(task_path):
                    pattern = generation.split("_", 1)[0]
                    if args.debug:
                        print(f"generation: {generation}, pattern: {pattern}")
                    if pattern != "full":
                         continue

                    generation_path = os.path.join(task_path, generation)


                    flat_tasks.append(
                        eval_ruc_async(
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
            print(f"Running {len(flat_tasks)} evaluations...")
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

    print(f"Results:")
    for rule in RULES:
        print(f"Rule: {rule}")
        print(f"  Syntax Check Pass Rate: {stx_results[rule]:.2f}%")
        print(f"  Equivalence Check Pass Rate: {eqy_results[rule]:.2f}%")
    

    path_save = os.path.join(args.output_path, "generation_eqy_results.json")
    print(f"Saving generation equivalence results to {path_save}")
    save_generation_eqy_results(generation_results, path_save)

    path_save_summary = os.path.join(args.output_path, "summary_results.json")
    print(f"Saving summary results to {path_save_summary}")
    save_generation_eqy_results({
        "syntax_check": stx_results,
        "equivalence_check": eqy_results,
    }, path_save_summary)


if __name__ == "__main__":
    args = parse_args_eval()
    print("Args parsed:")
    for arg, value in vars(args).items():
        print(f"  {arg}: {value}")
    process_results(args)