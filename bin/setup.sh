#!/bin/bash
# Set up Lacuna on Quartz: the venv, every package in requirements.txt, the MeSH / HGNC files and tables,
# and each experiment's linker dictionary. Each step is skipped when it is already done. Run it from a
# login node, from anywhere:
#     bash bin/setup.sh
# The fuzzy-match index is too heavy for a login node; build it afterwards with: sbatch bin/index.slurm
set -eo pipefail

VENV=/N/project/ontology_learning/L665/envs/lacuna
DATA=/N/project/ontology_learning/L665/data/background
cd "$(dirname "$0")/.."

module purge
module load gnu/12.2.0 python/3.12

if [ -x "$VENV/bin/python" ]; then
    echo "venv already at $VENV"
else
    echo "making venv at $VENV"
    python -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
fi
source "$VENV/bin/activate"

if python -c "import click, scispacy, spacy_transformers, cupy, duckdb, pyarrow, fastcoref, en_core_sci_scibert" 2>/dev/null; then
    echo "packages already installed"
else
    pip install -r requirements.txt
fi

python -c "import spacy; print('en_core_sci_scibert loads:', spacy.load('en_core_sci_scibert').pipe_names)"
# The coreference model (src/pipelines/biomed/defaults.toml) is fetched here, in case compute nodes have no internet.
python -c "from huggingface_hub import snapshot_download; print('coreference model at', snapshot_download('biu-nlp/f-coref'))"
for experiment in experiments/*.toml; do
    python src/00-setup/run.py --experiment "$experiment" --root "$DATA" --no-index
done
