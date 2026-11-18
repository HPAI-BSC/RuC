import asyncio
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor

from evaluation.evaluate_agentic_completion import evaluate_hole_async
from evaluation.utils import RESULTS_FILENAME, save_generation_eqy_results
from .parser import parse_args_agentic_benchmark
from .all_samples_selecter import process_agentic_dataset
from .bundle_selecter import select_bundles
from .generate_empty_outputs import write_hole
from .harbor_emitter import TASKS_DIRNAME, emit_tasks
from .project_manifest import generate_manifests


"""
Build the agentic benchmark.

The projects are described by a manifest, and the selected occurrences are grouped
into bundles and written out as agent tasks at the end.

Everything is produced under `output_path`:
    <output_path>/tmp/     the empty outputs checked by the functional validation step
    <output_path>/tasks/   one folder per bundle, ready for the agent harness
"""


class AgenticBenchmarkBuilder:
    def __init__(self, args):
        self.args = args

        # A timeout proves nothing, so an occurrence is only kept when its deletion is
        # proven to break the design
        self.check_args = copy.copy(args)
        self.check_args.timeout_is_pass = True
        self.check_results = {}

    def tmp_path(self):
        return os.path.join(self.args.output_path, "tmp")

    def tasks_path(self):
        return os.path.join(self.args.output_path, TASKS_DIRNAME)

    def run(self):
        print("[INFO] Running agentic benchmark build process...")

        if self.args.hole_visibility != "marked":
            print(f"[WARNING] hole_visibility '{self.args.hole_visibility}' is not implemented, falling back to 'marked'")

        print("\n[INFO] Step 1: Writing a manifest for each project...\n")
        self._generate_manifests()

        print("\n[INFO] Step 2: Computing rule occurrences summary in the dataset...\n")
        self._compute_dataset_summary()

        if self.args.fnc:
            print("\n[INFO] Step 3: Selecting bundles, checking EQV of each sampled occurrence deleted against the original...\n")
        else:
            print("\n[INFO] Step 3: Selecting bundles, without checking the occurrences since --fnc is not set...\n")
        self._select_bundles()

        print("\n[INFO] Step 4: Emitting the agent tasks...\n")
        self._emit_tasks()

    def _generate_manifests(self):
        generate_manifests(
            self.args.dataset_path,
            self.args.hdl,
            self.args.debug
        )

    def _compute_dataset_summary(self):
        summary = process_agentic_dataset(
            self.args.dataset_path,
            self.args.hdl,
            self.args.debug
        )

        print("[INFO] Dataset summary:")
        print(json.dumps(summary, indent=4))

    async def _is_meaningful(self, hole):
        """ Whether deleting the occurrence breaks equivalence with the original design """
        save_dir = os.path.join(
            self.tmp_path(), hole["rule"], hole["project"] + "-task" + str(hole["index"])
        )

        with open(os.path.join(self.args.dataset_path, hole["project"], hole["file"])) as f:
            write_hole(save_dir, hole, f.read(), self.args.hdl)

        result = await evaluate_hole_async(save_dir, self.check_args)
        self.check_results[save_dir] = result

        return not result["eqy_passed"]

    async def _select_bundles_async(self, max_bundles):
        # Every check runs its tools on the default executor, so its size bounds how
        # many run at once
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=max(1, self.args.max_parallel))
        )

        await select_bundles(
            self.args.dataset_path,
            max_bundles,
            self.args.holes_per_task,
            self.args.min_files_per_task,
            self.args.min_rules_per_task,
            self._is_meaningful if self.args.fnc else None,
            self.args.debug
        )

    def _select_bundles(self):
        if self.args.all:
            max_bundles = -1
        else:
            max_bundles = self.args.max_tasks

        asyncio.run(self._select_bundles_async(max_bundles))

        if self.args.fnc:
            print(f"[INFO] Equivalence checks run: {len(self.check_results)}")

            os.makedirs(self.tmp_path(), exist_ok=True)
            save_generation_eqy_results(
                self.check_results, os.path.join(self.tmp_path(), RESULTS_FILENAME)
            )

    def _emit_tasks(self):
        agentic = {
            "base_container": self.args.base_container,
            "docker_image": self.args.docker_image,
            "agent_timeout_s": self.args.agent_timeout_s,
            "verifier_timeout_s": self.args.verifier_timeout_s,
        }

        emit_tasks(
            self.args.dataset_path,
            self.tasks_path(),
            self.args.hdl,
            agentic,
            self.args.debug
        )


if __name__ == "__main__":
    args = parse_args_agentic_benchmark()

    print("[INFO] Arguments:")
    print(json.dumps(vars(args), indent=4))
    builder = AgenticBenchmarkBuilder(args)
    builder.run()
