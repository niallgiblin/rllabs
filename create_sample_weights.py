import torch
import torch.nn as nn

# Create a model matching the ModelBrain architecture (Sequential)
# This matches the training_config.json structure
layers = [
    nn.Conv2d(in_channels=3, out_channels=16, kernel_size=3, padding=1),
    nn.ReLU(),
    nn.Flatten(),
    nn.Linear(in_features=16 * 10 * 10, out_features=128),
    nn.ReLU(),
    nn.Linear(in_features=128, out_features=4)
]

model = nn.Sequential(*layers)
torch.save(model.state_dict(), "sample_model.pth")
print("Created sample_model.pth with Sequential architecture")