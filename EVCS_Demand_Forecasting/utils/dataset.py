import os
import glob
import pandas as pd
import numpy as np
import torch
from torch_geometric.data import Data
from typing import List, Tuple, Dict, Any, Optional
import config

def clean_numeric(val) -> float:
    """
    Cleans and converts values to float. Handles NaN, empty strings,
    and additive expressions like '50+50+22'.
    """
    if pd.isna(val):
        return np.nan
    val_str = str(val).strip()
    if not val_str:
        return np.nan
    if '+' in val_str:
        try:
            return float(sum(float(x.strip()) for x in val_str.split('+')))
        except ValueError:
            pass
    try:
        return float(val_str)
    except ValueError:
        return np.nan

class FeatureScaler:
    """
    Fits and applies standardization (Z-score normalization) to numerical features.
    Standardization is performed only on active node-month observations to prevent padding bias.
    """
    def __init__(self):
        self.means: Dict[str, float] = {}
        self.stds: Dict[str, float] = {}

    def fit(self, snapshots: List[Dict[str, Any]], num_cols: List[str], fit_indices: Optional[List[int]] = None):
        """
        Fits the scaler using active node observations across the specified snapshot indices.
        """
        if fit_indices is None:
            fit_indices = list(range(len(snapshots)))

        # Gather all active numerical feature values
        active_values = {col: [] for col in num_cols}
        for idx in fit_indices:
            snap = snapshots[idx]
            active_mask = snap['active_mask']
            # Retrieve values for active nodes in this snapshot
            for col in num_cols:
                # get raw values before scaling
                col_vals = snap['raw_features'][col][active_mask]
                active_values[col].extend(col_vals.tolist())

        # Compute mean and standard deviation
        for col in num_cols:
            vals = np.array(active_values[col])
            # Do not fit the scaler on NaNs (ignore them)
            valid_vals = vals[~np.isnan(vals)]
            if len(valid_vals) > 0:
                self.means[col] = float(np.mean(valid_vals))
                self.stds[col] = float(np.std(valid_vals))
            else:
                self.means[col] = 0.0
                self.stds[col] = 1.0
            
            # Prevent division by zero
            if self.stds[col] < 1e-8:
                self.stds[col] = 1.0

    def transform(self, values: np.ndarray, col_name: str) -> np.ndarray:
        """
        Standardizes the values of a column using fitted parameters.
        """
        mean = self.means.get(col_name, 0.0)
        std = self.stds.get(col_name, 1.0)
        scaled = (values - mean) / std
        # Replace remaining invalid values (NaN, inf) with the Z-score value of raw zero: -mean / std
        # This prevents boundary missing history from being treated as average demand (0.0)
        scaled_zero = -mean / std
        return np.nan_to_num(scaled, nan=scaled_zero, posinf=scaled_zero, neginf=scaled_zero)


