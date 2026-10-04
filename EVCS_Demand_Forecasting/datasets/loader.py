import torch
from torch_geometric.data import Data
from typing import List, Tuple, Generator, Optional

class TemporalGraphLoader:
    """
    Lightweight sequential loader for chronological iteration over monthly graph snapshots.
    Guarantees no shuffling and no temporal leakage.
    """
    def __init__(self, snapshots: List[Data]):
        """
        Args:
            snapshots: List of chronological PyG Data objects.
        """
        self.snapshots = snapshots
        self.current_idx = 0

    def __iter__(self) -> "TemporalGraphLoader":
        self.current_idx = 0
        return self

    def __next__(self) -> Data:
        if self.current_idx >= len(self.snapshots):
            raise StopIteration
        snapshot = self.snapshots[self.current_idx]
        self.current_idx += 1
        return snapshot

    def __len__(self) -> int:
        return len(self.snapshots)

    def __getitem__(self, idx: int) -> Data:
        return self.snapshots[idx]


class SlidingWindowSplitter:
    """
    Generates rolling sliding windows for training, validation, and testing.
    By default:
      - Train window size = 6 months
      - Validation window size = 1 month (7th month)
      - Test window size = 1 month (8th month)
      - Step size = 1 month
    """
    def __init__(
        self,
        snapshots: List[Data],
        train_size: int = 6,
        val_size: int = 1,
        test_size: int = 1,
        step_size: int = 1
    ):
        """
        Args:
            snapshots: Chronological sequence of monthly graphs.
            train_size: Number of months for training.
            val_size: Number of months for validation.
            test_size: Number of months for testing.
            step_size: Slide step size in months.
        """
        self.snapshots = snapshots
        self.train_size = train_size
        self.val_size = val_size
        self.test_size = test_size
        self.step_size = step_size
        self.window_size = train_size + val_size + test_size

    def get_windows(self) -> Generator[Tuple[List[Data], List[Data], List[Data]], None, None]:
        """
        Yields a sequence of (train_split, val_split, test_split) for each sliding window step.
        Each split is a list of PyG Data objects.
        """
        num_snapshots = len(self.snapshots)
        offset = 0
        
        while offset + self.window_size <= num_snapshots:
            train_end = offset + self.train_size
            val_end = train_end + self.val_size
            test_end = val_end + self.test_size
            
            train_split = self.snapshots[offset:train_end]
            val_split = self.snapshots[train_end:val_end]
            test_split = self.snapshots[val_end:test_end]
            
            yield train_split, val_split, test_split
            offset += self.step_size

    def get_num_windows(self) -> int:
        """
        Returns the total number of sliding windows.
        """
        num_snapshots = len(self.snapshots)
        if num_snapshots < self.window_size:
            return 0
        return (num_snapshots - self.window_size) // self.step_size + 1
