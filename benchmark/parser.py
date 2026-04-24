import argparse

"""
Parse the hyperparameters and configurations defined in the shell scripts.
"""


def parse_args_benchmark():
    parser = argparse.ArgumentParser(description="RuC!")
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog)")
    parser.add_argument("--max_tasks", type=int, default=100, help="Maximum number of tasks to select per rule")
    parser.add_argument("--fnc", action="store_true", help="Check functionality meaningfulness of rule occurrences.")
    parser.add_argument("--debug", type=bool, help="Print more information")
    

    args, _ = parser.parse_known_args()
    return args