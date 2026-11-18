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
    
    parser.add_argument("--all", action="store_true", help="Keep all available rule occurrences.")

    parser.add_argument("--fnc", action="store_true", help="Check functionality meaningfulness of rule occurrences.")

    parser.add_argument("--self", action="store_true", help="Check that the dataset can be evaluated at all, by proving every design equivalent to itself. Only read together with --evaluate.")

    parser.add_argument("--agentic", action="store_true", help="Build, run or evaluate the agentic benchmark, where one task holds several rule occurrences spread over several files of a project.")

    args = parser.parse_args()



    # Validate arguments

    if not (args.benchmark or args.inference or args.evaluate):
        print("Error: Either --benchmark, --inference, or --evaluate must be specified.")
        exit(1)

    if not args.model:
        print("Error: --model must always be specified, either for selecting the model to evaluate or using it's slurm.yml config.")


    if args.inference and not (args.prompt or args.agentic):
        print("Error: --prompt must be specified when using --inference unless --agentic is used.")
        exit(1)

    if args.evaluate and not (args.self or args.prompt or args.agentic):
        print("Error: --prompt must be specified when using --evaluate unless --self or --agentic is used.")
        exit(1)


    if args.self and not args.evaluate:
        print("Warning: --self is only used together with --evaluate, it is ignored here.")

    if args.benchmark:
        if not args.max_tasks and not args.all:
            print("Error: --max_tasks or --all must be specified when using --benchmark.")
            exit(1)

    return args
