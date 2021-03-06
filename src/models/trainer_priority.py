# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import unicode_literals
import os
from keras.callbacks import TensorBoard
import numpy as np
import tensorflow as tf
from models.prioritize_experience_network import QNetWork
from models.memory import Memory
from models.memory import MemoryTDerror


class Trainer_priority(object):

    def __init__(self, env, agent, mount_agent, model_dir="", data_end_index: int=98):
        self.env = env
        self.agent = agent
        self.mount_agent = mount_agent
        self.experience = []
        self.moder_dir = model_dir
        if not self.moder_dir:
            self.model_dir = os.path.join(os.path.dirname(__file__), "model")
            if not os.path.isdir(self.model_dir):
                os.mkdir(self.model_dir)
        self.agent.model = QNetWork()
        self.mount_agent.model = QNetWork()
        self._target_model = QNetWork()
        self._target_mount_model = QNetWork()
        self.callback = TensorBoard(self.model_dir)
        self.callback.set_model(self.agent.model)
        self.mount_base = 100
        self.data_end_index = data_end_index
        self.name_action = {0: "buy", 1: "sell", 2: "stay"}
        self.memory = Memory()
        self.memory_TDerror = MemoryTDerror()
        self.memory_mount_TDerror = MemoryTDerror()

    def get_batch(self, batch_size: int=32, gamma=0.99, agent=None, _target_model=None):
        batch_indices = np.random.randint(low=0,
                                          high=len(self.experience),
                                          size=batch_size)
        X = np.zeros((batch_size, + agent.input_shape[0]))
        y = np.zeros((batch_size, + agent.num_actions))
        for i, b_i in enumerate(batch_indices):
            s, a, r, next_s, game_over = self.experience[b_i]
            X[i] = s
            y[i] = agent.evaluate(s)
            Q_sa = np.max(self.agent.evaluate(next_s, model=_target_model))
            if game_over:
                y[i, a] = r
            else:
                y[i, a] = r + gamma * Q_sa
        return X, y

    def write_log(self, index, loss, score):
        for name, value in zip(("loss", "score"), (loss, score)):
            summary = tf.Summary()
            summary_value = summary.value.add()
            summary_value.simple_value = value
            summary_value.tag = name
            self.callback.writer.add_summary(summary, index)
            self.callback.writer.flush()

    def train(self,
              gamma: float=0.99,
              initial_epsilon: float=0.1,
              final_epsilon: float=0.0001,
              memory_size: int=50000,
              observation_epochs: int=100,
              train_epochs: int=2000,
              batch_size: int=32,
              ddqn_flag: bool=True,
              ):
        epochs = observation_epochs + train_epochs
        epsilon = initial_epsilon
        model_path = os.path.join(self.model_dir, "agent_network.h5")
        fmt = "Epoch {:04d}/{:d} | Loss {:.5f} | Score: {} e={:.4f} train={}"

        for e in range(epochs):
            loss = 0.0
            rewards = []
            self.env.reset()
            state = (self.env.balance,
                     self.env.stock_balance,
                     self.env.fx_time_data_buy[self.env.state],
                     self.env.fx_time_data_sell[self.env.state],
                     self.env.closeAsk_data[self.env.state],
                     self.env.closeBid_data[self.env.state],
                     self.env.lowAsk_data[self.env.state],
                     self.env.lowBid_data[self.env.state],
                     self.env.openAsk_data[self.env.state],
                     self.env.openBid_data[self.env.state],
                     )
            game_over = False
            is_training = True if e > observation_epochs else False

            while not game_over:
                if not is_training:
                    action = self.agent.act(state, epsilon=1)
                    mount = self.mount_agent.act(state, epsilon=1) + 1
                else:
                    action = self.agent.act(state, epsilon)
                    mount = self.mount_agent.act(state, epsilon=1) + 1

                reward = self.env.step(action=self.name_action[action],
                                       mount=mount * self.mount_base)
                if "success" in reward:
                    reward = reward["success"]
                elif "fail" in reward:
                    # print("******** fail process *************")
                    reward = reward["fail"]

                next_state = (self.env.balance,
                              self.env.stock_balance,
                              self.env.fx_time_data_buy[self.env.state],
                              self.env.fx_time_data_sell[self.env.state],
                              self.env.closeAsk_data[self.env.state],
                              self.env.closeBid_data[self.env.state],
                              self.env.lowAsk_data[self.env.state],
                              self.env.lowBid_data[self.env.state],
                              self.env.openAsk_data[self.env.state],
                              self.env.openBid_data[self.env.state],
                              )
                next_state = np.reshape(next_state, [1, 10])
                state = np.reshape(state, [1, 10])
                if self.env.balance == 0 or self.env.state > self.data_end_index:
                    game_over = True
                self.memory.add((state, action, reward, next_state))

                TDError = self.memory_TDerror.get_TDerror(self.memory, gamma,
                                                          self.agent.model,
                                                          self._target_model)
                self.memory_TDerror.add(TDError)
                TDError_mount = self.memory_mount_TDerror.get_TDerror(self.memory, gamma,
                                                                      self.mount_agent.model,
                                                                      self._target_mount_model)
                self.memory_mount_TDerror.add(TDError_mount)
                # self.experience.append(
                #     (state, action, reward, next_state, game_over))

                rewards.append(reward)
                # print("mount {}".format(mount))
                # print("reward {}".format(reward))

                if is_training:

                    if sum(rewards) / len(rewards) < 20:
                        loss += self.agent.model.replay(self.memory,
                                                        batch_size,
                                                        gamma,
                                                        self._target_model)
                        loss += self.mount_agent.model.replay(self.memory,
                                                              batch_size,
                                                              gamma,
                                                              self._target_mount_model)
                    else:
                        loss += self.agent.model.prioritized_experience_replay(self.memory,
                                                                               batch_size,
                                                                               gamma,
                                                                               self._target_model)
                        loss += self.mount_agent.model.prioritized_experience_replay(self.memory,
                                                                                     batch_size,
                                                                                     gamma,
                                                                                     self._target_mount_model)

                state = next_state

                self.memory_TDerror.update_TDerror(self.memory, gamma, self.agent.model, self._target_model)
                self.memory_mount_TDerror.update_TDerror(self.memory, gamma,
                                                         self.mount_agent.model,
                                                         self._target_mount_model)

            loss= loss / len(rewards)
            score = sum(rewards)

            if is_training:
                self.write_log(e - observation_epochs, loss, score)
                self._target_model.modeol.set_weights(self.agent.model.model.get_weights())
                self._target_mount_model.model.set_weights(self.mount_agent.model.model.get_weights())

            if epsilon > final_epsilon:
                epsilon -= (initial_epsilon - final_epsilon) / epochs

            print(fmt.format(e + 1, epochs, loss, score, epsilon, is_training))
            stock_value = self.env.fx_time_data_sell[self.env.state] * self.env.stock_balance
            print("balance {}, stock_value {} total_balance {}".
                  format(self.env.balance, stock_value,
                         self.env.balance + stock_value))

            if e % 100 == 0:
                self.agent.model.model.save(model_path, overwrite=True)

        self.agent.model.save(model_path, overwrite=True)