class TemporalEVCSDataset:
    """
    Dataset representing the EV Charging Demand sequence of monthly graphs.
    """
    def __init__(
        self,
        data_dir: str = "processed",
        fit_scale_indices: Optional[List[int]] = None,
        scale_features: bool = True
    ):
        """
        Args:
            data_dir: Path to directory containing processed files.
            fit_scale_indices: Indices of snapshots to use for fitting the feature scaling (e.g. first 24 months).
                               If None, fits on all 36 months.
            scale_features: Whether to scale numerical features.
        """
        self.data_dir = data_dir
        self.scale_features = scale_features
        self.fit_scale_indices = fit_scale_indices
        
        self.snapshots: List[Data] = []
        self._load_and_process()

    def _load_and_process(self):
        # 1. Load nodes_master
        master_path = os.path.join(self.data_dir, "nodes_master.csv")
        if not os.path.exists(master_path):
            raise FileNotFoundError(f"nodes_master.csv not found at {master_path}")
        nodes_master = pd.read_csv(master_path)
        
        # Ensure node_id is sorted and ranges from 0 to 933
        nodes_master = nodes_master.sort_values("node_id").reset_index(drop=True)
        num_nodes = len(nodes_master)
        
        # Clean numeric columns that might be strings (like connector_rating)
        nodes_master["charger_rating"] = nodes_master["charger_rating"].apply(clean_numeric)
        nodes_master["connector_rating"] = nodes_master["connector_rating"].apply(clean_numeric)
        
        # Impute charger_rating and connector_rating if they contain NaNs after cleaning
        median_charger = nodes_master["charger_rating"].median()
        if pd.isna(median_charger) or np.isnan(median_charger):
            median_charger = 7.4
        nodes_master["charger_rating"] = nodes_master["charger_rating"].fillna(median_charger)
        
        median_connector = nodes_master["connector_rating"].median()
        if pd.isna(median_connector) or np.isnan(median_connector):
            median_connector = 7.4
        nodes_master["connector_rating"] = nodes_master["connector_rating"].fillna(median_connector)
        
        # Impute total_connectors
        median_connectors = nodes_master["total_connectors"].median()
        if pd.isna(median_connectors) or np.isnan(median_connectors):
            median_connectors = 1.0
        nodes_master["total_connectors"] = nodes_master["total_connectors"].fillna(median_connectors)
        
        # Binary encode govt_private
        nodes_master["govt_private_bin"] = (nodes_master["govt_private"] == "Government").astype(float)
        
        # Encode districts (alphabetical order)
        all_districts = sorted(nodes_master["district"].dropna().unique())
        self.district_to_idx = {dist: i for i, dist in enumerate(all_districts)}
        district_one_hot = np.zeros((num_nodes, len(all_districts)))
        for i, dist in enumerate(nodes_master["district"]):
            if dist in self.district_to_idx:
                district_one_hot[i, self.district_to_idx[dist]] = 1.0

        # 2. Load node_poi_features
        poi_path = os.path.join(self.data_dir, "node_poi_features.csv")
        if not os.path.exists(poi_path):
            raise FileNotFoundError(f"node_poi_features.csv not found at {poi_path}")
        node_poi = pd.read_csv(poi_path).sort_values("node_id").reset_index(drop=True)
        
        # 3. Load node_static_features (contains population_density by year)
        static_feats_path = os.path.join(self.data_dir, "node_static_features.csv")
        if not os.path.exists(static_feats_path):
            raise FileNotFoundError(f"node_static_features.csv not found at {static_feats_path}")
        node_static = pd.read_csv(static_feats_path)


        # 4. Construct static district-wise complete graph edge_index
        edge_list = []
        for dist in all_districts:
            dist_node_ids = nodes_master[nodes_master["district"] == dist]["node_id"].values
            # Create a complete subgraph within the district (excluding self-loops)
            for u in dist_node_ids:
                for v in dist_node_ids:
                    if u != v:
                        edge_list.append((u, v))
                        
        if len(edge_list) > 0:
            edge_index = torch.tensor(edge_list, dtype=torch.long).t().contiguous()
        else:
            edge_index = torch.empty((2, 0), dtype=torch.long)

        # 5. Gather raw snapshot features and active masks per month
        monthly_dir = os.path.join(self.data_dir, "monthly_snapshots")
        monthly_files = sorted(glob.glob(os.path.join(monthly_dir, "nodes_*.csv")))
        
        if len(monthly_files) != 36:
            # Fallback search in processed/monthly_snapshots
            fallback_dir = os.path.join(os.path.dirname(self.data_dir), "processed", "monthly_snapshots")
            monthly_files = sorted(glob.glob(os.path.join(fallback_dir, "nodes_*.csv")))
            if len(monthly_files) != 36:
                raise ValueError(f"Expected 36 monthly snapshot CSV files, found {len(monthly_files)} at {monthly_dir}")

        raw_snapshots: List[Dict[str, Any]] = []
        
        for file_path in monthly_files:
            file_name = os.path.basename(file_path)
            # Extract period (YYYY-MM) from nodes_YYYY-MM.csv
            period = file_name.replace("nodes_", "").replace(".csv", "")
            year = int(period.split("-")[0])
            month = int(period.split("-")[1])
            
            df_month = pd.read_csv(file_path)
            
            # Active nodes mask
            active_node_ids = set(df_month["node_id"].values)
            active_mask = np.array([nid in active_node_ids for nid in range(num_nodes)], dtype=bool)
            
            # Map monthly dynamic features
            dynamic_df = pd.DataFrame({"node_id": range(num_nodes)})
            dynamic_df = dynamic_df.merge(
                df_month[["node_id", "units", "load", "ev_registrations", "charger_age_months"]],
                on="node_id",
                how="left"
            ).fillna(0.0)  # Pad inactive nodes dynamic features with 0.0
            
            # Lookup population density for all 934 nodes for the current year
            static_year_df = node_static[node_static["year"] == year].sort_values("node_id").reset_index(drop=True)
            pop_density = static_year_df["population_density"].values
            
            # Store raw values for scaling
            raw_snap = {
                "period": period,
                "year": year,
                "month": month,
                "active_mask": active_mask,
                "raw_features": {
                    "latitude": nodes_master["latitude"].values.astype(float),
                    "longitude": nodes_master["longitude"].values.astype(float),
                    "charger_rating": nodes_master["charger_rating"].values.astype(float),
                    "connector_rating": nodes_master["connector_rating"].values.astype(float),
                    "total_connectors": nodes_master["total_connectors"].values.astype(float),
                    "govt_private_bin": nodes_master["govt_private_bin"].values.astype(float),
                    "population_density": pop_density.astype(float),
                    "amenity_count": node_poi["amenity_count"].values.astype(float),
                    "shop_count": node_poi["shop_count"].values.astype(float),
                    "tourism_count": node_poi["tourism_count"].values.astype(float),
                    "fuel_count": node_poi["fuel_count"].values.astype(float),
                    "healthcare_count": node_poi["healthcare_count"].values.astype(float),
                    "education_count": node_poi["education_count"].values.astype(float),
                    "units": dynamic_df["units"].values.astype(float),
                    "load": dynamic_df["load"].values.astype(float),
                    "ev_registrations": dynamic_df["ev_registrations"].values.astype(float),
                    "charger_age_months": dynamic_df["charger_age_months"].values.astype(float),
                },
                # Targets
                "y": dynamic_df[["units", "load"]].values.astype(float)
            }
            raw_snapshots.append(raw_snap)

        # 5b. Generate engineered temporal and seasonal features for each snapshot
        for t in range(len(raw_snapshots)):
            snap = raw_snapshots[t]
            
            # Units/Load Lags
            snap["raw_features"]["units_lag1"] = raw_snapshots[t-1]["raw_features"]["units"] if t >= 1 else np.full(num_nodes, np.nan)
            snap["raw_features"]["units_lag2"] = raw_snapshots[t-2]["raw_features"]["units"] if t >= 2 else np.full(num_nodes, np.nan)
            snap["raw_features"]["units_lag3"] = raw_snapshots[t-3]["raw_features"]["units"] if t >= 3 else np.full(num_nodes, np.nan)
            
            snap["raw_features"]["load_lag1"] = raw_snapshots[t-1]["raw_features"]["load"] if t >= 1 else np.full(num_nodes, np.nan)
            snap["raw_features"]["load_lag2"] = raw_snapshots[t-2]["raw_features"]["load"] if t >= 2 else np.full(num_nodes, np.nan)
            snap["raw_features"]["load_lag3"] = raw_snapshots[t-3]["raw_features"]["load"] if t >= 3 else np.full(num_nodes, np.nan)
            
            # Rolling statistics (mean, std, max, min) on historical 3 months
            if t >= 3:
                units_hist = np.stack([
                    raw_snapshots[t-1]["raw_features"]["units"],
                    raw_snapshots[t-2]["raw_features"]["units"],
                    raw_snapshots[t-3]["raw_features"]["units"]
                ], axis=0)
                snap["raw_features"]["units_rolling_mean3"] = np.mean(units_hist, axis=0)
                snap["raw_features"]["units_rolling_std3"] = np.std(units_hist, axis=0)
                snap["raw_features"]["units_rolling_max3"] = np.max(units_hist, axis=0)
                snap["raw_features"]["units_rolling_min3"] = np.min(units_hist, axis=0)
                
                load_hist = np.stack([
                    raw_snapshots[t-1]["raw_features"]["load"],
                    raw_snapshots[t-2]["raw_features"]["load"],
                    raw_snapshots[t-3]["raw_features"]["load"]
                ], axis=0)
                snap["raw_features"]["load_rolling_mean3"] = np.mean(load_hist, axis=0)
                snap["raw_features"]["load_rolling_std3"] = np.std(load_hist, axis=0)
                snap["raw_features"]["load_rolling_max3"] = np.max(load_hist, axis=0)
                snap["raw_features"]["load_rolling_min3"] = np.min(load_hist, axis=0)
            else:
                snap["raw_features"]["units_rolling_mean3"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["units_rolling_std3"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["units_rolling_max3"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["units_rolling_min3"] = np.full(num_nodes, np.nan)
                
                snap["raw_features"]["load_rolling_mean3"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["load_rolling_std3"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["load_rolling_max3"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["load_rolling_min3"] = np.full(num_nodes, np.nan)
                
            # Growth Rates
            if t >= 2:
                lag1 = raw_snapshots[t-1]["raw_features"]["units"]
                lag2 = raw_snapshots[t-2]["raw_features"]["units"]
                units_growth = np.zeros_like(lag1)
                valid_units = lag2 > 0
                units_growth[valid_units] = (lag1[valid_units] - lag2[valid_units]) / lag2[valid_units]
                snap["raw_features"]["units_growth_rate"] = units_growth
                
                load_lag1 = raw_snapshots[t-1]["raw_features"]["load"]
                load_lag2 = raw_snapshots[t-2]["raw_features"]["load"]
                load_growth = np.zeros_like(load_lag1)
                valid_load = load_lag2 > 0
                load_growth[valid_load] = (load_lag1[valid_load] - load_lag2[valid_load]) / load_lag2[valid_load]
                snap["raw_features"]["load_growth_rate"] = load_growth
            else:
                snap["raw_features"]["units_growth_rate"] = np.full(num_nodes, np.nan)
                snap["raw_features"]["load_growth_rate"] = np.full(num_nodes, np.nan)
                
            # Seasonality Features
            snap["raw_features"]["month_sin"] = np.full(num_nodes, np.sin(2 * np.pi * snap["month"] / 12))
            snap["raw_features"]["month_cos"] = np.full(num_nodes, np.cos(2 * np.pi * snap["month"] / 12))
            snap["raw_features"]["quarter"] = np.full(num_nodes, (snap["month"] - 1) // 3 + 1)
            snap["raw_features"]["year_feat"] = np.full(num_nodes, float(snap["year"]))

        # 6. Fit target Z-score scaling parameters from designated train snapshots
        if self.fit_scale_indices is None:
            fit_indices = list(range(len(raw_snapshots)))
        else:
            fit_indices = self.fit_scale_indices

        active_y_units = []
        active_y_load = []
        for idx in fit_indices:
            snap = raw_snapshots[idx]
            active_mask = snap["active_mask"]
            active_y_units.extend(snap["y"][active_mask, 0].tolist())
            active_y_load.extend(snap["y"][active_mask, 1].tolist())

        active_y_units = np.array(active_y_units)
        active_y_load = np.array(active_y_load)

        units_mean = float(np.mean(active_y_units)) if len(active_y_units) > 0 else 0.0
        units_std = float(np.std(active_y_units)) if len(active_y_units) > 0 else 1.0
        if units_std < 1e-8:
            units_std = 1.0

        load_mean = float(np.mean(active_y_load)) if len(active_y_load) > 0 else 0.0
        load_std = float(np.std(active_y_load)) if len(active_y_load) > 0 else 1.0
        if load_std < 1e-8:
            load_std = 1.0

        self.target_mean = torch.tensor([units_mean, load_mean], dtype=torch.float32)
        self.target_std = torch.tensor([units_std, load_std], dtype=torch.float32)

        # 7. Fit feature scaling parameters based on config feature flags
        num_cols_to_scale = []
        if config.USE_GRAPH_FEATURES:
            num_cols_to_scale.extend(["charger_rating", "connector_rating", "total_connectors"])
        if config.USE_POPULATION:
            num_cols_to_scale.append("population_density")
        if config.USE_POI:
            num_cols_to_scale.extend([
                "amenity_count", "shop_count", "tourism_count",
                "fuel_count", "healthcare_count", "education_count"
            ])
            
        # Target variables (always scaled since they are inputs and regression labels)
        num_cols_to_scale.extend(["units", "load"])
        
        if config.USE_EV_REGISTRATIONS:
            num_cols_to_scale.append("ev_registrations")
        if config.USE_CHARGER_AGE:
            num_cols_to_scale.append("charger_age_months")
        if config.USE_LAGS:
            num_cols_to_scale.extend([
                "units_lag1", "units_lag2", "units_lag3",
                "load_lag1", "load_lag2", "load_lag3"
            ])
        if config.USE_ROLLING:
            num_cols_to_scale.extend([
                "units_rolling_mean3", "units_rolling_std3", "units_rolling_max3", "units_rolling_min3",
                "load_rolling_mean3", "load_rolling_std3", "load_rolling_max3", "load_rolling_min3"
            ])
        if config.USE_GROWTH:
            num_cols_to_scale.extend(["units_growth_rate", "load_growth_rate"])
            
        self.scaler = FeatureScaler()
        if self.scale_features:
            self.scaler.fit(raw_snapshots, num_cols_to_scale, fit_indices=self.fit_scale_indices)

        # 8. Construct PyG Data objects
        dynamic_cols = {"units", "load"}
        if config.USE_EV_REGISTRATIONS:
            dynamic_cols.add("ev_registrations")
        if config.USE_CHARGER_AGE:
            dynamic_cols.add("charger_age_months")
        if config.USE_LAGS:
            dynamic_cols.update(["units_lag1", "units_lag2", "units_lag3", "load_lag1", "load_lag2", "load_lag3"])
        if config.USE_ROLLING:
            dynamic_cols.update([
                "units_rolling_mean3", "units_rolling_std3", "units_rolling_max3", "units_rolling_min3",
                "load_rolling_mean3", "load_rolling_std3", "load_rolling_max3", "load_rolling_min3"
            ])
        if config.USE_GROWTH:
            dynamic_cols.update(["units_growth_rate", "load_growth_rate"])

        for raw_snap in raw_snapshots:
            feat_list = []
            
            # Static attributes (coordinates and one-hot district) - NOT standardized
            if config.USE_GRAPH_FEATURES:
                feat_list.append(raw_snap["raw_features"]["latitude"].reshape(-1, 1))
                feat_list.append(raw_snap["raw_features"]["longitude"].reshape(-1, 1))
            
            feat_list.append(district_one_hot)
            
            if config.USE_GRAPH_FEATURES:
                feat_list.append(raw_snap["raw_features"]["govt_private_bin"].reshape(-1, 1))
            
            # Numerical features (scaled/standardized if enabled)
            for col in num_cols_to_scale:
                vals = raw_snap["raw_features"][col]
                if self.scale_features:
                    scaled_vals = self.scaler.transform(vals, col)
                else:
                    # Impute NaNs with 0.0 if not scaling
                    scaled_vals = np.nan_to_num(vals, nan=0.0)
                
                # For inactive nodes, dynamic features should remain at the scaled zero representation (-mean/std), NOT 0.0!
                if col in dynamic_cols:
                    mean_val = self.scaler.means.get(col, 0.0)
                    std_val = self.scaler.stds.get(col, 1.0)
                    scaled_zero = -mean_val / std_val if self.scale_features else 0.0
                    scaled_vals[~raw_snap["active_mask"]] = scaled_zero
                    
                feat_list.append(scaled_vals.reshape(-1, 1))
                
            # Append cyclical seasonality features to the end of snapshot.x - NOT standardized
            if config.USE_SEASONAL:
                new_cols_raw = ["month_sin", "month_cos", "quarter"]
                for col in new_cols_raw:
                    vals = raw_snap["raw_features"][col].copy()
                    # Impute NaNs with 0.0 for neural network compatibility
                    vals = np.nan_to_num(vals, nan=0.0)
                    feat_list.append(vals.reshape(-1, 1))
                    
            if config.USE_YEAR:
                vals = raw_snap["raw_features"]["year_feat"].copy()
                vals = np.nan_to_num(vals, nan=0.0)
                feat_list.append(vals.reshape(-1, 1))
                
            x = np.hstack(feat_list)
            
            x_tensor = torch.tensor(x, dtype=torch.float32)
            
            # Normalize targets
            mean_np = self.target_mean.numpy()
            std_np = self.target_std.numpy()
            y_normalized = (raw_snap["y"] - mean_np) / std_np
            # For inactive nodes, target values should remain 0.0 after normalization
            y_normalized[~raw_snap["active_mask"]] = 0.0
            
            y_tensor = torch.tensor(y_normalized, dtype=torch.float32)
            active_mask_tensor = torch.tensor(raw_snap["active_mask"], dtype=torch.bool)
            
            # Create PyG Data object
            data = Data(
                x=x_tensor,
                edge_index=edge_index,
                y=y_tensor,
                active_mask=active_mask_tensor,
                period=raw_snap["period"]
            )
            self.snapshots.append(data)

    def inverse_transform_targets(self, normalized_y):
        """
        Converts normalized predictions or targets back to the original scale.
        Supports both numpy arrays and PyTorch tensors.
        """
        if torch.is_tensor(normalized_y):
            mean = self.target_mean.to(device=normalized_y.device, dtype=normalized_y.dtype)
            std = self.target_std.to(device=normalized_y.device, dtype=normalized_y.dtype)
            return normalized_y * std + mean
        else:
            mean_np = self.target_mean.cpu().numpy()
            std_np = self.target_std.cpu().numpy()
            return normalized_y * std_np + mean_np

    def get_feature_info(self) -> Dict[str, Any]:
        """
        Enlists the features present in the node feature matrix (data.x) based on
        their static and dynamic behavior, and explains how the model uses them.
        
        Returns:
            A dictionary containing feature category lists and detailed model usage explanation.
        """
        import config
        num_districts = len(self.district_to_idx)
        
        static_features = []
        if config.USE_GRAPH_FEATURES:
            static_features.extend([
                {"name": "latitude", "dimensions": 1, "description": "Station GPS latitude coordinate (raw/unscaled)."},
                {"name": "longitude", "dimensions": 1, "description": "Station GPS longitude coordinate (raw/unscaled)."}
            ])
            
        static_features.append({"name": "district_one_hot", "dimensions": num_districts, "description": "One-hot representation of the station district."})
        
        if config.USE_GRAPH_FEATURES:
            static_features.extend([
                {"name": "govt_private_bin", "dimensions": 1, "description": "Binary encoding of owner type: 1.0 for Government, 0.0 for Private."},
                {"name": "charger_rating", "dimensions": 1, "description": "Station charger power rating in kW (scaled)."},
                {"name": "connector_rating", "dimensions": 1, "description": "Connector rating in kW (scaled)."},
                {"name": "total_connectors", "dimensions": 1, "description": "Total active connectors at the station (scaled)."}
            ])
            
        if config.USE_POI:
            static_features.extend([
                {"name": "amenity_count", "dimensions": 1, "description": "Nearby POI amenities count within radius (scaled)."},
                {"name": "shop_count", "dimensions": 1, "description": "Nearby POI shops count within radius (scaled)."},
                {"name": "tourism_count", "dimensions": 1, "description": "Nearby POI tourism attractions count within radius (scaled)."},
                {"name": "fuel_count", "dimensions": 1, "description": "Nearby POI fuel stations count within radius (scaled)."},
                {"name": "healthcare_count", "dimensions": 1, "description": "Nearby POI healthcare facilities count within radius (scaled)."},
                {"name": "education_count", "dimensions": 1, "description": "Nearby POI education institutes count within radius (scaled)."}
            ])
        
        dynamic_features = []
        if config.USE_POPULATION:
            dynamic_features.append({"name": "population_density", "dimensions": 1, "description": "Population density of the station node district (scaled, changes yearly)."})
            
        dynamic_features.extend([
            {"name": "units", "dimensions": 1, "description": "Monthly charging demand in units (scaled, changes monthly)."},
            {"name": "load", "dimensions": 1, "description": "Monthly charging load in kW (scaled, changes monthly)."}
        ])
        
        if config.USE_EV_REGISTRATIONS:
            dynamic_features.append({"name": "ev_registrations", "dimensions": 1, "description": "District monthly new EV registrations (scaled, changes monthly)."})
        if config.USE_CHARGER_AGE:
            dynamic_features.append({"name": "charger_age_months", "dimensions": 1, "description": "Age of the station chargers in months (scaled, changes monthly)."})
            
        if config.USE_LAGS:
            dynamic_features.extend([
                {"name": "units_lag1", "dimensions": 1, "description": "Units from 1 month ago (scaled)."},
                {"name": "units_lag2", "dimensions": 1, "description": "Units from 2 months ago (scaled)."},
                {"name": "units_lag3", "dimensions": 1, "description": "Units from 3 months ago (scaled)."},
                {"name": "load_lag1", "dimensions": 1, "description": "Load from 1 month ago (scaled)."},
                {"name": "load_lag2", "dimensions": 1, "description": "Load from 2 months ago (scaled)."},
                {"name": "load_lag3", "dimensions": 1, "description": "Load from 3 months ago (scaled)."}
            ])
            
        if config.USE_ROLLING:
            dynamic_features.extend([
                {"name": "units_rolling_mean3", "dimensions": 1, "description": "3-month rolling mean of historical units (scaled)."},
                {"name": "units_rolling_std3", "dimensions": 1, "description": "3-month rolling std of historical units (scaled)."},
                {"name": "units_rolling_max3", "dimensions": 1, "description": "3-month rolling max of historical units (scaled)."},
                {"name": "units_rolling_min3", "dimensions": 1, "description": "3-month rolling min of historical units (scaled)."},
                {"name": "load_rolling_mean3", "dimensions": 1, "description": "3-month rolling mean of historical load (scaled)."},
                {"name": "load_rolling_std3", "dimensions": 1, "description": "3-month rolling std of historical load (scaled)."},
                {"name": "load_rolling_max3", "dimensions": 1, "description": "3-month rolling max of historical load (scaled)."},
                {"name": "load_rolling_min3", "dimensions": 1, "description": "3-month rolling min of historical load (scaled)."}
            ])
            
        if config.USE_GROWTH:
            dynamic_features.extend([
                {"name": "units_growth_rate", "dimensions": 1, "description": "Units growth rate between Lag1 and Lag2 (scaled)."},
                {"name": "load_growth_rate", "dimensions": 1, "description": "Load growth rate between Lag1 and Lag2 (scaled)."}
            ])
            
        if config.USE_SEASONAL:
            dynamic_features.extend([
                {"name": "month_sin", "dimensions": 1, "description": "Cyclical month sine encoding (raw/unscaled)."},
                {"name": "month_cos", "dimensions": 1, "description": "Cyclical month cosine encoding (raw/unscaled)."},
                {"name": "quarter", "dimensions": 1, "description": "Quarter of the year (1-4, raw/unscaled)."}
            ])
            
        if config.USE_YEAR:
            dynamic_features.append({"name": "year_feat", "dimensions": 1, "description": "Year coordinate (raw/unscaled)."})
            
        total_dimensions = sum(f["dimensions"] for f in static_features) + sum(f["dimensions"] for f in dynamic_features)
        
        model_usage = (
            "1. Feature Matrix Construction: The node feature matrix data.x (shape: [num_nodes, total_dimensions]) "
            "is constructed by concatenating all active static and dynamic features for each node.\n"
            "2. Spatial GNN Message Passing: The GNN encoder (ResidualEdgeConv) processes data.x along with the "
            "static district-wise complete subgraph edge_index. It aggregates neighboring node features to "
            "compute spatial representations, capturing district-level charging patterns.\n"
            "3. Recurrent Temporal Updates: The resulting GNN embeddings are fed into the GRUUpdater. Active node "
            "states are updated using the dynamic GRU transition, while inactive nodes (identified via data.active_mask) "
            "retain their historical hidden memory representation.\n"
            "4. Predictor Regression Head: The updated memory states are mapped by the regression_head "
            "to predict the two charging demand targets [Units, Load] for the upcoming month."
        )
        
        return {
            "total_feature_dimensions": total_dimensions,
            "static_features": static_features,
            "dynamic_features": dynamic_features,
            "model_usage_explanation": model_usage
        }


    def get_snapshots(self) -> List[Data]:
        """
        Returns the ordered list of 36 graph snapshots.
        """
        return self.snapshots

    def __len__(self) -> int:
        return len(self.snapshots)

    def __getitem__(self, idx: int) -> Data:
        return self.snapshots[idx]
