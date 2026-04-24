#!/bin/bash

#SBATCH --account=bsc70
#SBATCH --qos=acc_bsccs
#SBATCH --output=slurm_output/job_%j.out
#SBATCH --error=slurm_output/job_%j.err
#SBATCH --nodes=8
#SBATCH --time=04:00:00
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=80
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive


MODEL_NAME="Qwen3-Coder-480B-A35B-Instruct"
MODEL_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/models/${MODEL_NAME}"
PRECISION="bfloat16"

PROMPT_MODE="fim" # "fim" or "chat"
HDL="sv" # "v" for Verilog, "sv" for SystemVerilog

SIF_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/vllm-openai-0.8.5.sif"

DATASET_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/datasets/RuC-cve2_b72358c7-32k"
BASE_OUTPUT_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/results/RuC-CVE2-32k"
OUTPUT_PATH="${BASE_OUTPUT_PATH}/${MODEL_NAME}/${PROMPT_MODE}"
 
SEQUENCE_LENGTH_LIMIT=32768     # Model context length (controls prompt+output)
MAX_TOKENS=2048                 # Generation length (controls output length; must be smaller than context length!)
MAX_NUM_SEQS=64                 # This controls the number of requests the model runs concurrently


TEMPERATURE=0.2
TOP_P=0.95
TOP_K=-1
BATCH_SIZE=64

TENSOR_PARALLEL_SIZE=4                          # Parallelism: Each MN5 node has 4 GPUs
PIPELINE_PARALLEL_SIZE=$SLURM_JOB_NUM_NODES     # Parallelism: Split the model across all nodes we request
GPU_MEMORY_UTILIZATION=0.9
SWAP_SPACE=48
PATH_TEMPORARY_FILES="/dev/shm"


# Environment variables
export SLURM_CPU_BIND=none
export SRUN_CPUS_PER_TASK=80
export SLURM_GPUS_PER_TASK=4
export USE_SYSTEM_NCCL=1
export NCCL_IB_HCA="mlx5_0,mlx5_1,mlx5_4,mlx5_5"
# export NCCL_IB_DISABLE=1
# export NCCL_P2P_DISABLE=1
# export VLLM_USE_RAY_COMPILED_DAG_CHANNEL_TYPE="shm"
export NUMEXPR_MAX_THREADS=80
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
export TRITON_LIBCUDA_PATH=/usr/local/cuda/compat/lib.real/libcuda.so
export RAY_CGRAPH_get_timeout=800
export RAY_CGRAPH_submit_timeout=800
export VLLM_USE_V1=0
# export VLLM_ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND}"


echo "START TIME: $(date)"

set -e

set +x
module purge
module load singularity

set -euo pipefail
set -x

# Show vLLM version for debugging
singularity exec --nv $SIF_PATH bash -c "vllm --version"

SRUN_ARGS="--wait=60 --kill-on-bad-exit=1"

ray_port=$(python3 -c "import socket; s = socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")
vllm_port=$(python3 -c "import socket; s = socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")

# Timeout settings (in seconds)
TIMEOUT_HEAD=300
TIMEOUT_WORKER=800

# Getting the node names
nodes=$(scontrol show hostnames "$SLURM_JOB_NODELIST")
nodes_array=($nodes)

head_node=${nodes_array[0]}
head_node_ip=$(srun --nodes=1 --ntasks=1 -w "$head_node" hostname --ip-address)

ip_head=$head_node_ip:$ray_port
export ip_head
echo "IP Head: $ip_head"

# For head node
export VLLM_HOST_IP=$head_node_ip
echo "Starting HEAD at $head_node"

# Function to poll for Ray service readiness
wait_for_ray() {
    local node=$1
    local sleep_interval=10
    local timeout=$2
    local elapsed=0
    while true; do
        if singularity exec --nv "$SIF_PATH" bash -c "ray status" &>/dev/null; then
            echo "Ray service is up on $node."
            break
        fi
        sleep $sleep_interval
        elapsed=$((elapsed + sleep_interval))
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "Timeout waiting for Ray service on $node."
            exit 1
        fi
    done
}

# Function to poll for VLLM readiness using netcat
wait_for_vllm() {
    local host=$1
    local port=$2
    local timeout=$3
    local interval=10
    local elapsed=0
    while ! nc -z "$host" "$port"; do
        echo "Waiting for VLLM to be up at $host:$port..."
        sleep $interval
        elapsed=$((elapsed + interval))
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "Timeout waiting for VLLM service on $host:$port."
            exit 1
        fi
    done
    echo "VLLM service is up at $host:$port."
}

