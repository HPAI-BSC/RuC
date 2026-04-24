import argparse


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command-line arguments.
    Args:
        - None
    Returns:
        - argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Run RuC.")
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Create the benchmark.",
    )
    parser.add_argument("--model", help="Specific model to run.")
    parser.add_argument("--prompt", help="Define the prompting strategy (chat or fim).")
    parser.add_argument("--max_tasks", type=int, help="Maximum number of projects to include in the benchmark.")
    parser.add_argument(
        "--inference",
        action="store_true",
        help="Do inference on the benchmark",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Evaluate the results of the benchmark.",
    )
    parser.add_argument("--fnc", action="store_true", help="Check functionality meaningfulness of rule occurrences.")
    
    args = parser.parse_args()



    # Validate arguments

    if not (args.benchmark or args.inference or args.evaluate):
        print("Error: Either --benchmark, --inference, or --evaluate must be specified.")
        exit(1)

    if not args.benchmark:
        if not args.model or not args.prompt:
            print("Error: --model and --prompt must be specified when using --inference or --evaluate.")
            exit(1)
    else:
        if not args.max_tasks or not args.model:
            print("Error: --max_tasks and --model (to extract the tokenizer) must be specified when using --benchmark.")
            exit(1)

    return args
