from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class CRNN(nn.Module):
    def __init__(
        self,
        num_classes: int,
        hidden_size: int = 256,
        rnn_layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()

        self.cnn = nn.Sequential(
            nn.Conv2d(1, 64, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(64, 128, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=2, stride=2),
            nn.Conv2d(128, 256, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(256),
            nn.ReLU(inplace=True),
            nn.Conv2d(256, 256, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=(2, 1), stride=(2, 1)),
            nn.Conv2d(256, 512, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm2d(512),
            nn.ReLU(inplace=True),
            nn.Conv2d(512, 512, kernel_size=3, stride=1, padding=1),
            nn.ReLU(inplace=True),
            nn.MaxPool2d(kernel_size=(2, 1), stride=(2, 1)),
            nn.Conv2d(512, 512, kernel_size=2, stride=1, padding=0),
            nn.ReLU(inplace=True),
        )

        effective_dropout = dropout if rnn_layers > 1 else 0.0
        self.rnn = nn.LSTM(
            input_size=512,
            hidden_size=hidden_size,
            num_layers=rnn_layers,
            bidirectional=True,
            dropout=effective_dropout,
        )
        self.classifier = nn.Linear(hidden_size * 2, num_classes)
        self._init_weights()

    def _init_weights(self) -> None:
        for module in self.modules():
            if isinstance(module, nn.Conv2d):
                nn.init.kaiming_normal_(module.weight, nonlinearity="relu")
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0.0)
            elif isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                nn.init.constant_(module.bias, 0.0)

        # Prevent early CTC collapse to blank-only predictions.
        with torch.no_grad():
            self.classifier.bias[0] = -2.0

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        features = self.cnn(images)
        batch, channels, height, width = features.size()
        if height != 1:
            features = F.adaptive_avg_pool2d(features, (1, width))

        features = features.squeeze(2)  # [B, C, W]
        features = features.permute(2, 0, 1)  # [T, B, C]

        rnn_out, _ = self.rnn(features)
        logits = self.classifier(rnn_out)
        return logits
