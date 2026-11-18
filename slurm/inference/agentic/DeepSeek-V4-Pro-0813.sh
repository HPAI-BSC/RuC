#!/bin/bash
# Agentic inference: serve the model with vLLM and let OpenCode, run by Harbor, solve the tasks.
#SBATCH --account=<your_account>
#SBATCH --qos=<your_gpu_qos>
#SBATCH --output=slurm_output/job_%j.out
#SBATCH --error=slurm_output/job_%j.err
#SBATCH --nodes=8
#SBATCH --time=24:00:00
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=80
#SBATCH --ntasks-per-node=1
#SBATCH --exclusive

# ============================================================================
# CONFIGURATION PARAMETERS
# ============================================================================

# Model
MODEL_NAME="DeepSeek-V4-Pro-0813"
MODEL_ROOT="/path/to/models"
MODEL_PATH="${MODEL_ROOT}/${MODEL_NAME}"
MODEL_DTYPE="auto"

HDL="sv" # "v" for Verilog, "sv" for SystemVerilog

# Name vLLM serves the model under
SERVED_MODEL_NAME="${MODEL_PATH}"

# OpenCode provider for a vLLM server
HARBOR_PROVIDER="vllm-local"

# Container Configuration
SIF_PATH="/path/to/containers/vllm-0.26-prebuilt-compat-cu13.sif"

# Image the agent runs in, built from ruc-sim-opencode.def
BASE_CONTAINER="/path/to/containers/ruc-sim-opencode.sif"

# Harbor built by third_party/harbor/setup_harbor.sh (submit from the repository root)
HARBOR_PYTHON="${SLURM_SUBMIT_DIR}/third_party/harbor/.venv/bin/python"
HARBOR_SRC="${SLURM_SUBMIT_DIR}/third_party/harbor/harbor-src/src"

# RuC paths
BASE_OUTPUT_PATH="/path/to/results/RuC-Opentitan-Agentic-final"
TASKS_PATH="${BASE_OUTPUT_PATH}/tasks"
OUTPUT_PATH="${BASE_OUTPUT_PATH}/${MODEL_NAME}/agent"

# Agent params
AGENT="opencode"
CONCURRENCY=16                  # Tasks Harbor runs at a time
AGENT_TIMEOUT_S=1800            # Time budget of one bundle
VERIFIER_TIMEOUT_S=600          # Time budget of one bundle's tests
HARBOR_OVERHEAD_S=1800          # Slack before the harness itself is killed
DEBUG=True


# Sampling params
TEMPERATURE=0.2
TOP_P=0.95
TOP_K=-1
GEN_CONFIG="{\"temperature\": ${TEMPERATURE}, \"top_p\": ${TOP_P}, \"top_k\": ${TOP_K}}"

# Other configuration values, mostly fixed
TENSOR_PARALLEL_SIZE=4
DATA_PARALLEL_SIZE=8            # Shard size must be multiple of 128 (r = wq.size(0) // g)
#PIPELINE_PARALLEL_SIZE=1
GPU_MEMORY_UTILIZATION=0.9
MAX_NUM_SEQS=32
MAX_MODEL_LEN=200000            # Max reasoning requires 393216 max-model-len
OUTPUT_LIMIT=64000 
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
start_time=$(date +%s)

set -e

set +x
module purge
module load singularity
set -x

ray_port=$(( 20000 + (SLURM_JOB_ID % 9000) ))
vllm_port=$(( ray_port + 150 ))


# Getting the node names
nodes=$(scontrol show hostnames "$SLURM_JOB_NODELIST")
nodes_array=($nodes)

head_node=${nodes_array[0]}
head_node_ip=$(srun --nodes=1 --ntasks=1 -w "$head_node" hostname --ip-address)

IFACE_NAME=$(ip -o -4 addr show | awk -v ip="$head_node_ip" '$4 ~ ip"/" {print $2; exit}')
echo "Using interface: $IFACE_NAME"

export GLOO_SOCKET_IFNAME=$IFACE_NAME
export NCCL_SOCKET_IFNAME=$IFACE_NAME

ip_head=$head_node_ip:$ray_port
export ip_head
echo "IP Head: $ip_head"

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
    local interval=10
    local elapsed=0
    echo "Waiting for vLLM to become ready at $host:$port..."
    while ! python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(('$host', $port))
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; do
        sleep $interval
        elapsed=$((elapsed + interval))
        if [ "$elapsed" -ge "$timeout" ]; then
            echo "ERROR: Timeout waiting for vLLM on $host:$port."
            return 1
        fi
    done
    echo "vLLM service is up at $host:$port."
    return 0
}

# Start the head node
srun --nodes=1 --ntasks=1 -w "$head_node" \
    singularity exec --nv $SIF_PATH \
    bash -c "export VLLM_HOST_IP=$head_node_ip && ray start --head --node-ip-address='$head_node_ip' --port=$ray_port \
    --num-cpus '${SLURM_CPUS_PER_TASK}' --num-gpus '${SLURM_GPUS_PER_TASK}' --block" &

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

    wait_for_ray "$node_i" "$TIMEOUT_WORKER"
    singularity exec --nv $SIF_PATH bash -c "ray status"
