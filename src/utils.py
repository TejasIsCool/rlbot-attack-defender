# Basic utilities for the code

import itertools
import math
import threading
from typing import Optional, Self
from rlbot.messages.flat.ControllerState import ControllerState
from rlbot.messages.flat.PlayerInputChange import PlayerInputChange
from rlbot.socket.socket_manager import SocketRelay
from rlbot.utils.game_state_util import BallState, CarState, GameState, Physics, Vector3, Rotator, GameInfoState
from rlbot.utils.structures.bot_input_struct import PlayerInput
from rlbot.utils.structures.game_data_struct import GameTickPacket, PlayerInfo
from rlbot.utils.structures.game_interface import GameInterface

from math import pi, sqrt, sin, cos


def cstate_to_pinput(controls: ControllerState) -> PlayerInput:
    return PlayerInput(
        throttle=controls.Throttle(),
        steer=controls.Steer(),
        pitch=controls.Pitch(),
        yaw=controls.Yaw(),
        roll=controls.Roll(),
        jump=controls.Jump(),
        boost=controls.Boost(),
        handbrake=controls.Handbrake(),
        use_item=controls.UseItem(),
    )


def distance(a, b):
    return sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def clip(value, lower, upper):
    return lower if value < lower else upper if value > upper else value


def norm_vec(x, y, scale):
    mag = (x ** 2 + y ** 2 + 0.1) ** (0.5)
    return x / mag * scale, y / mag * scale


class Vector:
    def __init__(self, vec_list: list[float] = None, vec_3: Vector3 = None):
        self.x = 0
        self.y = 0
        self.z = 0
        if vec_3 is not None:
            self.x = vec_3.x
            self.y = vec_3.y
            self.z = vec_3.z
        if vec_list is not None:
            self.x, self.y, self.z = vec_list

    def magnitude(self) -> float:
        return sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def xy_mag(self) -> float:
        return sqrt(self.x ** 2 + self.y ** 2)

    def relative_vec(self, v2: Self) -> Self:
        """
        :param v2: The other vector to which relative vec is calculated
        :return: v2-v1
        """
        return Vector(vec_list=[v2.x - self.x, v2.y - self.y, v2.z - self.z])

    # Taken from rlbot python repo
    def rel_vec_with_axis(self, ori: 'Orientation', target: Self) -> Self:
        """
        Returns target as a relative location from center's point of view, using the given orientation. The components of
        the returned vector describes:

        * x: how far in front
        * y: how far right
        * z: how far above
        """
        x = (target - self).dot(ori.forward)
        y = (target - self).dot(ori.right)
        z = (target - self).dot(ori.up)
        return Vector(vec_list=[x, y, z])

    def norm_vec(self) -> Self:
        mag = self.magnitude()
        return Vector(vec_list=[self.x / mag, self.y / mag, self.z / mag])

    def norm_scale(self, scale: float) -> Self:
        mag = self.magnitude()
        mag *= scale
        return Vector(vec_list=[self.x / mag, self.y / mag, self.z / mag])

    def dot(self, v2: Self) -> float:
        return self.x * v2.x + self.y * v2.y + self.z * v2.z

    def cross(self, v2: Self) -> Self:
        c_x = self.y * v2.z - self.z * v2.y
        c_y = self.x * v2.z - self.z * v2.x
        c_z = self.x * v2.y - self.y * v2.x
        return Vector(vec_list=[c_x, c_y, c_z])

    def __add__(self, v2) -> Self:
        return Vector(vec_list=[self.x + v2.x, self.y + v2.y, self.z + v2.z])

    def __sub__(self, v2) -> Self:
        return Vector(vec_list=[self.x - v2.x, self.y - v2.y, self.z - v2.z])

    def __neg__(self) -> Self:
        return Vector(vec_list=[-self.x, -self.y, -self.z])

    def scale(self, scl):
        return Vector(vec_list=[self.x * scl, self.y * scl, self.z * scl])

    @staticmethod
    def from_angle_2d(theta: float, r: float) -> 'Vector':
        """

        :param theta: This Theta is from left x axis!, considering the blue side as main. ALthought doesent matter, as x axis is that way too
        :param r: Magnitude of vector
        :return:
        """
        return Vector(vec_list=[r * sin(theta), r * cos(theta), 0])

    def __str__(self):
        return f"[{self.x},{self.y},{self.z}]"


# Taken from rlbot python repo
class Orientation:
    """
    This class describes the orientation of an object from the rotation of the object.
    Use this to find the direction of cars: forward, right, up.
    It can also be used to find relative locations.
    """

    def __init__(self, rotation: Rotator):
        self.yaw = float(rotation.yaw)
        self.roll = float(rotation.roll)
        self.pitch = float(rotation.pitch)

        cr = math.cos(self.roll)
        sr = math.sin(self.roll)
        cp = math.cos(self.pitch)
        sp = math.sin(self.pitch)
        cy = math.cos(self.yaw)
        sy = math.sin(self.yaw)

        self.forward = Vector(vec_list=[cp * cy, cp * sy, sp])
        self.right = Vector(vec_list=[cy * sp * sr - cr * sy, sy * sp * sr + cr * cy, -cp * sr])
        self.up = Vector(vec_list=[-cr * cy * sp - sr * sy, -cr * sy * sp + sr * cy, cp * cr])


class ControlsTracker:
    def __init__(self, target_index) -> None:
        self.target_controls = PlayerInput(0, 0, 0, 0, 0, False, False, False, False)
        self.target_index = target_index
        self.socket_man = SocketRelay()
        self.socket_man.player_input_change_handlers.append(self.track_human_inputs)
        self.socket_thread = threading.Thread(target=self.run_socket_relay)
        self.socket_thread.start()

    def track_human_inputs(self, change: PlayerInputChange, seconds: float, frame_num: int):
        if change.PlayerIndex() == self.target_index:
            self.target_controls = cstate_to_pinput(change.ControllerState())

    def run_socket_relay(self):
        self.socket_man.connect_and_run(wants_quick_chat=True, wants_game_messages=True, wants_ball_predictions=False)


class Replay:
    def __init__(self) -> None:
        self.snapshots = []
        self.current_index = 0
        self.finished = False

    def add_snapshot(self, t, snapshot):
        self.snapshots.append((t, snapshot))

    def playback(self, t: float) -> Optional[CarState]:
        try:
            index, snapshot = next(snapshot for snapshot in self.snapshots if snapshot[0] >= t)
        except StopIteration:
            self.finished = True
            return None

        if index > self.current_index:
            self.current_index = index
            return snapshot
        return None

    def reset(self):
        self.current_index = 0
        self.finished = False
