#!/bin/bash
# Rule-completion inference: serve the model with vLLM (Ray across nodes) and query it.
#SBATCH --account=<your_account>
#SBATCH --qos=<your_gpu_qos>
#SBATCH --output=slurm_output/job_%j.out
#SBATCH --error=slurm_output/job_%j.err
#SBATCH --nodes=1
#SBATCH --time=02:00:00
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=80
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive

# ============================================================================
# CONFIGURATION PARAMETERS
# ============================================================================

# Model
MODEL_NAME="Qwen3.8-27B"
MODEL_PATH="/path/to/models/${MODEL_NAME}"
MODEL_DTYPE="bfloat16"

PROMPT_MODE="chat" # "fim" or "chat"
HDL="sv" # "v" for Verilog, "sv" for SystemVerilog

# Container Configuration
SIF_PATH="/path/to/containers/vllm-0.19-prebuilt-compat-transformers.sif"

DATASET_PATH="/path/to/datasets/RuC-Opentitan-NonAgentic-final/"
BASE_OUTPUT_PATH="/path/to/results/RuC-Opentitan-NonAgentic-final/"
OUTPUT_PATH="${BASE_OUTPUT_PATH}/${MODEL_NAME}/${PROMPT_MODE}"

SEQUENCE_LENGTH_LIMIT=262000     # Context length (prompt + output)
MAX_TOKENS=48000                  # Output length, below SEQUENCE_LENGTH_LIMIT
MAX_NUM_SEQS=64                  # Concurrent requests

# Inference params (fixed)
TEMPERATURE=0.2
TOP_P=0.95
TOP_K=-1
BATCH_SIZE=64
DEBUG=True


# Other configuration values
TENSOR_PARALLEL_SIZE=4
PIPELINE_PARALLEL_SIZE=1
GPU_MEMORY_UTILIZATION=0.9
TIMEOUT_HEAD=600
TIMEOUT_WORKER=3600

# ============================================================================
# END OF CONFIGURATION
# ============================================================================

# Environment variables
export SLURM_CPU_BIND=none
export SRUN_CPUS_PER_TASK=80
export SLURM_GPUS_PER_TASK=4
export USE_SYSTEM_NCCL=1
export NCCL_IB_HCA="mlx5_0,mlx5_1,mlx5_4,mlx5_5"     # InfiniBand adapters of your nodes
export NUMEXPR_MAX_THREADS=80
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export HF_HUB_OFFLINE=1
export HF_HOME=/tmp/huggingface
export HF_HUB_CACHE=/tmp/huggingface/hub
export HF_MODULES_CACHE=/tmp/huggingface/modules
export TORCH_NCCL_ASYNC_ERROR_HANDLING=1
export TRITON_LIBCUDA_PATH=/.singularity.d/libs
export RAY_CGRAPH_get_timeout=600
export RAY_CGRAPH_submit_timeout=600

mkdir -p "$HF_HOME" "$HF_HUB_CACHE" "$HF_MODULES_CACHE"

echo "START TIME: $(date)"
set -e

set +x
module purge
module load singularity
set -x

ray_port=$(python3 -c "import socket; s = socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")
vllm_port=$(python3 -c "import socket; s = socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")

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

# Wait until vLLM answers on /health, or stop if it died
wait_for_vllm() {
    local host=$1
    local port=$2
    local timeout=$3
    local pid=$4
    local interval=10
    local elapsed=0

    echo "Waiting for vLLM to become ready at $host:$port (pid $pid)..."
    while true; do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null
            local rc=$?
            echo "ERROR: vLLM process $pid exited (status $rc) after ${elapsed}s."
            echo "       Look for the worker traceback above this line in the .err file."
            return 1
        fi

        if python3 -c "
import sys, urllib.request
try:
    with urllib.request.urlopen('http://$host:$port/health', timeout=2) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
            echo "vLLM service is up at $host:$port after ${elapsed}s."
            return 0
        fi

        sleep $interval
        elapsed=$((elapsed + interval))
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "ERROR: Timeout waiting for vLLM on $host:$port after ${elapsed}s."
            return 1
        fi
    done
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

# Start vLLM server. 
server_start_time=$(date +%s)
singularity exec --nv $SIF_PATH \
    bash -c "export VLLM_HOST_IP=$head_node_ip && vllm serve ${MODEL_PATH} \
        --served-model-name ${MODEL_NAME} \
        --host $head_node_ip \
        --port $vllm_port \
        --distributed-executor-backend ray \
        --trust-remote-code \
        --dtype ${MODEL_DTYPE} \
        --max-num-seqs ${MAX_NUM_SEQS} \
        --max-model-len ${SEQUENCE_LENGTH_LIMIT} \
        --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION} \
        --tensor-parallel-size ${TENSOR_PARALLEL_SIZE} \
        --pipeline-parallel-size ${PIPELINE_PARALLEL_SIZE} \
        --enable-prefix-caching \
        --enable-chunked-prefill \
        --enable-auto-tool-choice \
        --tool-call-parser qwen3_coder \
        --reasoning-parser qwen3 \
        --language-model-only" &
VLLM_PID=$!

# Wait for VLLM to be ready with error handling
if ! wait_for_vllm "$head_node_ip" "$vllm_port" "$TIMEOUT_WORKER" "$VLLM_PID"; then
    echo "ERROR: VLLM server failed to start"
    kill -9 $VLLM_PID 2>/dev/null || true
    exit 1
fi


# Relaunching resumes the run. Delete ${OUTPUT_PATH} to start over.
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
        --debug ${DEBUG} \
    "

# Cleanup with error handling
pkill -f "vllm serve" || echo "Warning: Failed to kill vLLM server process"
sleep 20

# Force cleanup of any zombie processes
killall -9 python3.10 2>/dev/null || true
sleep 5

# Check Ray cluster health
if ! singularity exec --nv $SIF_PATH bash -c "ray status" >/dev/null 2>&1; then
    echo "ERROR: Ray cluster appears unhealthy. Attempting to recover..."
fi

echo "END TIME: $(date)"
