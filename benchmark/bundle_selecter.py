import asyncio
import itertools
import os
import json
import random

from configs.rules import RULES
from .project_manifest import read_manifest
from .utils import read_occurrence


"""
Group rule occurrences into agentic tasks.

An agentic task is a bundle: occurrences of one project that a model has to complete in
one session. There are two shapes:

- `--holes_per_task 1`: each task is a single hole, and `--max_tasks` holes are drawn per
  rule, taking one project at a time, so every rule gets the same number of tasks as long
  as the dataset has that many.
- `--holes_per_task N`: up to `--max_tasks` bundles per project, each one spread over
  several sources and several rule types.

With `--fnc` only the occurrences a selection actually uses are checked: the tasks are
drafted as if every occurrence were meaningful, the holes of the draft are checked, the
ones whose deletion did not break the design are dropped, and the tasks are drafted again
until every hole of the draft has passed.

Bundles are written to `bundle_idx.json`, the multi-file counterpart of `mask_idx.json`:

    {
        "b00": [
            {"id": "h1", "project": "cve2", "file": "rtl/cve2_alu.sv",
             "rule": "continuous_assign", "span": [1204, 1240], "index": 7},
            ...
        ]
    }

"""


BUNDLE_NAME = "bundle_idx.json"


def hole_key(hole):
    return hole["project"], hole["rule"], hole["index"]


def collect_candidates(project_dir):
    """ Every occurrence of a project that may become a hole """
    mask_path = os.path.join(project_dir, "all_mask_idx.json")

    if not os.path.exists(mask_path):
        return []

    with open(mask_path) as f:
        mask_idx = json.load(f)

    candidates = []

    for rule, entries in mask_idx.items():
        if rule not in RULES:
            continue

        for index, entry in enumerate(entries, start=1):
            source, span = read_occurrence(entry)

            if source is None:
                continue

            candidates.append({
                "project": os.path.basename(project_dir),
                "file": source,
                "rule": rule,
                "span": span,
                "index": index,
            })

    return candidates


def overlaps(candidate, chosen):
    """ True when a candidate shares any character with a hole already in the bundle """
    start, stop = candidate["span"]

    for hole in chosen:
        if hole["file"] != candidate["file"]:
            continue

        other_start, other_stop = hole["span"]

        if start < other_stop and other_start < stop:
            return True

    return False


def pick_bundle(candidates, holes_per_task, min_files, min_rules):
    """
    Greedily take holes for one bundle, preferring an unseen source and an unseen rule.

    Works on positions in `candidates` rather than on the dicts themselves, so nothing
    depends on two occurrences comparing equal. Returns the positions of the chosen
    holes, or None when no bundle meeting the diversity constraints fits in what is left.
    """
    chosen = []
    taken = set()

    while len(chosen) < holes_per_task:
        used_files = {candidates[p]["file"] for p in chosen}
        used_rules = {candidates[p]["rule"] for p in chosen}
        chosen_holes = [candidates[p] for p in chosen]

        best = None
        best_score = None

        for position, candidate in enumerate(candidates):
            if position in taken or overlaps(candidate, chosen_holes):
                continue

            # A candidate is worth more when it brings a new source or a new rule
            score = (
                candidate["file"] not in used_files,
                candidate["rule"] not in used_rules,
            )

            if best_score is None or score > best_score:
                best = position
                best_score = score

                if score == (True, True):
                    break

        if best is None:
            break

        chosen.append(best)
        taken.add(best)

    files = {candidates[p]["file"] for p in chosen}
    rules = {candidates[p]["rule"] for p in chosen}

    if len(chosen) < holes_per_task or len(files) < min_files or len(rules) < min_rules:
        return None

    return chosen


def interleave(groups):
    """ One item of each group in turn, so no group fills the head of the list """
    return [
        item
        for batch in itertools.zip_longest(*groups)
        for item in batch
        if item is not None
    ]


def draft_singles(per_project, quota):
    """
    The first `quota` holes not rejected yet, one project at a time, one task each.

    The projects are interleaved after dropping the rejected holes, so a failed hole is
    replaced by the next occurrence of its own project rather than by another project's.
    """
    def draft(rejected):
        kept = interleave([
            [hole for hole in holes if hole_key(hole) not in rejected] for holes in per_project
        ])
        return [[hole] for hole in (kept if quota < 0 else kept[:quota])]

    return draft


