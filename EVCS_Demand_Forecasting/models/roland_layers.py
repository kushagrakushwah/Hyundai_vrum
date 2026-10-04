import torch
import torch.nn as nn
from torch.nn import Parameter
from torch_geometric.nn.conv import MessagePassing
from torch_geometric.nn.inits import zeros
from torch_geometric.utils import add_remaining_self_loops
from typing import Optional, Union

class ResidualEdgeConvLayer(MessagePassing):
    """
    Message passing GNN layer inspired by Stanford ROLAND, with optional edge features and 
    flexible skip connections.
    """
    def __init__(
        self,
        in_channels: int,
        hidden_channels: int,
        out_channels: int,
        act: str = "relu",
        dropout: float = 0.0,
        edge_dim: Optional[int] = None,
        msg_direction: str = "both",
        normalize: bool = True,
        improved: bool = False,
        bias: bool = True,
        skip_connection: str = "affine",
        aggr: str = "add",
        **kwargs
    ):
        super(ResidualEdgeConvLayer, self).__init__(aggr=aggr, **kwargs)

        self.in_channels = in_channels
        self.hidden_channels = hidden_channels
        self.out_channels = out_channels
        self.improved = improved
        self.normalize = normalize
        self.msg_direction = msg_direction
        self.skip_connection = skip_connection
        self.edge_dim = edge_dim if edge_dim is not None else 0

        # Input dimension to message layer/MLP
        # If msg_direction is 'both', we concat target features, source features, and edge features:
        # [x_i, x_j, edge_attr] -> 2 * in_channels + edge_dim
        # If msg_direction is 'single', we concat source features and edge features:
        # [x_j, edge_attr] -> in_channels + edge_dim
        if self.msg_direction == "single":
            self.msg_in_dim = in_channels + self.edge_dim
        elif self.msg_direction == "both":
            self.msg_in_dim = in_channels * 2 + self.edge_dim
        else:
            raise ValueError(f"Invalid msg_direction '{self.msg_direction}'. Must be 'single' or 'both'.")

        # Configurable MLP or Linear layer for message computation
        # If hidden_channels > 0, we instantiate a 2-layer MLP with activation and dropout.
        # If hidden_channels == 0, we fall back to a single Linear layer matching the original ROLAND code.
        if hidden_channels > 0:
            if act.lower() == "relu":
                act_layer = nn.ReLU()
            elif act.lower() == "gelu":
                act_layer = nn.GELU()
            elif act.lower() == "leaky_relu":
                act_layer = nn.LeakyReLU()
            else:
                act_layer = nn.ReLU()
                
            self.linear_msg = nn.Sequential(
                nn.Linear(self.msg_in_dim, hidden_channels),
                act_layer,
                nn.Dropout(dropout),
                nn.Linear(hidden_channels, out_channels, bias=False)
            )
        else:
            self.linear_msg = nn.Linear(self.msg_in_dim, out_channels, bias=False)

        # Skip connection configuration
        if self.skip_connection == "affine":
            self.linear_skip = nn.Linear(in_channels, out_channels, bias=True)
        elif self.skip_connection == "identity":
            assert self.in_channels == self.out_channels, "Identity skip connection requires in_channels == out_channels."
            self.linear_skip = nn.Identity()
        else:
            self.linear_skip = None

        if bias:
            self.bias = Parameter(torch.Tensor(out_channels))
        else:
            self.register_parameter("bias", None)

        self.reset_parameters()

    def reset_parameters(self):
        if self.bias is not None:
            zeros(self.bias)
            
        if isinstance(self.linear_msg, nn.Sequential):
            for layer in self.linear_msg:
                if isinstance(layer, nn.Linear):
                    nn.init.xavier_uniform_(layer.weight)
        elif isinstance(self.linear_msg, nn.Linear):
            nn.init.xavier_uniform_(self.linear_msg.weight)

        if isinstance(self.linear_skip, nn.Linear):
            nn.init.xavier_uniform_(self.linear_skip.weight)

    @staticmethod
    def norm(edge_index, num_nodes, edge_weight=None, improved=False, dtype=None):
        if edge_weight is None:
            edge_weight = torch.ones((edge_index.size(1),), dtype=dtype, device=edge_index.device)

        fill_value = 1 if not improved else 2
        edge_index, edge_weight = add_remaining_self_loops(edge_index, edge_weight, fill_value, num_nodes)

        # Compute degree sums natively in PyTorch to avoid torch_scatter dependencies
        row, col = edge_index
        deg = torch.zeros((num_nodes,), dtype=edge_weight.dtype, device=edge_index.device)
        deg.scatter_add_(0, row, edge_weight)
        
        deg_inv_sqrt = deg.pow(-0.5)
        deg_inv_sqrt[deg_inv_sqrt == float("inf")] = 0.0

        return edge_index, deg_inv_sqrt[row] * edge_weight * deg_inv_sqrt[col]

    def forward(self, x, edge_index, edge_weight=None, edge_feature=None):
        if self.normalize:
            edge_index, norm = self.norm(edge_index, x.size(self.node_dim), edge_weight, self.improved, x.dtype)
        else:
            norm = edge_weight

        # Handle edge_feature padding for added self-loops
        if edge_feature is not None:
            diff = edge_index.size(1) - edge_feature.size(0)
            if diff > 0:
                pad = torch.zeros((diff, edge_feature.size(1)), dtype=edge_feature.dtype, device=edge_feature.device)
                edge_feature = torch.cat([edge_feature, pad], dim=0)
        elif self.edge_dim > 0:
            edge_feature = torch.zeros((edge_index.size(1), self.edge_dim), dtype=x.dtype, device=x.device)

        # Compute skip connection path
        if self.skip_connection == "affine":
            skip_x = self.linear_skip(x)
        elif self.skip_connection == "identity":
            skip_x = self.linear_skip(x)
        else:
            skip_x = 0.0

        # Perform message propagation
        out = self.propagate(edge_index, x=x, norm=norm, edge_feature=edge_feature)
        
        return out + skip_x

    def message(self, x_i, x_j, norm, edge_feature):
        # Concatenate features based on the message direction
        if self.msg_direction == "both":
            if edge_feature is not None:
                msg_input = torch.cat((x_i, x_j, edge_feature), dim=-1)
            else:
                msg_input = torch.cat((x_i, x_j), dim=-1)
        elif self.msg_direction == "single":
            if edge_feature is not None:
                msg_input = torch.cat((x_j, edge_feature), dim=-1)
            else:
                msg_input = x_j
        else:
            raise ValueError(f"Invalid msg_direction: {self.msg_direction}")

        # Compute message embeddings
        msg = self.linear_msg(msg_input)
        
        # Apply adjacency normalization coefficients
        return norm.view(-1, 1) * msg if norm is not None else msg

    def update(self, aggr_out):
        if self.bias is not None:
            aggr_out = aggr_out + self.bias
        return aggr_out

    def __repr__(self):
        return f"{self.__class__.__name__}(in_channels={self.in_channels}, out_channels={self.out_channels})"


