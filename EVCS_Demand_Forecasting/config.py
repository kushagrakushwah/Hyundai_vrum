# config.py

# =========================================================================
# 1. DATASET FEATURE CONFIGURATION FLAGS
# =========================================================================
# Control which feature groups are generated and appended to snapshot.x

USE_LAGS = True               # Include temporal demand lag features (lag1, lag2, lag3)
USE_ROLLING = False           # Redundant for recurrent GNNs (handled by GRU memory)
USE_GROWTH = False            # Redundant and introduces extreme outlier scaling distortions
USE_SEASONAL = True           # Helpful cyclical Month Sin/Cos encodings
USE_YEAR = False              # Monotonically increasing year leads to bad extrapolation
USE_CHARGER_AGE = True        # Station age in months (continuous temporal cue)
USE_EV_REGISTRATIONS = False  # Mandal-level new EV registrations
USE_POI = True                # Points of interest counts (amenities, shops, tourist sites)
USE_POPULATION = False        # Mandal population density
USE_GRAPH_FEATURES = True     # GPS coordinates (latitude, longitude) and Govt/Private ownership

# =========================================================================
# 2. TEMPORAL FORECASTING SPLITS
# =========================================================================
TRAIN_MONTHS = 26             # Default 26-5-5 split (Months 1-26)
VAL_MONTHS = 5                # Validation split (Months 27-31)
TEST_MONTHS = 5               # Testing split (Months 32-36)

# =========================================================================
# 3. MODEL & TRAINING HYPERPARAMETERS
# =========================================================================
HIDDEN_DIM = 256              # Hidden dimension of ROLAND GRU cell and convolutions
LR = 0.0005                   # Initial learning rate
WEIGHT_DECAY = 5e-4           # Adam optimizer weight decay
EPOCHS = 200                  # Maximum training epochs
PATIENCE = 40                 # Early stopping patience
GRAD_CLIPPING = 5.0           # Gradient clipping value to prevent exploding gradients
USE_SCHEDULER = True          # Use ReduceLROnPlateau learning rate scheduler
OPTIMIZER_NAME = "Adam"       # Optimizer choice
DEVICE = "cpu"                # Device selection: 'cpu', 'cuda'
CHECKPOINT_DIR = "checkpoints"

# Loss Function Settings
LOSS_FUNCTION = "mse"          # Standard Mean Squared Error Loss (Official Final Model)