def draft_bundles(candidates, max_bundles, holes_per_task, min_files, min_rules):
    """ Up to `max_bundles` bundles out of the candidates of one project not rejected yet """
    def draft(rejected):
        left = [hole for hole in candidates if hole_key(hole) not in rejected]
        bundles = []

        while max_bundles < 0 or len(bundles) < max_bundles:
            positions = pick_bundle(left, holes_per_task, min_files, min_rules)

            if positions is None:
                break

            bundles.append([left[p] for p in positions])

            # Highest position first so the earlier positions stay valid
            for position in sorted(positions, reverse=True):
                left.pop(position)

        return bundles

    return draft


async def settle(draft, check):
    """
    Draft the tasks of one group again and again until every hole they use passed `check`.

    A hole is checked as soon as a draft uses it, and the group is drafted again whenever a
    check ends, so a failed hole is replaced without waiting for slower checks. Every check
    adds a verdict, so this ends, and at worst it checks what checking every occurrence
    upfront would have.
    """
    verdicts = {}
    running = {}

    while True:
        rejected = {key for key, meaningful in verdicts.items() if not meaningful}
        bundles = draft(rejected)
        pending = [hole for bundle in bundles for hole in bundle if hole_key(hole) not in verdicts]

        if check is None or not pending:
            # A later draft can leave out a hole whose check is still running
            await asyncio.gather(*running.values())
            return bundles

        for hole in pending:
            if hole_key(hole) not in running:
                running[hole_key(hole)] = asyncio.create_task(check(hole))

        done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)

        for key, task in list(running.items()):
            if task in done:
                verdicts[key] = running.pop(key).result()


async def select_bundles(dataset_path, max_tasks, holes_per_task, min_files, min_rules,
                         check=None, debug=False):
    """
    Build the tasks of every project and write `bundle_idx.json` next to its sources.

    `check` is the coroutine of the functional validation step, which tells whether a hole
    is worth asking for. When it is None the occurrences are used as they are (matching
    `--benchmark` without `--fnc`). A negative `max_tasks` keeps everything there is.
    """
    candidates = {}

    for project in sorted(os.listdir(dataset_path)):
        project_dir = os.path.join(dataset_path, project)

        if not os.path.isdir(project_dir) or read_manifest(project_dir) is None:
            continue

        candidates[project] = collect_candidates(project_dir)
        random.shuffle(candidates[project])

        if debug:
            print(f"[INFO] {project}: {len(candidates[project])} candidate occurrences")

    if holes_per_task == 1:
        # Balanced per rule across the dataset, so each rule is drafted on its own. The
        # project order is shuffled per rule so the same projects do not lead every queue
        drafts = []

        for rule in RULES:
            per_project = [[h for h in holes if h["rule"] == rule] for holes in candidates.values()]
            random.shuffle(per_project)
            drafts.append(draft_singles(per_project, max_tasks))
    else:
        drafts = [
            draft_bundles(holes, max_tasks, holes_per_task, min_files, min_rules)
            for holes in candidates.values()
        ]

    groups = await asyncio.gather(*(settle(draft, check) for draft in drafts))

    bundles_by_project = {}
    for bundle in itertools.chain.from_iterable(groups):
        bundles_by_project.setdefault(bundle[0]["project"], []).append(bundle)

    rule_counts = {rule: 0 for rule in RULES}

    for project in candidates:
        out_path = os.path.join(dataset_path, project, BUNDLE_NAME)
        bundles = bundles_by_project.get(project)

        # The emitter reads whatever is there, so a project left without a task this time
        # must not keep the bundles of a previous build
        if not bundles:
            if os.path.exists(out_path):
                os.remove(out_path)
            continue

        indexed = {}
        for number, bundle in enumerate(bundles):
            indexed["b" + str(number).zfill(2)] = [
                dict(hole, id="h" + str(order)) for order, hole in enumerate(bundle, start=1)
            ]

            for hole in bundle:
                rule_counts[hole["rule"]] += 1

        with open(out_path, "w") as f:
            json.dump(indexed, f, indent=4)

        if debug:
            print(f"[INFO] {project}: {len(bundles)} bundles")

    total_bundles = sum(len(bundles) for bundles in bundles_by_project.values())
    skipped_projects = [project for project in candidates if project not in bundles_by_project]

    print("[INFO] Sampling Summary per Rule")
    for rule in RULES:
        print(f"{rule}: {rule_counts[rule]} holes")

    print(f"[INFO] Total bundles: {total_bundles}")

    if skipped_projects:
        print(f"[WARNING] {len(skipped_projects)} projects produced no bundle: {skipped_projects}")

    return total_bundles


def read_bundles(project_dir):
    """ The bundles of a project, or None when it has none """
    bundle_path = os.path.join(project_dir, BUNDLE_NAME)

    if not os.path.exists(bundle_path):
        return None

    with open(bundle_path) as f:
        return json.load(f)
