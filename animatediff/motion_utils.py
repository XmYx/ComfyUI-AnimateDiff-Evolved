from abc import ABC, abstractmethod
from typing import Union

import torch
import torch.nn.functional as F
from torch import Tensor, nn

import comfy.model_management as model_management
import comfy.ops
from comfy.cli_args import args
from comfy.ldm.modules.attention import attention_basic, attention_pytorch, attention_split, attention_sub_quad, default
from .motion_lora import MotionLoRAInfo

# until xformers bug is fixed, do not use xformers for VersatileAttention! TODO: change this when fix is out
# logic for choosing optimized_attention method taken from comfy/ldm/modules/attention.py
optimized_attention_mm = attention_basic
if model_management.xformers_enabled():
    pass
    #optimized_attention_mm = attention_xformers
if model_management.pytorch_attention_enabled():
    optimized_attention_mm = attention_pytorch
else:
    if args.use_split_cross_attention:
        optimized_attention_mm = attention_split
    else:
        optimized_attention_mm = attention_sub_quad


class CrossAttentionMM(nn.Module):
    def __init__(self, query_dim, context_dim=None, heads=8, dim_head=64, dropout=0., dtype=None, device=None, operations=comfy.ops):
        super().__init__()
        inner_dim = dim_head * heads
        context_dim = default(context_dim, query_dim)

        self.heads = heads
        self.dim_head = dim_head
        self.scale = None
        self.default_scale = dim_head ** -0.5

        self.to_q = operations.Linear(query_dim, inner_dim, bias=False, dtype=dtype, device=device)
        self.to_k = operations.Linear(context_dim, inner_dim, bias=False, dtype=dtype, device=device)
        self.to_v = operations.Linear(context_dim, inner_dim, bias=False, dtype=dtype, device=device)

        self.to_out = nn.Sequential(operations.Linear(inner_dim, query_dim, dtype=dtype, device=device), nn.Dropout(dropout))

    def forward(self, x, context=None, value=None, mask=None):
        q = self.to_q(x)
        context = default(context, x)
        k = self.to_k(context)
        if value is not None:
            v = self.to_v(value)
            del value
        else:
            v = self.to_v(context)

        # apply custom scale by multiplying k by scale factor;
        # division by default_scale is needed to account for internal attn code multiplying by default_scale
        if self.scale is not None:
            k *= (self.scale / self.default_scale)
        out = optimized_attention_mm(q, k, v, self.heads, mask)
        return self.to_out(out)


class BlockType:
    UP = "up"
    DOWN = "down"
    MID = "mid"


class InjectorVersion:
    V1_V2 = "v1/v2"
    HOTSHOTXL_V1 = "HSXL v1"


class GenericMotionWrapper(nn.Module, ABC):
    def __init__(self, mm_hash: str, mm_name: str, loras: list[MotionLoRAInfo]):
        super().__init__()
        self.down_blocks: nn.ModuleList = None
        self.up_blocks: nn.ModuleList = None
        self.mid_block = None
        self.mm_hash = mm_hash
        self.mm_name = mm_name
        self.version = "FILLTHISIN"
        self.injector_version = "VERYIMPORTANT_FILLTHISIN"
        self.AD_video_length: int = 0
        self.loras = loras

    def has_loras(self) -> bool:
        # TODO: fix this to return False if has an empty list as well
        # but only after implementing a fix for lowvram loading
        return self.loras is not None

    @abstractmethod
    def set_video_length(self, video_length: int):
        pass

    @abstractmethod
    def set_scale_multiplier(self, multiplier: Union[float, None]):
        pass

    def reset_scale_multiplier(self):
        self.set_scale_multiplier(None)

    @abstractmethod
    def set_sub_idxs(self, sub_idxs: list[int]):
        pass


class GroupNormAD(torch.nn.GroupNorm):
    def __init__(self, num_groups: int, num_channels: int, eps: float = 1e-5, affine: bool = True,
                 device=None, dtype=None) -> None:
        super().__init__(num_groups=num_groups, num_channels=num_channels, eps=eps, affine=affine, device=device, dtype=dtype)
    
    def forward(self, input: Tensor) -> Tensor:
        return F.group_norm(
             input, self.num_groups, self.weight, self.bias, self.eps)
