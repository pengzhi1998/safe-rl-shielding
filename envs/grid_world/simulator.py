#!/usr/bin/python
#
# Simulates an MDP-Strategy

import math
import os
import sys, code
import resource
import copy
import itertools
import random
from PIL import Image
import os, pygame, pygame.locals
from pybrain3.rl.environments import Environment
from pybrain3.rl.environments import Task
from pybrain3.rl.agents import LearningAgent
from pybrain3.rl.experiments import Experiment
from my_pybrain.my_explorer import MyUCBExplorer
from my_pybrain.my_explorer import MyGreedyExplorer
from my_pybrain.my_table import MyActionValueTable
from my_pybrain.my_learner import MyQ, SARSA
from pybrain3.utilities import abstractMethod

import numpy as np
from itertools import product
from copy import deepcopy
from scipy import argmax
from scipy import where
from random import choice
import importlib

# from scenario_9x9_shield_multi3 import Shield
# from cycling_enemy_shield_incl_enemy_multi3 import Shield

np.set_printoptions(threshold=np.inf)

import argparse

parser = argparse.ArgumentParser(description='Simulator')
parser.add_argument(dest="png_file_base")
parser.add_argument("-c", "--collect-data", dest="collect_data_file", help="Provide a file for collecting convergence data")
parser.add_argument('-g', "--gen-spec", dest='gen_spec', help='Generate shield files', action='store_true', default=False)
parser.add_argument('-l', "--load", dest='load_file', help='Load Q-Table from file')
parser.add_argument('-s', "--save", dest='save_file', help='Save Q-Table to file')
parser.add_argument('-t', "--train", dest='train', help='Training activated', type=float, default=.2)
parser.add_argument('-o', "--shield_options", dest='shield_options', help='Number of actions the shield can choose of. 0 disables the shield', type=int, default=1)
parser.add_argument('-n', "--negative-reward", dest='neg_reward', help='Indicated whether negative reward should be used for unsafe actions', action='store_true', default=False)
parser.add_argument('-p', "--huge-negative-reward", dest='huge_neg_reward', help='Indicated whether a huge negative reward should be used for unsafe actions', action='store_true', default=False)
parser.add_argument('-r', "--sarsa", dest='sarsa', help='Indicated whether to use SARSA or default Q-learning', action='store_true', default=False)
parser.add_argument("--num-steps", dest='num_steps', help='Number of interactions', type=int, default=1000000)

args = parser.parse_args()

collect_data_file = args.collect_data_file
gen_spec = args.gen_spec
specFile = args.png_file_base
shield_options = args.shield_options
load_file = args.load_file
save_file = args.save_file
exploration = args.train
neg_reward = args.neg_reward
huge_neg_reward = args.huge_neg_reward
MAX_STEPS = args.num_steps

pngfile = Image.open(specFile)
pngFileBasis = specFile[0:specFile.rfind(".png")]
path = pngFileBasis[:pngFileBasis.rfind(os.path.sep)]

# ==================================
# Settings
# ==================================
MAGNIFY = 64

# ==================================
# Read parameter file
# ==================================
parameterFileName = pngFileBasis+".params"
allParams = {}
for a in open(parameterFileName,"r").readlines():
    a = a.strip()
    if len(a)>0 and a[0]!='#':
        posEqual = a.index("=")
        allParams[a[0:posEqual].strip()] = a[posEqual+1:].strip()

# ==================================
# Parse parameter file
# ==================================
initX = int(allParams["initX"])
initY = int(allParams["initY"])
positionUpdateNoise = float(allParams["positionUpdateNoise"])
WALL = int(allParams["wall"])
NORMAL_FIELD = int(allParams["normalField"])
NUMBER_OF_COLORS = int(allParams["numberOfColors"])

enemies_enabled = False
# if "enemies" in allParams:
#     try:
#         mode_name = allParams["enemies"]
#         mode_name = path + "." + mode_name[:mode_name.rfind(".py")]
#         enemy_handler = importlib.import_module(mode_name.replace(os.path.sep, ".")).EnemyHandler()
#         enemies_enabled = True
#     except ImportError as e:
#         print ("Could not find file " + enemy_handler_file)
#         print (e)
#         exit()

