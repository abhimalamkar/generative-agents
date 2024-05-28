"""
Author: Joon Sung Park (joonspk@stanford.edu)

File: reverie.py
Description: This is the main program for running generative agent simulations
that defines the ReverieServer class. This class maintains and records all  
states related to the simulation. The primary mode of interaction for those  
running the simulation should be through the open_server function, which  
enables the simulator to input command-line prompts for running and saving  
the simulation, among other tasks.

Release note (June 14, 2023) -- Reverie implements the core simulation 
mechanism described in my paper entitled "Generative Agents: Interactive 
Simulacra of Human Behavior." If you are reading through these lines after 
having read the paper, you might notice that I use older terms to describe 
generative agents and their cognitive modules here. Most notably, I use the 
term "personas" to refer to generative agents, "associative memory" to refer 
to the memory stream, and "reverie" to refer to the overarching simulation 
framework.
"""
import json
import numpy
import datetime
import pickle
import time
import math
import os
import shutil
import traceback
import asyncio

from selenium import webdriver

import utils
from global_methods import *
from utils import *
from maze_neo import *
from persona.persona import *
from metrics import metrics
import argparse


##############################################################################
#                                  REVERIE                                   #
##############################################################################

class ReverieServer:
    def __init__(self,
                 fork_sim_code,
                 sim_code):
        # FORKING FROM A PRIOR SIMULATION:
        # <fork_sim_code> indicates the simulation we are forking from.
        # Interestingly, all simulations must be forked from some initial
        # simulation, where the first simulation is "hand-crafted".
        self.fork_sim_code = fork_sim_code
        fork_folder = f"{fs_storage}/{self.fork_sim_code}"

        # <sim_code> indicates our current simulation. The first step here is to
        # copy everything that's in <fork_sim_code>, but edit its
        # reverie/meta/json's fork variable.
        self.sim_code = sim_code
        sim_folder = f"{fs_storage}/{self.sim_code}"

        # remove the sim folder if it already exists
        if os.path.exists(sim_folder):
            shutil.rmtree(sim_folder)

        copyanything(fork_folder, sim_folder)

        #
        metrics.set_fold(sim_folder)
        utils.set_fold(sim_folder)

        with open(f"{sim_folder}/reverie/meta.json") as json_file:
            reverie_meta = json.load(json_file)

        with open(f"{sim_folder}/reverie/meta.json", "w") as outfile:
            reverie_meta["fork_sim_code"] = fork_sim_code
            outfile.write(json.dumps(reverie_meta, indent=2))

        # LOADING REVERIE'S GLOBAL VARIABLES
        # The start datetime of the Reverie:
        # <start_datetime> is the datetime instance for the start datetime of
        # the Reverie instance. Once it is set, this is not really meant to
        # change. It takes a string date in the following example form:
        # "June 25, 2022"
        # e.g., ...strptime(June 25, 2022, "%B %d, %Y")
        self.start_time = datetime.datetime.strptime(
            f"{reverie_meta['start_date']}, 00:00:00",
            "%B %d, %Y, %H:%M:%S")
        # <curr_time> is the datetime instance that indicates the game's current
        # time. This gets incremented by <sec_per_step> amount everytime the world
        # progresses (that is, everytime curr_env_file is recieved).
        self.curr_time = datetime.datetime.strptime(reverie_meta['curr_time'],
                                                    "%B %d, %Y, %H:%M:%S")
        # <sec_per_step> denotes the number of seconds in game time that each
        # step moves foward.
        self.sec_per_step = reverie_meta['sec_per_step']

        # <maze> is the main Maze instance. Note that we pass in the maze_name
        # (e.g., "double_studio") to instantiate Maze.
        # e.g., Maze("double_studio")
        self.maze = Maze(reverie_meta['maze_name'])

        # <step> denotes the number of steps that our game has taken. A step here
        # literally translates to the number of moves our personas made in terms
        # of the number of tiles.
        self.step = reverie_meta['step']

        # SETTING UP PERSONAS IN REVERIE
        # <personas> is a dictionary that takes the persona's full name as its
        # keys, and the actual persona instance as its values.
        # This dictionary is meant to keep track of all personas who are part of
        # the Reverie instance.
        # e.g., ["Isabella Rodriguez"] = Persona("Isabella Rodriguezs")
        self.personas = dict()
        # <personas_tile> is a dictionary that contains the tile location of
        # the personas (!-> NOT px tile, but the actual tile coordinate).
        # The tile take the form of a set, (row, col).
        # e.g., ["Isabella Rodriguez"] = (58, 39)
        self.personas_tile = dict()

        # # <persona_convo_match> is a dictionary that describes which of the two
        # # personas are talking to each other. It takes a key of a persona's full
        # # name, and value of another persona's full name who is talking to the
        # # original persona.
        # # e.g., dict["Isabella Rodriguez"] = ["Maria Lopez"]
        # self.persona_convo_match = dict()
        # # <persona_convo> contains the actual content of the conversations. It
        # # takes as keys, a pair of persona names, and val of a string convo.
        # # Note that the key pairs are *ordered alphabetically*.
        # # e.g., dict[("Adam Abraham", "Zane Xu")] = "Adam: baba \n Zane:..."
        # self.persona_convo = dict()

        # Loading in all personas.
        init_env_file = f"{sim_folder}/environment/{str(self.step)}.json"
        init_env = json.load(open(init_env_file))
        for persona_name in reverie_meta['persona_names']:
            persona_folder = f"{sim_folder}/personas/{persona_name}"
            p_x = init_env[persona_name]["x"]
            p_y = init_env[persona_name]["y"]
            start_node = init_env[persona_name]["start_node"]

            curr_persona = Persona(persona_name, persona_folder)

            self.personas[persona_name] = curr_persona
            self.personas_tile[persona_name] = start_node
            frontend_pos[persona_name] = start_node
            self.maze.add_event_from_tile(curr_persona.scratch.get_curr_event_and_desc(),
                                          start_node)
            # self.maze.tiles[p_y][p_x]["events"].add(curr_persona.scratch
            #                                         .get_curr_event_and_desc())

        # REVERIE SETTINGS PARAMETERS:
        # <server_sleep> denotes the amount of time that our while loop rests each
        # cycle; this is to not kill our machine.
        self.server_sleep = 0.0

        # SIGNALING THE FRONTEND SERVER:
        # curr_sim_code.json contains the current simulation code, and
        # curr_step.json contains the current step of the simulation. These are
        # used to communicate the code and step information to the frontend.
        # Note that step file is removed as soon as the frontend opens up the
        # simulation.
        curr_sim_code = dict()
        curr_sim_code["sim_code"] = self.sim_code
        # with open(f"{fs_temp_storage}/curr_sim_code.json", "w") as outfile:
        #     outfile.write(json.dumps(curr_sim_code, indent=2))

        curr_step = dict()
        curr_step["step"] = self.step
        # with open(f"{fs_temp_storage}/curr_step.json", "w") as outfile:
        #     outfile.write(json.dumps(curr_step, indent=2))

    def save(self):
        """
    Save all Reverie progress -- this includes Reverie's global state as well
    as all the personas.  

    INPUT
      None
    OUTPUT 
      None
      * Saves all relevant data to the designated memory directory
    """
        # <sim_folder> points to the current simulation folder.
        sim_folder = f"{fs_storage}/{self.sim_code}"

        # Save Reverie meta information.
        reverie_meta = dict()
        reverie_meta["fork_sim_code"] = self.fork_sim_code
        reverie_meta["start_date"] = self.start_time.strftime("%B %d, %Y")
        reverie_meta["curr_time"] = self.curr_time.strftime("%B %d, %Y, %H:%M:%S")
        reverie_meta["sec_per_step"] = self.sec_per_step
        reverie_meta["maze_name"] = self.maze.maze_name
        reverie_meta["persona_names"] = list(self.personas.keys())
        reverie_meta["step"] = self.step
        reverie_meta_f = f"{sim_folder}/reverie/meta.json"
        with open(reverie_meta_f, "w") as outfile:
            outfile.write(json.dumps(reverie_meta, indent=2))

        # Save the personas.
        for persona_name, persona in self.personas.items():
            save_folder = f"{sim_folder}/personas/{persona_name}/bootstrap_memory"
            persona.save(save_folder)

        metrics.save()

    async def start_server(self, int_counter):
        """
    The main backend server of Reverie. 
    This function retrieves the environment file from the frontend to 
    understand the state of the world, calls on each personas to make 
    decisions based on the world state, and saves their moves at certain step
    intervals. 
    INPUT
      int_counter: Integer value for the number of steps left for us to take
                   in this iteration. 
    OUTPUT 
      None
    """
        # <sim_folder> points to the current simulation folder.
        sim_folder = f"{fs_storage}/{self.sim_code}"

        # When a persona arrives at a game object, we give a unique event
        # to that object.
        # e.g., ('double studio[...]:bed', 'is', 'unmade', 'unmade')
        # Later on, before this cycle ends, we need to return that to its
        # initial state, like this:
        # e.g., ('double studio[...]:bed', None, None, None)
        # So we need to keep track of which event we added.
        # <game_obj_cleanup> is used for that.

        # The main while loop of Reverie.
        backend_data = {'time': self.curr_time.strftime("%B %d, %Y, %H:%M:%S"), 'persona': dict()}
        for k, v in frontend_pos.items():
            backend_data['persona'][k] = v
        game_obj_cleanup = dict()
        while True:
            # Done with this iteration if <int_counter> reaches 0.
            if int_counter == 0:
                break

            # <curr_env_file> file is the file that our frontend outputs. When the
            # frontend has done its job and moved the personas, then it will put a
            # new environment file that matches our step count. That's when we run
            # the content of this for loop. Otherwise, we just wait.
            curr_env_file = f"{sim_folder}/environment/{self.step}.json"

            frontend_data = sim_frontend(frontend_pos,backend_data, self.step, self.sim_code)

            if frontend_data is not None:
                if frontend_data is not None:
                    # This is where we go through <game_obj_cleanup> to clean up all
                    # object actions that were used in this cylce.
                    for key, val in game_obj_cleanup.items():
                        # We turn all object actions to their blank form (with None).
                        self.maze.turn_event_from_tile_idle(key, val)
                    # Then we initialize game_obj_cleanup for this cycle.
                    game_obj_cleanup = dict()

                    # We first move our personas in the backend environment to match
                    # the frontend environment.
                    for persona_name, persona in self.personas.items():
                        # <curr_tile> is the tile that the persona was at previously.
                        curr_tile = self.personas_tile[persona_name]
                        # <new_tile> is the tile that the persona will move to right now,
                        # during this cycle.
                        new_tile = frontend_data[persona_name]

                        # We actually move the persona on the backend tile map here.
                        self.personas_tile[persona_name] = new_tile
                        self.maze.remove_subject_events_from_tile(persona.name, curr_tile)
                        self.maze.add_event_from_tile(persona.scratch
                                                      .get_curr_event_and_desc(), new_tile)

                        # Now, the persona will travel to get to their destination. *Once*
                        # the persona gets there, we activate the object action.
                        if not persona.scratch.planned_path:
                            # We add that new object action event to the backend tile map.
                            # At its creation, it is stored in the persona's backend.
                            game_obj_cleanup[persona.scratch
                                .get_curr_obj_event_and_desc()] = new_tile
                            self.maze.add_event_from_tile(persona.scratch
                                                          .get_curr_obj_event_and_desc(), new_tile)
                            # We also need to remove the temporary blank action for the
                            # object that is currently taking the action.
                            blank = (persona.scratch.get_curr_obj_event_and_desc()[0],
                                     None, None, None)
                            self.maze.remove_event_from_tile(blank, new_tile)

                    # Then we need to actually have each of the personas perceive and
                    # move. The movement for each of the personas comes in the form of
                    # x y coordinates where the persona will move towards. e.g., (50, 34)
                    # This is where the core brains of the personas are invoked.
                    movements = {"persona": dict(),
                                 "meta": dict()}
                    for persona_name, persona in self.personas.items():
                        # <next_tile> is a x,y coordinate. e.g., (58, 9)
                        # <pronunciatio> is an emoji. e.g., "\ud83d\udca4"
                        # <description> is a string description of the movement. e.g.,
                        #   writing her next novel (editing her novel)
                        #   @ double studio:double studio:common room:sofa
                        persona.scratch.curr_tile = self.personas_tile[persona_name]
                        new_day = False
                        if not persona.scratch.curr_time: 
                            new_day = "First day"
                        elif (persona.scratch.curr_time.strftime('%a %b %d')
                                != self.curr_time.strftime('%a %b %d')):
                            new_day = "New day"
                        persona.scratch.curr_time = self.curr_time
                        perceived = await persona.perceive(self.maze)
                        
                        next_tile, pronunciatio, description, plan = await persona.move(
                            self.maze, self.personas, self.personas_tile[persona_name],
                            self.curr_time, perceived, new_day)
                        movements["persona"][persona_name] = {}
                        movements["persona"][persona_name]["movement"] = next_tile
                        backend_data["persona"][persona_name] = next_tile
                        movements["persona"][persona_name]["pronunciatio"] = pronunciatio
                        movements["persona"][persona_name]["description"] = description
                        movements["persona"][persona_name]["chat"] = (persona
                                                                      .scratch.chat)

                    # Include the meta information about the current stage in the
                    # movements dictionary.
                    movements["meta"]["curr_time"] = (self.curr_time
                                                      .strftime("%B %d, %Y, %H:%M:%S"))
                    backend_data['time'] = movements["meta"]["curr_time"]
                    # We then write the personas' movements to a file that will be sent
                    # to the frontend server.
                    # Example json output:
                    # {"persona": {"Maria Lopez": {"movement": [58, 9]}},
                    #  "persona": {"Klaus Mueller": {"movement": [38, 12]}},
                    #  "meta": {curr_time: <datetime>}}
                    curr_move_path = f"{sim_folder}/movement"
                    # If the folder doesn't exist, we create it.
                    if not os.path.exists(curr_move_path):
                        os.makedirs(curr_move_path)
                    curr_move_file = f"{sim_folder}/movement/{self.step}.json"
                    with open(curr_move_file, "w") as outfile:
                        outfile.write(json.dumps(movements, indent=2))

                    # After this cycle, the world takes one step forward, and the
                    # current time moves by <sec_per_step> amount.
                    self.step += 1
                    self.curr_time += datetime.timedelta(seconds=self.sec_per_step)

                    int_counter -= 1

            # Sleep so we don't burn our machines.
            time.sleep(self.server_sleep)

    def open_server(self):
        """
    Open up an interactive terminal prompt that lets you run the simulation 
    step by step and probe agent state. 

    INPUT 
      None
    OUTPUT
      None
    """
        print("Note: The agents in this simulation package are computational")
        print("constructs powered by generative agents architecture and LLM. We")
        print("clarify that these agents lack human-like agency, consciousness,")
        print("and independent decision-making.\n---")

        # <sim_folder> points to the current simulation folder.
        sim_folder = f"{fs_storage}/{self.sim_code}"

        while True:
            sim_command = input("Enter option: ")
            sim_command = sim_command.strip()
            ret_str = ""

            try:
                if sim_command.lower() in ["f", "fin", "finish", "save and finish"]:
                    # Finishes the simulation environment and saves the progress.
                    # Example: fin
                    self.save()
                    break

                elif sim_command.lower() == "start path tester mode":
                    # Starts the path tester and removes the currently forked sim files.
                    # Note that once you start this mode, you need to exit out of the
                    # session and restart in case you want to run something else.
                    shutil.rmtree(sim_folder)
                    self.start_path_tester_server()

                elif sim_command.lower() == "exit":
                    # Finishes the simulation environment but does not save the progress
                    # and erases all saved data from current simulation.
                    # Example: exit
                    shutil.rmtree(sim_folder)
                    break

                elif sim_command.lower() == "save":
                    # Saves the current simulation progress.
                    # Example: save
                    self.save()

                elif sim_command[:3].lower() == "run":
                    # Runs the number of steps specified in the prompt.
                    # Example: run 1000
                    int_count = int(sim_command.split()[-1])
                    rs.start_server(int_count)

                elif ("print persona schedule"
                      in sim_command[:22].lower()):
                    # Print the decomposed schedule of the persona specified in the
                    # prompt.
                    # Example: print persona schedule Isabella Rodriguez
                    ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                .scratch.get_str_daily_schedule_summary())

                elif ("print all persona schedule"
                      in sim_command[:26].lower()):
                    # Print the decomposed schedule of all personas in the world.
                    # Example: print all persona schedule
                    for persona_name, persona in self.personas.items():
                        ret_str += f"{persona_name}\n"
                        ret_str += f"{persona.scratch.get_str_daily_schedule_summary()}\n"
                        ret_str += f"---\n"

                elif ("print hourly org persona schedule"
                      in sim_command.lower()):
                    # Print the hourly schedule of the persona specified in the prompt.
                    # This one shows the original, non-decomposed version of the
                    # schedule.
                    # Ex: print persona schedule Isabella Rodriguez
                    ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                .scratch.get_str_daily_schedule_hourly_org_summary())

                elif ("print persona current tile"
                      in sim_command[:26].lower()):
                    # Print the x y tile coordinate of the persona specified in the
                    # prompt.
                    # Ex: print persona current tile Isabella Rodriguez
                    ret_str += str(self.personas[" ".join(sim_command.split()[-2:])]
                                   .scratch.curr_tile)

                elif ("print persona chatting with buffer"
                      in sim_command.lower()):
                    # Print the chatting with buffer of the persona specified in the
                    # prompt.
                    # Ex: print persona chatting with buffer Isabella Rodriguez
                    curr_persona = self.personas[" ".join(sim_command.split()[-2:])]
                    for p_n, count in curr_persona.scratch.chatting_with_buffer.items():
                        ret_str += f"{p_n}: {count}"

                elif ("print persona associative memory (event)"
                      in sim_command.lower()):
                    # Print the associative memory (event) of the persona specified in
                    # the prompt
                    # Ex: print persona associative memory (event) Isabella Rodriguez
                    ret_str += f'{self.personas[" ".join(sim_command.split()[-2:])]}\n'
                    ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                .a_mem.get_str_seq_events())

                elif ("print persona associative memory (thought)"
                      in sim_command.lower()):
                    # Print the associative memory (thought) of the persona specified in
                    # the prompt
                    # Ex: print persona associative memory (thought) Isabella Rodriguez
                    ret_str += f'{self.personas[" ".join(sim_command.split()[-2:])]}\n'
                    ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                .a_mem.get_str_seq_thoughts())

                elif ("print persona associative memory (chat)"
                      in sim_command.lower()):
                    # Print the associative memory (chat) of the persona specified in
                    # the prompt
                    # Ex: print persona associative memory (chat) Isabella Rodriguez
                    ret_str += f'{self.personas[" ".join(sim_command.split()[-2:])]}\n'
                    ret_str += (self.personas[" ".join(sim_command.split()[-2:])]
                                .a_mem.get_str_seq_chats())

                elif ("print persona spatial memory"
                      in sim_command.lower()):
                    # Print the spatial memory of the persona specified in the prompt
                    # Ex: print persona spatial memory Isabella Rodriguez
                    self.personas[" ".join(sim_command.split()[-2:])].s_mem.print_tree()

                elif ("print current time"
                      in sim_command[:18].lower()):
                    # Print the current time of the world.
                    # Ex: print current time
                    ret_str += f'{self.curr_time.strftime("%B %d, %Y, %H:%M:%S")}\n'
                    ret_str += f'steps: {self.step}'

                elif ("print tile event"
                      in sim_command[:16].lower()):
                    # Print the tile events in the tile specified in the prompt
                    # Ex: print tile event 50, 30
                    cooordinate = [int(i.strip()) for i in sim_command[16:].split(",")]
                    for i in self.maze.access_tile(cooordinate)["events"]:
                        ret_str += f"{i}\n"

                elif ("print tile details"
                      in sim_command.lower()):
                    # Print the tile details of the tile specified in the prompt
                    # Ex: print tile event 50, 30
                    cooordinate = [int(i.strip()) for i in sim_command[18:].split(",")]
                    for key, val in self.maze.access_tile(cooordinate).items():
                        ret_str += f"{key}: {val}\n"

                elif ("call -- analysis"
                      in sim_command.lower()):
                    # Starts a stateless chat session with the agent. It does not save
                    # anything to the agent's memory.
                    # Ex: call -- analysis Isabella Rodriguez
                    persona_name = sim_command[len("call -- analysis"):].strip()
                    self.personas[persona_name].open_convo_session("analysis")

                elif ("call -- load history"
                      in sim_command.lower()):
                    curr_file = maze_assets_loc + "/" + sim_command[len("call -- load history"):].strip()
                    # call -- load history the_ville/agent_history_init_n3.csv

                    rows = read_file_to_list(curr_file, header=True, strip_trail=True)[1]
                    clean_whispers = []
                    for row in rows:
                        agent_name = row[0].strip()
                        whispers = row[1].split(";")
                        whispers = [whisper.strip() for whisper in whispers]
                        for whisper in whispers:
                            clean_whispers += [[agent_name, whisper]]

                    load_history_via_whisper(self.personas, clean_whispers)

                print(ret_str)

            except Exception as e:
                metrics.fail_record(e)
                traceback.print_exc()
                print("Error.")
                pass


