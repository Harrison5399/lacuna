#!/bin/bash
#SBATCH --job-name=lacuna-literature
#SBATCH --account=r00998
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=48G
#SBATCH --time=12:00:00
#SBATCH --array=0-7
#SBATCH --output=lacuna-literature-%A_%a.out
#SBATCH --error=lacuna-literature-%A_%a.err
# Read an experiment's papers on GPUs (step 05), one slice per array task, into the pool's shared cache.
# From the lacuna folder:
#     sbatch bin/literature.slurm experiments/default.toml
# then, when every task is done, build the literature graph (light, fine on a login node):
#     python src/05-literature/run.py --experiment experiments/default.toml --graph-only \
#         --root /N/project/ontology_learning/L665/data/background --runs /N/project/ontology_learning/L665/runs \
#         --corpora /N/project/ontology_learning/L665/data
# Change --partition to the GPU partition `sinfo` lists (the H100s are on h100-multi). If spaCy can't find CUDA,
# add the CUDA module `module avail cuda` lists below the python one.
set -eo pipefail

EXPERIMENT=${1:-experiments/default.toml}
VENV=/N/project/ontology_learning/L665/envs/lacuna
ROOT=/N/project/ontology_learning/L665/data/background
RUNS=/N/project/ontology_learning/L665/runs
CORPORA=/N/project/ontology_learning/L665/data
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}"

module purge
module load gnu/12.2.0 python/3.12
source "$VENV/bin/activate"

python src/05-literature/run.py --experiment "$EXPERIMENT" --root "$ROOT" --runs "$RUNS" --corpora "$CORPORA" \
    --device gpu --task "$SLURM_ARRAY_TASK_ID" --tasks "$SLURM_ARRAY_TASK_COUNT"
