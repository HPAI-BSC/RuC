#!/bin/bash
#SBATCH --account=bsc70
#SBATCH --qos=gp_debug
#SBATCH --output=slurm_output/job_%j.out
#SBATCH --error=slurm_output/job_%j.err
#SBATCH --nodes=1
#SBATCH --time=02:00:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=80


# ARG PARSING
while [[ $# -gt 0 ]]; do
    case $1 in
        --file)
            FILE="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# VALIDATION

if [[ -z "$FILE" ]]; then
    echo "Error: --file is required"
    exit 1
fi


export SLURM_CPU_BIND=none
export SRUN_CPUS_PER_TASK=${SLURM_CPUS_PER_TASK}
export USE_SYSTEM_NCCL=1

echo "START TIME: $(date)"

set -e

export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export HF_HUB_OFFLINE=1

module purge
module load singularity

export USE_CUDA=1 USE_CUDNN=1 USE_MKLDNN=0 USE_MKL=1 USE_TENSORRT=1  USE_XPU=0 MKL_THREADING=OMP

FILE_PATH="benchmark/${FILE}"
if [[ ! -f "$FILE_PATH" ]]; then
    echo "Error: file '$FILE' not found in dataset/"
    exit 1
fi

singularity exec --nv /gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/containers/inference_images/singularity-slc.sif bash -c "python3 -u ${FILE_PATH}"
echo "END TIME: $(date)"
