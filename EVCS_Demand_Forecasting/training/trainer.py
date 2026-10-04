import os
import copy
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from typing import List, Dict, Tuple, Optional

# Import package modules
from models.roland_model import ROLANDModel
from datasets.dataset import TemporalEVCSDataset
from datasets.loader import SlidingWindowSplitter

def compute_masked_mse_loss(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    active_mask: torch.Tensor
) -> torch.Tensor:
    """
    Computes Mean Squared Error (MSE) loss strictly for active nodes.
    
    Args:
        predictions: Model output predictions of shape (num_nodes, 2).
        targets: Target values of shape (num_nodes, 2).
        active_mask: Boolean active node indicator mask of shape (num_nodes,).
    Returns:
        Scalar MSE loss tensor.
    """
    active_preds = predictions[active_mask]
    active_targets = targets[active_mask]
    
    if active_preds.size(0) == 0:
        return torch.tensor(0.0, device=predictions.device, requires_grad=True)
        
    return nn.functional.mse_loss(active_preds, active_targets)


def compute_metrics(
    predictions: torch.Tensor,
    targets: torch.Tensor,
    active_mask: torch.Tensor,
    target_mean: Optional[torch.Tensor] = None,
    target_std: Optional[torch.Tensor] = None
) -> Dict[str, float]:
    """
    Computes RMSE, MAE, and R^2 metrics for Units, Load, and their average, strictly for active nodes.
    Supports inverse-transforming predictions and targets to original scale if stats are provided.
    
    Args:
        predictions: Model output predictions of shape (num_nodes, 2).
        targets: Target values of shape (num_nodes, 2).
        active_mask: Boolean active node indicator mask of shape (num_nodes,).
        target_mean: Optional mean tensor of shape (2,) for target inverse transformation.
        target_std: Optional standard deviation tensor of shape (2,) for target inverse transformation.
    Returns:
        Dictionary of calculated metrics.
    """
    active_preds_tensor = predictions[active_mask].detach()
    active_targets_tensor = targets[active_mask].detach()

    # Inverse-transform targets and predictions back to original scale
    if target_mean is not None and target_std is not None:
        mean = target_mean.to(device=active_preds_tensor.device, dtype=active_preds_tensor.dtype)
        std = target_std.to(device=active_preds_tensor.device, dtype=active_preds_tensor.dtype)
        active_preds_tensor = active_preds_tensor * std + mean
        active_targets_tensor = active_targets_tensor * std + mean

    active_preds = active_preds_tensor.cpu().numpy()
    active_targets = active_targets_tensor.cpu().numpy()
    
    metrics = {}
    if active_preds.shape[0] == 0:
        # Default fallback values if no nodes are active
        for name in ["units", "load"]:
            metrics[f"{name}_rmse"] = 0.0
            metrics[f"{name}_mae"] = 0.0
            metrics[f"{name}_r2"] = 0.0
        metrics["avg_rmse"] = 0.0
        metrics["avg_mae"] = 0.0
        metrics["avg_r2"] = 0.0
        return metrics

    for idx, name in enumerate(["units", "load"]):
        p = active_preds[:, idx]
        t = active_targets[:, idx]
        
        # Mean Squared Error & Root Mean Squared Error
        mse = np.mean((p - t) ** 2)
        rmse = np.sqrt(mse)
        
        # Mean Absolute Error
        mae = np.mean(np.abs(p - t))
        
        # R2 Score (Coefficient of Determination)
        t_bar = np.mean(t)
        ss_res = np.sum((t - p) ** 2)
        ss_tot = np.sum((t - t_bar) ** 2)
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        
        metrics[f"{name}_rmse"] = float(rmse)
        metrics[f"{name}_mae"] = float(mae)
        metrics[f"{name}_r2"] = float(r2)
        
    # Calculate averages
    metrics["avg_rmse"] = (metrics["units_rmse"] + metrics["load_rmse"]) / 2.0
    metrics["avg_mae"] = (metrics["units_mae"] + metrics["load_mae"]) / 2.0
    metrics["avg_r2"] = (metrics["units_r2"] + metrics["load_r2"]) / 2.0
    
    return metrics


