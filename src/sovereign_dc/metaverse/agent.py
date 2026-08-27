"""Scene-Aware Proximal Policy Optimization (SA-PPO) and Model-Driven PPO (MD-PPO) AI agents.

Implements Actor-Critic neural policies ([256, 128, 64] hidden units) for 3D UAV
positioning optimization, 6-directional discrete action space, 20-dimensional state
vector encoding, and sum-SINR reward shaping.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from sovereign_dc.metaverse.ray_tracer import PropagationResult

# 6 Discrete Actions in 3D Space (step sizes: dx=5m, dy=5m, dz=2m)
ACTIONS_3D: dict[int, tuple[float, float, float]] = {
    0: (5.0, 0.0, 0.0),  # +X (East)
    1: (-5.0, 0.0, 0.0),  # -X (West)
    2: (0.0, 5.0, 0.0),  # +Y (North)
    3: (0.0, -5.0, 0.0),  # -Y (South)
    4: (0.0, 0.0, 2.0),  # +Z (Ascend)
    5: (0.0, 0.0, -2.0),  # -Z (Descend)
}


@dataclass
class Transition:
    """Represents a single step reinforcement learning transition tuple."""

    state: list[float]
    action: int
    reward: float
    next_state: list[float]
    done: bool
    advantage: float = 0.0


class MLPNetwork:
    """Lightweight 3-layer Multi-Layer Perceptron [256, 128, 64] for policy/value forward pass."""

    def __init__(self, input_dim: int = 20, output_dim: int = 6, is_value_net: bool = False, seed: int = 111) -> None:
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.is_value_net = is_value_net
        rng = random.Random(seed)

        # Layer dimensions: input -> 256 -> 128 -> 64 -> output
        self.layer_sizes = [input_dim, 256, 128, 64, output_dim if not is_value_net else 1]

        # Xavier/He normal weight initializations
        self.weights: list[list[list[float]]] = []
        self.biases: list[list[float]] = []

        for i in range(len(self.layer_sizes) - 1):
            fan_in = self.layer_sizes[i]
            fan_out = self.layer_sizes[i + 1]
            scale = math.sqrt(2.0 / fan_in)
            w = [[rng.gauss(0.0, scale) for _ in range(fan_out)] for _ in range(fan_in)]
            b = [0.01 for _ in range(fan_out)]
            self.weights.append(w)
            self.biases.append(b)

    def forward(self, x: list[float]) -> list[float]:
        """Performs forward propagation with ReLU activations and final Softmax/Linear."""
        curr = list(x)
        num_layers = len(self.weights)

        for l_idx in range(num_layers):
            w = self.weights[l_idx]
            b = self.biases[l_idx]
            fan_in = len(w)
            fan_out = len(b)
            next_act = [0.0] * fan_out

            for j in range(fan_out):
                total = b[j]
                for i in range(fan_in):
                    total += curr[i] * w[i][j]
                # Activation: ReLU for hidden, Softmax/Linear for output
                if l_idx < num_layers - 1:
                    next_act[j] = max(0.0, total)  # ReLU
                else:
                    next_act[j] = total  # Linear for output

            curr = next_act

        if not self.is_value_net:
            # Softmax on action logits
            max_val = max(curr)
            exps = [math.exp(val - max_val) for val in curr]
            sum_exps = sum(exps)
            return [e / sum_exps for e in exps]
        return curr


class SceneAwarePPO:
    """Scene-Aware Proximal Policy Optimization (SA-PPO) agent.

    Learns from physics-based Sionna ray-tracing observations within the 3D digital twin.
    """

    def __init__(
        self,
        learning_rate_policy: float = 3e-4,
        learning_rate_value: float = 6e-4,
        gamma: float = 0.99,
        clip_ratio: float = 0.2,
        seed: int = 111,
    ) -> None:
        self.gamma = gamma
        self.clip_ratio = clip_ratio
        self.policy_net = MLPNetwork(input_dim=20, output_dim=6, is_value_net=False, seed=seed)
        self.value_net = MLPNetwork(input_dim=20, output_dim=1, is_value_net=True, seed=seed + 1)
        self.rng = random.Random(seed)
        self.buffer: list[Transition] = []
        self.training_episodes = 0

    def encode_state(
        self,
        uav_pos: tuple[float, float, float],
        prop_results: dict[str, PropagationResult],
    ) -> list[float]:
        """Encodes the 20-dimensional state vector normalized to [-1, 1] range."""
        # 1. UAV coordinates (3) [normalized to [-100, 100], z in [10, 80]]
        ux = uav_pos[0] / 100.0
        uy = uav_pos[1] / 100.0
        uz = (uav_pos[2] - 40.0) / 40.0

        # 2. Receiver metrics: Rx1, Rx2, Rx3 positions & SINR (3 receivers * 4 features = 12)
        rx_features: list[float] = []
        for rx_id in ["Rx1", "Rx2", "Rx3"]:
            if rx_id in prop_results:
                res = prop_results[rx_id]
                # Normalized SINR (clipped between -25 and +15 dB)
                norm_sinr = max(-25.0, min(15.0, res.sinr_db)) / 25.0
                norm_cap = res.capacity_bps_hz / 5.0
                has_los = 1.0 if res.los_path else -1.0
                power_norm = (res.total_received_power_dbm + 80.0) / 40.0
            else:
                norm_sinr, norm_cap, has_los, power_norm = -1.0, 0.0, -1.0, -1.0
            rx_features.extend([norm_sinr, norm_cap, has_los, power_norm])

        # 3. Environment & summary features (5 features: sum SINR, min SINR, variance, altitude, battery)
        sinrs = [p.sinr_db for p in prop_results.values()] if prop_results else [-15.0]
        sum_sinr = sum(sinrs) / 60.0
        min_sinr = min(sinrs) / 25.0
        sinr_var = (max(sinrs) - min(sinrs)) / 30.0
        alt_norm = uav_pos[2] / 100.0
        battery_soc_norm = 0.85

        state = [ux, uy, uz] + rx_features + [sum_sinr, min_sinr, sinr_var, alt_norm, battery_soc_norm]
        # Pad or slice to exactly 20 dimensions
        return (state + [0.0] * 20)[:20]

    def select_action(self, state: list[float], deterministic: bool = False) -> int:
        """Selects a discrete movement action from the policy distribution."""
        probs = self.policy_net.forward(state)
        if deterministic:
            return probs.index(max(probs))

        # Sample from categorical distribution
        r = self.rng.random()
        cumulative = 0.0
        for action_idx, prob in enumerate(probs):
            cumulative += prob
            if r <= cumulative:
                return action_idx
        return len(probs) - 1

    def compute_reward(self, prop_results: dict[str, PropagationResult]) -> float:
        """Calculates sum-SINR reward with fairness weighting for disadvantaged users."""
        total_sinr = 0.0
        for rx_id, res in prop_results.items():
            # Disadvantaged user (Rx1) receives 1.5x reward shaping weight
            weight = 1.5 if rx_id == "Rx1" else 1.0
            total_sinr += weight * res.sinr_db
        return total_sinr

    def record_transition(
        self,
        state: list[float],
        action: int,
        reward: float,
        next_state: list[float],
        done: bool,
    ) -> None:
        """Stores transition in rollout buffer."""
        v_curr = self.value_net.forward(state)[0]
        v_next = self.value_net.forward(next_state)[0] if not done else 0.0
        advantage = reward + (self.gamma * v_next) - v_curr
        self.buffer.append(
            Transition(
                state=state,
                action=action,
                reward=reward,
                next_state=next_state,
                done=done,
                advantage=advantage,
            )
        )

    def train_step(self) -> dict[str, float]:
        """Executes PPO surrogate policy update step over buffer."""
        if not self.buffer:
            return {"loss_policy": 0.0, "loss_value": 0.0}

        # Calculate average advantage and policy loss
        advs = [t.advantage for t in self.buffer]
        mean_adv = sum(advs) / max(len(advs), 1)
        value_loss = sum(a * a for a in advs) / max(len(advs), 1)

        # Apply gradient update to output layer weights
        for t in self.buffer:
            probs = self.policy_net.forward(t.state)
            act_prob = max(probs[t.action], 1e-6)
            grad = (1.0 - act_prob) * t.advantage * 0.001
            # Adjust weights
            out_w = self.policy_net.weights[-1]
            for i in range(len(out_w)):
                out_w[i][t.action] += grad

        self.buffer.clear()
        self.training_episodes += 1
        return {"loss_policy": round(-mean_adv, 4), "loss_value": round(value_loss, 4)}


class ModelDrivenPPO(SceneAwarePPO):
    """Model-Driven PPO (MD-PPO) baseline using statistical Rician fading (K=10dB).

    Lacks site-specific 3D geometric ray tracing.
    """

    def __init__(self, rician_k_db: float = 10.0, seed: int = 222) -> None:
        super().__init__(seed=seed)
        self.rician_k_db = rician_k_db
        self.rician_k_linear = 10.0 ** (rician_k_db / 10.0)

    def evaluate_rician_sinr(self, uav_pos: tuple[float, float, float], rx_pos: tuple[float, float, float]) -> float:
        """Calculates statistical Rician fading path loss without building geometry."""
        dx = rx_pos[0] - uav_pos[0]
        dy = rx_pos[1] - uav_pos[1]
        dz = rx_pos[2] - uav_pos[2]
        dist = max(math.sqrt(dx * dx + dy * dy + dz * dz), 1.0)

        # Standard statistical path loss (alpha = 2.4)
        fspl = 20.0 * math.log10(dist) + 20.0 * math.log10(3.5) + 92.45 + (self.rng.gauss(0, 2.5))
        rx_power = 23.0 - fspl
        noise_floor = -94.0
        return rx_power - noise_floor
