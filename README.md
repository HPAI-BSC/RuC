# 🫏 RuC: Grammar-Driven Benchmark Generator for Agentic and Non-Agentic RTL Code Completion

RuC is a framework that turns your own hardware designs into code-completion benchmarks,
and evaluates models on them. Give it Verilog or SystemVerilog sources, and it:

1. parses them with the HDL grammar,
2. removes code regions that match the grammar rules you choose (an `assign`, a `case`, a
   module instantiation, ...),
3. asks a model to write each region back, and
4. scores every answer on **STX** (the completed design still compiles) and **EQV** (it is
   formally equivalent to the original, proven with Yosys).

From the same sources RuC can generate two types of benchmark:

- **Rule completion.** Each task is one hole in one file. The model gets the surrounding
  code as a fill-in-the-middle (`fim`) or chat (`chat`) prompt and answers with the
  missing code.
- **Agentic.** Each task is a whole project with one or more holes, possibly in different
  files. An agent (OpenCode) works in a real copy of the project, where it can read files
  and run the simulators. Every hole is still scored on its own.


## How it runs

RuC is built for a SLURM cluster with Singularity, where compute nodes may have no
internet. Models are open-weight checkpoints on disk, served with **vLLM**.

- **Benchmark generation and evaluation** run as SLURM jobs inside the evaluation image.
  `src/run.py` submits them.
- **Rule-completion inference** runs vLLM inside the inference image. Small models go
  through `src/run.py`. Models that need several nodes use a script in
  `slurm/inference/large/`, which starts a vLLM server over Ray and sends it the prompts.
- **Agentic inference** uses a script in `slurm/inference/agentic/`. It starts a vLLM
  server, then runs **Harbor** on the host. Harbor starts one Singularity container per
  task from the agentic image and runs **OpenCode** inside it. OpenCode talks to the vLLM
  server through its OpenAI-compatible API.

## Setup

You need three Singularity images:

