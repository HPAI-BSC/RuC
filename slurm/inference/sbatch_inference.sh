#!/bin/bash
#SBATCH --account=bsc70
#SBATCH --qos=acc_debug
#SBATCH --output=slurm_output/job_%j.out
#SBATCH --error=slurm_output/job_%j.err
#SBATCH --nodes=1
#SBATCH --time=02:00:00
#SBATCH --gres=gpu:4
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=80
#SBATCH --exclusive


MODEL_NAME=""
PROMPT_MODE="fim" # "fim" or "chat"
#FIM_MODE="psm" # "psm" or "spm"


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
        # --fim_type)
        #     FIM_MODE="$2"
        #     shift 2
        #     ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# VALIDATION

if [[ "$PROMPT_MODE" != "fim" && "$PROMPT_MODE" != "chat" ]]; then
    echo "Error: --prompt must be 'fim' or 'chat'"
    exit 1
fi

# if [[ "$PROMPT_MODE" == "fim" ]]; then
#     if [[ "$FIM_MODE" != "psm" && "$FIM_MODE" != "spm" ]]; then
#         echo "Error: --fim_type must be 'psm' or 'spm'"
#         exit 1
#     fi
# fi

# PATHS AND CONFIG
MODEL_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/models/${MODEL_NAME}"
PRECISION="bfloat16"

SIF_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/vllm-openai-0.10.1-k2.sif" 

#TASK="RuC-NST"
TASK="RuC-CVE2-32k"
#DATASET="tt-dataset-eqy-32k"
DATASET="RuC-cve2_b72358c7-32k"
HDL="sv"
DATASET_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/datasets/${DATASET}"
BASE_OUTPUT_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/results/${TASK}"
OUTPUT_PATH="${BASE_OUTPUT_PATH}/${MODEL_NAME}/${PROMPT_MODE}"
#OUTPUT_PATH="${BASE_OUTPUT_PATH}/em_test"
#SHUTTLES=("tt07")


SEQUENCE_LENGTH_LIMIT=32768
MAX_TOKENS=2048

TEMPERATURE=0.2
TOP_P=0.95
TOP_K=-1
BATCH_SIZE=64

TENSOR_PARALLEL_SIZE=4
GPU_MEMORY_UTILIZATION=0.85
SWAP_SPACE=16
PATH_TEMPORARY_FILES="/dev/shm"


export SLURM_CPU_BIND=none
export NUMEXPR_MAX_THREADS=80
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
export TRITON_LIBCUDA_PATH=/usr/local/cuda/compat/lib.real/libcuda.so
export VLLM_USE_V1=1
export VLLM_ATTENTION_BACKEND=FLASH_ATTN #XFORMERS #FLASH_ATTN 
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export VLLM_USE_CUDA_GRAPH=0

echo "START TIME: $(date)"

set -e

set +x
module purge
module load singularity
set -x


#vllm_port=$(python3 -c "import socket; s = socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")
#host_ip=$(hostname --ip-address)


singularity exec --nv "${SIF_PATH}" \
    bash -c "
    python3 -u -m inference/generate.py \
        --model ${MODEL_PATH} \
        --dataset_path ${DATASET_PATH} \
        --max_tokens ${MAX_TOKENS} \
        --sequence_length_limit ${SEQUENCE_LENGTH_LIMIT} \
        --temperature ${TEMPERATURE} \
        --top_p ${TOP_P} \
        --top_k ${TOP_K} \
        --batch_size ${BATCH_SIZE} \
        --output_path ${OUTPUT_PATH} \
        --gpu_memory_utilization ${GPU_MEMORY_UTILIZATION} \
        --swap_space ${SWAP_SPACE} \
        --precision ${PRECISION} \
        --tensor_parallel_size ${TENSOR_PARALLEL_SIZE} \
        --prompt_mode ${PROMPT_MODE} \
        --hdl ${HDL} \
    "
        # --shuttles ${SHUTTLES[@]} \
        # --fim_mode ${FIM_MODE} \
        
echo "Stopping vLLM..."
kill ${VLLM_PID} || true
sleep 10
pkill -f vllm || true

echo "END TIME: $(date)"
