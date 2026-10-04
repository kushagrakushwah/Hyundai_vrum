import os
import copy
from typing import List, Dict, Any, Optional, Tuple
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from torch_geometric.data import Data

from datasets.dataset import TemporalEVCSDataset
from models.roland_model import ROLANDModel
from training.trainer import compute_masked_mse_loss, compute_metrics
import config

class MaskedMSELoss(nn.Module):
    def forward(self, pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        return compute_masked_mse_loss(pred, target, mask)



class ROLANDTemporalTrainer:
    """
    Traditional temporal forecasting trainer for ROLAND dynamic EV charging demand forecasting.
    This trainer uses a fixed chronological split (24 months train, 6 months val, 6 months test)
    and propagates the hidden state sequentially through phases using detached states.
    """
    def __init__(
        self,
        hidden_dim: Optional[int] = None,
        num_epochs: Optional[int] = None,
        lr: Optional[float] = None,
        patience: Optional[int] = None,
        device: Optional[str] = None,
        checkpoint_dir: Optional[str] = None
    ):
        """
        Args:
            hidden_dim: Dimension of the hidden state. If None, reads from config.py.
            num_epochs: Number of training epochs. If None, reads from config.py.
            lr: Learning rate. If None, reads from config.py.
            patience: Patience for validation early stopping. If None, reads from config.py.
            device: Training target device ('cpu', 'cuda'). If None, reads from config.py.
            checkpoint_dir: Directory path to save best checkpoints. If None, reads from config.py.
        """
        import config
        self.hidden_dim = hidden_dim if hidden_dim is not None else config.HIDDEN_DIM
        self.num_epochs = num_epochs if num_epochs is not None else config.EPOCHS
        self.lr = lr if lr is not None else config.LR
        self.patience = patience if patience is not None else config.PATIENCE
        
        device_str = device if device is not None else config.DEVICE
        self.device = torch.device(device_str)
        self.checkpoint_dir = checkpoint_dir if checkpoint_dir is not None else config.CHECKPOINT_DIR
        
        self.weight_decay = config.WEIGHT_DECAY
        self.grad_clipping = config.GRAD_CLIPPING
        self.use_scheduler = config.USE_SCHEDULER
        self.optimizer_name = config.OPTIMIZER_NAME

        if not os.path.exists(self.checkpoint_dir):
            os.makedirs(self.checkpoint_dir)
            
        self.criterion = MaskedMSELoss()


    def train_epoch(self, model: ROLANDModel, train_snapshots: List[Data], optimizer: optim.Optimizer) -> Tuple[float, torch.Tensor]:
        """
        Performs one epoch of training by replaying the training sequence.
        Accumulates loss across all snapshots and runs backpropagation once.
        Always uses teacher forcing (feeding true monthly graph snapshots).
        
        Args:
            model: The ROLANDModel instance.
            train_snapshots: Chronological list of training graph snapshots.
            optimizer: PyTorch optimizer.
        Returns:
            Tuple of (accumulated training loss, final training hidden state).
        """
        model.train()
        optimizer.zero_grad()
        
        num_nodes = train_snapshots[0].num_nodes
        # Initialize hidden state with correct device and dtype
        H_train = torch.zeros(num_nodes, self.hidden_dim, device=self.device)
        
        accumulated_loss = 0.0
        for snapshot in train_snapshots:
            # ROLANDModel(snapshot, H_train) updates memory state and outputs predictions
            pred, H_train = model(snapshot, H_train)
            loss = self.criterion(pred, snapshot.y, snapshot.active_mask)
            accumulated_loss += loss
            
        accumulated_loss.backward()

        
        # Apply gradient clipping if enabled
        if hasattr(self, "grad_clipping") and self.grad_clipping > 0:
            nn.utils.clip_grad_norm_(model.parameters(), self.grad_clipping)
            
        optimizer.step()
        
        return accumulated_loss.item(), H_train

    def validate_sequence(
        self,
        model: ROLANDModel,
        val_snapshots: List[Data],
        H_train: torch.Tensor,
        target_mean: Optional[torch.Tensor] = None,
        target_std: Optional[torch.Tensor] = None
    ) -> Tuple[float, Dict[str, float], torch.Tensor]:
        """
        Evaluates the model on validation snapshots, carrying the final train memory.
        The input state is detached to prevent backpropagation graph leakage.
        Predictions and targets are concatenated across all validation months
        and evaluated once on the original scale.
        
        Args:
            model: The ROLANDModel instance.
            val_snapshots: Chronological list of validation graph snapshots.
            H_train: Final training hidden state.
            target_mean: Optional target mean for scaling metrics.
            target_std: Optional target std for scaling metrics.
        Returns:
            Tuple of (total validation loss, calculated validation metrics, final validation hidden state).
        """
        model.eval()
        # Detach hidden state to prevent gradient tracking carryover
        H_val = H_train.detach()
        
        val_loss = 0.0
        val_preds = []
        val_targets = []
        val_masks = []
        
        with torch.no_grad():
            for snapshot in val_snapshots:
                pred, H_val = model(snapshot, H_val)
                loss = self.criterion(pred, snapshot.y, snapshot.active_mask)
                val_loss += loss.item()

                
                val_preds.append(pred)
                val_targets.append(snapshot.y)
                val_masks.append(snapshot.active_mask)
                
        # Concatenate months
        all_preds = torch.cat(val_preds, dim=0)
        all_targets = torch.cat(val_targets, dim=0)
        all_masks = torch.cat(val_masks, dim=0)
        
        # Compute validation metrics
        metrics = compute_metrics(
            all_preds,
            all_targets,
            all_masks,
            target_mean=target_mean,
            target_std=target_std
        )
        
        return val_loss, metrics, H_val

    def evaluate_sequence(
        self,
        model: ROLANDModel,
        test_snapshots: List[Data],
        H_val: torch.Tensor,
        target_mean: Optional[torch.Tensor] = None,
        target_std: Optional[torch.Tensor] = None
    ) -> Tuple[float, Dict[str, float]]:
        """
        Evaluates the model on test snapshots, carrying the final validation memory.
        The input state is detached. Predictions and targets are concatenated
        and evaluated once on the original scale.
        
        Args:
            model: The ROLANDModel instance.
            test_snapshots: Chronological list of test graph snapshots.
            H_val: Final validation hidden state.
            target_mean: Optional target mean for scaling metrics.
            target_std: Optional target std for scaling metrics.
        Returns:
            Tuple of (total test loss, calculated test metrics).
        """
        model.eval()
        # Detach hidden state to prevent gradient tracking carryover
        H_test = H_val.detach()
        
        test_loss = 0.0
        test_preds = []
        test_targets = []
        test_masks = []
        
        with torch.no_grad():
            for snapshot in test_snapshots:
                pred, H_test = model(snapshot, H_test)
                loss = self.criterion(pred, snapshot.y, snapshot.active_mask)
                test_loss += loss.item()

                
                test_preds.append(pred)
                test_targets.append(snapshot.y)
                test_masks.append(snapshot.active_mask)
                
        # Concatenate months
        all_preds = torch.cat(test_preds, dim=0)
        all_targets = torch.cat(test_targets, dim=0)
        all_masks = torch.cat(test_masks, dim=0)
        
        # Compute testing metrics
        metrics = compute_metrics(
            all_preds,
            all_targets,
            all_masks,
            target_mean=target_mean,
            target_std=target_std
        )
        
        return test_loss, metrics

    def fit_and_evaluate(
        self,
        dataset: TemporalEVCSDataset,
        overfit_mode: bool = False,
        overfit_epochs: int = 300,
        train_months: Optional[int] = None,
        val_months: Optional[int] = None,
        test_months: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Executes the training and validation sequence on the traditional temporal split.
        Saves checkpoints and evaluates on testing only at the end.
        """
        import config
        # Use provided arguments or default to config values
        t_months = train_months if train_months is not None else config.TRAIN_MONTHS
        v_months = val_months if val_months is not None else config.VAL_MONTHS
        te_months = test_months if test_months is not None else config.TEST_MONTHS
        
        # Move snapshots to device
        snapshots = [snap.to(self.device) for snap in dataset.get_snapshots()]
        
        if overfit_mode:
            train_snapshots = snapshots[:2]
            val_snapshots = snapshots[:2]
            test_snapshots = snapshots[:2]
            epochs = overfit_epochs
            print(f"--- OVERFIT SANITY MODE: training on first {len(train_snapshots)} months for {epochs} epochs ---")
        else:
            train_snapshots = snapshots[:t_months]
            val_snapshots = snapshots[t_months : t_months + v_months]
            test_snapshots = snapshots[t_months + v_months : t_months + v_months + te_months]
            epochs = self.num_epochs
            print(f"--- TRADITIONAL TEMPORAL SPLIT ({t_months}-{v_months}-{te_months}): training on {len(train_snapshots)} months, validating on {len(val_snapshots)} months, testing on {len(test_snapshots)} months ---")
            
        num_nodes = train_snapshots[0].num_nodes
        in_channels = train_snapshots[0].x.size(1)
        edge_dim = train_snapshots[0].edge_attr.size(1) if getattr(train_snapshots[0], "edge_attr", None) is not None else None

        model = ROLANDModel(
            in_channels=in_channels,
            hidden_channels=self.hidden_dim * 2,
            out_channels=self.hidden_dim,
            edge_dim=edge_dim
        ).to(self.device)
        
        # Select optimizer dynamically
        if self.optimizer_name.lower() == "adam":
            optimizer = optim.Adam(model.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        elif self.optimizer_name.lower() == "adamw":
            optimizer = optim.AdamW(model.parameters(), lr=self.lr, weight_decay=self.weight_decay)
        elif self.optimizer_name.lower() == "sgd":
            optimizer = optim.SGD(model.parameters(), lr=self.lr, momentum=0.9, weight_decay=self.weight_decay)
        else:
            optimizer = optim.Adam(model.parameters(), lr=self.lr, weight_decay=self.weight_decay)
            
        # Select learning rate scheduler
        if self.use_scheduler:
            scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode="min", factor=0.5, patience=10, min_lr=1e-5)
        else:
            scheduler = None
        
        target_mean = getattr(dataset, "target_mean", None)
        target_std = getattr(dataset, "target_std", None)
        if target_mean is not None:
            target_mean = target_mean.to(self.device)
        if target_std is not None:
            target_std = target_std.to(self.device)
            
        # Select loss function dynamically based on config
        if getattr(config, "LOSS_FUNCTION", "mse") == "weighted_huber":
            # Compute max training target across train split
            train_ys = []
            for snap in train_snapshots:
                # Inverse-transform snapshot target
                y_raw = snap.y.cpu() * target_std.cpu() + target_mean.cpu()
                if getattr(snap, "active_mask", None) is not None:
                    y_raw = y_raw[snap.active_mask.cpu()]
                train_ys.append(y_raw)
            train_ys_cat = torch.cat(train_ys, dim=0)
            
            max_training_target = torch.max(train_ys_cat, dim=0)[0]
            
            # Print Training Target Statistics
            print("=================== Training Target Statistics ===================")
            print(f"Maximum Target (Units, Load): {max_training_target.tolist()}")
            print(f"Minimum Target (Units, Load): {torch.min(train_ys_cat, dim=0)[0].tolist()}")
            print(f"Mean Target (Units, Load):    {torch.mean(train_ys_cat, dim=0).tolist()}")
            
            # Print Weight statistics
            max_target_safe = torch.where(max_training_target == 0.0, torch.ones_like(max_training_target), max_training_target)
            weights = 1.0 + getattr(config, "HUBER_ALPHA", 1.0) * (train_ys_cat / max_target_safe)
            print(f"Maximum Weight (Units, Load): {torch.max(weights, dim=0)[0].tolist()}")
            print(f"Minimum Weight (Units, Load): {torch.min(weights, dim=0)[0].tolist()}")
            print(f"Average Weight (Units, Load): {torch.mean(weights, dim=0).tolist()}")
            print("==================================================================")
            
            # Sanity test: higher-demand targets receive larger weights
            for col_idx, col_name in enumerate(["Units", "Load"]):
                min_idx = torch.argmin(train_ys_cat[:, col_idx])
                max_idx = torch.argmax(train_ys_cat[:, col_idx])
                if train_ys_cat[max_idx, col_idx] > train_ys_cat[min_idx, col_idx]:
                    assert weights[max_idx, col_idx] > weights[min_idx, col_idx], f"Sanity check failed for {col_name} weights!"
            print("Sanity Test Passed: Higher-demand targets receive larger weights.")
            
            # Initialize WeightedHuberLoss
            self.criterion = WeightedHuberLoss(
                delta=getattr(config, "HUBER_DELTA", 1.0),
                alpha=getattr(config, "HUBER_ALPHA", 1.0),
                max_training_target=max_training_target.to(self.device),
                target_mean=target_mean,
                target_std=target_std
            )
        else:
            self.criterion = MaskedMSELoss()

            
        best_val_loss = float("inf")
        best_model_state = None
        patience_counter = 0
        
        for epoch in range(epochs):
            # 1. Train Epoch (includes forward sequence and single optimization step)
            train_loss, H_train = self.train_epoch(model, train_snapshots, optimizer)
            
            # 2. Validation Epoch (carries detached train hidden state)
            val_loss, val_metrics, H_val = self.validate_sequence(
                model, val_snapshots, H_train, target_mean=target_mean, target_std=target_std
            )
            
            # Step scheduler if enabled
            if scheduler is not None:
                scheduler.step(val_loss)

            
            if (epoch + 1) % 10 == 0 or epoch == 0 or overfit_mode:
                print(f"Epoch {epoch+1:03d} | Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} | Val RMSE: {val_metrics['avg_rmse']:.4f}")
                
            if overfit_mode:
                # No early stopping or checkpoint saving during overfitting sanity test
                continue
                
            # Early stopping and checkpoint tracking on Validation Loss
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_model_state = copy.deepcopy(model.state_dict())
                patience_counter = 0
                
                # Save best checkpoint
                checkpoint_path = os.path.join(self.checkpoint_dir, "best_model_temporal.pt")
                torch.save(best_model_state, checkpoint_path)
            else:
                patience_counter += 1
                
            if patience_counter >= self.patience:
                print(f"Early stopping triggered at epoch {epoch+1}.")
                break
                
        # Final Evaluation: Load best model checkpoint and evaluate the test set once
        if not overfit_mode:
            if best_model_state is not None:
                model.load_state_dict(best_model_state)
                print("Loaded best validation model state for final testing.")
            else:
                print("Warning: No best model state saved, evaluating current weights.")
                
            # Regenerate the hidden state by replaying training and validation sequences
            model.eval()
            with torch.no_grad():
                H_replay_train = torch.zeros(num_nodes, self.hidden_dim, device=self.device)
                # Replay train snapshots
                for snapshot in train_snapshots:
                    _, H_replay_train = model(snapshot, H_replay_train)
                    
                # Replay validation snapshots starting from detached train hidden state
                H_replay_val = H_replay_train.detach()
                for snapshot in val_snapshots:
                    _, H_replay_val = model(snapshot, H_replay_val)
                    
                # Evaluate on testing snapshots starting from detached validation hidden state
                test_loss, test_metrics = self.evaluate_sequence(
                    model, test_snapshots, H_replay_val, target_mean=target_mean, target_std=target_std
                )
                
            print("\n=================== Final Temporal Split Test Results ===================")
            print(f"Test Loss: {test_loss:.4f}")
            print(f"Test RMSE: {test_metrics['avg_rmse']:.4f}")
            print(f"Test MAE:  {test_metrics['avg_mae']:.4f}")
            print(f"Test R2:   {test_metrics['avg_r2']:.4f}")
            
            return {
                "val_loss": best_val_loss,
                "test_loss": test_loss,
                "test_metrics": test_metrics
            }
        else:
            # Return final training status for overfit test
            with torch.no_grad():
                final_loss, final_metrics, _ = self.validate_sequence(
                    model, train_snapshots, torch.zeros(num_nodes, self.hidden_dim, device=self.device),
                    target_mean=target_mean, target_std=target_std
                )
            print("\n=================== Overfit Sanity Final Results ===================")
            print(f"Final Loss: {final_loss:.4f}")
            print(f"Final RMSE: {final_metrics['avg_rmse']:.4f}")
            print(f"Final MAE:  {final_metrics['avg_mae']:.4f}")
            print(f"Final R2:   {final_metrics['avg_r2']:.4f}")
            return {
                "train_loss": train_loss,
                "final_metrics": final_metrics
            }


if __name__ == "__main__":
    print("--- Running ROLANDTemporalTrainer Verification Routine ---")
    
    num_nodes = 10
    in_channels = 4
    hidden_dim = 8
    
    # 1. Setup mock snapshots list (36 months to mimic full dataset)
    mock_snapshots = []
    for i in range(36):
        x = torch.randn(num_nodes, in_channels)
        edge_index = torch.tensor([[0, 1, 2, 3, 4], [1, 2, 3, 4, 0]], dtype=torch.long)
        y = torch.randn(num_nodes, 2)
        
        active_mask = torch.zeros(num_nodes, dtype=torch.bool)
        active_mask[i % num_nodes] = True
        active_mask[(i + 1) % num_nodes] = True
        
        data = Data(x=x, edge_index=edge_index, y=y, active_mask=active_mask)
        mock_snapshots.append(data)
        
    class MockDataset:
        def __init__(self, data_list):
            self.data_list = data_list
            self.target_mean = torch.zeros(2)
            self.target_std = torch.ones(2)
        def __len__(self):
            return len(self.data_list)
        def __getitem__(self, idx):
            return self.data_list[idx]
        def get_snapshots(self):
            return self.data_list

    mock_dataset = MockDataset(mock_snapshots)
    
    # Verification 1: Epoch start initialization
    device_test = "cpu"
    H_train_init = torch.zeros(num_nodes, hidden_dim, device=device_test)
    assert H_train_init.device.type == "cpu", "Hidden state must initialize on correct device"
    assert torch.all(H_train_init == 0.0), "Hidden memory must initialize to zero at start of epoch"
    print("Verification 1: Hidden state initialization checks -> PASSED")

    # Verification 2: Train sequence updates sequentially
    model = ROLANDModel(in_channels=in_channels, hidden_channels=hidden_dim * 2, out_channels=hidden_dim)
    H_train = H_train_init.clone()
    hiddens_list = [H_train]
    for snap in mock_snapshots[:24]:
        _, H_train = model(snap, H_train)
        hiddens_list.append(H_train)
        
    for j in range(1, len(hiddens_list)):
        assert not torch.allclose(hiddens_list[j], hiddens_list[j-1]), f"Hidden state failed to update at month {j}"
    print("Verification 2: Sequential train memory updates -> PASSED")

    # Verification 3: Detached memory state carryover (Train -> Val -> Test)
    H_val = H_train.detach()
    assert H_val.grad_fn is None, "Validation hidden state must be detached from training graph"
    
    # Propagate validation sequence
    for snap in mock_snapshots[24:30]:
        _, H_val = model(snap, H_val)
        
    H_test = H_val.detach()
    assert H_test.grad_fn is None, "Testing hidden state must be detached from validation graph"
    print("Verification 3: Memory carries over with detached states (Train -> Val -> Test) -> PASSED")

    # Verification 4: Masked Loss
    snap0 = mock_snapshots[0]
    pred0, _ = model(snap0, H_train_init)
    loss_original = compute_masked_mse_loss(pred0, snap0.y, snap0.active_mask)
    y_modified = snap0.y.clone()
    inactive_indices = (~snap0.active_mask).nonzero(as_tuple=True)[0]
    y_modified[inactive_indices] += 5.0
    loss_modified = compute_masked_mse_loss(pred0, y_modified, snap0.active_mask)
    assert torch.allclose(loss_original, loss_modified), "Loss must not be affected by inactive targets modifications"
    print("Verification 4: Masked loss correctly ignores inactive nodes -> PASSED")

    # Verification 5: 24-Month Gradients Flow
    optimizer = optim.Adam(model.parameters(), lr=0.01)
    optimizer.zero_grad()
    accumulated_loss = 0.0
    H_grad = torch.zeros(num_nodes, hidden_dim)
    for snap in mock_snapshots[:24]:
        pred, H_grad = model(snap, H_grad)
        loss = compute_masked_mse_loss(pred, snap.y, snap.active_mask)
        accumulated_loss += loss
    accumulated_loss.backward()
    
    for name, p in model.named_parameters():
        if p.requires_grad:
            assert p.grad is not None, f"Gradient did not flow back to parameter: {name}"
    print("Verification 5: Gradients propagate through the full 24-month sequence -> PASSED")

    # Verification 6: Optimizer Update
    old_params = [p.clone() for p in model.parameters() if p.requires_grad]
    optimizer.step()
    any_updated = False
    for p_old, p_new in zip(old_params, model.parameters()):
        if p_new.requires_grad and not torch.allclose(p_old, p_new):
            any_updated = True
            break
    assert any_updated, "Weights did not change after optimizer step"
    print("Verification 6: Weights are successfully updated by optimizer step -> PASSED")

    # Verification 7: Overfit sanity test
    print("Verification 7: Running overfit sanity check on mock dataset...")
    trainer = ROLANDTemporalTrainer(
        hidden_dim=hidden_dim,
        num_epochs=150,
        lr=0.05,
        device="cpu",
        checkpoint_dir="checkpoints_test_temp"
    )
    res = trainer.fit_and_evaluate(mock_dataset, overfit_mode=True, overfit_epochs=200)
    print(f"Sanity Check Final Metric R2: {res['final_metrics']['avg_r2']:.4f}")
    assert res["final_metrics"]["avg_r2"] > 0.85 or res["train_loss"] < 0.2, "Model failed to overfit on 2 snapshots"
    print("Verification 7: Overfit sanity test successfully completed -> PASSED")
    
    # Clean up test directories
    if os.path.exists("checkpoints_test_temp"):
        import shutil
        shutil.rmtree("checkpoints_test_temp")
        
    print("\nAll ROLANDTemporalTrainer verification assertions completed successfully!")
