import torch
import torch.nn as nn

class WeightedHuberLoss(nn.Module):
    """
    Weighted Huber Loss:
    Loss = Weight(target) * Huber(target, prediction)
    
    Weights are computed from the original raw scale targets (inverse-transformed) to avoid scale bias.
    Weighting function:
        weight = 1 + alpha * (target_original / max_training_target)
    """
    def __init__(self, delta: float = 1.0, alpha: float = 1.0, max_training_target: torch.Tensor = None, target_mean: torch.Tensor = None, target_std: torch.Tensor = None):
        super(WeightedHuberLoss, self).__init__()
        self.delta = delta
        self.alpha = alpha
        
        # Save max training target and standardization parameters
        # These are PyTorch Tensors of shape [2] (for units and load)
        self.register_buffer("max_training_target", max_training_target)
        self.register_buffer("target_mean", target_mean)
        self.register_buffer("target_std", target_std)
        
    def forward(self, pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor = None) -> torch.Tensor:
        """
        Computes the masked weighted Huber loss.
        """
        # Inverse transform to original raw scale target values
        if self.target_mean is not None and self.target_std is not None:
            target_original = target * self.target_std + self.target_mean
        else:
            target_original = target
            
        # Compute weights: 1 + alpha * (target_original / max_training_target)
        if self.max_training_target is not None:
            max_target_safe = torch.where(self.max_training_target == 0.0, torch.ones_like(self.max_training_target), self.max_training_target)
            weight = 1.0 + self.alpha * (target_original / max_target_safe)
        else:
            weight = torch.ones_like(target)
            
        # Huber Loss computation
        huber_val = torch.nn.functional.huber_loss(pred, target, delta=self.delta, reduction='none')
        
        # Apply weight
        weighted_loss = weight * huber_val
        
        if mask is not None:
            weighted_loss = weighted_loss[mask]
            
        return torch.mean(weighted_loss)
