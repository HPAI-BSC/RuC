import os
import sys
from typing import Dict, List, Optional
import shlex


class RucCommandBuilder:
    """
    A class to dynamically build BigCode evaluation commands based on benchmark configuration.
    """

    def __init__(self, benchmark_config: Dict, args: Dict):
        """
        Initialize the command builder with the benchmark configuration.

        :param benchmark_config: A dictionary containing the benchmark configuration.
        :param args: arguments defined from the parser, used to pass extra information to the command builder if needed.
        """
        self.benchmark_config = benchmark_config
        self.args = args

    def get_launcher_command(self) -> List[str]:
        """
        Determine the launcher command based on the model type and execution mode.
        """
        if self.args.inference:
            command_ = '"python3 -u -m inference.generate'
        if self.args.evaluate:
            command_ = '"python3 -u -m evaluation.eval_ruc'
        # Not completed yet
        if self.args.benchmark:
            command_ = '"python3 -u -m benchmark.build_benchmark'

        return command_

    def build_dynamic_parameters(self, model_name: str) -> List[str]:
        """
        Build dynamic parameters for the command excluding slurm_config.

        Parameters:
            model_name: Name of the model to filter parameters for.
        Returns:
            A list of command-line arguments as strings.
        """
        params = []

        # 1. Validte the benchmark configuration
        if not hasattr(self, "benchmark_config") or not isinstance(self.benchmark_config, dict):
            raise ValueError("Configuration is not valid. Please check the benchmark configuration.")

        # 2. find the specific model configuration
        model_config = next(
            (m for m in self.benchmark_config.get("models", []) if m.get("name") == model_name),
            None,
        )

        if not model_config:
            raise ValueError(f"Model '{model_name}' was not found in the benchmark configuration.")

        # 3. Adding task name to the list
        # task_name = self.benchmark_config.get("task")
        # if task_name:
        #     params.append(f"--task {task_name}")

        # 4. List of excluded parameters
        excluded_params = {"name", "slurm_config_inference", "slurm_config_evaluation", "multinode"}

        # 5. Add temperature parameter
        # temperature = model_config.get("temperature")

        # 6. Process each parameter in the model configuration
        for key, value in model_config.items():
            # check especial cases
            if key in excluded_params:
                continue

            # behavior for bolean values
            if isinstance(value, bool):
                params.append(f"--{key} {value}")
                continue

            # behavior for none values
            if value is None:
                continue

            # All rest of the parameters
            params.append(f"--{key} {value}")

        # 7. Add the dataset path and temp files
        if self.args.benchmark:
            path_output = self.benchmark_config.get("output_path")
            if path_output:
                out_path = os.path.join(path_output, "tmp")
                params.append(f"--output_path '{out_path}'")
                
            max_tasks = self.args.max_tasks
            if max_tasks:
                params.append(f"--max_tasks {self.args.max_tasks}")

            fnc = self.args.fnc
            if fnc:
                params.append(f"--fnc {fnc}")
                
        else:
            path_output = self.benchmark_config.get("output_path")
            if path_output:
                params.append(f"--output_path '{self.benchmark_config.get('output_path')}{model_name}/{self.args.prompt}'")

        path_model = self.benchmark_config.get("model_path")
        if path_model:
            params.append(f"--model_path '{self.benchmark_config.get('model_path')}{model_name}'")
        path_dataset = self.benchmark_config.get("dataset_path")
        if path_dataset:
            params.append(f"--dataset_path '{self.benchmark_config.get('dataset_path')}'")

        hdl = self.benchmark_config.get("hdl")
        if hdl:
            params.append(f"--hdl {hdl}")

        if self.args.prompt:
            params.append(f"--prompt_mode {self.args.prompt}")

        return params

    def build_command(self, model: str) -> str:
        """
        Build the full BigCode command based on the provided configuration.

        Parameters:
            task_name: Name of the specific task to run (optional).
            use_accelerate: Boolean indicating whether to use accelerate or plain Python.
        return:
            The complete command list of string.
        """
        # Get the execution command prefix
        execution_command = self.get_launcher_command()

        # Build dynamic parameters
        dynamic_params = self.build_dynamic_parameters(model)

        # Add global parameters (e.g., model path)
        global_params = [
            f"--model {self.benchmark_config['model_path']}{model}",
        ]

        # Combine all parts into the final command
        full_command = execution_command + " " + " ".join(global_params) + " " + " ".join(dynamic_params)
        return full_command + '"'
