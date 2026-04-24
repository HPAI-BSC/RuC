#!/bin/bash
#SBATCH --account=bsc70
#SBATCH --qos=gp_bsccs
#SBATCH --output=slurm_output/job_%j.out
#SBATCH --error=slurm_output/job_%j.err
#SBATCH --nodes=1
#SBATCH --time=04:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=80


# CONFIGURATION PARAMETERS


MODEL_NAME=""
PROMPT_MODE="fim" # "fim" or "chat"
HDL="sv" # "sv" or "v"


# ARG PARSING
while [[ $# -gt 0 ]]; do
    case $1 in
        --model)
            MODEL_NAME="$2"
            shift 2
            ;;
        --prompt)
            PROMPT_MODE="$2"
            shift 2
            ;;
        --hdl)
            HDL="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# VALIDATION

if [[ "$HDL" != "sv" && "$HDL" != "v" ]]; then
    echo "Error: --type must be 'sv' or 'v'"
    exit 1
fi

if [[ "$PROMPT_MODE" != "fim" && "$PROMPT_MODE" != "chat" ]]; then
    echo "Error: --prompt must be 'fim' or 'chat'"
    exit 1
fi


MODEL_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/models/${MODEL_NAME}"
PRECISION="bfloat16"

#SIF_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/notsotiny-eval-slang.sif"
SIF_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/new-fixed-notsotiny-beta.sif" 

TASK="RuC-NST"
#TASK="RuC-CVE2-32k"
DATASET="tt-dataset-eqy-32k"
#DATASET="RuC-cve2_b72358c7-32k"
DATASET_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/datasets/${DATASET}"
BASE_OUTPUT_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/results/${TASK}"
OUTPUT_PATH="${BASE_OUTPUT_PATH}/${MODEL_NAME}/${PROMPT_MODE}"
#OUTPUT_PATH="${BASE_OUTPUT_PATH}/no_generations"


TENSOR_PARALLEL_SIZE=4
GPU_MEMORY_UTILIZATION=0.9
SWAP_SPACE=48
PATH_TEMPORARY_FILES="/dev/shm"

DEBUG="True"

export SLURM_CPU_BIND=none
export NUMEXPR_MAX_THREADS=80
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
export TRITON_LIBCUDA_PATH=/usr/local/cuda/compat/lib.real/libcuda.so
export VLLM_USE_V1=0
export VLLM_ATTENTION_BACKEND=XFORMERS
export VLLM_WORKER_MULTIPROC_METHOD=spawn

echo "START TIME: $(date)"

set -e

set +x
module purge
module load singularity
set -x


singularity exec --nv "${SIF_PATH}" \
    bash -c "
    python3 -u evaluation/eval_ruc.py \
        --model ${MODEL_PATH} \
        --hdl ${HDL} \
        --dataset_path ${DATASET_PATH} \
        --output_path ${OUTPUT_PATH} \
        --debug ${DEBUG} \
    "

echo "Stopping vLLM..."
kill ${VLLM_PID} || true
sleep 10
pkill -f vllm || true

echo "END TIME: $(date)"