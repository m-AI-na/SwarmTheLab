#!/usr/bin/env python3
"""Build the Module 2 dataset: a small lung adenocarcinoma subset of the single-cell lung cancer atlas.

Source: "The single-cell lung cancer atlas (LuCA) -- extended atlas" (Salcher et al., Cancer Cell 2022),
read from CELLxGENE Census at a pinned release so every rebuild gives the same cells.

The subset keeps raw counts (what Scanpy's preprocessing tutorial, and so the Scanpy paper agent, expects),
uses gene symbols as names (so CD276 can be found by name), and keeps the authors' cell-type labels,
including "malignant cell", so malignant and non-malignant cells can be compared.

Run it in the workshop environment (takes a few minutes and downloads a few hundred MB):

    conda activate swarm-lab
    python tools/make_workshop_dataset.py ~/workshop/data/workshop.h5ad
"""
import argparse
from pathlib import Path

import numpy as np

CENSUS_VERSION = "2025-11-08"
DATASET_ID = "1e6a6ef9-7ec9-4c90-bbfb-2ad3c3165fd1"   # LuCA extended atlas
N_MALIGNANT, N_OTHER, SEED = 4_000, 16_000, 2026
OBS_COLUMNS = ["dataset_id", "donor_id", "cell_type", "tissue", "tissue_general", "disease",
               "assay", "sex", "development_stage", "is_primary_data"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    import cellxgene_census

    with cellxgene_census.open_soma(census_version=CENSUS_VERSION) as census:
        human = census["census_data"]["homo_sapiens"]
        obs = human.obs.read(
            value_filter=f"dataset_id == '{DATASET_ID}' and disease == 'lung adenocarcinoma' and is_primary_data == True",
            column_names=["soma_joinid", "cell_type"]).concat().to_pandas()
        rng = np.random.default_rng(SEED)
        malignant = obs.loc[obs["cell_type"].astype(str) == "malignant cell", "soma_joinid"].to_numpy()
        other = obs.loc[obs["cell_type"].astype(str) != "malignant cell", "soma_joinid"].to_numpy()
        keep = np.sort(np.concatenate([rng.choice(malignant, min(N_MALIGNANT, len(malignant)), replace=False),
                                       rng.choice(other, min(N_OTHER, len(other)), replace=False)]))
        print(f"Selected {len(keep):,} of {len(obs):,} LUAD cells ({min(N_MALIGNANT, len(malignant)):,} malignant).")
        adata = cellxgene_census.get_anndata(census, organism="Homo sapiens", obs_coords=keep,
                                             obs_column_names=OBS_COLUMNS)

    # Gene symbols as names; Ensembl IDs stay in var["feature_id"].
    adata.var_names = adata.var["feature_name"].astype(str)
    adata.var_names_make_unique()
    adata.var.index.name = None
    # Drop genes with no counts in these cells, to keep the file small.
    counts = np.asarray((adata.X > 0).sum(axis=0)).ravel()
    adata = adata[:, counts > 0].copy()
    for col in adata.obs.columns:
        if adata.obs[col].dtype == object:
            adata.obs[col] = adata.obs[col].astype("category")
    adata.obs_names = adata.obs_names.astype(str)
    adata.uns["workshop_source"] = {
        "census_version": CENSUS_VERSION, "dataset_id": DATASET_ID,
        "title": "The single-cell lung cancer atlas (LuCA) -- extended atlas",
        "citation": "Salcher S, et al. High-resolution single-cell atlas reveals diversity and plasticity of "
                    "tissue-resident neutrophils in non-small cell lung cancer. Cancer Cell 2022.",
        "selection": f"lung adenocarcinoma, primary data; {N_MALIGNANT} malignant + {N_OTHER} other cells, seed {SEED}",
        "X": "raw counts",
    }
    assert "CD276" in adata.var_names, "CD276 is missing from the subset"
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(args.destination, compression="gzip")
    print(f"Wrote {args.destination}: {adata.n_obs:,} cells x {adata.n_vars:,} genes, "
          f"{(adata[:, 'CD276'].X > 0).sum():,} cells with CD276 counts.")


if __name__ == "__main__":
    main()