bombs = []
if "bombs" in allParams:
    # careful with evil evals
    bombs = eval(allParams["bombs"])
else:
    bombs = []

# ==================================
# Read input image
# ==================================

if shield_options > 0:
    try:
        mod_name = pngFileBasis + "_" + str(shield_options)
        Shield = importlib.import_module(mod_name.replace(os.path.sep, ".")).Shield
    except ImportError as e:
        print ("Could not find file " + pngFileBasis + "_" + str(shield_options) + ".py")
        print (e)
        exit()

else:
    from no_shield import Shield



xsize = pngfile.size[0]
ysize = pngfile.size[1]
imageData = pngfile.getdata()
palette = pngfile.getpalette()

# for i in range(len(imageData)):
#     print (imageData[i])

if "colorOrder" in allParams:
    colors = eval(allParams["colorOrder"])
else:
    assert(max(imageData) == NUMBER_OF_COLORS + 1)
    colors = range(max(imageData) + 1)
    colors.remove(WALL)
    colors.remove(NORMAL_FIELD)

# ==================================
# Construct MDP --> States
# ==================================
"""
Notice this map dictionary doesn't contain any agent positions at the walls.
"""
stateMapper = {}
for xA in range(0,xsize):
    for yA in range(0,ysize):
        for (csf,payoff) in [(x, 0) for x in range(NUMBER_OF_COLORS)] + [(0,1)]:
            if (imageData[xA+yA*xsize]!=WALL):
                stateNum = len(stateMapper)
                stateMapper[(xA,yA,csf,payoff)] = stateNum

# print (stateMapper)
BAD_STATE = len(stateMapper)
# Add error state
errorState = len(stateMapper)
errorStateKey = (-1,-1,0,0)
stateMapper[errorStateKey] = errorState


# ==================================
# Construct MDP --> Transition file
# ==================================

# First, a function that computes the possible/likely
# transitions when going from a (x,y)-cell into some
# direction. It computes the image of the complete cell
# and then performs probability-weighting according to
# the areas of overlap
def computeSuccs(xpos,ypos,direction):

    # If direction is "4", this means no move
    if (direction==4):
        return [(xpos,ypos,1.0)]

    finalSuccs = []
    errorProb = 0.0
    if (direction==0):
        succs = [(xpos+1,ypos),(xpos+1,ypos+1)]
    elif (direction==1):
        succs = [(xpos,ypos+1),(xpos-1,ypos+1)]
    elif (direction==2):
        succs = [(xpos-1,ypos),(xpos-1,ypos-1)]
    elif (direction==3):
        succs = [(xpos,ypos-1),(xpos+1,ypos-1)]

    if succs[0][0]<0:
        errorProb += 1-positionUpdateNoise
    elif succs[0][0]>=xsize:
        errorProb += 1-positionUpdateNoise
    elif succs[0][1]<0:
        errorProb += 1-positionUpdateNoise
    elif succs[0][1]>=ysize:
        errorProb += 1-positionUpdateNoise
    else:
        finalSuccs.append((succs[0][0],succs[0][1],1-positionUpdateNoise))

    if succs[1][0]<0:
        errorProb += positionUpdateNoise
    elif succs[1][0]>=xsize:
        errorProb += positionUpdateNoise
    elif succs[1][1]<0:
        errorProb += positionUpdateNoise
    elif succs[1][1]>=ysize:
        errorProb += positionUpdateNoise
    else:
        finalSuccs.append((succs[1][0],succs[1][1],positionUpdateNoise))

    if errorProb>0.0:
        finalSuccs.append((-1,-1,errorProb))

    return finalSuccs


