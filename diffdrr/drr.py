# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/00_drr.ipynb.

# %% ../notebooks/api/00_drr.ipynb 3
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from fastcore.basics import patch

from .detector import Detector
from .renderers import Siddon, Trilinear

# %% auto 0
__all__ = ['DRR', 'Registration']

# %% ../notebooks/api/00_drr.ipynb 7
from .data import Subject


class DRR(nn.Module):
    """PyTorch module that computes differentiable digitally reconstructed radiographs."""

    def __init__(
        self,
        subject: Subject,  # Data wrapper for the CT volume
        sdd: float,  # Source-to-detector distance (i.e., the C-arm's focal length)
        height: int,  # Height of the rendered DRR
        delx: float,  # X-axis pixel size
        width: int | None = None,  # Width of the rendered DRR (default to `height`)
        dely: float | None = None,  # Y-axis pixel size (if not provided, set to `delx`)
        x0: float = 0.0,  # Principal point X-offset
        y0: float = 0.0,  # Principal point Y-offset
        p_subsample: float | None = None,  # Proportion of pixels to randomly subsample
        reshape: bool = True,  # Return DRR with shape (b, 1, h, w)
        reverse_x_axis: bool = True,  # If pose includes reflection (i.e., E(3), not SE(3)), reverse x-axis
        patch_size: int | None = None,  # Render patches of the DRR in series
        mask: np.ndarray = None,  # Segmentation mask the same size as the CT volume
        renderer: str = "siddon",  # Rendering backend, either "siddon" or "trilinear"
        **renderer_kwargs,  # Kwargs for the renderer
    ):
        super().__init__()

        # Initialize the X-ray detector
        width = height if width is None else width
        dely = delx if dely is None else dely
        if p_subsample is not None:
            n_subsample = int(height * width * p_subsample)
        else:
            n_subsample = None
        self.detector = Detector(
            sdd,
            height,
            width,
            delx,
            dely,
            x0,
            y0,
            reverse_x_axis=reverse_x_axis,
            n_subsample=n_subsample,
        )

        # Initialize the volume
        self.subject = subject
        self.register_buffer("density", subject.density)
        self.register_buffer("spacing", subject.spacing)
        self.register_buffer("origin", subject.origin)
        if mask is not None:
            self.register_buffer("mask", torch.tensor(mask).to(torch.int16))

        # Initialize the renderer
        if renderer == "siddon":
            self.renderer = Siddon(**renderer_kwargs)
        elif renderer == "trilinear":
            self.renderer = Trilinear(**renderer_kwargs)
        else:
            raise ValueError(f"renderer must be 'siddon', not {renderer}")
        self.reshape = reshape
        self.patch_size = patch_size
        if self.patch_size is not None:
            self.n_patches = (height * width) // (self.patch_size**2)

    def reshape_transform(self, img, batch_size):
        if self.reshape:
            if self.detector.n_subsample is None:
                img = img.view(-1, 1, self.detector.height, self.detector.width)
            else:
                img = reshape_subsampled_drr(img, self.detector, batch_size)
        return img

# %% ../notebooks/api/00_drr.ipynb 8
def reshape_subsampled_drr(img: torch.Tensor, detector: Detector, batch_size: int):
    n_points = detector.height * detector.width
    drr = torch.zeros(batch_size, n_points).to(img)
    drr[:, detector.subsamples[-1]] = img
    drr = drr.view(batch_size, 1, detector.height, detector.width)
    return drr

# %% ../notebooks/api/00_drr.ipynb 10
from .pose import convert


@patch
def forward(
    self: DRR,
    *args,  # Some batched representation of SE(3)
    parameterization: str = None,  # Specifies the representation of the rotation
    convention: str = None,  # If parameterization is Euler angles, specify convention
    labels: list = None,  # Labels from the mask of structures to render
    **kwargs,  # Passed to the renderer
):
    """Generate DRR with rotational and translational parameters."""
    # Initialize the camera pose
    if parameterization is None:
        pose = args[0]
    else:
        pose = convert(*args, parameterization=parameterization, convention=convention)
    source, target = self.detector(pose)

    # Apply mask
    if labels is not None:
        if isinstance(labels, int):
            labels = [labels]
        mask = torch.any(torch.stack([self.mask == idx for idx in labels]), dim=0)
        density = self.density * mask
    else:
        density = self.density

    # Render the DRR
    if self.patch_size is not None:
        n_points = target.shape[1] // self.n_patches
        img = []
        for idx in range(self.n_patches):
            t = target[:, idx * n_points : (idx + 1) * n_points]
            partial = self.renderer(
                density, self.origin, self.spacing, source, t, **kwargs
            )
            img.append(partial)
        img = torch.cat(img, dim=1)
    else:
        img = self.renderer(
            density, self.origin, self.spacing, source, target, **kwargs
        )
    return self.reshape_transform(img, batch_size=len(pose))

# %% ../notebooks/api/00_drr.ipynb 11
@patch
def set_intrinsics(
    self: DRR,
    sdd: float = None,
    delx: float = None,
    dely: float = None,
    x0: float = None,
    y0: float = None,
):
    self.detector = Detector(
        sdd if sdd is not None else self.detector.sdd,
        self.detector.height,
        self.detector.width,
        delx if delx is not None else self.detector.delx,
        dely if dely is not None else self.detector.dely,
        x0 if x0 is not None else self.detector.x0,
        y0 if y0 is not None else self.detector.y0,
        n_subsample=self.detector.n_subsample,
        reverse_x_axis=self.detector.reverse_x_axis,
    ).to(self.volume)

# %% ../notebooks/api/00_drr.ipynb 12
from .pose import RigidTransform


@patch
def perspective_projection(
    self: DRR,
    pose: RigidTransform,
    pts: torch.Tensor,
):
    extrinsic = pose.inverse().compose(self.detector.flip_z)
    x = extrinsic(pts)
    x = torch.einsum("ij, bnj -> bni", self.detector.intrinsic, x)
    z = x[..., -1].unsqueeze(-1).clone()
    x = x / z
    return x[..., :2]

# %% ../notebooks/api/00_drr.ipynb 13
from torch.nn.functional import pad


@patch
def inverse_projection(
    self: DRR,
    pose: RigidTransform,
    pts: torch.Tensor,
):
    extrinsic = (
        self.detector.flip_xz.inverse()
        .compose(self.detector.translate.inverse())
        .compose(pose)
    )
    x = -self.detector.sdd * torch.einsum(
        "ij, bnj -> bni",
        self.detector.intrinsic.inverse(),
        pad(pts, (0, 1), value=1),  # Convert to homogenous coordinates
    )
    return extrinsic(x)

# %% ../notebooks/api/00_drr.ipynb 15
class Registration(nn.Module):
    """Perform automatic 2D-to-3D registration using differentiable rendering."""

    def __init__(
        self,
        drr: DRR,
        rotation: torch.Tensor,
        translation: torch.Tensor,
        parameterization: str,
        convention: str = None,
    ):
        super().__init__()
        self.drr = drr
        self.rotation = nn.Parameter(rotation)
        self.translation = nn.Parameter(translation)
        self.parameterization = parameterization
        self.convention = convention

    def forward(self):
        return self.drr(
            self.rotation,
            self.translation,
            parameterization=self.parameterization,
            convention=self.convention,
        )

    def get_rotation(self):
        return self.rotation.clone().detach().cpu()

    def get_translation(self):
        return self.translation.clone().detach().cpu()
