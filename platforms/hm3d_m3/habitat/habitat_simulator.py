"""
Implement a VLNSimulator that step without returning observation
in order to save space and time.
"""

#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from typing import (
    TYPE_CHECKING,
    Dict,
    Union,
    cast,
)


if TYPE_CHECKING:
    pass


from habitat_sim.simulator import MutableMapping, MutableMapping_T
from habitat.sims.habitat_simulator.habitat_simulator import HabitatSim
from habitat.core.registry import registry
from habitat.core.simulator import (
    Simulator,
)

# inherit habitat-lab/habitat/sims/habitat_simulator/habitat_simulator.py
@registry.register_simulator(name="Sim-v1")
class Simulator(HabitatSim):
    r"""Simulator wrapper over habitat-sim

    habitat-sim repo: https://github.com/facebookresearch/habitat-sim

    Args:
        config: configuration for initializing the simulator.
    """

    def __init__(self, config) -> None:
        super().__init__(config)

    def step_without_obs(self,
        action: Union[str, int, MutableMapping_T[int, Union[str, int]]],
        dt: float = 1.0 / 60.0,):
        self._num_total_frames += 1
        if isinstance(action, MutableMapping):
            return_single = False
        else:
            action = cast(Dict[int, Union[str, int]], {self._default_agent_id: action})
            return_single = True
        collided_dict: Dict[int, bool] = {}
        for agent_id, agent_act in action.items():
            agent = self.get_agent(agent_id)
            collided_dict[agent_id] = agent.act(agent_act)
            self.__last_state[agent_id] = agent.get_state()


        multi_observations = {}
        for agent_id in action.keys():
            agent_observation = {}
            agent_observation["collided"] = collided_dict[agent_id]
            multi_observations[agent_id] = agent_observation

        if return_single:
            sim_obs = multi_observations[self._default_agent_id]
        else:
            sim_obs = multi_observations

        self._prev_sim_obs = sim_obs


@registry.register_simulator(name="Sim-MA-v1")
class MultiAgentSimulator(Simulator):
    """Multi-agent variant of ``Sim-v1`` supporting N concurrent agents in one HM3D scene."""

    def _num_agents(self) -> int:
        return int(getattr(self.habitat_config, "NUM_AGENTS", 1))

    def _agent_names(self):
        names = list(getattr(self.habitat_config, "AGENTS", ["AGENT_0"]))
        n = self._num_agents()
        if len(names) < n:
            names = [f"AGENT_{i}" for i in range(n)]
        return names[:n]

    def create_sim_config(self, _sensor_suite):
        cfg = super().create_sim_config(_sensor_suite)
        n = self._num_agents()
        if n <= 1:
            return cfg
        base_agent_cfg = cfg.agents[0]
        cfg.agents = [base_agent_cfg for _ in range(n)]
        return cfg

    def _per_agent_obs(self):
        out = []
        for i in range(self._num_agents()):
            sim_obs_i = self.get_sensor_observations(agent_ids=i)
            out.append(self._sensor_suite.get_observations(sim_obs_i))
        return out

    def reset(self):
        n = self._num_agents()
        if n <= 1:
            return super().reset()
        import habitat_sim
        _ = habitat_sim.Simulator.reset(self)
        obs = self._per_agent_obs()
        self._prev_sim_obs = obs
        return obs

    def step(self, action, agent_id=None):
        import habitat_sim
        from habitat.sims.habitat_simulator.actions import HabitatSimActions
        n = self._num_agents()
        if n <= 1:
            return super().step(action)
        if agent_id is None:
            action_dict = {
                i: int(action[i])
                for i in range(n)
                if int(action[i]) != int(HabitatSimActions.STOP)
            }
            if action_dict:
                habitat_sim.Simulator.step(self, action_dict)
        elif int(action) != int(HabitatSimActions.STOP):
            habitat_sim.Simulator.step(self, {int(agent_id): int(action)})
        obs = self._per_agent_obs()
        self._prev_sim_obs = obs
        return obs

    def get_agent_state(self, agent_id: int = 0):
        return self.get_agent(agent_id).get_state()
