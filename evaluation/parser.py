import argparse

from .utils import str_to_bool

"""
Parse the hyperparameters and configurations defined in the shell scripts.
"""


def parse_args_eval():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--hdl", help="Language of file we are evaluating either v (Verilog) or sv (SystemVerilog)")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--timeout_is_pass", type=str_to_bool, default=True, help="Count an equivalence timeout as a pass")
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args


def parse_args_agentic_eval():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--hdl", help="Language of file we are evaluating either v (Verilog) or sv (SystemVerilog)")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to the completed holes to evaluate")
    parser.add_argument("--timeout_is_pass", type=str_to_bool, default=False, help="Count an equivalence timeout as a pass")
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args


def parse_args_agentic_self():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--hdl", help="Language of file we are evaluating either v (Verilog) or sv (SystemVerilog)")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to save the self-check report")
    parser.add_argument("--max_parallel", type=int, default=4)
    parser.add_argument("--timeout_is_pass", type=str_to_bool, default=False)
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args