# Start the head node
srun --nodes=1 --ntasks=1 -w "$head_node" \
    singularity exec --nv $SIF_PATH \
    bash -c "export VLLM_HOST_IP=$head_node_ip && ray start --head --node-ip-address='$head_node_ip' --port=$ray_port \
    --num-cpus '${SLURM_CPUS_PER_TASK}' --num-gpus '${SLURM_GPUS_PER_TASK}' --block" &

# Wait for head node to be ready
wait_for_ray "$head_node" "$TIMEOUT_HEAD"
singularity exec --nv $SIF_PATH bash -c "ray status"

# Start worker nodes
worker_num=$((SLURM_JOB_NUM_NODES - 1))

for ((i = 1; i <= worker_num; i++)); do
    node_i=${nodes_array[$i]}
    echo "Starting WORKER $i at $node_i"
    node_ip=$(srun --nodes=1 --ntasks=1 -w "$node_i" hostname --ip-address)
    echo "Node ip $node_ip"
    srun --nodes=1 --ntasks=1 -w "$node_i" \
        singularity exec --nv $SIF_PATH \
        bash -c "export VLLM_HOST_IP=$node_ip && ray start --address '$ip_head' \
        --num-cpus '${SLURM_CPUS_PER_TASK}' --num-gpus '${SLURM_GPUS_PER_TASK}' --block" &

    # Poll until worker has successfully joined Ray cluster
    wait_for_ray "$node_i" "$TIMEOUT_WORKER"
    singularity exec --nv $SIF_PATH bash -c "ray status"
done

# Final cluster status check
echo "Final Ray cluster status:"
singularity exec --nv $SIF_PATH bash -c "ray status"


# Start vLLM server
server_start_time=$(date +%s)
singularity exec --nv $SIF_PATH \
    bash -c "export VLLM_USE_V1=$VLLM_USE_V1 && export VLLM_HOST_IP=$head_node_ip && export VLLM_CUDA_MEM_ALIGN_KV_CACHE=1 && vllm serve ${MODEL_PATH} \
        --served-model-name ${MODEL_NAME} \
        --host $head_node_ip \
        --port $vllm_port \
        --distributed-executor-backend ray \
        --enable-expert-parallel \
        --trust-remote-code \
        --dtype ${PRECISION} \
        --swap-space ${SWAP_SPACE} \
	    --max-num-seqs ${MAX_NUM_SEQS} \
        --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION} \
        --tensor-parallel-size ${TENSOR_PARALLEL_SIZE} \
        --pipeline-parallel-size ${PIPELINE_PARALLEL_SIZE} &"

# Wait for VLLM to be ready with error handling
if ! wait_for_vllm "$head_node_ip" "$vllm_port" "$TIMEOUT_WORKER"; then
    echo "ERROR: VLLM server failed to start"
    continue
fi


singularity exec --nv "${SIF_PATH}" \
    bash -c "python3 -u -m inference.generate_ray \
        --model_name ${MODEL_NAME} \
        --model_path ${MODEL_PATH} \
        --ip ${head_node_ip} \
        --port ${vllm_port} \
        --dataset_path ${DATASET_PATH} \
        --max_tokens ${MAX_TOKENS} \
        --sequence_length_limit ${SEQUENCE_LENGTH_LIMIT} \
        --temperature ${TEMPERATURE} \
        --top_p ${TOP_P} \
        --top_k ${TOP_K} \
        --batch_size ${BATCH_SIZE} \
        --output_path ${OUTPUT_PATH} \
        --prompt_mode ${PROMPT_MODE} \
        --hdl ${HDL} \
    "


# Cleanup with error handling
pkill -f "vllm serve" || echo "Warning: Failed to kill vLLM server process"
sleep 20  # Increased wait time for better cleanup

# Force cleanup of any zombie processes
killall -9 python3.10 2>/dev/null || true
sleep 5

# Check Ray cluster health
if ! singularity exec --nv $SIF_PATH bash -c "ray status" >/dev/null 2>&1; then
    echo "ERROR: Ray cluster appears unhealthy. Attempting to recover..."
    # Could add Ray cluster recovery logic here if needed
fi

echo "END TIME: $(date)"
