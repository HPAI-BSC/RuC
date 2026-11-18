import argparse

"""
Parse the hyperparameters and configurations defined in the shell scripts.
"""

def parse_args_gen():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--dataset_path", help="Path to the dataset root directory")
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--max_tokens", type=int, help="Maximum number of tokens to generate per output sequence")
    parser.add_argument("--sequence_length_limit", type=int, help="The longest length of the models forward graph to capture when running the VLLM.")
    parser.add_argument("--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--top_p", type=float, help="Top-p sampling parameter")
    parser.add_argument("--top_k", type=int, help="Top-k sampling parameter")
    parser.add_argument("--batch_size", type=int)
    parser.add_argument("--gpu_memory_utilization", type=float, help="GPU memory utilization limit")
    parser.add_argument("--swap_space", type=int, help="Swap space for GPU")
    parser.add_argument("--precision", help="Precision for model inference")
    parser.add_argument("--tensor_parallel_size", type=int, help="Tensor parallel size for model inference")
    parser.add_argument("--prompt_mode", type=str, help="Prompt mode to use ('chat' or 'fim')")
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog)")
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args

def parse_args_agent():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model", help="Path to the model")
    parser.add_argument("--model_name", default="", help="Name the harness passes to the agent")
    parser.add_argument("--tasks_path", help="Folder holding the emitted agent tasks")
    parser.add_argument("--output_path", help="Path to save the collected workspaces and holes")
    parser.add_argument("--stage", default="all", choices=["run", "collect", "all"],
                        help="Run the harness, collect the holes it left, or both")
    parser.add_argument("--harness", default="harbor.cli.main", help="Module the harness is invoked as")
    parser.add_argument("--harbor_python", default="python3", help="Interpreter that has the harness installed")
    parser.add_argument("--harbor_src", default="", help="Folder prepended to PYTHONPATH so the harness is importable")
    parser.add_argument("--agent", default="opencode", help="Agent the harness should drive")
    parser.add_argument("--executor", default="singularity", help="Harness backend (singularity or docker)")
    parser.add_argument("--base_container", default="", help="Unused at run time: the image is read from each task.toml, written at emit time")
    parser.add_argument("--provider", default="", help="Harness provider prefix of the model argument, e.g. vllm-local")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of tasks the harness runs at a time")
    parser.add_argument("--agent_timeout_s", type=int, default=1800, help="Time budget of one agent task, in seconds. The budget the tasks were emitted with wins when they name one")
    parser.add_argument("--verifier_timeout_s", type=int, default=600, help="Time budget of one task's tests, in seconds. Must match what the tasks were emitted with")
    parser.add_argument("--harbor_overhead_s", type=int, default=1800, help="Slack added on top of the per-task budgets before the harness is killed")
    parser.add_argument("--ip", default="", help="IP of a served model the agent should use")
    parser.add_argument("--port", default="", help="Port of a served model the agent should use")
    parser.add_argument("--context_limit", type=int, default=None, help="Served model's context window (--max-model-len)")
    parser.add_argument("--output_limit", type=int, default=None, help="Served model's max output tokens per call. Must be set together with --context_limit: OpenCode's config schema requires both or neither")
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog)")
    parser.add_argument("--debug", type=bool, help="Print more information")

    args, _ = parser.parse_known_args()
    return args


def parse_args_gen_ray():
    parser = argparse.ArgumentParser(description="RuC!", allow_abbrev=False)
    parser.add_argument("--model_name", type=str)
    parser.add_argument("--model_path", help="Path of the model")
    parser.add_argument("--output_path", help="Path to save generations")
    parser.add_argument("--ip", type=str, required=True, help="IP address of the vLLM server")
    parser.add_argument("--port", type=int, required=True, help="Port of the vLLM server")
    parser.add_argument("--dataset_path")
    parser.add_argument("--max_tokens", type=int)
    parser.add_argument("--sequence_length_limit", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--top_p", type=float)
    parser.add_argument("--top_k", type=int)
    parser.add_argument("--batch_size", type=int)
    parser.add_argument("--prompt_mode", type=str)
    parser.add_argument("--hdl", help="Hardware description language (v for Verilog, sv for SystemVerilog)")
    parser.add_argument("--debug", type=bool, help="Print more information")  

    args, _ = parser.parse_known_args()
    return args