"""
Iterate over all cells and compute transition probabilities
Specifically, this transitionLines list is providing the transition function: [s_t, a_t, s_{t+1}, p(s_t+1|s_t,a_t)]
"""
transitionLines = []
overallNofTransitions = 0
for xA in range(0,xsize):
    for yA in range(0,ysize):
        for (csf,payoff) in [(x,0) for x in range(NUMBER_OF_COLORS)] + [(0,1)]:
            if (imageData[xA+yA*xsize]!=WALL):
                sourceState = stateMapper[(xA,yA,csf,payoff)]
                overallNofTransitions += 5
                for dirA in [0,1,2,3,4]: # Action 4 is standing still
                    errorProb = 0
                    succA = computeSuccs(xA,yA,dirA)
                    for (destXA,destYA,probA) in succA:
                        if destXA==-1:
                            errorProb += probA
                        elif (imageData[destXA+destYA*xsize]==WALL):
                            errorProb += probA
                        else:
                            """
                            notice that 0 is wall, 5 is normal field (other areas)
                            and 1-4 are the colors, they are in order: [2, 4, 3, 1] which means if
                            you have visited color 2, you need to visit color 4 next. Only when the agent has
                            visited all 4 colors, it can get the payoff (reward). 
                            Note this is confusing: csf is from 0 to 3, while colors are from 1 to 4.
                            """
                            if imageData[destXA+destYA*xsize]==colors[csf]:
                                csfPrime = csf + 1
                                payoffPrime = 1
                            else:
                                csfPrime = csf
                                payoffPrime = 0
                            if csfPrime==NUMBER_OF_COLORS:
                                csfPrime = 0
                            else:
                                payoffPrime = 0

                            # transitionLines.append([sourceState,dirA,stateMapper[(destXA,destYA,csfPrime,payoffPrime)],probA*0.99999])
                            transitionLines.append([sourceState,dirA,stateMapper[(destXA,destYA,csfPrime,payoffPrime)],probA])
                    # errorProb += 0.00001*(1-errorProb)
                    """
                    the following code indicates that when there is a probability of error, the agent will go to the 
                    error state, which is specified as the last state in the stateMapper.
                    """
                    if errorProb>0:
                        transitionLines.append([sourceState,dirA,errorState,errorProb])

# ==================================
# Prepare reverse state mapper and
# Searchable transition list
# ==================================
reverseStateMapper = {}
for (a,b) in stateMapper.items():
    reverseStateMapper[b] = a
transitionLists = {}
"""
The code below is to create a new dictionary (transitionLists) of transitionLines, 
where the key is the current state and chosen action, and the value is all possible next states and their probabilities.

You could notice some keys have one value, while some keys have multiple values. This is because in the code above,
if the probability of going to errorstate is 0, then this transition wouldn't be added to the transitionLines list.
"""
for (a,b,c,d) in transitionLines:
    if not (a,b) in transitionLists:
        transitionLists[(a,b)] = [(c,d)]
    else:
        transitionLists[(a,b)].append((c,d))

NUMBER_OF_BITS = int(math.ceil(math.log((len(reverseStateMapper) - 1) / (NUMBER_OF_COLORS + 1), 2)))
# print ("Number of bits for states:", NUMBER_OF_BITS)
danger_zone = [(7, 6), (7, 7), (7, 8), (8, 6), (8, 7), (8, 8), (9, 6), (9, 7), (9, 8)]
max_steps_in_zone = 3

num_steps_on_bomb = 3

# =========================================
# Initialize interactive display
# =========================================
pygame.init()
displayInfo = pygame.display.Info()
MAGNIFY = min(MAGNIFY,displayInfo.current_w*3/4/xsize)
MAGNIFY = min(MAGNIFY,displayInfo.current_h*3/4/ysize)