class ResidualEdgeConv(nn.Module):
    """
    Modular Stanford ROLAND ResidualEdgeConv wrapper module.
    """
    def __init__(
        self,
        in_channels: int,
        hidden_channels: int,
        out_channels: int,
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
            out_channels: Output node embedding dimension.
            act: Activation function name ("relu", "gelu", "leaky_relu").
            dropout: Dropout probability.
            edge_dim: Optional dimension of edge features.
            msg_direction: Message aggregation direction ("both" or "single").
            normalize: Whether to apply symmetric degree normalization.
            improved: Whether to use improved self-loops during normalization.
            bias: Whether to add a bias parameter to the node embeddings.
            skip_connection: Type of residual path skip connection ("affine", "identity", "none").
            aggr: GNN aggregation method ("add", "mean", "max").
        """
        super(ResidualEdgeConv, self).__init__()
        self.conv = ResidualEdgeConvLayer(
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

    def forward(
        self,
        x: torch.Tensor,
        edge_index: torch.Tensor,
        edge_attr: Optional[torch.Tensor] = None
    ) -> torch.Tensor:
        """
        Args:
            x: Node feature matrix of shape (num_nodes, in_channels).
            edge_index: Graph connectivity matrix of shape (2, num_edges).
            edge_attr: Optional edge feature matrix of shape (num_edges, edge_dim).
        Returns:
            Output node embeddings of shape (num_nodes, out_channels).
        """
        return self.conv(x, edge_index, edge_feature=edge_attr)
class GRUUpdater(nn.Module):
    """
    Stanford ROLAND GRU-based node state updater with active node masking.
    Provides a manual three-gate (GRU_Z, GRU_R, GRU_H_Tilde) state transition
    to update dynamic node states across temporal snapshots.
    """
    def __init__(self, dim_in: int, dim_out: int):
        """
        Args:
            dim_in: Dimension of incoming node embeddings (X).
            dim_out: Dimension of previous and current hidden states (H).
        """
        super(GRUUpdater, self).__init__()
        
        # Update gate
        self.GRU_Z = nn.Sequential(
            nn.Linear(dim_in + dim_out, dim_out, bias=True),
            nn.Sigmoid()
        )
        # Reset gate
        self.GRU_R = nn.Sequential(
            nn.Linear(dim_in + dim_out, dim_out, bias=True),
            nn.Sigmoid()
        )
        # Candidate hidden state gate
        self.GRU_H_Tilde = nn.Sequential(
            nn.Linear(dim_in + dim_out, dim_out, bias=True),
            nn.Tanh()
        )

    def forward(
        self,
        x: torch.Tensor,
        prev_memory: torch.Tensor,
        active_mask: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            x: Node feature/embedding matrix of shape (num_nodes, dim_in).
            prev_memory: Previous snapshot node state matrix of shape (num_nodes, dim_out).
            active_mask: Boolean mask of shape (num_nodes,) where True indicates active nodes in the current snapshot.
        Returns:
            Updated snapshot node states of shape (num_nodes, dim_out).
        """
        # Concatenate current input and previous memory state
        concat_input = torch.cat([x, prev_memory], dim=1)
        
        # Calculate update gate Z and reset gate R
        Z = self.GRU_Z(concat_input)
        R = self.GRU_R(concat_input)
        
        # Calculate candidate state H_tilde with reset memory applied
        reset_memory = R * prev_memory
        concat_reset = torch.cat([x, reset_memory], dim=1)
        H_tilde = self.GRU_H_Tilde(concat_reset)
        
        # Compute fully updated state H_gru
        H_gru = Z * prev_memory + (1.0 - Z) * H_tilde
        
        # Masked update: only update active nodes, keep prev_memory for inactive nodes
        mask = active_mask.unsqueeze(1)
        updated_memory = torch.where(mask, H_gru, prev_memory)
        
        return updated_memory


if __name__ == "__main__":
    print("--- Testing ResidualEdgeConv Layer ---")
    N = 150  # Arbitrary node count
    F = 16   # Input feature dimension
    E = 400  # Edge count
    out_dim = 32
    hidden_dim = 64

    # Dummy inputs
    x = torch.randn(N, F)
    edge_index = torch.randint(0, N, (2, E))
    
    # 1. Test standard layer with MLP (hidden_channels > 0)
    layer = ResidualEdgeConv(
        in_channels=F,
        hidden_channels=hidden_dim,
        out_channels=out_dim,
        act="relu",
        dropout=0.1
    )
    out = layer(x, edge_index)
    print("Test 1 (MLP message layer) output shape:", out.shape)
    assert out.shape == (N, out_dim), f"Expected shape {(N, out_dim)}, got {out.shape}"

    # 2. Test fallback to linear layer (hidden_channels = 0)
    layer_linear = ResidualEdgeConv(
        in_channels=F,
        hidden_channels=0,
        out_channels=out_dim
    )
    out_linear = layer_linear(x, edge_index)
    print("Test 2 (Linear message layer) output shape:", out_linear.shape)
    assert out_linear.shape == (N, out_dim), f"Expected shape {(N, out_dim)}, got {out_linear.shape}"

    # 3. Test with edge attributes
    D = 8  # Edge feature dimension
    edge_attr = torch.randn(E, D)
    layer_edge = ResidualEdgeConv(
        in_channels=F,
        hidden_channels=hidden_dim,
        out_channels=out_dim,
        edge_dim=D
    )
    out_edge = layer_edge(x, edge_index, edge_attr=edge_attr)
    print("Test 3 (With edge attributes) output shape:", out_edge.shape)
    assert out_edge.shape == (N, out_dim), f"Expected shape {(N, out_dim)}, got {out_edge.shape}"

    # 4. Test backward pass (gradient flow)
    loss = out.sum()
    loss.backward()
    print("Test 4 (Backward pass) completed successfully. Gradients computed.")

    print("\n--- Testing GRUUpdater ---")
    dim_in = 32
    dim_out = 64
    num_nodes = 100

    # 5. Test forward pass of GRUUpdater
    updater = GRUUpdater(dim_in=dim_in, dim_out=dim_out)
    
    x_gru = torch.randn(num_nodes, dim_in)
    prev_mem = torch.randn(num_nodes, dim_out)
    
    # Active mask: first 30 nodes are active, rest 70 are inactive
    active_mask = torch.zeros(num_nodes, dtype=torch.bool)
    active_mask[:30] = True
    
    updated_mem = updater(x_gru, prev_mem, active_mask)
    print("Test 5 (GRUUpdater forward pass) output shape:", updated_mem.shape)
    assert updated_mem.shape == (num_nodes, dim_out), f"Expected shape {(num_nodes, dim_out)}, got {updated_mem.shape}"

    # 6. Test state preservation for inactive nodes
    # For inactive nodes (indices 30 to 99), updated memory should equal previous memory exactly
    inactive_equal = torch.allclose(updated_mem[30:], prev_mem[30:])
    print("Test 6 (State preservation for inactive nodes):", inactive_equal)
    assert inactive_equal, "Inactive nodes states were modified!"

    # 7. Test state update for active nodes
    # For active nodes (indices 0 to 29), updated memory should NOT equal previous memory
    active_equal = torch.allclose(updated_mem[:30], prev_mem[:30])
    print("Test 7 (State change for active nodes - should be False):", active_equal)
    assert not active_equal, "Active nodes states were not updated!"

    # 8. Test backward pass (gradient flow) on GRUUpdater
    loss_gru = updated_mem.sum()
    loss_gru.backward()
    print("Test 8 (GRUUpdater backward pass) completed successfully. Gradients computed.")
    
    print("\nAll ResidualEdgeConv and GRUUpdater tests passed successfully!")
