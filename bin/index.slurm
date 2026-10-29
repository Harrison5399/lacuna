#!/bin/bash
#SBATCH --job-name=lacuna-index
#SBATCH --account=r00998
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --output=lacuna-index-%j.out
#SBATCH --error=lacuna-index-%j.err
# Build the fuzzy-match index for every experiment's linker dictionary. Run bin/setup.sh first.
#     sbatch bin/index.slurm          (from the lacuna folder)
set -eo pipefail

VENV=/N/project/ontology_learning/L665/envs/lacuna
DATA=/N/project/ontology_learning/L665/data/background
cd "${SLURM_SUBMIT_DIR:-$(dirname "$0")/..}"

module purge
module load gnu/12.2.0 python/3.12
source "$VENV/bin/activate"

for experiment in experiments/*.toml; do
    python src/00-setup/run.py --experiment "$experiment" --root "$DATA" --processes "$SLURM_CPUS_PER_TASK"
done
