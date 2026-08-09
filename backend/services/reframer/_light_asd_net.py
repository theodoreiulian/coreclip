"""
Vendored Light-ASD network (audio-visual active speaker detection).

Architecture and weights are from Junhua-Liao/Light-ASD (MIT licence).
The model class is vendored verbatim — pinned and offline — so the reframer
never has to pip-install or download the upstream repo at runtime. Only the
inference path is kept; training-only code (losses, optimisers) is dropped.

The pretrained checkpoint lives at backend/.models/asd/light_asd.pth and is
SHA256-verified by asd.py before loading.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


# ── Encoders ───────────────────────────────────────────────────────────


class Audio_Block(nn.Module):
    def __init__(self, in_channels, out_channels):
        super().__init__()
        self.relu = nn.ReLU()
        self.m_3 = nn.Conv2d(in_channels, out_channels, kernel_size=(3, 1), padding=(1, 0), bias=False)
        self.bn_m_3 = nn.BatchNorm2d(out_channels, momentum=0.01, eps=0.001)
        self.t_3 = nn.Conv2d(out_channels, out_channels, kernel_size=(1, 3), padding=(0, 1), bias=False)
        self.bn_t_3 = nn.BatchNorm2d(out_channels, momentum=0.01, eps=0.001)
        self.m_5 = nn.Conv2d(in_channels, out_channels, kernel_size=(5, 1), padding=(2, 0), bias=False)
        self.bn_m_5 = nn.BatchNorm2d(out_channels, momentum=0.01, eps=0.001)
        self.t_5 = nn.Conv2d(out_channels, out_channels, kernel_size=(1, 5), padding=(0, 2), bias=False)
        self.bn_t_5 = nn.BatchNorm2d(out_channels, momentum=0.01, eps=0.001)
        self.last = nn.Conv2d(out_channels, out_channels, kernel_size=(1, 1), padding=(0, 0), bias=False)
        self.bn_last = nn.BatchNorm2d(out_channels, momentum=0.01, eps=0.001)

    def forward(self, x):
        x_3 = self.relu(self.bn_m_3(self.m_3(x)))
        x_3 = self.relu(self.bn_t_3(self.t_3(x_3)))
        x_5 = self.relu(self.bn_m_5(self.m_5(x)))
        x_5 = self.relu(self.bn_t_5(self.t_5(x_5)))
        x = x_3 + x_5
        x = self.relu(self.bn_last(self.last(x)))
        return x


class Visual_Block(nn.Module):
    def __init__(self, in_channels, out_channels, is_down=False):
        super().__init__()
        self.relu = nn.ReLU()
        if is_down:
            self.s_3 = nn.Conv3d(in_channels, out_channels, kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1), bias=False)
            self.s_5 = nn.Conv3d(in_channels, out_channels, kernel_size=(1, 5, 5), stride=(1, 2, 2), padding=(0, 2, 2), bias=False)
        else:
            self.s_3 = nn.Conv3d(in_channels, out_channels, kernel_size=(1, 3, 3), padding=(0, 1, 1), bias=False)
            self.s_5 = nn.Conv3d(in_channels, out_channels, kernel_size=(1, 5, 5), padding=(0, 2, 2), bias=False)
        self.bn_s_3 = nn.BatchNorm3d(out_channels, momentum=0.01, eps=0.001)
        self.t_3 = nn.Conv3d(out_channels, out_channels, kernel_size=(3, 1, 1), padding=(1, 0, 0), bias=False)
        self.bn_t_3 = nn.BatchNorm3d(out_channels, momentum=0.01, eps=0.001)
        self.bn_s_5 = nn.BatchNorm3d(out_channels, momentum=0.01, eps=0.001)
        self.t_5 = nn.Conv3d(out_channels, out_channels, kernel_size=(5, 1, 1), padding=(2, 0, 0), bias=False)
        self.bn_t_5 = nn.BatchNorm3d(out_channels, momentum=0.01, eps=0.001)
        self.last = nn.Conv3d(out_channels, out_channels, kernel_size=(1, 1, 1), padding=(0, 0, 0), bias=False)
        self.bn_last = nn.BatchNorm3d(out_channels, momentum=0.01, eps=0.001)

    def forward(self, x):
        x_3 = self.relu(self.bn_s_3(self.s_3(x)))
        x_3 = self.relu(self.bn_t_3(self.t_3(x_3)))
        x_5 = self.relu(self.bn_s_5(self.s_5(x)))
        x_5 = self.relu(self.bn_t_5(self.t_5(x_5)))
        x = x_3 + x_5
        x = self.relu(self.bn_last(self.last(x)))
        return x


class visual_encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.block1 = Visual_Block(1, 32, is_down=True)
        self.pool1 = nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
        self.block2 = Visual_Block(32, 64)
        self.pool2 = nn.MaxPool3d(kernel_size=(1, 3, 3), stride=(1, 2, 2), padding=(0, 1, 1))
        self.block3 = Visual_Block(64, 128)
        self.maxpool = nn.AdaptiveMaxPool2d((1, 1))

    def forward(self, x):
        x = self.block1(x)
        x = self.pool1(x)
        x = self.block2(x)
        x = self.pool2(x)
        x = self.block3(x)
        x = x.transpose(1, 2)
        B, T, C, W, H = x.shape
        x = x.reshape(B * T, C, W, H)
        x = self.maxpool(x)
        x = x.view(B, T, C)
        return x


class audio_encoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.block1 = Audio_Block(1, 32)
        self.pool1 = nn.MaxPool3d(kernel_size=(1, 1, 3), stride=(1, 1, 2), padding=(0, 0, 1))
        self.block2 = Audio_Block(32, 64)
        self.pool2 = nn.MaxPool3d(kernel_size=(1, 1, 3), stride=(1, 1, 2), padding=(0, 0, 1))
        self.block3 = Audio_Block(64, 128)

    def forward(self, x):
        x = self.block1(x)
        x = self.pool1(x)
        x = self.block2(x)
        x = self.pool2(x)
        x = self.block3(x)
        x = torch.mean(x, dim=2, keepdim=True)
        x = x.squeeze(2).transpose(1, 2)
        return x


class BGRU(nn.Module):
    def __init__(self, channel):
        super().__init__()
        self.gru_forward = nn.GRU(input_size=channel, hidden_size=channel, num_layers=1, bidirectional=False, bias=True, batch_first=True)
        self.gru_backward = nn.GRU(input_size=channel, hidden_size=channel, num_layers=1, bidirectional=False, bias=True, batch_first=True)
        self.gelu = nn.GELU()

    def forward(self, x):
        x, _ = self.gru_forward(x)
        x = self.gelu(x)
        x = torch.flip(x, dims=[1])
        x, _ = self.gru_backward(x)
        x = torch.flip(x, dims=[1])
        x = self.gelu(x)
        return x


class ASD_Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.visualEncoder = visual_encoder()
        self.audioEncoder = audio_encoder()
        self.GRU = BGRU(128)

    def forward_visual_frontend(self, x):
        B, T, W, H = x.shape
        x = x.view(B, 1, T, W, H)
        x = (x / 255 - 0.4161) / 0.1688
        x = self.visualEncoder(x)
        return x

    def forward_audio_frontend(self, x):
        x = x.unsqueeze(1).transpose(2, 3)
        x = self.audioEncoder(x)
        return x

    def forward_audio_visual_backend(self, x1, x2):
        x = x1 + x2
        x = self.GRU(x)
        x = torch.reshape(x, (-1, 128))
        return x


class LightASDNet(nn.Module):
    """ASD_Model + the lossAV classification head, inference only."""

    def __init__(self):
        super().__init__()
        self.model = ASD_Model()
        self.fc = nn.Linear(128, 2)

    @torch.no_grad()
    def speaking_prob(self, audio_feature: torch.Tensor, visual_feature: torch.Tensor) -> torch.Tensor:
        """
        audio_feature : (1, T_audio, 13)   MFCC at 100 fps
        visual_feature: (1, T_video, 112, 112)  grayscale face crops at 25 fps
        returns       : (T_video,) speaking probability in [0, 1]
        """
        embed_a = self.model.forward_audio_frontend(audio_feature)
        embed_v = self.model.forward_visual_frontend(visual_feature)
        out = self.model.forward_audio_visual_backend(embed_a, embed_v)
        logits = self.fc(out)
        return F.softmax(logits, dim=-1)[:, 1]

    def load_checkpoint(self, path: str, device: str = "cpu") -> None:
        """Load the upstream checkpoint, remapping its prefixed keys."""
        raw = torch.load(path, map_location="cpu")
        remapped = {}
        for key, val in raw.items():
            if key.startswith("model."):
                remapped[key] = val
            elif key.startswith("lossAV.FC."):
                remapped["fc." + key[len("lossAV.FC."):]] = val
            # lossV.* is training-only — ignored
        missing, unexpected = self.load_state_dict(remapped, strict=False)
        # the only acceptable "missing" is nothing; unexpected should be empty
        real_missing = [k for k in missing if not k.startswith("fc.")] if False else missing
        if real_missing or unexpected:
            raise RuntimeError(
                f"Light-ASD checkpoint mismatch: missing={real_missing[:4]} unexpected={unexpected[:4]}"
            )
        self.eval()
        self.to(device)
