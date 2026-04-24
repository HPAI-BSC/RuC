import logging
import os
import re
import subprocess
import tempfile
import time
import yaml
from pathlib import Path
from typing import Dict, List

from slurm_commands import SlurmConfigLoader
from ruc_commands import RucCommandBuilder


class WorkflowExecutor:
    """This class is responsible for executing python workflows
    using subprocess."""

    def __init__(self, args: Dict, logger: logging.Logger) -> None:
        """Initialize the WorkflowExecutor.
        Parameters:
            - args: Dictionary of command line arguments
            - logger: Configured logger instance
        Returns:
            None
        """
        self.args = args
        self.CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
        self.logger = logger
        self.slurm_loader = None

        # Multi-node execution state
        self.head_node_ip = None
        self.ray_port = None
        self.vllm_port = None
        self.running_processes = []

    def load_yaml_config(self, file_path: Path) -> Dict:
        """Load a YAML configuration file.
        Parameters:
            - file_path: Path to the YAML file.
        Returns:
            - config: A dictionary containing the loaded configuration.
        """

        try:
            with open(file_path, "r") as file:
                return yaml.safe_load(file)
        except yaml.YAMLError as e:
            logging.error(f"Error parsing YAML file '{file_path}': {e}")
            return {}

    def build_singularity_commands(self, task_image_list: list[tuple[str, str]]) -> dict[str, str]:
        """Build a dictionary of Singularity commands for each task.

        Args:
            task_image_list: List of tuples where each tuple contains (task_name, image_path)

        Returns:
            Dictionary mapping task names to their corresponding Singularity commands
        """
        commands = {}
        for task_name, image_path in task_image_list:
            commands[task_name] = (
                f"SLURM_CPU_BIND=none NUMEXPR_MAX_THREADS=80 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 TORCH_NCCL_ASYNC_ERROR_HANDLING=1 TRITON_LIBCUDA_PATH=/usr/local/cuda/compat/lib.real/libcuda.so VLLM_USE_V1=1 VLLM_ATTENTION_BACKEND=FLASH_ATTN VLLM_WORKER_MULTIPROC_METHOD=spawn VLLM_USE_CUDA_GRAPH=0 singularity exec --nv {image_path} bash -c "
            )
        return commands

    def build_docker_commands(self, task_image_list: list[tuple[str, str]]) -> dict[str, str]:
        """Build a dictionary of Docker commands for each task.

        Args:
            task_image_list: List of tuples where each tuple contains (task_name, image_name)

        Returns:
            Dictionary mapping task names to their corresponding Docker commands
        """
        commands = {}
        for task_name, image_name in task_image_list:
            commands[task_name] = f"docker run --gpus all {image_name} bash -c "
        return commands

    # Methods for multi-node execution
    def _setup_environment(self, slurm_config: str = None) -> None:
        """Set up environment variables for distributed execution."""
        # Essential environment variables from bash script
        enviroment_vars = self.slurm_loader.get_env_vars(slurm_config)

        # Apply environment variables
        eviroment = {}
        for key, value in enviroment_vars.items():
            eviroment[key] = value

        return eviroment

    def parse_sbatch_string(self, slurm_command: str) -> Dict[str, str]:
        """Parse a string containing sbatch parameters into a dictionary.
        Parameters:
            - slurm_command: String containing sbatch parameters
        Returns:
            - Dictionary with parameter names as keys and their values
        """
        # Delete 'sbatch' from the beginning of the string if it exists
        if slurm_command.startswith("sbatch "):
            sbatch_str = slurm_command[7:]

        # Split the string into parts
        parts = slurm_command.split()

        result = {}

        for part in parts:
            # check if the part starts with '--' (indicating a parameter)
            if part.startswith("--"):
                # split the part into key and value
                key_value = part[2:].split("=", 1)
                key = key_value[0]
                # If there is no value (e.g., --exclusive), assign None
                value = key_value[1] if len(key_value) > 1 else None
                result[key] = value

        return result

    def run_command(self, command: str) -> None:
        """Execute a shell command safely.
        Parameters:
            - command: Command string to execute
        Returns:
            - result: CompletedProcess object containing the result of the command
        """
        print("Executing command:")
        print(command)
        try:
            result = subprocess.run(
                command,
                shell=True,
                text=True,
                capture_output=True,
            )

            if result.returncode != 0:
                self.logger.error(f"Command failed (exit code {result.returncode})")
            else:
                self.logger.info("Command executed successfully")

            return result
        except Exception as e:
            self.logger.exception(f"Exception while executing command: {str(e)}")
            # Create a dummy result to return in case of exception
            return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr=str(e))

    def run_job(
        self,
        slurm_command: str,
        slurm_config: str,
        singularity_command: str,
        ruc_command: str,
        task: str,
        singularity_image: str,
        model_path: str,
        model_name: str,
        data_path: str,
        output_path: str,
        hdl: str,
    ) -> subprocess.CompletedProcess:
        """Run job using either SLURM or direct execution."""

        self.logger.info(f"JOB START TIME: {time.ctime()}")

        # API MODE
        # if slurm_config == "api":
        #     self.logger.info("API execution mode detected - running locally without SLURM")
        #     clean_command = ruc_command.strip('"')
        #     return self.run_command(clean_command)

        # SLURM PARSING
        if slurm_command:
            dictionary = self.parse_sbatch_string(slurm_command)
            dictionary["slurm_enabled"] = True
        else:
            dictionary = {}

        # BASE METADATA
        dictionary.update({
            "task": task,
            "model_name": model_name,
            "model_path": model_path,
            "data_path": data_path,
            "output_path": output_path,
            "hdl": hdl,
        })

        # SINGULARITY CONFIG
        if singularity_image:
            dictionary["singularity_enabled"] = True

            dictionary["singularity_image"] = singularity_image

        # RuC COMMAND TRANSFORM
        if "--model" not in ruc_command:
            raise ValueError("ruc_command must contain '--model'")

        prefix, suffix = ruc_command.split("--model", 1)
        new_ruc_command = (
            f"{prefix.strip()} --ip ${{head_node_ip}} --port ${{vllm_port}} --model{suffix}"
        )
        dictionary["ruc_commands"] = new_ruc_command

        # ENVIRONMENT SETUP
        dictionary.update(self._setup_environment(slurm_config))
      
        # if getattr(self.args, "evaluation_only", False):
        #     slurm_command = re.sub(r"--qos=\S+", "--qos=gp_bsccs", slurm_command)
        #     slurm_command = re.sub(r"--gres=gpu:\d+", "", slurm_command)
        #     slurm_command = re.sub(r"#SBATCH\s+--exclusive", "", slurm_command)
        #     slurm_command = re.sub(
        #         r"--cpus-per-task=\d+",
        #         "--cpus-per-task=80",
        #         slurm_command,
        #     )

        #     ruc_command = ruc_command.replace(
        #         "--save_generations True", "--save_generations False"
        #     )
        #     ruc_command = ruc_command.replace(
        #         "--save_generations_path", "--load_generations_path"
        #     )

        full_cmd = (
            f"{slurm_command} --wrap="
            f"'module purge && module load singularity && "
            f"{singularity_command} {ruc_command}'"
        )

        return self.run_command(full_cmd)

    def filter_model_config(self, config_dict: Dict, model_name: str) -> Dict:
        """
        Filters a configuration dictionary to include only the specified model's configuration.

        Parameters:
            - config_dict (dict): The original configuration dictionary containing multiple models.
            - model_name (str): The name of the model to filter for (e.g., "CodeLlama-70b-hf").

        Returns:
            - dict: A new dictionary with the same structure as the input, but containing only
                    the configuration for the specified model.
        """
        # Create a deep copy of the original dictionary to avoid modifying it
        filtered_config = config_dict.copy()

        # Find the model with the matching name
        matching_models = [model for model in config_dict["models"] if model["name"] == model_name]

        if not matching_models:
            raise ValueError(f"Model '{model_name}' not found in configuration")

        # Replace the models list with only the matching model
        filtered_config["models"] = [matching_models[0]]

        return filtered_config

    def load_benchmark_config(self) -> List[Dict]:
        """
        Load the appropriate benchmark configuration based on args.
        Args:
            - None
        Returns:
            - benchmark_config: is a dictionary or a list of dictionaries
              containing the benchmark configuration.
        """
        try:
            config_path = os.path.join(self.CONFIGS_DIR, "ruc.yml")
        except Exception as e:
            self.logger.error(f"Error loading RuC config: {e}")
            return []
        
        # finde into the benchmark config dictionary the name of model
        benchmark_config = self.load_yaml_config(config_path)["benchmark"][0]
        return [self.filter_model_config(benchmark_config, self.args.model)]

    def load_jobs(self, benchmark_config: List[Dict]) -> None:
        """Execute the job with the given configuration."""

        benchmark = benchmark_config[0]
        model = benchmark["models"][0]

        _task = benchmark.get("task")
        model_name = model.get("name")
        temp = model.get("temperature")

        # SLURM
        if self.args.inference:
            slurm_key = "slurm_config_inference"
        else:
            slurm_key = "slurm_config_evaluate"

        if slurm_key in model and os.path.isfile(self.CONFIGS_DIR / "slurm.yml"):
            slurm_config_raw = self.load_yaml_config(self.CONFIGS_DIR / "slurm.yml")
            self.slurm_loader = SlurmConfigLoader(slurm_config_raw)
            slurm_configs_commands = self.slurm_loader.get_all_configs()
            slurm_commands = slurm_configs_commands.get(model.get(slurm_key), "")
        else:
            slurm_commands = ""

        # SINGULARITY
        if self.args.inference:
            singularity_image = benchmark.get("singularity_image")
        else:
            singularity_image = benchmark.get("evaluation_image")

        singularity_commands = self.build_singularity_commands(
            [(benchmark["task"], singularity_image)]
        )

        # BUILDER
        builder = RucCommandBuilder(benchmark, self.args)

        self.logger.info("=" * 80)
        self.logger.info(
            self.aligns_text(f"Process task: {_task} - Model: {model_name}")
        )
        self.logger.info("=" * 80)

        ruc_command = builder.build_command(model=model_name)

        result = self.run_job(
            slurm_command=slurm_commands,
            slurm_config=model.get(slurm_key),
            singularity_command=singularity_commands.get(_task),
            ruc_command=ruc_command,
            task=_task,
            singularity_image=benchmark.get("singularity_image"),
            model_path=benchmark.get("path_model"),
            model_name=model_name,
            data_path=benchmark.get("path_dataset"),
            output_path=benchmark.get("path_output"),
            hdl=benchmark.get("hdl"),
        )

        job_id = result.stdout.strip()

        self.logger.info("=" * 80)
        self.logger.info(self.aligns_text("FINAL REPORT"))

        if job_id:
            self.logger.info(f"Total jobs submitted: 1")
            self.logger.info(f"Job ID: {job_id}")
        else:
            self.logger.error(f"Error to send job: {result.stderr}")
            self.logger.info("Total jobs submitted: 0")

    def aligns_text(self, tittle: str = "", weight: int = 80) -> str:
        """Center the text in a string of a given width.
        Parameters:
            - tittle: The text to center
            - weight: The width of the string
        Returns:
            - centered_text: The centered text
        """
        # If the title is longer than the weight, return it as is
        if len(tittle) >= weight:
            return tittle

        # find the number of white spaces to add
        white_spaces = (weight - len(tittle)) // 2
        return " " * white_spaces + tittle

    def execute(self) -> None:
        """Execute the complete workflow."""
        # Load benchmark configuration
        benchmark_config = self.load_benchmark_config()

        # Execute the job
        self.load_jobs(benchmark_config)
