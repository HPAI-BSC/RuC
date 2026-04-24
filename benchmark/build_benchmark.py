import json
from transformers import AutoTokenizer

from evaluation.eval_ruc import process_results
from .parser import parse_args_benchmark
from .compute_lines import process_dataset
from .not_generate import not_generate
from .select_random_lines import select_random_lines, select_random_lines_no_fnc


class BenchmarkBuilder:
    def __init__(self, args):
        self.args = args

        self.tokenizer = AutoTokenizer.from_pretrained(
            args.model,
            use_fast=False,
            trust_remote_code=True,
        )

    def run(self):
        print("Running benchmark build process...")

        print("\nStep 1: Computing rule occurrences summary in the dataset...\n")
        self._compute_dataset_summary()
        if self.args.fnc:
            print("\nStep 2: Generating empty outputs for each occurrence...\n")
            self._generate_empty_outputs()
            print("\nStep 3: Checking EQV on the files with the empty rule against the original...\n")
            self._evaluate()

        else:
            print("\nSkipping steps 2 and 3 since --fnc is not set. Proceeding to sample selection...\n")

        print("\nStep 4: Selecting random samples for the benchmark...\n")
        self._select_samples()


    def _compute_dataset_summary(self):
        summary = process_dataset(
            self.args.dataset_path,
            self.tokenizer,
            self.args.hdl,
            self.args.debug
        )

        print("Dataset summary:")
        print(json.dumps(summary, indent=4))

    def _generate_empty_outputs(self):
        not_generate(
            self.args.dataset_path,
            self.args.output_path,
            self.args.hdl,
            self.args.debug
        )

    def _evaluate(self):
        process_results(self.args)

    def _select_samples(self):
        if self.args.fnc:
            results_path = self.args.output_path + "/generation_eqy_results.json"
            select_random_lines(
                results_path,
                self.args.dataset_path,
                self.args.max_tasks,
                self.args.debug
            )
        else:
            select_random_lines_no_fnc(
                self.args.dataset_path,
                self.args.max_tasks,
                self.args.debug
            )


def main():
    args = parse_args_benchmark()

    print("Arguments:")
    print(json.dumps(vars(args), indent=4))
    builder = BenchmarkBuilder(args)
    builder.run()


if __name__ == "__main__":
    main()