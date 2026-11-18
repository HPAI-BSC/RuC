import json
import os

from evaluation.evaluate_rule_completion import process_evaluation_results
from evaluation.utils import RESULTS_FILENAME
from .parser import parse_args_benchmark
from .all_samples_selecter import process_dataset
from .generate_empty_outputs import generate_empty_outputs
from .project_manifest import MANIFEST_NAME
from .random_samples_selecter import select_random_lines, select_random_lines_without_fnc


class BenchmarkBuilder:
    def __init__(self, args):
        self.args = args

    def run(self):
        print("[INFO] Running benchmark build process...")

        self._check_dataset_layout()

        print("\n[INFO] Step 1: Computing rule occurrences summary in the dataset...\n")
        self._compute_dataset_summary()
        if self.args.fnc:
            print("\n[INFO] Step 2: Generating empty outputs for each occurrence...\n")
            self._generate_empty_outputs()
            print("\n[INFO] Step 3: Checking EQV on the files with the empty rule against the original...\n")
            self._evaluate()

        else:
            print("\n[INFO] Skipping steps 2 and 3 since --fnc is not set. Proceeding to sample selection...\n")

        print("\n[INFO] Step 4: Selecting random samples for the benchmark...\n")
        self._select_samples()


    def _check_dataset_layout(self):
        """
        Refuse a dataset laid out for the agentic flow.
        """
        agentic_projects = [
            project for project in os.listdir(self.args.dataset_path)
            if os.path.exists(os.path.join(self.args.dataset_path, project, MANIFEST_NAME))
        ]

        if agentic_projects:
            raise SystemExit(
                f"[ERROR] {self.args.dataset_path} is an agentic dataset: {len(agentic_projects)} "
                f"projects hold a {MANIFEST_NAME}. Build it with --agentic, or point dataset_path "
                f"at a dataset with a single flattened HDL file per project."
            )

    def _compute_dataset_summary(self):
        summary = process_dataset(
            self.args.dataset_path,
            self.args.hdl,
            self.args.debug
        )

        print("[INFO] Dataset summary:")
        print(json.dumps(summary, indent=4))

    def _generate_empty_outputs(self):
        generate_empty_outputs(
            self.args.dataset_path,
            self.args.output_path,
            self.args.hdl,
            self.args.debug
        )

    def _evaluate(self):
        process_evaluation_results(self.args)

    def _select_samples(self):
        if self.args.all:
            max_tasks = -1 
        else:
            max_tasks = self.args.max_tasks

        if self.args.fnc:
            results_path = self.args.output_path + "/" + RESULTS_FILENAME
            select_random_lines(
                results_path,
                self.args.dataset_path,
                max_tasks,
                self.args.debug
            )
        else:
            select_random_lines_without_fnc(
                self.args.dataset_path,
                max_tasks,
                self.args.debug
            )

if __name__ == "__main__":
    args = parse_args_benchmark()

    print("[INFO] Arguments:")
    print(json.dumps(vars(args), indent=4))
    builder = BenchmarkBuilder(args)
    builder.run()