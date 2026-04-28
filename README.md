# **RuC**: HDL-Agnostic Rule Completion Benchmark Generation
RuC is a grammar-driven, rule-selectable benchmark generator that automatically produces RTL code-completion tasks from a set of input hardware description sources. It uses the target HDL grammar to mask syntactically defined code regions and prompts a model to regenerate them using the surrounding unmasked code as context.


## Overview
RuC supports three main stages:
1. **Benchmark creation**  
   Select rule occurrences from source projects and build a benchmark.
2. **Inference**  
   Run a model on the benchmark using either FIM or chat-style prompting.
3. **Evaluation**  
   Measure syntax and equivalence correctness of the generated completions.


## Repository structure
### configs
Contains files to define the configuration of RuC.

### src and slurm
Contains files to launch RuC on a cluster.

### benchmark
Contains files used to analyze the dataset and create the benchmark.

- Collect all selected rule occurrences in the dataset and store them in the same dataset. Inside each folder of the dataset, an `all_mask_idx.json` file is created with the rule occurrences of the HDL file in it.
- For each rule occurrence, create a copy of the original file with that occurrence removed.
- Check functional equivalence (EQV) of the empty generations against the original files to ensure that occurrences chosen are functionally meaningful.
- Select random samples from the functionally meaningful subset in `all_mask_idx.json`, ensuring file-level diversity. `mask_idx.json` is created for each project with the actual positions of the samples of the new benchmark.


### inference
Contains files used to run inference with vLLM on the created benchmark.


### evaluation
Contains files used to evaluate model generations on the created benchmark.

`eval_ruc.py` runs STX and EQV checks by comparing the original files with files where generated code replaces the masked rule occurrences. It reports the average percentage of STX and EQV passes per rule type.
The STX and EQV scripts are in `eval_notsotiny.py`.


## Cluster Usage
Each project folder in the dataset must contain exactly one HDL file, named identically to the top module (i.e., <top_module_name>.sv or .v, matching the module declaration inside the file).

We recommend preprocessing the design (e.g., with vppreproc) to produce a single, self-contained file with all includes and macros resolved.
```
<dataset_path>
|-- <p1>
|----<p1_top_module>.sv
|-- <p2>
|----<p2_top_module>.sv
```


1. Edit `configs/slurm.yml`.
Adjust the SLURM configuration to match your cluster setup. Each configuration entry defines a reusable SLURM profile, referenced later in ruc.yml via slurm_config_inference and slurm_config_evaluate.
```
configurations:
  - type: general-partition-debug
    account: bsc70
    qos: gp_debug
    output: slurm_output/job_%j.out
    error: slurm_output/job_%j.err
    nodes: 1
    time: "02:00:00"
    ntasks: 1
    cpus-per-task: 80
```

2. Edit `configs/ruc.yml`. 
Specify:
- HDL of the source projects. Currently supported Verilog (v) and SystemVerilog (sv).
- Paths to models, dataset, output directory, and singularity images.
- Model configuration and inference hyperparameters.

```
benchmark:
  - task: RuC
    hdl: sv # sv (SystemVerilog) or v (Verilog)
    dataset_path: /gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/datasets/RuC-cve2_b72358c7-32k/
    model_path: /gpfs/scratch/bsc70/hpai/storage/projects/heka/models/  # Folder containing the models
    output_path: /gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/results/RuC-CVE2-32k/  # Folder to store the generation results
    singularity_image: /gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/vllm-openai-0.10.1-k2.sif  # Singularity image for inference
    evaluation_image: /gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/ruc-eval-slang.sif  # Singularity image for evaluation and benchmark creation
    models:
      - name: Qwen2.5-Coder-14B
        slurm_config_inference: accelerate-partition-debug
        slurm_config_evaluate: general-partition-debug
        precision: bfloat16
        temperature: 0.2
        sequence_length_limit: 32768
        max_tokens: 2048
        top_p: 0.95
        top_k: -1
        gpu_memory_utilization: 0.85
        swap_space: 16
        tensor_parallel_size: 4
        batch_size: 64
        debug: true
```
The inference Singularity image must include vLLM and its dependencies. The evaluation image must include antlr4-python3-runtime and Yosys, or yosys-slang depending on the Yosys flow used.

3. Edit `configs/rules.py`.
Select the desired rules to create the benchmark.
```
RULES = [
        "module_program_interface_instantiation",
        "continuous_assign",
]
```

4. Edit `configs/prompts.py`.
Define:
- FIM template used for fill-in-the-middle-based prompting
- System prompt used for chat-based prompting
```
FIM_TEMPLATE = "<|fim_prefix|>{prefix}<|fim_suffix|>{suffix}<|fim_middle|>"

SYSTEM_PROMPT = """
    You are a professional Verilog hardware designer.
    ...
"""
```

5. Create a benchmark:
```python3 src/run.py --model <model_name> --max_tasks <number_tasks> --fnc --benchmark```
- --model: Model name whose tokenizer is used to compute token-level statistics and identify rule occurrences (no inference is performed at this stage).
- --max_tasks: Maximum number of benchmark instances generated per rule.
- --fnc (optional): Restrict selection to functionally valid occurrences, determined via equivalence checking (significantly increases runtime). If omitted, occurrences are sampled randomly.

This step generates, for each project folder, a file (`mask_idx.json`) containing the start and end indices of selected rule occurrences.

6. Inference with a model on the created benchmark:
```python3 src/run.py --model <model_name> --prompt <prompt_type> --inference```
- --model: name of the model to evaluate.
- --prompt: prompting strategy to use during generation (fim or chat).

Outputs are written to `output_path/<model_name>/<prompt_type>/`. Inside that directory:
- One folder is created per rule type.
- Inside each rule folder, one folder is created per benchmark occurrence.
- Each occurrence folder contains the model generation and the complete HDL file with the generation inserted in place of the original masked region.

7. Evaluate the generations:
```python3 src/run.py --model <model_name> --prompt <prompt_type> --evaluate```

Outputs are written to `output_path/<model_name>`. The following files are generated:
- Generation_eqy_results.json: Per-instance syntax and equivalence results
- Summary_results.json: Aggregated syntax and equivalence pass rates per rule


### Multi-node inference
To do inference on larger model across multiple nodes, we support vLLM + Ray under SLURM.
- SLURM → allocates nodes and GPUs
- Ray → connects those nodes into a cluster
- vLLM → runs the model across that cluster
- Benchmark script → sends inference requests

Use as a template `slurm/inference/gpt-oss-120b-0109.sh`, and create your own `slurm/inference/<model_name>.sh` with your specific configurations. 
In order to run it:
`sbatch slurm/inference/<model_name>.sh`