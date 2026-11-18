import argparse

"""
Parse the hyperparameters and configurations defined in the shell scripts.

Every parser here uses `parse_known_args`, because `configs/ruc.yml` forwards the same
option list to every stage and each one only reads what it needs. That makes
`allow_abbrev=False` mandatory: with abbreviation on, an option meant for another stage
is silently matched to a longer one of this stage -- `--agent agentic-opencode`, meant
for the inference stage, binds to `--agent_timeout_s` here and aborts the build.
"""


def parse_args_benchmark():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog, vhd for VHDL)")
    parser.add_argument("--max_tasks", type=int, default=100, help="Maximum number of tasks to select per rule")
    parser.add_argument("--fnc", action="store_true", help="Check functionality meaningfulness of rule occurrences.")
    parser.add_argument("--all", action="store_true", help="Keep all available rule occurrences.")
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args


def parse_args_agentic_benchmark():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Base folder for the tmp and tasks folders")
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog, vhd for VHDL)")
    parser.add_argument("--max_tasks", type=int, default=1, help="Maximum number of bundles to build per project, or of tasks per rule when --holes_per_task is 1")
    parser.add_argument("--holes_per_task", type=int, default=5, help="Number of rule occurrences to remove per bundle")
    parser.add_argument("--min_files_per_task", type=int, default=2, help="Minimum number of distinct sources a bundle must span (unused when --holes_per_task is 1)")
    parser.add_argument("--min_rules_per_task", type=int, default=2, help="Minimum number of distinct rule types a bundle must span (unused when --holes_per_task is 1)")
    parser.add_argument("--max_parallel", type=int, default=4, help="Number of --fnc equivalence checks running at once")
    parser.add_argument("--hole_visibility", default="marked", help="How the holes are shown to the agent (only 'marked' is implemented)")
    parser.add_argument("--base_container", default="", help="Image (.sif path or docker name) every task boots from, written into each task.toml as the only place Harbor reads it from")
    parser.add_argument("--docker_image", default="", help="Fallback image name when no base_container is configured")
    parser.add_argument("--agent_timeout_s", type=int, default=1800, help="Time budget of one agent task, in seconds")
    parser.add_argument("--verifier_timeout_s", type=int, default=600, help="Time budget of the in-container checks, in seconds")
    parser.add_argument("--fnc", action="store_true", help="Check functionality meaningfulness of rule occurrences.")
    parser.add_argument("--all", action="store_true", help="Keep all available rule occurrences.")
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args