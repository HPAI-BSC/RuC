import os
import json
import random

from configs.rules import RULES
from .utils import extract_project_task



def process_empty_results(results):
    filtered = {}

    for path, data in results.items():
        if data.get("eqy_passed"):
            continue

        rule = data["rule"]
        project = os.path.basename(os.path.dirname(path))

        filtered.setdefault(rule, []).append(project)

    return filtered


def select_projects(empty_results, max_projects):
    selected = {}

    for rule, projects in empty_results.items():
        if rule not in RULES:
            continue

        if len(projects) <= max_projects:
            selected[rule] = projects
            continue

        grouped = {}
        for p in projects:
            prj, _ = extract_project_task(p)
            grouped.setdefault(prj, []).append(p)

        base = max(1, max_projects // len(grouped))

        chosen = []
        for group in grouped.values():
            chosen.extend(random.sample(group, min(base, len(group))))

        if len(chosen) > max_projects:
            chosen = random.sample(chosen, max_projects)

        if len(chosen) < max_projects:
            remaining = list(set(projects) - set(chosen))
            if remaining:
                chosen.extend(
                    random.sample(remaining, min(len(remaining), max_projects - len(chosen)))
                )

        selected[rule] = chosen

    return selected


def restructure(selected):
    out = {}

    for rule, projects in selected.items():
        for p in projects:
            prj, task = extract_project_task(p)
            out.setdefault(prj, {}).setdefault(rule, []).append(task)

    return out


def write_mask_idx(selected, dataset_dir):
    for project, rules in selected.items():
        src_path = os.path.join(dataset_dir, project, "all_mask_idx.json")

        with open(src_path) as f:
            mask_idx = json.load(f)

        new_idx = {}

        for rule, tasks in rules.items():
            for t in tasks:
                idx = t - 1
                if rule in mask_idx and idx < len(mask_idx[rule]):
                    new_idx.setdefault(rule, []).append(mask_idx[rule][idx])

        out_path = os.path.join(dataset_dir, project, "mask_idx.json")
        with open(out_path, "w") as f:
            json.dump(new_idx, f)


def select_random_lines(results_path, dataset_path, max_projects, debug):
    with open(results_path) as f:
        results = json.load(f)

    empty = process_empty_results(results)
    selected = select_projects(empty, max_projects)

    if debug:
        print(f"Number of rules selected: {len(selected)}")
        for rule, projects in selected.items():
            print(f"{rule}: {len(projects)}")

    structured = restructure(selected)        
    write_mask_idx(structured, dataset_path)


def select_random_lines_no_fnc(dataset_path, max_tasks, debug):
    # Collect all candidates globally per rule
    candidates_by_rule = {rule: [] for rule in RULES}

    for project in os.listdir(dataset_path):
        project_path = os.path.join(dataset_path, project)
        src_path = os.path.join(project_path, "all_mask_idx.json")

        if not os.path.exists(src_path):
            continue

        with open(src_path) as f:
            mask_idx = json.load(f)

        for rule, occurrences in mask_idx.items():
            if rule not in RULES:
                continue

            for task_num in range(1, len(occurrences) + 1):
                candidates_by_rule[rule].append((project, task_num))

    # Stratified sampling per rule (project diversity)
    selected_by_project = {}
    rule_counts = {rule: 0 for rule in RULES}

    for rule, candidates in candidates_by_rule.items():
        if not candidates:
            continue

        # Group candidates by project
        grouped = {}
        for project, task_num in candidates:
            grouped.setdefault(project, []).append(task_num)

        num_projects = len(grouped)
        base = max(1, max_tasks // num_projects)

        chosen = []

        # Balanced sampling per project
        for project, tasks in grouped.items():
            k = min(base, len(tasks))
            sampled = random.sample(tasks, k)
            chosen.extend([(project, t) for t in sampled])

        # Trim if exceeded
        if len(chosen) > max_tasks:
            chosen = random.sample(chosen, max_tasks)

        # Fill remaining slots if needed
        if len(chosen) < max_tasks:
            chosen_set = set(chosen)
            remaining = []

            for project, tasks in grouped.items():
                for t in tasks:
                    candidate = (project, t)
                    if candidate not in chosen_set:
                        remaining.append(candidate)

            if remaining:
                extra = random.sample(
                    remaining,
                    min(len(remaining), max_tasks - len(chosen))
                )
                chosen.extend(extra)

        rule_counts[rule] = len(chosen)

        # Store selections per project
        for project, task_num in chosen:
            selected_by_project.setdefault(project, {}).setdefault(rule, []).append(task_num)

    # Write mask_idx.json per project
    for project, rules in selected_by_project.items():
        src_path = os.path.join(dataset_path, project, "all_mask_idx.json")

        with open(src_path) as f:
            mask_idx = json.load(f)

        new_idx = {}

        for rule, tasks in rules.items():
            for t in tasks:
                idx = t - 1
                if rule in mask_idx and idx < len(mask_idx[rule]):
                    new_idx.setdefault(rule, []).append(mask_idx[rule][idx])

        out_path = os.path.join(dataset_path, project, "mask_idx.json")
        with open(out_path, "w") as f:
            json.dump(new_idx, f)

    print("Sampling Summary per Rule")
    for rule in RULES:
        print(f"{rule}: {rule_counts[rule]} samples")
        print("Sampling Summary per Rule")
        for rule in RULES:
            print(f"{rule}: {rule_counts[rule]} samples")