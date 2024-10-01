import itertools
import math
from math import pi, sqrt
import threading
import time
from typing import Optional
import keyboard
import random

from utils import *

from rlbot.messages.flat.ControllerState import ControllerState
from rlbot.messages.flat.PlayerInputChange import PlayerInputChange
from rlbot.socket.socket_manager import SocketRelay
from rlbot.utils.game_state_util import BallState, CarState, GameState, Physics, Vector3, Rotator, GameInfoState
from rlbot.utils.structures.bot_input_struct import PlayerInput
from rlbot.utils.structures.game_data_struct import GameTickPacket, PlayerInfo
from rlbot.utils.structures.game_interface import GameInterface

Mode_Settings = {
    # Keybinds
    'Reset Attack': 'backspace',
    'Retry Attack': '=',
    'Retry Attack But Change Defense': '-',

    # Delay Settings
    'Initial Delay': 0.1,
    'Over Delay': 0.2,
    'Time Limit': 120,

    # Simple bot settings
    # A very simple ball/player chasing bot, to put pressure
    # Does not affect the attack replay
    'Defense bot': True,
    'Should boost': True,
    'Should Jump': True,
    'Aim at': 'Random'  # 'Random' or 'Player' or 'Ball'
}


# GL to anyone else trying to understand this whole code, cause i cant lol
# It's a mess

# Future ideas

# Maybe retry previous defense?
# Maybe retry previous offense?
# Maybe offence+defence against the real bots?
# Maybe loading training packs as initial setups, instead of random (idk how thatll be done)