| Image | Used for | Must contain |
|---|---|---|
| inference | inference | vLLM |
| evaluation | benchmark generation, evaluation | Python with `antlr4-python3-runtime`, Yosys (with the [yosys-slang](https://github.com/povik/yosys-slang) plugin for SystemVerilog), Verilator (SystemVerilog) or Icarus Verilog (Verilog) |
| agentic | agentic tasks | Built from `slurm/inference/agentic/ruc-sim-opencode.def` |

On the login node you need Python with `pyyaml`, to run `src/run.py`.

For agentic benchmarks, also build the agentic image and Harbor. Both steps need internet:

```
cd slurm/inference/agentic
./fetch_vendor.sh                     # set OPENCODE_VERSION to pin OpenCode
singularity build --fakeroot ruc-sim-opencode.sif ruc-sim-opencode.def
cd -
third_party/harbor/setup_harbor.sh    # patched Harbor, see third_party/harbor/README.md
```

Then edit the configuration:

- `configs/ruc.yml`: HDL (`sv` or `v`), the paths to the dataset, models, results and
  images, and one entry per model with its sampling parameters. The `agentic` block sets
  the task shape and the time budgets.
- `configs/slurm.yml`: your account, QoS and partitions.
- `configs/rules.py`: the grammar rules RuC may remove.
- `configs/prompts.py`: the FIM template and the chat system prompt.
- The slurm scripts you use: fill in every `<...>` and `/path/to/...`.

Run every command from the repository root.

## Ready-to-use benchmarks

[HPAI-BSC/RuC-datasets](https://huggingface.co/datasets/HPAI-BSC/RuC-datasets) holds the
rule-completion benchmarks of the paper, already generated:

## Generate a rule-completion benchmark

**Input.** One folder per design, holding one file named after its top module. Flatten
each design first, so includes and macros are resolved (for example with `vppreproc`):

```
<dataset_path>/
├── <design_1>/<top_module>.sv
└── <design_2>/<top_module>.sv
```

**Generate, run and score:**

```
python3 src/run.py --model <model> --max_tasks <n> [--fnc] --benchmark   # 1. generate the benchmark
python3 src/run.py --model <model> --prompt chat --inference             # 2. run a model (or fim)
python3 src/run.py --model <model> --prompt chat --evaluate              # 3. score it
```

1. Finds every occurrence of the rules in `configs/rules.py` and picks up to `<n>` per
   rule. `--fnc` keeps only holes whose removal changes what the design does. This is
   slower, as it runs an equivalence check per candidate. `--model` is only used for its
   tokenizer, for the token statistics.
2. For a model that needs several nodes, run `sbatch slurm/inference/large/<model>.sh`
   instead.

**What the benchmark looks like.** Step 1 writes two files into each design folder:
`all_mask_idx.json` with every candidate, and `mask_idx.json` with the holes chosen. Both
map each rule to character spans in the source:

```json
{"continuous_assign": [[205, 228], [231, 248]], "always_construct": [[251, 356]]}
```

Step 2 writes one folder per hole under `<output_path>/<model>/<prompt>/`. Each one holds
the model's answer (`gen1.sv`), the design with the answer filled in (`full_gen1.sv`), and
`gen1.json` with the raw output, the reasoning and the finish reason. Step 3 writes
`generation_eqy_results.json` (per hole) and `summary_results.json` (per rule).

## Generate an agentic benchmark

**Input.** One folder per project, with its real source tree. Each project gets a
`ruc_project.json` with its top module and source order, which you can edit by hand.
Existing ones are never overwritten. Folders whose name starts with `_` are skipped. Use
one, such as `_lib/`, for code shared by several projects.

```
<dataset_path>/
├── _lib/                    shared code, filled by the dependency resolver
└── <project>/
    ├── ruc_project.json
    └── rtl/*.sv
```

**Generate, run and score:**

```
# once per dataset: write the manifests, then copy in the modules and headers the projects use from elsewhere
python3 -c "from benchmark.project_manifest import generate_manifests; generate_manifests('<dataset>', 'sv')"
python3 -m benchmark.dependency_resolver --dataset_path <dataset> --hdl sv --search_path <repo> --write

python3 src/run.py --model <model> --agentic --self --evaluate                       # check the dataset
python3 src/run.py --model <model> --max_tasks <n> [--fnc] --agentic --benchmark     # generate the benchmark
sbatch slurm/inference/agentic/<model>.sh                                            # run a model
python3 src/run.py --model <model> --agentic --evaluate                              # score it
```

- `--self --evaluate` proves each project equivalent to itself. A project that fails can
  never be scored, so fix or drop it before generating tasks.
- The shape of a task is set in the `agentic` block of `configs/ruc.yml`: `holes_per_task`,
  `min_files_per_task` and `min_rules_per_task`. With one hole per task, `<n>` is the
  number of tasks per rule. With more, it is the number of tasks per project.
- The `sbatch` step cannot go through `src/run.py`: Harbor has to start containers itself,
  so it runs on the host rather than inside one.

**What the benchmark looks like.** One [Harbor](https://github.com/harbor-framework/harbor)
task per bundle of holes, under `<output_path>/tasks/`:

```
<project>-bundle00/
├── task.toml          time budgets and the image to run in
├── instruction.md     the prompt given to the agent
├── environment/       the project with its holes removed, copied to /app for the agent
├── tests/test.sh      checks run after the agent finishes
└── solution/          the original sources and the hole positions, never shown to the agent
```

Each hole is marked in the sources the agent sees:

```systemverilog
  // RUC_HOLE_BEGIN id=h1
  // RUC_HOLE_END id=h1
```

The agent writes its code between the markers. A hole whose markers are deleted counts as
failed. Scoring writes the same result files as rule completion, plus how many tasks were
solved in full.

The same holes can also be scored without an agent. This turns the tasks into a
rule-completion benchmark. Point `dataset_path` at `<export>` and run steps 2 and 3 of the
rule-completion flow, without `--benchmark`:

```
python3 -m benchmark.export_rule_completion <output_path>/tasks --dataset_path <dataset> --output_path <export>
```

## Good to know

- **Resuming.** Relaunching inference resumes it: finished holes or tasks are skipped.
- **Timeouts.** An equivalence check that times out counts as a pass, unless
  `timeout_is_pass: False` is set. `equiv_method` in the results says how each proof
  ended: `proven`, `failed`, `timeout` or `error`.
- **New models.** Adapt the vLLM flags from the scripts in `slurm/`, such as parallelism, tool
  and reasoning parsers, and context length.
