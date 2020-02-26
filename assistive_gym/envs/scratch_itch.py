import os
from gym import spaces
import numpy as np
import pybullet as p
import random
from scipy.spatial.transform import Rotation as R

from .env import AssistiveEnv

class ScratchItchEnv(AssistiveEnv):
    def __init__(self, robot_type='pr2', human_control=False):
        super(ScratchItchEnv, self).__init__(robot_type=robot_type, task='scratch_itch', human_control=human_control, frame_skip=5, time_step=0.02, action_robot_len=7, action_human_len=(10 if human_control else 0), obs_robot_len=30, obs_human_len=(34 if human_control else 0))

    def step(self, action):
        self.take_step(action, robot_arm='left', gains=self.config('robot_gains'), forces=self.config('robot_forces'), human_gains=0.05)

        total_force_on_human, tool_force, tool_force_at_target, target_contact_pos = self.get_total_force()
        end_effector_velocity = np.linalg.norm(p.getLinkState(self.tool, 1, computeForwardKinematics=True, computeLinkVelocity=True, physicsClientId=self.id)[6])
        if target_contact_pos is not None:
            target_contact_pos = np.array(target_contact_pos)
        obs = self._get_obs([tool_force], [total_force_on_human, tool_force_at_target])

        # Get human preferences
        preferences_score = self.human_preferences(end_effector_velocity=end_effector_velocity, total_force_on_human=total_force_on_human, tool_force_at_target=tool_force_at_target)
        noicepters_density = np.array([[ 0,  0,  0,  0, 16, 16, 16, 16, 16,  0,  0,  0,  0],
                                       [ 0,  0,  0, 16, 16, 16, 16, 16, 16, 16,  0,  0,  0],
                                       [ 0,  0, 16, 16, 16, 16, 16, 16, 16, 16, 16,  0,  0],
                                       [ 0, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16,  0],
                                       [16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16],
                                       [16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16],
                                       [16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16, 16],
                                       [ 0,  0,  0,  0,  4,  4,  4,  4,  4,  0,  0,  0,  0],
                                       [12, 12, 12, 12, 12,  8,  8,  8, 12, 12, 12, 12, 12],
                                       [12, 12, 12, 12,  8,  8,  8,  8,  8, 12, 12, 12, 12],
                                       [12, 12, 12, 12,  8,  8,  8,  8,  8, 12, 12, 12, 12],
                                       [12, 12,  4,  4,  4,  4,  4,  4,  4,  4,  4, 12, 12],
                                       [12, 12,  4,  4,  4,  4,  4,  4,  4,  4,  4, 12, 12],
                                       [ 0, 12,  0,  0,  0,  0,  0,  0,  0,  0,  0, 12,  0],
                                       [ 0, 12,  4,  4,  4,  4,  4,  4,  4,  4,  4, 12,  0],
                                       [ 0,  0,  4,  4,  4,  4,  4,  4,  4,  4,  4,  0,  0],
                                       [ 0,  0,  0,  0,  4,  4,  4,  4,  4,  0,  0,  0,  0]])
        pain = self.get_face_pain(noicepters_density)

        tool_pos = np.array(p.getLinkState(self.tool, 1, computeForwardKinematics=True, physicsClientId=self.id)[0])
        reward_distance = -np.linalg.norm(self.target_pos - tool_pos) # Penalize distances away from target
        reward_action = -np.sum(np.square(action)) # Penalize actions
        reward_force_scratch = 0.0 # Reward force near the target
        if target_contact_pos is not None and np.linalg.norm(target_contact_pos - self.prev_target_contact_pos) > 0.01 and tool_force_at_target < 10:
            # Encourage the robot to move around near the target to simulate scratching
            reward_force_scratch = tool_force_at_target
            self.prev_target_contact_pos = target_contact_pos
            self.task_success += 1

        reward = self.config('distance_weight')*reward_distance + self.config('action_weight')*reward_action + self.config('tool_force_weight')*tool_force_at_target + self.config('scratch_reward_weight')*reward_force_scratch + preferences_score - pain

        if self.gui and tool_force_at_target > 0:
            print('Task success:', self.task_success, 'Tool force at target:', tool_force_at_target, reward_force_scratch)

        info = {'total_force_on_human': total_force_on_human, 'task_success': int(self.task_success >= self.config('task_success_threshold')), 'action_robot_len': self.action_robot_len, 'action_human_len': self.action_human_len, 'obs_robot_len': self.obs_robot_len, 'obs_human_len': self.obs_human_len}
        done = False

        return obs, reward, done, info

    def get_face_pain(self, noicepters_density):
        topleft, topright, bottomleft, bottomright = (-0.10, 0.18), (0.10, 0.18), (-0.10, -0.02), (0.10, -0.02)
        pain = 0
        for c in p.getContactPoints(bodyA=self.human, linkIndexA=23):
            contact_dist = c[8]
            contact_force = c[9]
            s_Contact_pos = c[5]

            if contact_force > 4.0:
                s_Head_pos, s_Head_ori = p.getLinkState(self.human, linkIndex=23)[:2]
                s_T_b = np.zeros((4, 4))
                s_T_b[0:3,0:3] = R.from_quat(s_Head_ori).as_matrix()
                s_T_b[0:4,3] = np.array([s_Head_pos[0], s_Head_pos[1], s_Head_pos[2], 1])
                b_Contact_pos = np.dot(np.linalg.inv(s_T_b), np.array([s_Contact_pos[0], s_Contact_pos[1], s_Contact_pos[2], 1]))
                # print(b_Contact_pos[0], b_Contact_pos[2])
                xi, zi = (int(round((b_Contact_pos[0] / (topright[0] - topleft[0])) * 19)) + 7, 15 - int(round((b_Contact_pos[2] / (topleft[1] - bottomleft[1])) * 19)))
                xi = 0 if xi < 0 or xi >= 13 else xi
                zi = 0 if zi < 0 or zi >= 17 else zi
                pain += abs(noicepters_density[zi, xi] * contact_dist * 2000)

        return pain


    def get_total_force(self):
        total_force_on_human = 0
        tool_force = 0
        tool_force_at_target = 0
        target_contact_pos = None
        for c in p.getContactPoints(bodyA=self.tool, physicsClientId=self.id):
            tool_force += c[9]
        for c in p.getContactPoints(bodyA=self.tool, bodyB=self.human, physicsClientId=self.id):
            total_force_on_human += c[9]
            linkA = c[3]
            contact_position = c[6]
            if linkA in [0, 1]:
                # Enforce that contact is close to the target location
                if np.linalg.norm(contact_position - self.target_pos) < 0.025:
                    tool_force_at_target += c[9]
                    target_contact_pos = contact_position
        for c in p.getContactPoints(bodyA=self.robot, bodyB=self.human, physicsClientId=self.id):
            total_force_on_human += c[9]
        return total_force_on_human, tool_force, tool_force_at_target, target_contact_pos

    def _get_obs(self, forces, forces_human):
        torso_pos = np.array(p.getLinkState(self.robot, 15 if self.robot_type == 'pr2' else 0, computeForwardKinematics=True, physicsClientId=self.id)[0])
        state = p.getLinkState(self.tool, 1, computeForwardKinematics=True, physicsClientId=self.id)
        tool_pos = np.array(state[0])
        tool_orient = np.array(state[1]) # Quaternions
        robot_joint_states = p.getJointStates(self.robot, jointIndices=self.robot_left_arm_joint_indices, physicsClientId=self.id)
        robot_joint_positions = np.array([x[0] for x in robot_joint_states])
        robot_pos, robot_orient = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.id)
        if self.human_control:
            human_pos = np.array(p.getBasePositionAndOrientation(self.human, physicsClientId=self.id)[0])
            human_joint_states = p.getJointStates(self.human, jointIndices=self.human_controllable_joint_indices, physicsClientId=self.id)
            human_joint_positions = np.array([x[0] for x in human_joint_states])

        # Human shoulder, elbow, and wrist joint locations
        shoulder_pos, shoulder_orient = p.getLinkState(self.human, 5, computeForwardKinematics=True, physicsClientId=self.id)[:2]
        elbow_pos, elbow_orient = p.getLinkState(self.human, 7, computeForwardKinematics=True, physicsClientId=self.id)[:2]
        wrist_pos, wrist_orient = p.getLinkState(self.human, 9, computeForwardKinematics=True, physicsClientId=self.id)[:2]

        robot_obs = np.concatenate([tool_pos-torso_pos, tool_orient, tool_pos - self.target_pos, self.target_pos-torso_pos, robot_joint_positions, shoulder_pos-torso_pos, elbow_pos-torso_pos, wrist_pos-torso_pos, forces]).ravel()
        if self.human_control:
            human_obs = np.concatenate([tool_pos-human_pos, tool_orient, tool_pos - self.target_pos, self.target_pos-human_pos, human_joint_positions, shoulder_pos-human_pos, elbow_pos-human_pos, wrist_pos-human_pos, forces_human]).ravel()
        else:
            human_obs = []

        return np.concatenate([robot_obs, human_obs]).ravel()

    def reset(self):
        self.setup_timing()
        self.task_success = 0
        self.prev_target_contact_pos = np.zeros(3)
        self.human, self.wheelchair, self.robot, self.robot_lower_limits, self.robot_upper_limits, self.human_lower_limits, self.human_upper_limits, self.robot_right_arm_joint_indices, self.robot_left_arm_joint_indices, self.gender = self.world_creation.create_new_world(furniture_type='wheelchair', static_human_base=True, human_impairment='random', print_joints=False, gender='random')
        self.robot_lower_limits = self.robot_lower_limits[self.robot_left_arm_joint_indices]
        self.robot_upper_limits = self.robot_upper_limits[self.robot_left_arm_joint_indices]
        self.reset_robot_joints()
        if self.robot_type == 'jaco':
            wheelchair_pos, wheelchair_orient = p.getBasePositionAndOrientation(self.wheelchair, physicsClientId=self.id)
            p.resetBasePositionAndOrientation(self.robot, np.array(wheelchair_pos) + np.array([-0.35, -0.3, 0.3]), p.getQuaternionFromEuler([0, 0, -np.pi/2.0], physicsClientId=self.id), physicsClientId=self.id)

        joints_positions = [(3, np.deg2rad(30)), (6, np.deg2rad(-90)), (16, np.deg2rad(-90)), (28, np.deg2rad(-90)), (31, np.deg2rad(80)), (35, np.deg2rad(-90)), (38, np.deg2rad(80))]
        self.human_controllable_joint_indices = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        self.world_creation.setup_human_joints(self.human, joints_positions, self.human_controllable_joint_indices, use_static_joints=True, human_reactive_force=None if self.human_control else 1, human_reactive_gain=0.01)
        p.resetBasePositionAndOrientation(self.human, [0, 0.03, 0.89 if self.gender == 'male' else 0.86], [0, 0, 0, 1], physicsClientId=self.id)
        human_joint_states = p.getJointStates(self.human, jointIndices=self.human_controllable_joint_indices, physicsClientId=self.id)
        self.target_human_joint_positions = np.array([x[0] for x in human_joint_states])
        self.human_lower_limits = self.human_lower_limits[self.human_controllable_joint_indices]
        self.human_upper_limits = self.human_upper_limits[self.human_controllable_joint_indices]

        shoulder_pos, shoulder_orient = p.getLinkState(self.human, 5, computeForwardKinematics=True, physicsClientId=self.id)[:2]
        elbow_pos, elbow_orient = p.getLinkState(self.human, 7, computeForwardKinematics=True, physicsClientId=self.id)[:2]
        wrist_pos, wrist_orient = p.getLinkState(self.human, 9, computeForwardKinematics=True, physicsClientId=self.id)[:2]

        if self.robot_type == 'pr2':
            target_pos = np.array([-0.55, 0, 0.8]) + self.np_random.uniform(-0.05, 0.05, size=3)
            target_orient = np.array(p.getQuaternionFromEuler(np.array([0, 0, 0]), physicsClientId=self.id))
            self.position_robot_toc(self.robot, 76, [(target_pos, target_orient)], [(shoulder_pos, None), (elbow_pos, None), (wrist_pos, None)], self.robot_left_arm_joint_indices, self.robot_lower_limits, self.robot_upper_limits, ik_indices=range(29, 29+7), pos_offset=np.array([0.1, 0, 0]), max_ik_iterations=200, step_sim=True, check_env_collisions=False, human_joint_indices=self.human_controllable_joint_indices, human_joint_positions=self.target_human_joint_positions)
            self.world_creation.set_gripper_open_position(self.robot, position=0.25, left=True, set_instantly=True)
            self.tool = self.world_creation.init_tool(self.robot, mesh_scale=[0.001]*3, pos_offset=[0, 0, 0], orient_offset=p.getQuaternionFromEuler([0, 0, 0], physicsClientId=self.id), maximal=False)
        elif self.robot_type == 'jaco':
            #target_pos = np.array([-0.5, 0, 0.8]) + self.np_random.uniform(-0.05, 0.05, size=3)
            head_pos, head_orient = p.getLinkState(self.human, 23, computeForwardKinematics=True, physicsClientId=self.id)[:2]
            target_pos = head_pos - np.array([0, 0.3, 0]) + self.np_random.uniform(-0.05, 0.05, size=3)
            target_orient = p.getQuaternionFromEuler(np.array([-np.pi/2.0, np.pi/2.0, 0]), physicsClientId=self.id)
            self.util.ik_random_restarts(self.robot, 8, target_pos, target_orient, self.world_creation, self.robot_left_arm_joint_indices, self.robot_lower_limits, self.robot_upper_limits, ik_indices=[0, 1, 2, 3, 4, 5, 6], max_iterations=1000, max_ik_random_restarts=40, random_restart_threshold=0.03, step_sim=True)
            self.world_creation.set_gripper_open_position(self.robot, position=1, left=True, set_instantly=True)
            self.tool = self.world_creation.init_tool(self.robot, mesh_scale=[0.001]*3, pos_offset=[0, 0, 0.02], orient_offset=p.getQuaternionFromEuler([0, -np.pi/2.0, 0], physicsClientId=self.id), maximal=False)
        else:
            target_pos = np.array([-0.55, 0, 0.8]) + self.np_random.uniform(-0.05, 0.05, size=3)
            target_orient = p.getQuaternionFromEuler(np.array([0, np.pi/2.0, 0]), physicsClientId=self.id)
            if self.robot_type == 'baxter':
                self.position_robot_toc(self.robot, 48, [(target_pos, target_orient)], [(shoulder_pos, None), (elbow_pos, None), (wrist_pos, None)], self.robot_left_arm_joint_indices, self.robot_lower_limits, self.robot_upper_limits, ik_indices=range(10, 17), pos_offset=np.array([0, 0, 0.975]), max_ik_iterations=200, step_sim=True, check_env_collisions=False, human_joint_indices=self.human_controllable_joint_indices, human_joint_positions=self.target_human_joint_positions)
            else:
                self.position_robot_toc(self.robot, 19, [(target_pos, target_orient)], [(shoulder_pos, None), (elbow_pos, None), (wrist_pos, None)], self.robot_left_arm_joint_indices, self.robot_lower_limits, self.robot_upper_limits, ik_indices=[0, 2, 3, 4, 5, 6, 7], pos_offset=np.array([-0.1, 0, 0.975]), max_ik_iterations=200, step_sim=True, check_env_collisions=False, human_joint_indices=self.human_controllable_joint_indices, human_joint_positions=self.target_human_joint_positions)
            self.world_creation.set_gripper_open_position(self.robot, position=0.015, left=True, set_instantly=True)
            self.tool = self.world_creation.init_tool(self.robot, mesh_scale=[0.001]*3, pos_offset=[0, 0.125, 0], orient_offset=p.getQuaternionFromEuler([0, 0, np.pi/2.0], physicsClientId=self.id), maximal=False)

        self.generate_target()

        p.setGravity(0, 0, 0, physicsClientId=self.id)
        p.setGravity(0, 0, -1, body=self.human, physicsClientId=self.id)

        # Enable rendering
        p.configureDebugVisualizer(p.COV_ENABLE_RENDERING, 1, physicsClientId=self.id)

        return self._get_obs([0], [0, 0])

    def generate_target(self):
        if self.gender == 'male':
            i = random.randint(0, 7)
            self.selected_pos = [[0, -.11, .13], [.04, -.10, .13], [-.04, -.10, .13], [.05, -.095, .06], [-.05, -.095, .06], [0, -.11, .01], [0, -.11, .05], [.02, -.10, .10]][i]
        else :
            self.selected_pos = [0, -0.11, 0.03]
        head_pos, head_orient = p.getLinkState(self.human, 23, computeForwardKinematics=True, physicsClientId=self.id)[ :2]
        target_pos, target_orient = p.multiplyTransforms(head_pos, head_orient, self.selected_pos, [0, 0, 0, 1], physicsClientId=self.id)

        sphere_collision = -1
        sphere_visual = p.createVisualShape(shapeType=p.GEOM_SPHERE, radius=0.01, rgbaColor=[0, 1, 1, 1], physicsClientId=self.id)
        self.target = p.createMultiBody(baseMass=0.0, baseCollisionShapeIndex=sphere_collision, baseVisualShapeIndex=sphere_visual, basePosition=target_pos, useMaximalCoordinates=False, physicsClientId=self.id)

        self.update_targets()

    def update_targets(self):
        head_pos, head_orient = p.getLinkState(self.human, 23, computeForwardKinematics=True, physicsClientId=self.id)[ :2]
        target_pos, target_orient = p.multiplyTransforms(head_pos, head_orient, self.selected_pos, [0, 0, 0, 1], physicsClientId=self.id)
        self.target_pos = np.array(target_pos)
        p.resetBasePositionAndOrientation(self.target, self.target_pos, [0, 0, 0, 1], physicsClientId=self.id)

