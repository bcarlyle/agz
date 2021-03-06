import logging
import sys
import copy
import random
import time

import numpy as np

from six.moves import input

from gostate import GoState

from policyvalue import NaivePolicyValue
from policyvalue import SimpleCNN

# import tqdm


BOARD_SIZE = 5
C_PUCT = 1.0
N_SIMULATIONS = 160

"""
MCTS logic and go playing / visualisation.

TODO/fix:
- Decide on CLI arguments and use argparse

"""

# '-d level' argument for printing specific level:
if "-d" in sys.argv:
    level_idx = sys.argv.index("-d") + 1
    level = int(sys.argv[level_idx]) if level_idx < len(sys.argv) else 10
    logging.basicConfig(level=level)
else:
    logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)
np.set_printoptions(3)



def step(state, choice):
    """Functional stateless version of env.step() """
    t0 = time.time()
    new_state = copy.deepcopy(state)
    logger.log(6, "took {} to deepcopy \n{}".format(time.time()-t0, state) )
    new_state.step(choice)
    return new_state


class TreeStructure(object):
    def __init__(self, state, parent=None, choice_that_led_here=None):

        self.children = {}  # map from choice to node

        self.parent = parent
        self.state = state

        self.w = np.zeros(len(state.valid_actions))
        self.n = np.zeros(len(state.valid_actions))
        self.n += (1.0 + np.random.rand(len(self.n)))*1e-10
        self.prior_policy = 1.0

        self.sum_n = 1
        self.choice_that_led_here = choice_that_led_here

        self.move_number = 0

        if parent:
            self.move_number = parent.move_number + 1


    def history_sample(self):
        pi = np.zeros(self.state.action_space)
        pi[self.state.valid_actions] = self.n/self.n.sum()
        return [self.state, self.state.observed_state(), pi]

def sample(probs):
    """Sample from unnormalized probabilities"""

    probs = probs / probs.sum()
    return np.random.choice(np.arange(len(probs)), p=probs.flatten())

def puct_distribution(node):
    """Puct equation"""
    # this should never be a distribution but always maximised over?
    logger.debug("Selecting node at move {}".format(node.move_number))
    logger.debug(node.w)
    logger.debug(node.n)
    logger.debug(node.prior_policy)

    return node.w/node.n + C_PUCT*node.prior_policy*np.sqrt(node.sum_n)/(1 + node.n)

def puct_choice(node):
    """Selects the next move."""
    return np.argmax(puct_distribution(node))


def choice_to_play(node, opponent=None):
    """Samples a move if beginning of self play game."""
    logger.debug("Selecting move # {}".format(node.move_number))
    logger.debug(node.w)
    logger.debug(node.n)
    logger.debug(node.prior_policy)

    if node.move_number < 30 and opponent is None:
        return sample(node.n)
    else:
        return np.argmax(node.n)

def backpropagate(node, value):
    """MCTS backpropagation"""

    def _increment(node, choice, value):
        # Mirror value for odd states:
        value *= 1 - 2*(node.move_number % 2)  # TODO: use node.state.current_player after changing it to (+1, -1)
        node.w[choice] += value
        node.n[choice] += 1
        node.sum_n += 1

    while node.parent:
        _increment(node.parent, node.choice_that_led_here, value)
        node = node.parent


def mcts(tree_root, policy_value, n_simulations):
    # for i in tqdm.tqdm(range(n_simulations)):
    for i in range(n_simulations):
        node = tree_root
        # Select from "PUCT/UCB1 equation" in paper.
        choice = puct_choice(node)
        while choice in node.children.keys():
            node = node.children[choice]
            choice = puct_choice(node)

        if node.state.game_over:
            # This only happens the second time we go to a winning state.
            # Logic for visiting "winning nodes" multiple times is probably correct?
            value = node.state.winner
            backpropagate(node, value)
            continue

        # Expand tree:
        new_state = step(node.state, choice)
        node.children[choice] = TreeStructure(new_state, node, choice)
        node = node.children[choice]

        if new_state.game_over:
            value = new_state.winner  # Probably look at the depth to see who won here?
        else:
            policy, value = policy_value.predict(node.state)
            node.prior_policy = policy[node.state.valid_actions]

        backpropagate(node, value)