done

echo "Final Ray cluster status:"
singularity exec --nv $SIF_PATH bash -c "ray status"

# DP: Specify "--data-parallel-backend ray" or vLLM ignores the Ray cluster, 
# "--data-parallel-size-local" defaults to full DP size

# DeepSeek V4 has no Jinja chat template. reasoning_effort defaults to "low" when unset
CHAT_KWARGS='{"thinking": true, "reasoning_effort": "high"}'

# ----------------------------------------------------------------------------
# Start vLLM server.
# ----------------------------------------------------------------------------
singularity exec --nv \
    $SIF_PATH \
    bash -c "export VLLM_HOST_IP=$head_node_ip && vllm serve ${MODEL_PATH} \
        --served-model-name ${SERVED_MODEL_NAME} \
        --host $head_node_ip \
        --port $vllm_port \
        --distributed-executor-backend ray \
        --trust-remote-code \
        --override-generation-config '${GEN_CONFIG}' \
        --dtype ${MODEL_DTYPE} \
        --kv-cache-dtype fp8 \
        --max-num-seqs ${MAX_NUM_SEQS} \
        --max-model-len ${MAX_MODEL_LEN} \
        --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION} \
        --tensor-parallel-size ${TENSOR_PARALLEL_SIZE} \
        --data-parallel-size ${DATA_PARALLEL_SIZE} \
        --data-parallel-backend ray \
        --data-parallel-size-local 1 \
        --enable-expert-parallel \
        --enable-prefix-caching \
        --enable-chunked-prefill \
        --no-scheduler-reserve-full-isl \
        --no-enable-flashinfer-autotune \
        --enable-auto-tool-choice \
        --tokenizer-mode deepseek_v4 \
        --tool-call-parser deepseek_v4 \
        --reasoning-parser deepseek_v4 \
        --default-chat-template-kwargs '${CHAT_KWARGS}'" &
VLLM_PID=$!

if ! wait_for_vllm "$head_node_ip" "$vllm_port" "$TIMEOUT_WORKER"; then
    echo "ERROR: vLLM server failed to start"
    kill -9 $VLLM_PID 2>/dev/null || true
    exit 1
fi

# ----------------------------------------------------------------------------
# Run the agent on every task. Runs on the host, as Harbor starts the containers.
# ----------------------------------------------------------------------------
export OPENAI_STREAM=false
export OPENAI_USE_RESPONSES_API=false
export OPENAI_DISABLE_RESPONSES_API=1
export OPENAI_API_KEY="EMPTY"
export OPENAI_BASE_URL="http://${head_node_ip}:${vllm_port}/v1"
export PYTHONPATH="${HARBOR_SRC}"

# Relaunching resumes the run. Delete ${OUTPUT_PATH}/harbor/run to start over.

python3 -u -m inference.generate_agent \
    --model "${MODEL_PATH}" \
    --model_name "${MODEL_NAME}" \
    --tasks_path "${TASKS_PATH}" \
    --output_path "${OUTPUT_PATH}" \
    --stage all \
    --harness harbor.cli.main \
    --harbor_python "${HARBOR_PYTHON}" \
    --harbor_src "${HARBOR_SRC}" \
    --agent "${AGENT}" \
    --executor singularity \
    --provider "${HARBOR_PROVIDER}" \
    --concurrency ${CONCURRENCY} \
    --agent_timeout_s ${AGENT_TIMEOUT_S} \
    --verifier_timeout_s ${VERIFIER_TIMEOUT_S} \
    --harbor_overhead_s ${HARBOR_OVERHEAD_S} \
    --ip ${head_node_ip} \
    --port ${vllm_port} \
    --context_limit ${MAX_MODEL_LEN} \
    --output_limit ${OUTPUT_LIMIT} \
    --hdl ${HDL} \
    --debug ${DEBUG}

echo ""
echo "============================================"
echo "Agentic inference completed. Shutting down vLLM..."
echo "============================================"

kill -TERM $VLLM_PID 2>/dev/null || true
sleep 10
if kill -0 $VLLM_PID 2>/dev/null; then
    kill -KILL $VLLM_PID 2>/dev/null || true
fi
wait $VLLM_PID 2>/dev/null || true

pkill -f "vllm serve" || echo "Warning: Failed to kill vLLM server process"
sleep 20

if ! singularity exec --nv $SIF_PATH bash -c "ray status" >/dev/null 2>&1; then
    echo "ERROR: Ray cluster appears unhealthy."
fi

end_time=$(date +%s)
elapsed=$(( end_time - start_time ))
printf 'Total elapsed time: %02dh:%02dm:%02ds\n' $((elapsed/3600)) $(((elapsed%3600)/60)) $((elapsed%60))
echo "END TIME: $(date)"
