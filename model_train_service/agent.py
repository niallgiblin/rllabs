import torch
import torch.nn as nn
import random
import logging
from model_brain import ModelBrain

logger = logging.getLogger(__name__)

class Agent:
    def __init__(self, grid_params, dqn_config, model_weights_path):
        self.model = ModelBrain(dqn_config)
        # Try to load weights, but allow training from scratch if weights don't match
        try:
            state_dict = torch.load(model_weights_path)
            missing_keys, unexpected_keys = self.model.load_state_dict(state_dict, strict=False)
            if missing_keys or unexpected_keys:
                logger.warning(f"Weight mismatch - missing: {missing_keys}, unexpected: {unexpected_keys}")
                logger.info("Training from scratch with random initialization (weights don't match)")
            else:
                logger.info(f"Successfully loaded weights from {model_weights_path}")
        except Exception as e:
            logger.warning(f"Could not load weights from {model_weights_path}: {e}")
            logger.info("Training from scratch with random initialization")
        self.grid_params = grid_params
        self.H = grid_params["grid_height"]
        self.W = grid_params["grid_width"]
        self.memory = []
        
        # Convert to lists to allow mutation
        self.agent_pos = list(grid_params["channels"][0])
        self.initial_agent_pos = list(grid_params["channels"][0])
        self.reward_pos = list(grid_params["channels"][1])
        self.initial_reward_pos = list(grid_params["channels"][1])
        
        # Handle punishment positions (list of tuples/lists)
        self.punishment_positions = [list(pos) for pos in grid_params["channels"][2]]
        self.initial_punishment_pos = [list(pos) for pos in grid_params["channels"][2]]
        
        self.reset_inputs()
        self.epsilon = grid_params["initial_epsilon_value"]
        self.criterion = nn.MSELoss()
        self.optimizer = torch.optim.Adam(
            self.model.parameters(), 
            lr=grid_params["initial_learning_rate"]
        )
        self.gamma = grid_params["initial_gamma_value"]

    def reset_inputs(self):
        self.agent_pos = list(self.initial_agent_pos)
        self.reward_pos = list(self.initial_reward_pos)
        self.punishment_positions = [list(pos) for pos in self.initial_punishment_pos]
        return self.get_state()

    def get_state(self):
        agent_channel = torch.zeros((self.H, self.W))
        reward_channel = torch.zeros((self.H, self.W))
        punish_channel = torch.zeros((self.H, self.W))

        agent_channel[self.agent_pos] = 1.0
        reward_channel[self.reward_pos] = 1.0
        for pos in self.punishment_positions:
            punish_channel[pos] = 1.0
        grid_tensor = torch.stack([agent_channel, reward_channel, punish_channel], dim = 0)
        return grid_tensor.unsqueeze(0)

    def step(self, action):
        # Actions: 0=up, 1=down, 2=left, 3=right
        if action == 0 and self.agent_pos[0] > 0:
            self.agent_pos[0] -= 1
        elif action == 1 and self.agent_pos[0] < self.H - 1:
            self.agent_pos[0] += 1
        elif action == 2 and self.agent_pos[1] > 0:
            self.agent_pos[1] -= 1
        elif action == 3 and self.agent_pos[1] < self.W - 1:
            self.agent_pos[1] += 1

        # Compute reward
        if tuple(self.agent_pos) == tuple(self.reward_pos):
            reward = 1.0
            done = True
        elif tuple(self.agent_pos) in self.punishment_positions:
            reward = -1.0
            done = True
        else:
            # small penalty to encourage faster learning
            reward = -0.01
            done = False

        return self.get_state(), reward, done

    def select_action(self, state):
        if (random.random() < self.epsilon):
            return random.randint(0, 3)
        else:
            q_values = self.model(state)
            return q_values.argmax().item()

    def train_step(self, num_episodes=50):
        for episode in range(num_episodes):
            state = self.reset_inputs()
            done = False
            total_reward = 0.0

            while not done:
                action = self.select_action(state)

                next_state, reward, done = self.step(action)

                q_values = self.model(state)
                next_q_values = self.model(next_state)

                target_q_values = q_values.clone().detach()
                target_q = reward + self.gamma * torch.max(next_q_values).item() * (1 - int(done))
                target_q_values[0, action] = target_q

                loss = self.criterion(q_values, target_q_values)
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

                state = next_state
                total_reward += reward

            self.epsilon = max(0.1, self.epsilon * 0.999)

            logger.info(f"Episode {episode+1}/{num_episodes} | Total Reward: {total_reward:.2f} | Epsilon: {self.epsilon:.2f}")

    def save_weights(self, filepath: str):
        """
        Save the trained model weights to a file
        
        Args:
            filepath: Path where to save the model weights (.pth file)
        """
        torch.save(self.model.state_dict(), filepath)
        logger.info(f"Model weights saved to {filepath}")

        
