import torch
import torch.nn as nn
from models.roland_layers import ResidualEdgeConv, GRUUpdater
from typing import Optional, Tuple

class ROLANDModel(nn.Module):
    """
    Stanford ROLAND model composing ResidualEdgeConv, GRUUpdater, and a prediction regression head.
    Matches the layer wrapping behavior of GeneralRecurrentLayer and GraphRecurrentLayerWrapper.
    """
    def __init__(
        self,
        in_channels: int,
        hidden_channels: int,
        out_channels: int,
        num_targets: int = 2,
        act: str = "relu",
        dropout: float = 0.0,
        edge_dim: Optional[int] = None,
        msg_direction: str = "both",
        normalize: bool = True,
        improved: bool = False,
        bias: bool = True,
        skip_connection: str = "affine",
        aggr: str = "add"
    ):
        """
        Args:
            in_channels: Input node feature dimension.
            hidden_channels: Hidden dimension inside message MLP. Set to 0 to use a single linear layer.
            out_channels: Output node embedding/state memory dimension.
            num_targets: Number of target prediction values (defaults to 2 for Units and Load).
            act: Activation function name ("relu", "gelu", "leaky_relu").
            dropout: Dropout probability.
            edge_dim: Optional dimension of edge features.
            msg_direction: Message aggregation direction ("both" or "single").
            normalize: Whether to apply GCN degree normalization.
            improved: Whether to use improved self-loops in normalization.
            bias: Whether to add a bias parameter.
            skip_connection: Type of residual path skip connection ("affine", "identity", "none").
            aggr: GNN aggregation method ("add", "mean", "max").
        """
        super(ROLANDModel, self).__init__()

        # 1. Spatial GNN Encoder (ResidualEdgeConv)
        self.encoder = ResidualEdgeConv(
            in_channels=in_channels,
            hidden_channels=hidden_channels,
            out_channels=out_channels,
            act=act,
            dropout=dropout,
            edge_dim=edge_dim,
            msg_direction=msg_direction,
            normalize=normalize,
            improved=improved,
            bias=bias,
            skip_connection=skip_connection,
            aggr=aggr
        )

        # 2. Recurrent Memory state update (GRUUpdater)
        self.memory = GRUUpdater(
            dim_in=out_channels,  # Takes computed node representation from the encoder
            dim_out=out_channels  # State size remains constant
        )

        # 3. Regression prediction head (Linear -> ReLU -> Linear)
        # Generates final regression targets per node
        mid_dim = out_channels // 2 if out_channels >= 4 else out_channels
        self.regression_head = nn.Sequential(
            nn.Linear(out_channels, mid_dim),
            nn.ReLU(),
            nn.Linear(mid_dim, num_targets)
        )

    def forward(
        self,
        data,
        prev_memory: torch.Tensor
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Performs the forward computation step for a single temporal graph snapshot.
        Memory initialization is handled outside the model.
        
        Args:
            data: PyTorch Geometric Data snapshot object containing:
                  - x: Node feature matrix of shape (num_nodes, in_channels).
                  - edge_index: Graph connectivity matrix of shape (2, num_edges).
                  - active_mask: Boolean active node indicator of shape (num_nodes,).
                  - edge_attr: Optional edge attribute matrix of shape (num_edges, edge_dim).
            prev_memory: Node hidden state memory of shape (num_nodes, out_channels).
        Returns:
            predictions: Target demand predictions of shape (num_nodes, num_targets).
            Ht: Updated node memory states of shape (num_nodes, out_channels).
        """
        # 1. Spatial GNN encoding step
        Xt = self.encoder(
            x=data.x,
            edge_index=data.edge_index,
            edge_attr=getattr(data, "edge_attr", None)
        )

        # 2. Recurrent memory updating step (under active node mask)
        Ht = self.memory(
            x=Xt,
            prev_memory=prev_memory,
            active_mask=data.active_mask
        )

        # 3. Regression output generation step
        predictions = self.regression_head(Ht)

        return predictions, Ht


if __name__ == "__main__":
    import torch_geometric as pyg
    from torch_geometric.data import Data
    
    print("--- Testing ROLANDModel ---")
    N = 120    # Arbitrary node count
    F = 45     # Input node features (matching dynamic EVCS feature counts)
    E = 350    # Edge count
    out_dim = 64
    hidden_dim = 128
    
    # 1. Prepare dummy Data object
    x = torch.randn(N, F)
    edge_index = torch.randint(0, N, (2, E))
    active_mask = torch.zeros(N, dtype=torch.bool)
    active_mask[:40] = True  # 40 active nodes, 80 inactive
    
    data = Data(x=x, edge_index=edge_index, active_mask=active_mask)
    prev_memory = torch.randn(N, out_dim)
    
    # Initialize ROLAND model
    model = ROLANDModel(
        in_channels=F,
        hidden_channels=hidden_dim,
        out_channels=out_dim,
        num_targets=2
    )
    
    # 2. Test forward pass
    predictions, updated_memory = model(data, prev_memory)
    print("Predictions shape:", predictions.shape)
    print("Updated memory shape:", updated_memory.shape)
    
    assert predictions.shape == (N, 2), f"Expected predictions shape {(N, 2)}, got {predictions.shape}"
    assert updated_memory.shape == (N, out_dim), f"Expected memory shape {(N, out_dim)}, got {updated_memory.shape}"
    
    # 3. Test state preservation of inactive nodes in memory
    # For inactive nodes (indices 40 to 119), updated memory must match prev_memory
    inactive_preserved = torch.allclose(updated_memory[40:], prev_memory[40:])
    print("State preservation for inactive nodes:", inactive_preserved)
    assert inactive_preserved, "Inactive node memories were modified!"
    
    # Active nodes should be updated
    active_changed = torch.allclose(updated_memory[:40], prev_memory[:40])
    print("State change for active nodes (should be False):", active_changed)
    assert not active_changed, "Active node memories were not updated!"
    
    # 4. Test forward pass with optional edge features
    D = 10  # Edge attribute dimension
    edge_attr = torch.randn(E, D)
    data_with_edge = Data(x=x, edge_index=edge_index, active_mask=active_mask, edge_attr=edge_attr)
    
    model_with_edge = ROLANDModel(
        in_channels=F,
        hidden_channels=hidden_dim,
        out_channels=out_dim,
        num_targets=2,
        edge_dim=D
    )
    
    pred_edge, mem_edge = model_with_edge(data_with_edge, prev_memory)
    print("Predictions (with edge features) shape:", pred_edge.shape)
    print("Updated memory (with edge features) shape:", mem_edge.shape)
    assert pred_edge.shape == (N, 2)
    assert mem_edge.shape == (N, out_dim)
    
    # 5. Test gradient propagation
    loss = predictions.sum()
    loss.backward()
    print("Gradient propagation verification: backward pass completed successfully.")
    
    # Verify that gradients exist for model parameters
    has_gradients = True
    for name, param in model.named_parameters():
        if param.requires_grad and param.grad is None:
            has_gradients = False
            print(f"Parameter {name} has no gradient!")
            break
    print("Gradient existence check:", has_gradients)
    assert has_gradients, "Some model parameters did not receive gradients!"
    
    print("\nAll ROLANDModel tests passed successfully!")