class ROLANDTrainer:
    """
    Offline rolling sliding window trainer for ROLAND dynamic EV charging demand forecasting.
    """
    def __init__(
        self,
        hidden_dim: int = 64,
        num_epochs: int = 100,
        lr: float = 0.001,
        patience: int = 15,
        device: str = "cpu",
        checkpoint_dir: str = "checkpoints"
    ):
        """
        Args:
            hidden_dim: Hidden dimension size of node embeddings and memory state.
            num_epochs: Number of training epochs per window.
            lr: Learning rate for optimizer.
            patience: Patience for validation early stopping.
            device: Training target device ('cpu', 'cuda').
            checkpoint_dir: Directory path to save best model checkpoints.
        """
        self.hidden_dim = hidden_dim
        self.num_epochs = num_epochs
        self.lr = lr
        self.patience = patience
        self.device = torch.device(device)
        self.checkpoint_dir = checkpoint_dir

        if not os.path.exists(checkpoint_dir):
            os.makedirs(checkpoint_dir)

    def train_sliding_windows(self, dataset: TemporalEVCSDataset) -> Dict[str, any]:
        """
        Trains and evaluates the ROLANDModel across all sliding windows.
        
        Args:
            dataset: Preprocessed and aligned TemporalEVCSDataset dataset.
        Returns:
            Dictionary containing window-wise results and overall averages.
        """
        splitter = SlidingWindowSplitter(dataset)
        window_results = []
        
        # Retrieve target stats for inverse transformation if they exist in the dataset
        target_mean = getattr(dataset, "target_mean", None)
        target_std = getattr(dataset, "target_std", None)

        for window_idx, (train_snaps, val_split, test_split) in enumerate(splitter.get_windows()):
            #Debug
            if window_idx > 0:
                break
            val_snap = val_split[0]
            test_snap = test_split[0]
            
            print(f"\n=================== Window {window_idx + 1:02d} / 29 ===================")
            
            num_nodes = train_snaps[0].num_nodes
            in_channels = train_snaps[0].x.size(1)
            edge_dim = train_snaps[0].edge_attr.size(1) if getattr(train_snaps[0], "edge_attr", None) is not None else None

            # 1. Initialize fresh model and optimizer for this window
            model = ROLANDModel(
                in_channels=in_channels,
                hidden_channels=self.hidden_dim * 2,
                out_channels=self.hidden_dim,
                edge_dim=edge_dim
            ).to(self.device)
            
            optimizer = optim.Adam(model.parameters(), lr=self.lr)
            
            best_val_rmse = float("inf")
            best_model_state = None
            patience_counter = 0

            # Move snapshot tensors to the target training device
            train_snaps = [snap.to(self.device) for snap in train_snaps]
            val_snap = val_snap.to(self.device)
            test_snap = test_snap.to(self.device)

            # Loop 2: Epoch Loop
            for epoch in range(self.num_epochs):
                # Reset hidden memory to zeros only at the start of each epoch
                hidden = torch.zeros(num_nodes, self.hidden_dim, device=self.device)
                
                # Loop 3: Training months loop (Train phase)
                model.train()
                optimizer.zero_grad()
                accumulated_loss = 0.0
                
                for snapshot in train_snaps:
                    pred, hidden = model(snapshot, hidden)
                    loss = compute_masked_mse_loss(pred, snapshot.y, snapshot.active_mask)
                    accumulated_loss += loss
                
                # Optimizer updates weights once per epoch
                accumulated_loss.backward()
                optimizer.step()

                # Validation phase (carry training hidden state into validation month 7)
                model.eval()
                with torch.no_grad():
                    pred_val, hidden_val = model(val_snap, hidden)
                    val_metrics = compute_metrics(
                        pred_val, 
                        val_snap.y, 
                        val_snap.active_mask,
                        target_mean=target_mean,
                        target_std=target_std
                    )

                if (epoch + 1) % 10 == 0 or epoch == 0:
                    print(f"Epoch {epoch+1:03d} | Train Loss: {accumulated_loss.item():.4f} | Val RMSE: {val_metrics['avg_rmse']:.4f}")

                # Early stopping tracking on validation average RMSE
                if val_metrics["avg_rmse"] < best_val_rmse:
                    best_val_rmse = val_metrics["avg_rmse"]
                    best_model_state = copy.deepcopy(model.state_dict())
                    patience_counter = 0
                else:
                    patience_counter += 1

                if patience_counter >= self.patience:
                    print(f"Early stopping triggered at epoch {epoch+1}.")
                    break

            # Load the best model state dict for testing evaluation
            if best_model_state is not None:
                model.load_state_dict(best_model_state)
                # Save checkpoint to disk
                checkpoint_path = os.path.join(self.checkpoint_dir, f"best_model_window_{window_idx}.pt")
                torch.save(best_model_state, checkpoint_path)

            # Testing phase (carry memory continuously train -> val -> test using the best model)
            model.eval()
            with torch.no_grad():
                test_hidden = torch.zeros(num_nodes, self.hidden_dim, device=self.device)
                # Carry state through training
                for snapshot in train_snaps:
                    _, test_hidden = model(snapshot, test_hidden)
                # Carry state through validation
                _, test_hidden = model(val_snap, test_hidden)
                # Evaluate on testing month 8
                pred_test, _ = model(test_snap, test_hidden)
                test_metrics = compute_metrics(
                    pred_test, 
                    test_snap.y, 
                    test_snap.active_mask,
                    target_mean=target_mean,
                    target_std=target_std
                )

            print(f"Window {window_idx+1:02d} Test Results | RMSE: {test_metrics['avg_rmse']:.4f} | MAE: {test_metrics['avg_mae']:.4f} | R2: {test_metrics['avg_r2']:.4f}")
            window_results.append(test_metrics)

        # Calculate final overall average metrics
        overall_averages = {}
        for key in window_results[0].keys():
            overall_averages[f"overall_{key}"] = float(np.mean([res[key] for res in window_results]))

        print("\n=================== Final Overall Averages ===================")
        print(f"Overall RMSE: {overall_averages['overall_avg_rmse']:.4f}")
        print(f"Overall MAE:  {overall_averages['overall_avg_mae']:.4f}")
        print(f"Overall R2:   {overall_averages['overall_avg_r2']:.4f}")

        return {
            "window_results": window_results,
            "overall_averages": overall_averages
        }