def print_tree(tree_root, level):
    if logger.level > 2:
        print(" "*level, tree_root.choice_that_led_here, tree_root.state.state, tree_root.n, tree_root.w)
        [print_tree(tree_root.children[i], level + 1) for i in tree_root.children]


# TODO: Create agent class from this that can be queried
def play_game(start_state=GoState(),
              policy_value=NaivePolicyValue(),
              opponent=None,
              n_simulations=N_SIMULATIONS):
    """
    Plays a game against itself or specified opponent.

    The state should be prepared so that it is the agents turn, 
    and so that `self.winner == 1` when the agent won.
    """

    # TODO: This will set .move_number = 0, should maybe track whose turn it is instead:
    tree_root = TreeStructure(start_state)
    policy, value = policy_value.predict(tree_root.state)
    tree_root.prior_policy = policy[tree_root.state.valid_actions]
    game_history = []

    while not tree_root.state.game_over:

        mcts(tree_root, policy_value, n_simulations)

        print_tree(tree_root,0)
        # Store the state and distribution before we prune the tree:
        # TODO: Refactor this

        game_history.append(tree_root.history_sample())

        choice = choice_to_play(tree_root, bool(opponent))
        tree_root = tree_root.children[choice]
        tree_root.parent = None

        if opponent:
            game_history.append(tree_root.history_sample())
            choice = opponent(tree_root.state)
            if choice in tree_root.children:
                tree_root = tree_root.children[choice]
            else:
                new_state = step(tree_root.state, choice)
                tree_root = TreeStructure(new_state, tree_root)
                #FIXME: Should set policy here
            tree_root.parent = None



    return game_history, tree_root.state.winner


# UI code below:
def human_opponent(state):
    """Queries human for move when called."""
    print(state)
    while True:
        inp = input("What is your move? \n")
        if inp == 'pass':
            return len(state.valid_actions) - 1
        if inp == 'random':
            return random.randint(0, len(state.valid_actions) - 1)

        try:
            pos = [int(x) for x in inp.split()]
            action = pos[0]*state.board_size + pos[1]
            choice = state.valid_actions.index(action)
            return choice
        except:
            print("Invalid move {} try again.".format(inp))


def self_play_visualisation(board_size=BOARD_SIZE):
    """Visualises one game of self_play"""
    policy_value = SimpleCNN([board_size, board_size, 2])
    history, winner = play_game(policy_value=policy_value)
    print("Watching game replay\nPress Return to advance board")
    for state, board, hoice in history:
        print(state)
        input("")

    if winner == 1:
        print("Black won")
    else:
        print("White won")



def main(policy_value=NaivePolicyValue(), board_size=BOARD_SIZE, n_simulations=N_SIMULATIONS):

    if "-selfplay" in sys.argv:
        self_play_visualisation()
        return

    if "-40" in sys.argv:
        n_simulations = 40
        print("Letting MCTS search for {} moves!".format(n_simulations))

    # Loads weights that trained for 60 iterations
    policy_value = SimpleCNN([board_size, board_size, 2])
    policy_value.load(6)

    print("")
    print("Welcome!")
    print("Format moves like: y x")
    print("(or pass/random)")
    print("")
    try:
        history, winner = play_game(start_state=GoState(board_size),
                                    policy_value=policy_value,
                                    opponent=human_opponent,
                                    n_simulations=n_simulations)
    except KeyboardInterrupt:
        print("Game aborted.")
        return

    if winner == 1:
        print("AI won")
    else:
        print("Human won")

if __name__ == "__main__":
    main()
