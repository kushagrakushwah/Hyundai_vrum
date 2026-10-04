# Statewide Dynamic EVCS Demand Forecasting (Stanford ROLAND GNN)

## 1. Project Overview

This project presents a dynamic Spatio-Temporal Graph Neural Network (GNN) framework for statewide Electric Vehicle Charging Station (EVCS) demand forecasting across all 33 districts of Telangana, India (934 charging station nodes).

The canonical implementation is derived directly from `Untitled.ipynb` (Section: *Final ROLAND Training - Best Feature Configuration*).

Key methodological components:
- **Dynamic Node Activation**: Accounts for continuous station commissioning over 36 monthly graph snapshots (January 2023 – December 2025) using active node masking.
- **District-Wise Spatial Connectivity**: Uses district-wise complete subgraphs to eliminate geographic cross-talk while maintaining administrative boundaries.
- **Stanford ROLAND Architecture**: Combines `ResidualEdgeConv` for spatial message passing with `GRUUpdater` for recurrent state transitions across monthly snapshots.
- **Pre-trained Checkpoints Included**: Includes canonical checkpoints `roland_final_submission_80_02.pt` and `roland_final_weights_80_02.pt` in `final_model/` for immediate inference without retraining.

---

## 2. Directory & Folder Structure

```
EVCS_Demand_Forecasting/
│
├── README.md                              # Submission documentation
├── requirements.txt                       # Python dependencies
├── config.py                              # Configuration parameters & feature flags
│
├── data/                                  # Data directory
├── processed/                             # Precomputed graph & feature artifacts
│   ├── nodes_master.csv                   # Master node metadata (934 nodes)
│   ├── node_static_features.csv           # Yearly population density
│   ├── node_poi_features.csv              # Aggregated Points-of-Interest counts
│   ├── node_dynamic_features.csv          # Monthly dynamic feature sequences
│   └── monthly_snapshots/                 # 36 monthly PyG graph snapshot CSVs
│
├── models/                                # Model architecture implementation
│   ├── __init__.py
│   ├── roland_layers.py                   # ResidualEdgeConv & GRUUpdater
│   └── roland_model.py                    # ROLANDModel wrapper
│
├── training/                              # Training & loss routines
│   ├── __init__.py
│   ├── losses.py                          # Loss function module
│   ├── trainer.py                         # Metric evaluation calculators
│   └── trainer_temporal.py                # ROLANDTemporalTrainer sequence engine
│
├── utils/                                 # Utility & dataset modules
│   ├── __init__.py
│   ├── dataset.py                         # TemporalEVCSDataset & scaling
│   └── loader.py                          # Graph loading utilities
│
├── final_model/                           # Pre-trained canonical checkpoints
│   ├── roland_final_submission_80_02.pt   # Complete submission checkpoint
│   └── roland_final_weights_80_02.pt      # Canonical model state dict weights
│
└── notebooks/                             # Demonstration notebook
    └── Final_Demo.ipynb                   # Primary evaluation & inference notebook
```

---

## 3. Environment & Dependencies

- **Python Version**: Python 3.10, 3.11, or 3.12.
- **Core Dependencies**:
  - `torch >= 2.0.0`
  - `torch-geometric >= 2.3.0`
  - `pandas >= 1.5.0`
  - `numpy >= 1.23.0`
  - `scipy >= 1.9.0`
  - `matplotlib >= 3.6.0`
  - `seaborn >= 0.12.0`

### Installation Command:
```bash
pip install -r requirements.txt
```

---

## 4. How to Run Inference (Default Workflow)

1. Extract the submission ZIP archive `EVCS_Demand_Forecasting.zip`.
2. Ensure dependencies are installed in your Python environment.
3. Open `notebooks/Final_Demo.ipynb` in Jupyter Notebook or JupyterLab:
   ```bash
   cd notebooks
   jupyter notebook Final_Demo.ipynb
   ```
4. Execute all cells sequentially (`Cell -> Run All`).
5. The notebook will automatically:
   - Load parameters from `config.py`.
   - Load the 36 monthly dynamic graph snapshots from `processed/`.
   - Zero-mask spatial coordinates (`snap.x[:, 0:2] = 0.0`).
   - Construct the exact 256-dim ROLAND architecture.
   - Load pre-trained weights from `final_model/roland_final_weights_80_02.pt` (or `roland_final_submission_80_02.pt`).
   - Execute chronological sequence replay over test set (Months 32-36) and print evaluation metrics.

---

## 5. Optional Retraining Workflow

Training code is fully preserved. To run optional retraining from scratch:
```python
from training.trainer_temporal import ROLANDTemporalTrainer
from models.roland_model import ROLANDModel
# Initialize model and trainer, then call trainer.fit_and_evaluate(dataset)
```

---

## 6. Reproducibility & Integrity Guarantee

The package defaults to loading pre-trained checkpoints from `final_model/`. Pre-trained checkpoints match the constructed model architecture (`in_channels=52`, `hidden_channels=512`, `out_channels=256`) with zero shape mismatches.