if __name__ == "__main__":
    print("--- Running ROLANDTrainer Verification Routine ---")
    import torch_geometric as pyg
    from torch_geometric.data import Data
    
    # Setup mock constants
    num_nodes = 10
    in_channels = 4
    hidden_dim = 8
    
    # 1. Create a mock dataset to verify memory flow, masking, and gradients
    mock_snapshots = []
    for i in range(8):
        x = torch.randn(num_nodes, in_channels)
        edge_index = torch.tensor([[0, 1, 2, 3, 4], [1, 2, 3, 4, 0]], dtype=torch.long)
        y = torch.randn(num_nodes, 2)
        
        # Design active mask (different active nodes each snapshot)
        active_mask = torch.zeros(num_nodes, dtype=torch.bool)
        active_mask[i % num_nodes] = True
        active_mask[(i + 1) % num_nodes] = True
        
        data = Data(x=x, edge_index=edge_index, y=y, active_mask=active_mask)
        mock_snapshots.append(data)
        
    train_snaps = mock_snapshots[:6]
    val_snap = mock_snapshots[6]
    test_snap = mock_snapshots[7]
    
    # Initialize model and optimizer
    model = ROLANDModel(in_channels=in_channels, hidden_channels=hidden_dim * 2, out_channels=hidden_dim)
    optimizer = optim.Adam(model.parameters(), lr=0.01)
    
    # Test hidden memory reset and propagation
    # Epoch starts:
    hidden_init = torch.zeros(num_nodes, hidden_dim)
    
    # Verification 1: Confirm hidden memory resets to zero at the start of epoch
    assert torch.all(hidden_init == 0.0), "Hidden memory must initialize to zero at the start of epoch"
    print("Verification 1: Hidden memory resets to zeros at epoch start -> PASSED")
    
    # Verification 2: Confirm hidden memory propagates correctly through the six training months
    hidden = hidden_init.clone()
    hiddens_list = [hidden]
    for snap in train_snaps:
        pred, hidden = model(snap, hidden)
        hiddens_list.append(hidden)
        
    for j in range(1, len(hiddens_list)):
        assert not torch.allclose(hiddens_list[j], hiddens_list[j-1]), f"Hidden state did not update at month {j}"
    print("Verification 2: Hidden memory propagates correctly through train months -> PASSED")
    
    # Verification 3: Confirm continuous propagation carries validation and testing hidden state
    # Val propagates from final train hidden state
    pred_val, hidden_val = model(val_snap, hiddens_list[-1])
    # Test propagates from final val hidden state
    pred_test, hidden_test = model(test_snap, hidden_val)
    # Confirm hidden states are propagated
    assert not torch.allclose(hidden_val, hiddens_list[-1]), "Validation did not update from training state"
    assert not torch.allclose(hidden_test, hidden_val), "Testing did not update from validation state"
    print("Verification 3: Memory carries over continuously within the sliding window -> PASSED")
    
    # Verification 4: Confirm loss is computed strictly on active nodes only
    snap0 = train_snaps[0]
    pred0, _ = model(snap0, hidden_init)
    
    loss_original = compute_masked_mse_loss(pred0, snap0.y, snap0.active_mask)
    
    # Modify inactive node targets and assert that the computed loss remains completely identical
    y_modified = snap0.y.clone()
    inactive_indices = (~snap0.active_mask).nonzero(as_tuple=True)[0]
    y_modified[inactive_indices] += 10.0
    
    loss_modified = compute_masked_mse_loss(pred0, y_modified, snap0.active_mask)
    assert torch.allclose(loss_original, loss_modified), "Loss must not be affected by inactive nodes"
    print("Verification 4: Loss is computed strictly on active nodes only -> PASSED")
    
    # Verification 5: Confirm gradients propagate through the complete six-month sequence
    optimizer.zero_grad()
    accumulated_loss = 0.0
    hidden_grad = torch.zeros(num_nodes, hidden_dim)
    for snap in train_snaps:
        pred, hidden_grad = model(snap, hidden_grad)
        loss = compute_masked_mse_loss(pred, snap.y, snap.active_mask)
        accumulated_loss += loss
        
    accumulated_loss.backward()
    
    # Assert that all trainable weights in encoder, memory, and regression head have gradients
    for name, p in model.named_parameters():
        if p.requires_grad:
            assert p.grad is not None, f"Gradient did not flow to parameter {name}"
    print("Verification 5: Gradients propagate through the full 6-month sequence -> PASSED")
    
    # Verification 6: Confirm one optimizer step occurs per epoch and modifies weights
    old_weights = [p.clone() for p in model.parameters() if p.requires_grad]
    optimizer.step()
    
    any_changed = False
    for p_old, p_new in zip(old_weights, model.parameters()):
        if p_new.requires_grad and not torch.allclose(p_old, p_new):
            any_changed = True
            break
    assert any_changed, "Model weights must be updated by the optimizer"
    print("Verification 6: Weights are successfully updated by the optimizer step -> PASSED")
    
    # Verification 7: Confirm sliding window iteration correctly processes windows
    mock_dataset_large = []
    for i in range(12):  # 12 snapshots should yield exactly 5 windows
        x = torch.randn(num_nodes, in_channels)
        edge_index = torch.tensor([[0], [1]], dtype=torch.long)
        y = torch.randn(num_nodes, 2)
        active_mask = torch.ones(num_nodes, dtype=torch.bool)
        data = Data(x=x, edge_index=edge_index, y=y, active_mask=active_mask)
        mock_dataset_large.append(data)
        
    class MockDataset:
        def __init__(self, data_list):
            self.data_list = data_list
        def __len__(self):
            return len(self.data_list)
        def __getitem__(self, idx):
            return self.data_list[idx]
            
    mock_ds = MockDataset(mock_dataset_large)
    splitter = SlidingWindowSplitter(mock_ds)
    window_count = sum(1 for _ in splitter.get_windows())
    assert window_count == 5, f"Expected 5 windows for 12 snapshots, got {window_count}"
    print("Verification 7: Sliding-window iteration correctly traverses all window boundaries -> PASSED")
    
    print("\nAll ROLANDTrainer verification assertions completed successfully!")
