import importlib
import traceback
from pathlib import Path

from rlbot.agents.base_script import BaseScript
from rlbot.matchconfig.loadout_config import LoadoutConfig
from rlbot.matchconfig.match_config import PlayerConfig, MatchConfig, MutatorConfig
from rlbot.parsing.match_settings_config_parser import *
from rlbot.setup_manager import SetupManager

import attack_defender


def human_config():
    player_config = PlayerConfig()
    player_config.bot = False
    player_config.team = 0
    player_config.name = "Human"
    return player_config


def create_player_config(name: str, team):
    player_config = PlayerConfig()
    player_config.bot = True
    player_config.rlbot_controlled = True
    player_config.name = name
    player_config.team = team
    player_config.loadout_config = LoadoutConfig()
    player_config.loadout_config.team_color_id = 26
    return player_config


def build_match_config(game_map="Mannfield_Night", game_mode="Soccer", existing_match_settings=None):
    match_config = MatchConfig()
    # We only really need 1 other car
    match_config.player_configs = [create_player_config("You", 0)] + [human_config()]

    # Doesent have to be soccer!
    match_config.game_mode = game_mode
    match_config.game_map = game_map

    match_config.mutators = MutatorConfig()
    # / represetns its allowed, x not
    # 'BallBouncinessOption', /
    # 'BallMaxSpeedOption', /
    # 'BallSizeOption', /
    # 'BallTypeOption', /
    # 'BallWeightOption', /
    # 'BoostOption', /
    # 'BoostStrengthOption', /
    # 'DemolishOption', x
    # 'GameSpeedOption', /
    # 'GetRootAsMutatorSettings',
    # 'GravityOption', /
    # 'Init',
    # 'MatchLength', x
    # 'MaxScore', x
    # 'OvertimeOption', x
    # 'RespawnTimeOption', x
    # 'RumbleOption', /
    # 'SeriesLengthOption',
    if existing_match_settings is not None:
        existing_mutators = existing_match_settings.MutatorSettings()
        # Gameplay changes
        match_config.mutators.game_speed = game_speed_mutator_types[existing_mutators.GameSpeedOption()]
        match_config.mutators.boost_amount = boost_amount_mutator_types[existing_mutators.BoostOption()]
        match_config.mutators.boost_strength = boost_strength_mutator_types[existing_mutators.BoostStrengthOption()]
        match_config.mutators.gravity = gravity_mutator_types[existing_mutators.GravityOption()]
        match_config.mutators.rumble = rumble_mutator_types[existing_mutators.RumbleOption()]

        # Ball mutators
        match_config.mutators.ball_type = ball_type_mutator_types[existing_mutators.BallTypeOption()]
        match_config.mutators.ball_size = ball_size_mutator_types[existing_mutators.BallSizeOption()]
        match_config.mutators.ball_max_speed = ball_max_speed_mutator_types[existing_mutators.BallMaxSpeedOption()]
        match_config.mutators.ball_bounciness = ball_bounciness_mutator_types[existing_mutators.BallBouncinessOption()]
        match_config.mutators.ball_weight = ball_weight_mutator_types[existing_mutators.BallWeightOption()]

    # match_config.mutators.boost_amount = "Unlimited"
    match_config.mutators.match_length = "Unlimited"
    match_config.mutators.respawn_time = "Disable Goal Reset"
    match_config.mutators.demolish = "Disabled"

    match_config.enable_state_setting = True
    match_config.enable_rendering = True

    match_config.existing_match_behavior = "Restart"
    match_config.instant_start = True

    return match_config


class MinigameRunner(BaseScript):
    def __init__(self):
        super().__init__("Attack and Defend")
        self.setup_manager = SetupManager()
        self.setup_manager.game_interface = self.game_interface

        current_game_map = int(self.game_interface.get_match_settings().GameMap())
        current_game_map = list(game_map_dict.keys())[current_game_map]

        current_game_mode = int(self.game_interface.get_match_settings().GameMode())
        current_game_mode = game_mode_types[current_game_mode]

        # copied this from TrackAndField, without this rlbot crashes for some reason
        self.setup_manager.num_participants = 0
        self.setup_manager.launch_bot_processes(MatchConfig())

        existing_match_settings = self.get_match_settings()

        self.setup_manager.load_match_config(
            build_match_config(current_game_map, current_game_mode, existing_match_settings)
        )
        self.setup_manager.start_match()

        while True:
            packet = self.wait_game_tick_packet()
            if packet.game_info.is_round_active:
                break
        self.minigame = attack_defender.AtkDef(self.game_interface, packet)

        self.minigame_file = Path(__file__).parent / "attack_defender.py"
        self.last_mtime = self.minigame_file.lstat().st_mtime

    def run(self):
        while True:
            packet = self.wait_game_tick_packet()

            # hot reload
            mtime = self.minigame_file.lstat().st_mtime
            if mtime > self.last_mtime:
                try:
                    importlib.reload(attack_defender)
                    self.minigame = attack_defender.AtkDef(self.game_interface, packet)
                    print(f"[{mtime}] Reloaded game")
                    self.last_mtime = mtime

                except Exception as ex:
                    print()
                    print("-----------------RELOAD EXCEPTION-----------------")
                    print(ex)
                    print(traceback.format_exc())

            try:
                self.minigame.step(packet)

            except Exception as ex:
                print()
                print("-----------------STEP EXCEPTION-----------------")
                print(ex)
                print(traceback.format_exc())

                time.sleep(1.0)
                continue


if __name__ == '__main__':
    script = MinigameRunner()
    script.run()
