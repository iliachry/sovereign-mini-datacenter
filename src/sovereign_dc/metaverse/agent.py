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
    action_prob: float = 0.0  # pi_old(action | state) at collection time (0.0 = not recorded)


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
        self.last_hidden: list[float] = []

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
            if l_idx == num_layers - 1:
                self.last_hidden = list(curr)
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
        self.learning_rate_policy = learning_rate_policy
        self.learning_rate_value = learning_rate_value
        self.gamma = gamma
        self.clip_ratio = clip_ratio
        self.policy_net = MLPNetwork(input_dim=20, output_dim=6, is_value_net=False, seed=seed)
        self.value_net = MLPNetwork(input_dim=20, output_dim=1, is_value_net=True, seed=seed + 1)
        self.rng = random.Random(seed)
        self.buffer: list[Transition] = []
        self.training_episodes = 0
        # Running advantage statistics (Welford) used to standardise advantages when a
        # rollout is too short to provide its own (e.g. single-transition updates).
        self._adv_count = 0
        self._adv_mean = 0.0
        self._adv_m2 = 0.0
        self.last_update_stats: dict[str, float] = {}

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
        action_prob = self.policy_net.forward(state)[action]
        self.buffer.append(
            Transition(
                state=state,
                action=action,
                reward=reward,
                next_state=next_state,
                done=done,
                advantage=advantage,
                action_prob=action_prob,
            )
        )

    def normalize_advantages(self, advantages: list[float]) -> list[float]:
        """Standardises raw advantages to zero mean / unit variance.

        Raw TD advantages are on the reward scale (tens of dB), so they must be
        normalised before use; clipping them directly would saturate every update.
        Rollouts with >= 2 samples use their own statistics; shorter ones fall back to
        the running statistics accumulated over previous updates.
        """
        for adv in advantages:
            self._adv_count += 1
            delta = adv - self._adv_mean
            self._adv_mean += delta / self._adv_count
            self._adv_m2 += delta * (adv - self._adv_mean)

        if len(advantages) >= 2:
            mean = sum(advantages) / len(advantages)
            var = sum((a - mean) ** 2 for a in advantages) / (len(advantages) - 1)
        elif self._adv_count >= 2:
            mean = self._adv_mean
            var = self._adv_m2 / (self._adv_count - 1)
        else:
            # A single sample ever seen carries no scale information: no policy signal.
            return [0.0 for _ in advantages]

        std = math.sqrt(max(var, 0.0))
        if std < 1e-8:
            return [0.0 for _ in advantages]
        return [(a - mean) / std for a in advantages]

    def train_step(self) -> dict[str, float]:
        """Executes PPO surrogate policy update step over buffer.

        Advantages are standardised first. The PPO clip (``clip_ratio``) is applied to
        the probability ratio pi_new/pi_old: a sample whose ratio has already moved past
        the clip range in the direction favoured by its advantage contributes no gradient.
        """
        if not self.buffer:
            return {"loss_policy": 0.0, "loss_value": 0.0}

        # Calculate average advantage and policy loss
        advs = [t.advantage for t in self.buffer]
        mean_adv = sum(advs) / max(len(advs), 1)
        value_loss = sum(a * a for a in advs) / max(len(advs), 1)
        norm_advs = self.normalize_advantages(advs)
        clipped_updates = 0

        # Apply PPO clipped policy and value network gradient updates
        for t, norm_adv in zip(self.buffer, norm_advs, strict=True):
            probs = self.policy_net.forward(t.state)
            act_prob = max(probs[t.action], 1e-6)
            old_prob = t.action_prob if t.action_prob > 0.0 else act_prob
            ratio = act_prob / max(old_prob, 1e-6)
            ratio_clipped = (norm_adv > 0.0 and ratio > 1.0 + self.clip_ratio) or (
                norm_adv < 0.0 and ratio < 1.0 - self.clip_ratio
            )
            if ratio_clipped:
                clipped_updates += 1
            else:
                grad_scale = (1.0 - act_prob) * norm_adv * self.learning_rate_policy
                out_w = self.policy_net.weights[-1]
                hidden = self.policy_net.last_hidden
                for i in range(len(out_w)):
                    h_val = hidden[i] if i < len(hidden) else 1.0
                    out_w[i][t.action] += grad_scale * h_val
                self.policy_net.biases[-1][t.action] += grad_scale

            # Update critic (value network) output weights toward TD target
            self.value_net.forward(t.state)
            val_w = self.value_net.weights[-1]
            val_hidden = self.value_net.last_hidden
            val_grad = max(-1.0, min(1.0, t.advantage)) * self.learning_rate_value
            for i in range(len(val_w)):
                vh_val = val_hidden[i] if i < len(val_hidden) else 1.0
                val_w[i][0] += val_grad * vh_val
            self.value_net.biases[-1][0] += val_grad

        self.last_update_stats = {
            "samples": float(len(norm_advs)),
            "ratio_clipped_fraction": clipped_updates / len(norm_advs),
            "max_abs_normalized_advantage": max(abs(a) for a in norm_advs),
        }
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

        # Free-space path loss in the metres / GHz form used by SionnaRayTracer:
        # FSPL(dB) = 32.44 + 20*log10(d_m) + 20*log10(f_GHz)   (92.45 is the km / GHz constant)
        fspl = 32.44 + 20.0 * math.log10(dist) + 20.0 * math.log10(3.5) + (self.rng.gauss(0, 2.5))
        rx_power = 23.0 - fspl
        noise_floor = -94.0
        return rx_power - noise_floor
