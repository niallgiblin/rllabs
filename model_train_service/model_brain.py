import torch
import torch.nn as nn
import json
from functools import reduce
import operator

LAYER_MAP = {
    "Conv2d": nn.Conv2d,
    "Linear": nn.Linear,
    "ReLU": nn.ReLU,
    "Flatten": nn.Flatten,
    "MaxPool2d": nn.MaxPool2d,
    "Dropout": nn.Dropout
}

class ModelBrain(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.input_shape = config["shape"]
        self.layers = self.build_layers(config)
        self.model = nn.Sequential(*self.layers)

    def forward(self, x):
        return self.model(x)

    def build_layers(self, config):
      layers = []
      shape = self.input_shape  
      for layer_cfg in config["layers"]:
          layer_type = layer_cfg.pop("type")
          if layer_type not in LAYER_MAP:
              raise ValueError(f"Unsupported layer type: {layer_type}")

          if layer_type == "Linear":
              # Infer in_features if missing
              if "in_features" not in layer_cfg:
                  if len(shape) > 1:
                      in_features = reduce(operator.mul, shape)
                  else:
                      in_features = shape[0]
                  layer_cfg["in_features"] = in_features
              if "out_features" not in layer_cfg:
                  raise ValueError("Linear layer must specify out_features")

          layer_class = LAYER_MAP[layer_type]
          layers.append(layer_class(**layer_cfg) if layer_cfg else layer_class())

          # Update shape for next layer
          if layer_type == "Conv2d":
              C, H, W = shape
              kernel = layer_cfg.get("kernel_size", 1)
              padding = layer_cfg.get("padding", 0)
              stride = layer_cfg.get("stride", 1)
              if isinstance(kernel, int):
                  kernel = (kernel, kernel)
              if isinstance(stride, int):
                  stride = (stride, stride)
              H_out = (H + 2*padding - kernel[0]) // stride[0] + 1
              W_out = (W + 2*padding - kernel[1]) // stride[1] + 1
              C_out = layer_cfg["out_channels"]
              shape = (C_out, H_out, W_out)
          elif layer_type == "Flatten":
              shape = (reduce(operator.mul, shape),)
          elif layer_type == "Linear":
              shape = (layer_cfg["out_features"],)
      return layers