frontend_pos = dict()


def sim_frontend(frontend_pos,backend_data, step, sim_code):
    ## backend send data
    curr_time = backend_data['time']
    print(f"frontend time:{curr_time}")

    persona_dict = backend_data['persona']

    ## frontend process data
    for person_name, person_info in persona_dict.items():
        frontend_pos[person_name] = person_info

    step += 1

    environment = dict()
    for k, v in persona_dict.items():
        environment[k] = frontend_pos[k]

    data = dict()
    data['step'] = step
    data['sim_code'] = sim_code
    data['environment'] = environment
    with open(f"../../environment/frontend_server/storage/{sim_code}/environment/{step}.json", "w") as outfile:
        outfile.write(json.dumps(environment, indent=2))
    return environment


def rs_answer_question(file_name, rs, _question):
    question_list = [
        "Did you know there is a Valentine’s Day party?",
        "Do you know who is running for mayor? Please provide a specific name.",
        "Have you participated in the Valentine’s Day party? If you didn't participate, what was the reason that "
        "stopped you? "
    ]

    with open(f"./{file_name}_question.txt", 'w') as f:
        for question in question_list:
            answers = dict()
            for k, v in rs.personas.items():
                answers[k] = rs.personas[k].answer_question(question)
            f.writelines(f"Question: {question}\n")
            f.writelines(f"Answer:\n")

            for person_name, answer in answers.items():
                f.writelines(f"{person_name}: {answer}\n")
                print(f"{person_name}: {answer}")
            f.writelines(f"\n\n")


def opt():
    parser = argparse.ArgumentParser(description='This is the offline version of reverie which can run without the '
                                                 'frontend')
    parser.add_argument('-o', '--origin', type=str, default='base_rivenwood_elara_brian_finn',
                        help='the forked simulation')
    parser.add_argument('-t', '--target', type=str, help='the new simulation', default='offline')
    parser.add_argument('-s', '--step', type=int, help='the total run step', default=5000)
    parser.add_argument('--disable_policy', action='store_false', help='Disable the lifestyle policy')
    parser.add_argument('--disable_relationship', action='store_false', help='Disable the social impression memory')
    parser.add_argument('-c', '--call', type=str, help='call interview with agent')
    parser.add_argument('-q', '--question', type=str, help='ask all agents with the question')
    return parser.parse_args()


if __name__ == '__main__':
    args = opt()
    utils.use_policy = args.disable_policy
    utils.use_relationship = args.disable_relationship

    rs = ReverieServer(args.origin, args.target)
    if args.call is not None:
        persona_name = args.call
        rs.personas[persona_name].open_convo_session("analysis")
    elif args.question is not None:
        rs_answer_question(args.target, rs, args.question)
    else:
        asyncio.run(rs.start_server(args.step))
        rs.save()