class Map(Environment):
    def __init__(self):
        self.reset()

    def reset(self):
        # print "reset called"
        self.state = 0
        self.penalty = 0

    def performAction(self, action):
        error = len(reverseStateMapper) - 1  # error state, 310
        # action = int(action[0])

        """
        two examples:
        (1)
        action = [ 4.  0.  3. -1. -1.], actions = [ 4.  0.  3.]
        actions after "list(map())" = [4, 0, 3]
        encoded_actions = [[1, 0, 0], [0, 0, 0], [0, 1, 1]] 
        
        (2)
        action = [ 3.  2.  3. -1. -1.], actions = [ 3.  2.  3.]
        actions after "list(map())" = [3, 2, 3]
        encoded_actions = [[0, 1, 1], [0, 1, 0], [0, 1, 1]] 
        The reason why the input is a list with size 5 is because there are intotal 5 possible actions, right, down, 
        left, up, and stay. However, we only care about three shielding-options, so the rest 2 actions are set to -1. 
        For example, in the above first example, the agent would firstly take action 4 with priority.
        """
        actions = action[action != -1]
        actions = list(map(int, actions))

        # print action
        # state_enc = map(int, list(bin(self.state / (NUMBER_OF_COLORS + 1))[2:].rjust(NUMBER_OF_BITS, '0')))

        """
        As shown in the examples from the comment above, we could see the encoded_actions is just the binary encoding
        for the three actions.
        """
        encoded_actions = []
        for a in actions:
            encoded_actions.append(list(map(int, list(bin(a)[2:].rjust(3, '0')))))

        (robotXA, robotYA, csf, payoff) = reverseStateMapper[self.state]

        """
        For state_enc:
        bits 0-3 show the agent's current position's right, down, left, up neighbors' validity;
        bit 4 shows whether agent is currently on a bomb;
        bit 5-13 show the three actions' binary encodings.
        """
        # simulate sensors
        state_enc = []
        for a in range(4):
            """
            The filter below is to remove the successors with probability 0.
            If succ[0] == -1, then the robot is out of the map which is not a valid successor.
            """
            succs = filter(lambda t: t[2] > 0, computeSuccs(robotXA, robotYA, a))
            valid = True
            for succ in succs:
                if succ[0] == -1 or not (succ[0], succ[1], 0, 0) in stateMapper:
                    valid = False
                    break
            state_enc.append(0 if valid else 1)


        # print state_enc
 # print "action" + str(encoded_actions[0])

        # if enemies_enabled:
        #     enemy_state = 0
        #     (robotXA,robotYA,csf,payoff) = reverseStateMapper[level.state]
        #     for enemy in enemy_handler.getEnemyPositions():
        #         x_diff = abs(enemy[0] - robotXA)
        #         y_diff = abs(enemy[1] - robotYA)
        #         if x_diff + y_diff <= 2:
        #             enemy_state = (enemy[0] - robotXA + 2) * 5 + (enemy[1] - robotYA + 3)
        #             break
        #     enemy_state_enc = list(map(int, list(bin(enemy_state)[2:].rjust(5, '0'))))
        #
        #     state_enc.extend(enemy_state_enc)

        # print "Colors seen so far:", csf
        if len(bombs) > 0:
            state_enc.append(1 if (robotXA + 1, robotYA + 1) in bombs else 0)
        for enc_action in encoded_actions:
            state_enc.extend(enc_action)

        # print state_enc
        corr_action = shield.tick(state_enc)

        """convert a binary number to decimal int"""
        corr_action = int("".join(list(map(str, corr_action[:len(corr_action) -1]))), 2)


        # if (actions[0] != corr_action) and huge_neg_reward:
        #     self.penalty += 1.

        # if (actions[0] != corr_action) and neg_reward and args.sarsa:
        #     self.penalty += 0.1
            # qvalue = self.module.getValue(self.laststate, action)
            # self.module.updateValue(self.laststate, action, qvalue + self.alpha * ((-1 if self.neg_reward else self.lastreward) - qvalue))
            #experiment.acc_reward -= .3
        #     print False
        used_actions = []
        for a in actions:
            if a == corr_action: break
            used_actions.append(a)
            # learner.explorer.n_values.params.reshape(learner.explorer.n_values.numRows,learner.explorer.n_values.numColumns)[self.state, a] += 1
        if huge_neg_reward:
            action = actions[0]
        else:
            """this has been executed"""
            action = corr_action

        used_actions.append(action)

        while len(used_actions) < 5:
            used_actions.append(-1)

        """
        In the code above, the used_actions is a list with size 5, with the tried actions and values -1.
        For example:
        (1)
        if the corr_action is 2 (choosing action 2, this is computed based on the actions binary encodings and 
        shielding), and the actions are [2, 1, 3], then the used_actions would be [2, -1, -1, -1, -1].
        (2)
        if the corr_action is 0 (choosing action 0, this is computed based on the actions binary encodings and 
        shielding), and the actions are [3, 1, 0], then the used_actions would be [3, 1, 0, -1, -1].
        """
        agent.lastaction = used_actions

        transitionList = transitionLists[(self.state, action)]

        dest = None
        randomNumber = random.random()
        for (a,b) in transitionList:
            if randomNumber<=b:
                dest = a
                randomNumber = 123.0
            else:
                randomNumber -= b  # in this example, b would always be 0, while randomNumber seems to be useless here.
        # Rounding error?
        if (dest==None):
            dest = transitionList[0][0]

        if dest == len(reverseStateMapper) - 1:
            experiment.acc_reward -= 1
            self.penalty += 1
            # self.reset()
            if shield_options > 0 and not args.huge_neg_reward:
                print ("Shields are not allowed to make errors!")
                exit()
            transitionList = transitionLists[(self.state, 4)]
            dest = None
            randomNumber = random.random()
            for (a,b) in transitionList:
                if randomNumber<=b:
                    dest = a
                    randomNumber = 123.0
                else:
                    randomNumber -= b
            # Rounding error?
            if (dest==None):
                dest = transitionList[0][0]

        # learner.explorer.n_values.params.reshape(learner.explorer.n_values.numRows,learner.explorer.n_values.numColumns)[self.state, action] += 1

        self.state = dest

    def getSensors(self):
        return [self.state]

