import argparse

"""
Parse the hyperparameters and configurations defined in the shell scripts.
"""


def parse_args_eval():
    parser = argparse.ArgumentParser(description="RuC!")
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--hdl", help="Language of file we are evaluating either v (Verilog) or sv (SystemVerilog)")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--debug", type=bool, help="Print more information")
    
    args, _ = parser.parse_known_args()
    return args