class AtkDef:
    def __init__(self, interface: GameInterface, packet: GameTickPacket):
        self.interface = interface
        self.renderer = interface.renderer

        indices_cars = list(enumerate(packet.game_cars[:packet.num_cars]))
        self.human_index = next(index for index, car in indices_cars if not car.is_bot)
        self.bot_index = [index for index, car in indices_cars if car.is_bot and car.team == 0][0]
        print([index for index, car in indices_cars if car.is_bot and car.team == 0])

        self.controls_tracker = ControlsTracker(self.human_index)
        self.time_measure = 0
        self.attacker_touch_toggle = False
        self.is_back = False
        self.is_retry = False
        self.length_of_attack = 10  # (This 10 value doesnt do anything, just a temp value)
        self.spawned_bot = False
        self.playing_anim = False
        self.store_defense = True
        self.store_offense = True
        self.bot_attack_ball = True

        self.ball_data = [
            Vector3(0, 0, 0),  # Location
            Vector3(0, 0, 0),  # Velocity
        ]

        # These initial values don't change anything
        self.defend_car_data = [
            Vector3(0, 5500, 0),  # Location
            Rotator(0, -pi / 2, 0),  # Rotation
            Vector3(0, 0, 0),  # Velocity
            100  # Boost amount
        ]
        self.attack_car_data = [
            Vector3(0, -5500, 0),  # Location
            # 0 yaw: to the left from the blue side
            Rotator(0, pi / 2, 0),  # Rotation
            Vector3(0, 0, 0),  # Velocity
            100  # Always 100 tbf, can easily allow changing tho, tho i dont see a need. Maybe low boost offense?
        ]

        self.restart_completely()
        self.start_stage(packet)

    def restart_completely(self):
        self.attack_replay = Replay()
        # Do we want a defending replay? Maybe in some other update
        # Like if the defender aves, attacker can try again, against the defender replay
        self.defend_replay = Replay()
        self.old_ball_replay = Replay()
        self.new_ball_replay = Replay()

        self.state = "attack"
        self.initial_delay = Mode_Settings['Initial Delay']
        self.over_delay = Mode_Settings['Over Delay']
        self.time_limit = Mode_Settings['Time Limit']

    def start_stage(self, packet, dont_restart=False):
        self.last_reset_time = None
        self.replaying_ball = True

        if not dont_restart:
            self.new_ball_replay = Replay()
            self.current_replay = Replay()

            self.old_ball_replay.reset()
            self.attack_replay.reset()

        if self.store_offense:
            # if its turn of attacker, then we spawn a new location, else old is fine
            if self.state == "attack":
                self.ball_data[0] = Vector3(
                    random.randint(-3000, 3000),
                    random.randint(-4600, 1000),
                    # Either roll, or high, inbetween sucks
                    random.choice([
                        random.randint(400, 600),
                        random.randint(0, 150),
                        0
                    ])
                )

                self.ball_data[1] = Vector3(
                    random.randint(-100, 100),
                    random.randint(-100, 100),
                    random.randint(0, 400) if self.ball_data[0].z > 100 else 0
                )
                self.is_back = False
                # If its very back(or maybe randomly), we want it to roll upwards ball,
                # for full field air dribble practice
                if self.ball_data[0].y < -4100 or (
                        self.ball_data[0].y < -3600 and random.choice([True, False])):
                    self.ball_data[0].z = 0
                    self.is_back = True
                    # Within goal area, move it if so
                    if abs(self.ball_data[0].x) < 900:
                        self.ball_data[0].x += math.copysign(900, self.ball_data[0].x)
                    self.ball_data[1].y = random.randint(-2500, -1500)
                    self.ball_data[1].z = 0
                    self.ball_data[1].x = 0

        # initial game state
        self.interface.set_game_state(GameState(
            ball=BallState(Physics(
                # location=Vector3(-1000, -3000, 93),
                location=self.ball_data[0],
                rotation=Rotator(0, 0, 0),
                velocity=self.ball_data[1],
                # velocity=Vector3(3, 0, 5),
                # velocity=Vector3(0, 0, 0),
                angular_velocity=Vector3(0, 0, 0),
            ))
        ))
        time.sleep(0.1)

    def show_text(self, text, color):
        self.renderer.clear_screen()
        self.renderer.begin_rendering()
        scale = 5
        for dx in [-3, 0, 3]:
            for dy in [-3, 0, 3]:
                self.renderer.draw_string_2d(100 + dx, 100 + dy, scale, scale, text, self.renderer.black())
        for _ in range(3):
            self.renderer.draw_string_2d(100, 100, scale, scale, text, color)
        self.renderer.end_rendering()

    def fail_or_saved(self, custom_text="You failed! Try again", timeout=0.5, fail=False):
        self.show_text(custom_text, self.renderer.red())
        self.interface.set_game_state(GameState(game_info=GameInfoState(game_speed=0.1)))
        time.sleep(timeout)
        self.interface.set_game_state(GameState(game_info=GameInfoState(game_speed=1.0)))
        if not fail:
            self.restart_completely()

    def step(self, packet: GameTickPacket):
        if self.last_reset_time is None:
            self.last_reset_time = packet.game_info.seconds_elapsed
            self.prev_blue_score = packet.teams[0].score
            self.prev_orange_score = packet.teams[1].score

        t = packet.game_info.seconds_elapsed - self.last_reset_time

        max_t = self.time_limit + self.initial_delay

        if t < self.initial_delay:
            self.show_text("Get ready!", self.renderer.yellow())
        else:
            if self.state == "attack":
                self.show_text(f"{max_t - t:.1f}", self.renderer.white())
            else:
                self.show_text(f"{(self.length_of_attack + 0.5) - t:.1f}", self.renderer.white())

            # Somehow detect orange team has touched ball
            # Success when the defender touched the ball, and then some time has elapsed without goal

            if self.state == "defend":
                self.spawned_bot = False
                who_touched = packet.game_ball.latest_touch.player_name

                # Let bot touch ball, then when we touch back, then start measure time
                if who_touched != packet.game_cars[self.human_index].name:
                    self.time_measure = t
                    self.attacker_touch_toggle = True

                # Success!
                if (who_touched == packet.game_cars[self.human_index].name
                    and t - self.time_measure > 0.5 and self.attacker_touch_toggle) \
                        or t > self.length_of_attack + 0.5:
                    self.time_measure = t
                    self.attacker_touch_toggle = False
                    self.show_text("Nice Block!", self.renderer.lime())
                    time.sleep(self.over_delay)
                    self.prepare_next_stage()
                    self.store_offense = True
                    self.store_defense = True
                    self.is_retry = False
                    return self.start_stage(packet)

                # Fail, retry saving! (Need to figure out how to restart the bot replay or smth
                if packet.teams[0].score > self.prev_blue_score:
                    self.time_measure = t
                    self.attacker_touch_toggle = False
                    self.fail_or_saved(custom_text="You missed! Try again", fail=True)
                    self.last_reset_time = None
                    self.is_retry = True

                    self.old_ball_replay.reset()
                    self.attack_replay.reset()

                    return self.start_stage(packet, dont_restart=True)

        # next timeline
        if packet.teams[0].score > self.prev_blue_score:
            self.spawned_bot = False
            self.time_measure = t
            self.attacker_touch_toggle = False
            # If score as attacker, become defender next stage
            if self.state == "attack":
                self.prepare_next_stage()
                self.length_of_attack = t
            else:
                # If you own goal
                self.fail_or_saved()
            return self.start_stage(packet)

        if t > max_t:
            self.store_offense = False
            self.fail_or_saved()
            self.spawned_bot = False
            return self.start_stage(packet)

        # reset button
        if t > self.initial_delay and keyboard.is_pressed(Mode_Settings['Reset Attack']):
            self.store_offense = True
            self.spawned_bot = False
            self.store_defense = True
            self.time_measure = t
            self.attacker_touch_toggle = False
            self.fail_or_saved(timeout=self.over_delay)
            return self.start_stage(packet)

        # Retry offense
        # The defense position will be same
        # Maybe another keybind, to allow defense position to be different, but offense same?
        if t > self.initial_delay and keyboard.is_pressed(Mode_Settings['Retry Attack']):
            self.store_offense = False
            self.store_defense = False
            self.spawned_bot = False
            self.time_measure = t
            self.attacker_touch_toggle = False
            self.fail_or_saved(timeout=self.over_delay)
            self.start_stage(packet)
            return

        if t > self.initial_delay and keyboard.is_pressed(Mode_Settings['Retry Attack But Change Defense']):
            self.store_offense = False
            self.store_defense = True
            self.spawned_bot = False
            self.time_measure = t
            self.attacker_touch_toggle = False
            self.fail_or_saved(timeout=self.over_delay)
            self.start_stage(packet)
            return

        target_game_state = GameState(cars={})

        # car drop

        # info for attacking
        ball_location = packet.game_ball.physics.location

        # This code block runs at start of each round ig
        if t < self.initial_delay:
            if self.state == "attack":
                if self.store_offense:
                    # car coords should be near the ball in some direction, on the blue size,
                    # towards the center
                    x_coord_sign = abs(ball_location.x) / (ball_location.x + 0.1) + 0.1
                    # need to convert to vector 3 later
                    x_offset = x_coord_sign * random.randint(100, 1000)
                    # If ball is really back, better do a back wall dribble
                    y_offset = random.randint(600, 1400)
                    if self.is_back:
                        y_offset *= 0.8
                        y_offset = - y_offset

                    # Rudimentary outofbounds prevention. Not really a big deal
                    if abs(ball_location.x - x_offset) > 4096:
                        x_offset *= 0.5
                    if abs(ball_location.y - y_offset) > 5120:
                        y_offset *= 0.5
                    car_loc = Vector3(
                        ball_location.x - x_offset,
                        ball_location.y - y_offset,
                        10
                    )

                    # Need to make the offset to euler angles?
                    # Ig its just 2d
                    # cars rotation should be such, that is almost facing the ball,
                    # but maybe some random offset
                    car_rot = Rotator(0, math.atan2(y_offset, x_offset) + random.randint(-30, 30) / 100, 0)

                    # Some initial speed, proportional to how far away from ball we spawned, But slower if ball is high
                    # 0.1 for preventing division by 0
                    car_vel = Vector3(
                        x_offset * 0.9 / (ball_location.z / 80 + 0.1),
                        y_offset * 0.9 / (ball_location.z / 80 + 0.1),
                        0
                    )

                    self.attack_car_data[0] = car_loc
                    self.attack_car_data[1] = car_rot
                    self.attack_car_data[2] = car_vel

                target_game_state.cars[self.human_index] = CarState(
                    physics=Physics(
                        location=self.attack_car_data[0],
                        rotation=self.attack_car_data[1],
                        velocity=self.attack_car_data[2],
                        angular_velocity=Vector3(0, 0, 0),
                    ),
                    boost_amount=self.attack_car_data[3],
                )

                # Also predefine a place for defense
                # For defensive, should spawn in different areas i think
                # or maybe random, in a shadow position

                if self.store_defense:
                    # Position data
                    self.defend_car_data[0] = Vector3(
                        clip(ball_location.x + random.randint(-3500, 3500), -4000, 4000),
                        clip(ball_location.y + random.randint(2000, 4000), 0, 4000),
                        5
                    )

                    # Either we are shadowing, or going aggressive against attacker
                    # Both are very distinct, so should have different set of rotations and vels
                    # Maybe a low boost scenario too
                    choices = ["Aggressive", "Shadow", "Low boost"]
                    got_choice = random.choice(choices)
                    if got_choice == choices[1]:
                        # Shadow
                        # Get a relative vec from car loc to closest corner of goal, that our angle
                        # Goal_corners = [
                        #     (-893,5120),
                        #     (893,5120)
                        # ]
                        # The closest corner is decided by the sign on our x/horizontal coordinate
                        rel_vec_x = math.copysign(893, self.defend_car_data[0].x) - self.defend_car_data[0].x
                        rel_vec_y = 5120 - self.defend_car_data[0].y

                        # Moving towards goal with some vel, so same vector, just scaled by some amount
                        velocity_x = math.copysign(893, self.defend_car_data[0].x) - self.defend_car_data[0].x
                        velocity_y = 5120 - self.defend_car_data[0].y

                        # the farther away, make it move more slower
                        velocity_x, velocity_y = norm_vec(velocity_x, velocity_y,
                                                          8000000 / (velocity_x ** 2 + velocity_y ** 2 + 0.1) ** (0.5))

                        boost_amt = 100

                    elif got_choice == choices[0]:
                        # Aim at ball!
                        rel_vec_x = self.ball_data[0].x - self.defend_car_data[0].x
                        rel_vec_y = self.ball_data[0].y - self.defend_car_data[0].y

                        # Aggression is risky, so slower
                        velocity_x = self.ball_data[0].x - self.defend_car_data[0].x
                        velocity_y = self.ball_data[0].y - self.defend_car_data[0].y

                        velocity_x, velocity_y = norm_vec(velocity_x, velocity_y, 2000)

                        boost_amt = 100

                    else:
                        self.defend_car_data[0].y = 4000
                        boost_amt = random.randint(5, 20)
                        rel_vec_x = math.copysign(893, self.defend_car_data[0].x) - self.defend_car_data[0].x
                        rel_vec_y = 5120 - self.defend_car_data[0].y

                        # Moving towards goal with some vel, so same vector, just scaled by some amount
                        velocity_x = math.copysign(893, self.defend_car_data[0].x) - self.defend_car_data[0].x
                        velocity_y = 5120 - self.defend_car_data[0].y

                        velocity_x, velocity_y = norm_vec(velocity_x, velocity_y, 1500)

                    gotten_angle = math.atan2(rel_vec_y, rel_vec_x)

                    # Undefined/Nan/inf check, default to -pi/2
                    if not (gotten_angle > 0 or gotten_angle < 0 or gotten_angle == 0):
                        gotten_angle = -pi / 2

                    self.defend_car_data[1] = Rotator(0, gotten_angle, 0)
                    self.defend_car_data[2] = Vector3(velocity_x * 0.5, velocity_y * 0.5, 0)
                    self.defend_car_data[3] = boost_amt

                    # What the bot should do in the stage
                    if Mode_Settings['Aim at'] == "Random":
                        self.bot_attack_ball = random.choice([True, False])
                    elif Mode_Settings['Aim at'] == "Ball":
                        self.bot_attack_ball = True
                    else:
                        self.bot_attack_ball = False

                    # To only allow changing location once
                    self.store_defense = False

            else:
                target_game_state.cars[self.human_index] = CarState(
                    physics=Physics(
                        #location=Vector3(0, 4608, 18),
                        location=self.defend_car_data[0],
                        rotation=self.defend_car_data[1],
                        velocity=self.defend_car_data[2],
                        angular_velocity=Vector3(0, 0, 0),
                    ),
                    boost_amount=self.defend_car_data[3],
                )
                self.store_defense = True

        # record
        snapshot = GameState.create_from_gametickpacket(packet)
        self.current_replay.add_snapshot(t, (snapshot.cars[self.human_index], self.controls_tracker.target_controls))
        self.new_ball_replay.add_snapshot(t, snapshot.ball)

        # Maybe bot should move towards the ball, to put pressure
        if not self.playing_anim:
            if not self.spawned_bot:
                target_game_state.cars[self.bot_index] = CarState(
                    Physics(
                        location=self.defend_car_data[0], rotation=self.defend_car_data[1],
                        velocity=self.defend_car_data[2]
                    ), boost_amount=self.defend_car_data[3])
            self.spawned_bot = True

            if Mode_Settings['Defense bot']:

                if self.bot_attack_ball:
                    target = Vector(vec_3=packet.game_ball.physics.location)
                    target_vel = Vector(vec_3=packet.game_ball.physics.velocity)
                    updated_target = target + target_vel.scale(0.5)
                else:
                    target = Vector(vec_3=packet.game_cars[self.human_index].physics.location)
                    target_vel = Vector(vec_3=packet.game_cars[self.human_index].physics.velocity)
                    updated_target = target + target_vel.scale(0.5)

                bot_coords = Vector(vec_3=packet.game_cars[self.bot_index].physics.location)
                bot_orientation = packet.game_cars[self.bot_index].physics.rotation
                rel_vec = bot_coords.rel_vec_with_axis(Orientation(bot_orientation), updated_target)
                angle = math.atan2(rel_vec.y, rel_vec.x)
                turn_angle = clip(angle, -1, 1)

                xy_mag = rel_vec.xy_mag()

                pitch = 1
                jump = 0
                if self.bot_attack_ball:
                    if xy_mag < 800 and rel_vec.z > 100 and Mode_Settings['Should Jump']:
                        jump = 1
                else:
                    if xy_mag < 800 and target.z > 30 and Mode_Settings['Should Jump']:
                        jump = 1
                        pitch = 0

                boost = 0
                if (xy_mag > 1600 or xy_mag < 1200) and Mode_Settings['Should boost']:
                    boost = 1

                handbrake = 0
                if 1600 > xy_mag > 1200:
                    handbrake = 1

                self.interface.update_player_input(
                    PlayerInput(
                        throttle=1,
                        steer=turn_angle,
                        pitch=pitch,
                        yaw=turn_angle,
                        roll=0,
                        jump=jump,
                        boost=boost,
                        handbrake=handbrake,
                        use_item=0,
                    ),
                    self.bot_index
                )

        # playback cars
        replay = self.attack_replay
        if replay.finished:
            self.playing_anim = False

        # No need to delete cars now ig
        # del target_game_state.cars[index]

        state = replay.playback(t)
        if state:
            self.playing_anim = True
            car_state, controls = state

            # gets rid of the warning console spam
            car_state.jumped = None
            car_state.double_jumped = None
            self.interface.update_player_input(controls, self.bot_index)
            target_game_state.cars[self.bot_index] = car_state

        if distance(packet.game_cars[self.human_index].physics.location, packet.game_ball.physics.location) < 300:
            self.replaying_ball = False

        # playback ball
        ball_state = self.old_ball_replay.playback(t)
        if self.replaying_ball and ball_state:
            target_game_state.ball = ball_state

        self.interface.set_game_state(target_game_state)

    def prepare_next_stage(self):
        pre_state = self.state
        if self.state == "attack":
            self.attack_replay = self.current_replay

        self.old_ball_replay = self.new_ball_replay

        # Restart then
        if self.state == "defend":
            self.restart_completely()

        self.state = "defend" if pre_state == "attack" else "attack"