class VisitAllColors(Task):
    def __init__(self, env):
        Task.__init__(self, env)
        self.last_reward = 0

    def getReward(self):
        # if (reverseStateMapper[self.env.state][3] != 0):
        # print "all colors visited"
        ret = self.last_reward
        self.last_reward = reverseStateMapper[self.env.state][3] - self.env.penalty
        self.env.penalty = 0
        return self.last_reward


class MyExperiment(Experiment):
    def __init__(self, task, agent):
        Experiment.__init__(self, task, agent)

        agent.learner.explorer.experiment = self
        # agent.learner.module.getValue()

        self.screen = pygame.display.set_mode(((xsize+2)*MAGNIFY,(ysize+2)*MAGNIFY))
        pygame.display.set_caption('Policy Visualizer')
        self.clock = pygame.time.Clock()

        self.screenBuffer = pygame.Surface(self.screen.get_size())
        self.screenBuffer = self.screenBuffer.convert()
        self.screenBuffer.fill((64, 64, 64)) # Dark Gray

        self.bombImage = pygame.image.load("bomb_image.png")
        self.bombImage = pygame.transform.scale(self.bombImage, (MAGNIFY - 2, MAGNIFY - 2))


        self.isPaused = False
        self.isCrashed = False
        self.speed = 10
        self.num = 0
        self.robotXA = -1
        self.robotYA = -1
        self.bomb_counter = 0

        self.count = 0
        self.acc_reward = 0
        self.collect_data = False
        if collect_data_file != None:
            self.collect_data = True
            self.collect_episode_data_file = open(collect_data_file + "_episodelen.data", "w")
            self.collect_reward_data_file = open(collect_data_file + "_avg_reward.data", "w")

    def _oneInteraction(self):
        global draw

        resetInThisRound = False

        # Process events
        for event in pygame.event.get():
            if event.type == pygame.locals.QUIT or (event.type == pygame.locals.KEYDOWN and event.key in [pygame.locals.K_ESCAPE,pygame.locals.K_q]):
                return
            if (event.type == pygame.locals.KEYDOWN and event.key == pygame.locals.K_SPACE):
                controller.params.reshape(controller.numRows, controller.numColumns).tofile("test.table")
                self.isPaused = not self.isPaused
            if (event.type == pygame.locals.KEYDOWN and event.key == pygame.locals.K_r):
                resetInThisRound = True
            if (event.type == pygame.locals.KEYDOWN and event.key == pygame.locals.K_PLUS):
                self.speed += 1
            if (event.type == pygame.locals.KEYDOWN and event.key == pygame.locals.K_MINUS):
                self.speed = max(self.speed-1,1)
            if (event.type == pygame.locals.KEYDOWN and event.key == pygame.locals.K_d):
                draw = not draw

        # if self.isCrashed:
  #           self.isCrashed = False
  #           # level.reset()
  #
        # Update
        if resetInThisRound:
            print ("reset")
            level.reset()


        old = (self.robotXA, self.robotYA)
        (self.robotXA,self.robotYA,csf,payoff) = reverseStateMapper[level.state]

        # if not self.isCrashed and enemies_enabled:
        #     enemy_handler.update(old)
        #     for e in enemy_handler.getEnemyPositions():
        #         if (self.robotXA, self.robotYA) == e:
        #             self.isCrashed = True
        #             level.penalty += 1
        #             self.acc_reward -= 1
        #             if shield_options > 0 and not args.huge_neg_reward:
        #                 print ("Shields are not allowed to make errors!")
        #                 exit()
        #             break

        if (self.robotXA + 1, self.robotYA + 1) in bombs:
            self.bomb_counter += 1
            if self.bomb_counter == 4:
                self.isCrashed = True
                level.penalty += 1
                self.acc_reward -= 1
                if shield_options > 0 and not args.huge_neg_reward:
                    print ("Shields are not allowed to make errors! Stay in the bomb for 4 steps")
                    exit()
        else:
            self.bomb_counter = 0

        if draw:
            q_max = 0
            for state in range(len(reverseStateMapper) - 1):
                q_max = max(q_max, max(controller.getActionValues(state)))

            # Draw Field
            for x in range(xsize):
                for y in range(ysize):
                    paletteColor = imageData[y*xsize+x]
                    color = palette[paletteColor*3:paletteColor*3+3]
                    pygame.draw.rect(self.screenBuffer,color,((x+1)*MAGNIFY,(y+1)*MAGNIFY,MAGNIFY,MAGNIFY),0)

            # Draw boundary
            if self.robotXA==-1 or self.isCrashed:
                boundaryColor = (255,0,0)
            else:
                boundaryColor = (64,64,64)
            pygame.draw.rect(self.screenBuffer,boundaryColor,(0,0,MAGNIFY*(xsize+2),MAGNIFY),0)
            pygame.draw.rect(self.screenBuffer,boundaryColor,(0,MAGNIFY,MAGNIFY,MAGNIFY*(ysize+1)),0)
            pygame.draw.rect(self.screenBuffer,boundaryColor,(MAGNIFY*(xsize+1),MAGNIFY,MAGNIFY,MAGNIFY*(ysize+1)),0)
            pygame.draw.rect(self.screenBuffer,boundaryColor,(MAGNIFY,MAGNIFY*(ysize+1),MAGNIFY*xsize,MAGNIFY),0)
            # pygame.draw.rect(screenBuffer,boundaryColor,(0,0,MAGNIFY*(xsize+2),MAGNIFY),0)

            # Draw cell frames
            for x in range(xsize):
                for y in range(ysize):
                    pygame.draw.rect(self.screenBuffer,(0,0,0),((x+1)*MAGNIFY,(y+1)*MAGNIFY,MAGNIFY,MAGNIFY),1)
                    if (x+1,y+1) in bombs:
                        self.screenBuffer.blit(self.bombImage, ((x+1)*MAGNIFY+1,(y+1)*MAGNIFY+1))
            pygame.draw.rect(self.screenBuffer,(0,0,0),(MAGNIFY-1,MAGNIFY-1,MAGNIFY*xsize+2,MAGNIFY*ysize+2),1)

            # Draw "Good" Robot
            if self.robotXA!=-1:
                pygame.draw.circle(self.screenBuffer, (192,32,32), ((self.robotXA+1)*MAGNIFY+MAGNIFY/2,(self.robotYA+1)*MAGNIFY+MAGNIFY/2) , MAGNIFY/3-2, 0)
                pygame.draw.circle(self.screenBuffer, (255,255,255), ((self.robotXA+1)*MAGNIFY+MAGNIFY/2,(self.robotYA+1)*MAGNIFY+MAGNIFY/2) , MAGNIFY/3-1, 1)
                pygame.draw.circle(self.screenBuffer, (0,0,0), ((self.robotXA+1)*MAGNIFY+MAGNIFY/2,(self.robotYA+1)*MAGNIFY+MAGNIFY/2) , MAGNIFY/3, 1)

            # Draw "Bad" Robots
            if enemies_enabled:
                for (e_x, e_y) in enemy_handler.getEnemyPositions():
                    pygame.draw.circle(self.screenBuffer, (32,32,192), ((e_x+1)*MAGNIFY+MAGNIFY/2,(e_y+1)*MAGNIFY+MAGNIFY/2) , MAGNIFY/3-2, 0)
                    pygame.draw.circle(self.screenBuffer, (255,255,255), ((e_x+1)*MAGNIFY+MAGNIFY/2,(e_y+1)*MAGNIFY+MAGNIFY/2) , MAGNIFY/3-1, 1)
                    pygame.draw.circle(self.screenBuffer, (0,0,0), ((e_x+1)*MAGNIFY+MAGNIFY/2,(e_y+1)*MAGNIFY+MAGNIFY/2) , MAGNIFY/3, 1)


            # zone_width = danger_zone[-1][0] - danger_zone[0][0] + 1
     #        zone_height = danger_zone[-1][1] - danger_zone[0][1] + 1
     # pygame.draw.rect(screenBuffer,(200,200,0),(MAGNIFY*(danger_zone[0][0]+1),MAGNIFY*(danger_zone[0][1]+1),MAGNIFY*zone_width,MAGNIFY*zone_height),5)


            # Flip!
            self.screen.blit(self.screenBuffer, (0, 0))
            pygame.display.flip()

            # Make the transition
            if not self.isPaused:
                # Done
                self.clock.tick(self.speed)
            else:
                self.clock.tick(3)

        self.acc_reward += payoff * 10
        if self.collect_data:
            self.count += 1
            if payoff > 0:
                self.collect_episode_data_file.write(str(self.count) + "\n")
                self.count = 0
            if self.stepid % 100 == 0:
                self.collect_reward_data_file.write(str(self.acc_reward / 100.) + "\n")
                self.acc_reward = 0
            if self.stepid % 100000 == 0:
                pass

        if self.stepid % 100 == 0:
            sys.stdout.write("\033[K")
            sys.stdout.write("[{2}{3}] ({0}/{1}) | alpha = {4} | epsilon = {5}\n".format(self.stepid, MAX_STEPS, '#'*int(math.floor(self.stepid/float(MAX_STEPS)*20)), ' '*int((20 - math.floor(self.stepid/float(MAX_STEPS)*20))), learner.alpha, learner.explorer.exploration))
            sys.stdout.write("\033[F")




        if self.stepid >= MAX_STEPS:
            print ("\nSimulation done!")

            sys.exit()

        if payoff > 0:
            # episode done
            if save_file != None:
                controller.params.reshape(controller.numRows, controller.numColumns).tofile(save_file)
            learner.alpha *= 1.#0.999
            learner.explorer.exploration *= 1.#0.999

        self.isCrashed = False
        if not self.isPaused:
            return Experiment._oneInteraction(self)
        else: return self.stepid



