"""
Custom attention modules for YOLOv8 neck.

CBAM (Convolutional Block Attention Module, Woo et al. 2018) applies
channel attention followed by spatial attention. Placed after C2f blocks
in the PANet neck so pretrained backbone weights remain fully transferable.

Defaults are tuned for YOLOv8n's narrow channels (64/128/256 after width
multiplier 0.25): reduction=8 keeps mid-channels ≥8 at every scale,
kernel_size=3 cuts spatial-conv params vs the paper's 7.
"""

import torch
import torch.nn as nn


class ChannelAttention(nn.Module):
    def __init__(self, c1: int, reduction: int = 8) -> None:
        super().__init__()
        c_mid = max(1, c1 // reduction)
        self.avg_pool = nn.AdaptiveAvgPool2d(1)
        self.max_pool = nn.AdaptiveMaxPool2d(1)
        self.fc = nn.Sequential(
            nn.Conv2d(c1, c_mid, 1, bias=False),
            nn.ReLU(inplace=True),
            nn.Conv2d(c_mid, c1, 1, bias=False),
        )
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.sigmoid(self.fc(self.avg_pool(x)) + self.fc(self.max_pool(x)))


class SpatialAttention(nn.Module):
    def __init__(self, kernel_size: int = 3) -> None:
        super().__init__()
        padding = kernel_size // 2
        self.conv = nn.Conv2d(2, 1, kernel_size, padding=padding, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        avg_out = x.mean(dim=1, keepdim=True)
        max_out, _ = x.max(dim=1, keepdim=True)
        return self.sigmoid(self.conv(torch.cat([avg_out, max_out], dim=1)))


class CBAM(nn.Module):
    """
    Convolutional Block Attention Module.

    Ultralytics parse_model usage:
        - [-1, 1, CBAM, [c1]]            # c1 = input channels (no channel change)
        - [-1, 1, CBAM, [c1, reduction]] # optional reduction override
    """

    def __init__(self, c1: int, reduction: int = 8, kernel_size: int = 3) -> None:
        super().__init__()
        self.ca = ChannelAttention(c1, reduction)
        self.sa = SpatialAttention(kernel_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x * self.ca(x)
        return x * self.sa(x)


class ECA(nn.Module):
    """
    Efficient Channel Attention (Wang et al. 2020).
    Lighter than CBAM: a single depthwise 1-D conv over the channel dimension,
    no dimensionality reduction. Use as a drop-in swap for CBAM.

    Ultralytics parse_model usage:
        - [-1, 1, ECA, [c1]]
    """

    def __init__(self, c1: int, k: int = 3) -> None:
        super().__init__()
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.conv = nn.Conv1d(1, 1, k, padding=k // 2, bias=False)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.pool(x)                                  # (B, C, 1, 1)
        y = self.conv(y.squeeze(-1).transpose(-1, -2))    # (B, 1, C)
        y = y.transpose(-1, -2).unsqueeze(-1)             # (B, C, 1, 1)
        return x * self.sigmoid(y)


def register_custom_modules() -> None:
    """
    Inject custom modules into the Ultralytics namespace so parse_model finds
    them by name from YAML definitions.

    parse_model in tasks.py resolves unknown module names via globals() of that
    module (i.e. tasks.__dict__), so we must inject there — not just into
    ultralytics.nn.modules. Both are patched for completeness.

    parse_model falls through to `else: c2 = ch[f]` for unregistered modules,
    which correctly propagates passthrough channels for CBAM/ECA (c_in == c_out).
    """
    import ultralytics.nn.modules as _m
    import ultralytics.nn.tasks as _tasks

    for cls in (CBAM, ECA, ChannelAttention, SpatialAttention):
        setattr(_m, cls.__name__, cls)
        _tasks.__dict__[cls.__name__] = cls  # tasks.py globals() lookup