# ==================================
# Call main program
# ==================================



#
# def enemy_random(enemy, good):
#     possible_next_positions = set([])
#     for action in range(5):
#         next = computeSuccs(enemy[0], enemy[1], action)
#         for t in next:
#             if t[0] != -1 and t[2] > 0 and (t[0], t[1], 0, 0) in stateMapper:
#                 possible_next_positions.add((t[0], t[1]))
#
#     next_position_invalid = True
#     while next_position_invalid:
#         idx = random.randint(0, len(possible_next_positions) - 1)
#         enemy = list(possible_next_positions)[idx]
#         # we do not allow to drive at the old position of the good robot
#         next_position_invalid = enemy == good
#
#     return enemy
#
#
#
# enemies = []

shield = Shield()
level = Map()
task = VisitAllColors(level)
controller = MyActionValueTable(len(reverseStateMapper) - 1, 5)
if load_file != None:
    controller.initialize(np.fromfile(load_file))
else:
    controller.initialize(0.)  # this was executed by default.
alpha = .2
gamma = .95
if not args.sarsa:
    learner = MyQ(alpha, gamma, neg_reward)
    # learner.explorer = MyGreedyExplorer(shield_options, exploration)
# elif args.sarsa:
#     learner = SARSA(alpha, gamma)
learner.explorer = MyGreedyExplorer(shield_options, exploration)
learner.explorer._setModule(controller)
agent = LearningAgent(controller, learner)

draw = False
EXPLORATION_FACTOR = exploration

experiment = MyExperiment(task, agent)
while 1:
    experiment.doInteractions(100)

    agent.learn()
    agent.reset()
