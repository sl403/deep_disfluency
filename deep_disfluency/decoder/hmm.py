# Adapted from: Hidden Markov Models in Python
# Katrin Erk, March 2013
#
# This HMM addresses the problem of disfluency/end of utterance tagging.
# It estimates the probability of a tag sequence for a given word sequence as
# follows:
#
# Say words = w1....wN
# and tags = t1..tN
#
# then
# P(tags | words) is_proportional_to  product P(ti | t{i-1}) P(wi | ti)
#
# To find the best tag sequence for a given sequence of words,
# we want to find the tag sequence that has the maximum P(tags | words)
from __future__ import division
import os
import re
import math
from copy import deepcopy
import numpy as np
from collections import defaultdict
import cPickle as pickle
import nltk

from hmm_utils import convert_to_dot
from hmm_utils import load_data_from_corpus_file

# the weights for the source language model and the timing duration
# classifier
source_weight = 0.7
timing_weight = 1.0 # 10 gives 0.756, no great gains with higher weight
#  NB on 30.04 this is just a weight on the <t class as timer not working
# Given this can improve things from 0.70 -> 0.76 weighting worth looking at

def log(prob):
    if prob == 0.0:
        return - float("Inf")
    return math.log(prob, 2)


def tabulate_cfd(cfd, *args, **kwargs):
    """
    Tabulate the given samples from the conditional frequency distribution
    or conditional probability distribution.

    :param samples: The samples to plot
    :type samples: list
    :param conditions: The conditions to plot (default is all)
    :type conditions: list
    :param cumulative: A flag to specify whether the freqs are cumulative
    (default = False)
    :type title: bool
    """

    cumulative = False
    # conditions = sorted([c for c in cfd.conditions()
    #                     if "_t_" in c])   # only concerned with act-final
    conditions = sorted([c for c in cfd.conditions()])
    if type(cfd) == nltk.ConditionalProbDist:
        samples = sorted(set(v for c in conditions for v in cfd[c].samples()))
    else:
        samples = sorted(set(v for c in conditions for v in cfd[c]))
    if samples == []:
        print "No conditions for tabulate!"
        return None
    width = max(len("%s" % s) for s in samples)
    freqs = dict()
    for c in conditions:
        if cumulative:
            freqs[c] = list(cfd[c]._cumulative_frequencies(samples))
        else:
            if type(cfd) == nltk.ConditionalProbDist:
                freqs[c] = [cfd[c].prob(sample) for sample in samples]
            else:
                freqs[c] = [cfd[c][sample] for sample in samples]
        width = max(width, max(len("%d" % f) for f in freqs[c]))

    # condition_size = max(len("%s" % c) for c in conditions)
    final_string = ""
    # final_string += ' ' * condition_size
    if type(cfd) == nltk.ConditionalProbDist:
        width += 1
    for s in samples:
        # final_string += "%*s" % (width, s)
        final_string += "\t" + str(s)
    final_string = final_string + "\n"
    for c in conditions:
        # final_string += "%*s" % (condition_size, c)
        final_string += str(c) + "\t"
        for f in freqs[c]:

            if type(cfd) == nltk.ConditionalProbDist:
                # final_string += "%*.2f" % (width, f)
                if f == 0:
                    final_string += "\t"
                else:
                    final_string += "{:.3f}\t".format(f)
            else:
                # final_string += "%*d" % (width, f)
                if f == 0:
                    final_string += "\t"
                else:
                    final_string += "{}\t".format(f)
        final_string = final_string[:-1] + "\n"
    return final_string


def uttseg_pattern(tag):
    trp_tag = ""
    if "<c" in tag:  # i.e. a continuation of utterance from the previous word
        trp_tag += "c_{}"
    elif "<t" in tag:  # i.e. a first word of an utterance
        trp_tag += "t_{}"
    if "c/>" in tag:  # i.e. predicts a continuation next
        trp_tag += "_c"
    elif "t/>" in tag:  # i.e. predicts an end of utterance for this word
        trp_tag += "_t"
    assert trp_tag != "" and trp_tag[0] != "_" and trp_tag[-1] != "}",\
        "One or both TRP tags not given " + str(trp_tag) + " for:" + tag
    return trp_tag


def convert_to_disfluency_tag(previous, tag):
    """Returns (dis)fluency tag which is dealt with in a uniform way by
    the Markov model.
    """
    if not previous:
        previous = ""
    if "<f" in tag:
        if "rpSM" in previous or "rpM" in previous:
            # TODO change back for later ones without MID!
            return "rpM"  # can be mid repair
        return "f"
    elif "<e" in tag and "<i" not in tag:
        if "rpM" in previous or "rpSM" in previous or 'eR' in previous:
            #  Not to punish mid-repair
            # return "eR"  # edit term mid-repair phase, not interreg
            return "e"
        return "e"
    elif "<i" in tag:
        return "i"
    elif "<rm-" in tag:
        if "<rpEnd" in tag:
            return "rpSE"
        return "rpSM"
    elif "<rpMid" in tag:
        return "rpM"
    elif "<rpEnd" in tag:
        return "rpE"
    print "NO TAG for" + tag


def convert_to_disfluency_uttseg_tag(previous, tag):
    """Returns joint a list of (dis)fluency and trp tag which is dealt with
    in a uniform way by the Markov model.
    """
    if not previous:
        previous = ""
    trp_tag = uttseg_pattern(tag)
    if "<f" in tag:
        if "rpSM" in previous or "rpM" in previous:
            # TODO change back for later ones without MID!
            if "<t" not in tag and "t/>" not in tag:
                return trp_tag.format("rpM")  # can be mid repair
        return trp_tag.format("f")
    elif "<e" in tag and "<i" not in tag:
        if "rpM" in previous or "rpSM" in previous or 'eR' in previous:
            # Not to punish mid-repair
            if "t/>" not in tag:
                # edit term mid-repair phase, not interreg
                # return trp_tag.format("eR")
                return trp_tag.format("e")
        return trp_tag.format("e")
    elif "<i" in tag:
        return trp_tag.format("i")  # This should always be t_i_t
    elif "<rm-" in tag:
        if "<rpEnd" in tag:
            return trp_tag.format("rpSE")
        return trp_tag.format("rpSM")
    elif "<rpMid" in tag:
        return trp_tag.format("rpM")
    elif "<rpEnd" in tag:
        return trp_tag.format("rpE")
    print "NO TAG for" + tag


def convert_to_disfluency_tag_simple(previous, tag):
    if not previous:
        previous = ""
    if "<f" in tag:
        return "f"
    elif "<i" in tag:
        return "i"  # This should always be t_i_t
    elif "<e" in tag:
        return "e"
    elif "<rm-" in tag:
        return "rpSE"
    print "NO TAG for" + tag


def convert_to_disfluency_uttseg_tag_simple(previous, tag):
    """Returns joint a list of (dis)fluency and trp tag which is dealt with in
    a uniform way by the Markov model.
    Simpler version with fewer classes.
    """
    if not previous:
        previous = ""
    trp_tag = uttseg_pattern(tag)
    return trp_tag.format(convert_to_disfluency_tag_simple(previous, tag))


def convert_to_diact_tag(previous, tag):
    """Returns the dialogue act.
    """
    if not previous:
        previous = ""
    diact = ""
    m = re.search(r'<diact type="([^\s]*)"/>', tag)
    if m:
        diact = m.group(1)
        return diact
    print "NO TAG for" + tag


def convert_to_diact_uttseg_tag(previous, tag):
    """Returns joint a list of dialgoue and trp tag which is dealt with in
    a uniform way by the Markov model.
    """
    # print previous, tag
    if not previous:
        previous = ""
    trp_tag = uttseg_pattern(tag)
    return trp_tag.format(convert_to_diact_tag(previous, tag))


def convert_to_diact_interactive_tag(previous, tag):
    """Returns the dialogue act but with the fact it is keeping or
    taking the turn.
    """
    if not previous:
        previous = ""
    diact = convert_to_diact_tag(previous, tag)
    m = re.search(r'speaker floor="([^\s]*)"/>', tag)
    if m:
        return diact + "_" + m.group(1)
    print "NO TAG for" + tag


def convert_to_diact_uttseg_interactive_tag(previous, tag):
    """Returns the dialogue act but with the fact it is keeping or
    taking the turn.
    """
    if not previous:
        previous = ""
    trp_tag = uttseg_pattern(tag)
    return trp_tag.format(convert_to_diact_interactive_tag(previous, tag))


def convert_to_source_model_tags(seq, uttseg=True):
    """Convert seq of repair tags to the source model tags.
    Source model tags are:
    <s/> start fluent word (utteseg only)
    <f/> fluent mid utterance word
    <e/> edited word
    """
    source_tags = []
    # print seq
    for i, tag in enumerate(seq):
        if "<e" in tag or "<i" in tag:
            source_tags.append("<e/>")
        elif "<rm-" in tag:
            m = re.search("<rm-([0-9]+)\/>", tag)
            if m:
                back = min([int(m.group(1)), len(source_tags)])
                # print back, i, source_tags
                for b in range(i-back, i):
                    source_tags[b] = "<e/>"
                source_tags.append("<f/>")
            else:
                raise Exception('NO REPARANDUM DEPTH {}'.format(tag))
        else:
            if "<t" in tag:
                source_tags.append("<s/>")
                continue
            source_tags.append("<f/>")
    return source_tags


class FirstOrderHMM():
    """A standard hmm model which interfaces with any sequential channel model
    that outputs the softmax over all labels at each time step.
    A first order model where the internal state probabilities only depend
    on the previous state.
    """
    def __init__(self, disf_dict, markov_model_file=None,
                 timing_model=None, timing_model_scaler=None,
                 n_history=20, constraint_only=True, noisy_channel=None):

        self.tagToIndexDict = disf_dict  # dict maps from tags -> indices
        self.n_history = n_history  # how many steps back we should store
        self.observation_tags = set(self.tagToIndexDict.keys())
        self.observation_tags.add('s')  # all tag sets need a start tag
        self.cfd_tags = nltk.ConditionalFreqDist()
        self.cpd_tags = None
        self.tag_set = None
        self.timing_model = None
        self.timing_model_scaler = None
        self.constraint_only = constraint_only
        self.noisy_channel_source_model = noisy_channel

        if any(["<ct/>" in x for x in self.observation_tags]):
            # if a segmentation problem
            if any(["<rm-2" in x for x in self.observation_tags]):
                # full set
                self.convert_tag = convert_to_disfluency_uttseg_tag
            elif any(["<rm-" in x for x in self.observation_tags]):
                self.convert_tag = convert_to_disfluency_uttseg_tag_simple
            elif any(["<speaker" in x for x in self.observation_tags]):
                self.convert_tag = convert_to_diact_uttseg_interactive_tag
            else:
                # if only dialogue acts
                self.convert_tag = convert_to_diact_uttseg_tag
        else:
            # no segmentation in this task
            self.observation_tags.add('se')  # add end tag in pre-seg mode
            if any(["<rm-2" in x for x in self.observation_tags]):
                # full set
                self.convert_tag = convert_to_disfluency_tag
            elif any(["<rm-" in x for x in self.observation_tags]):
                self.convert_tag = convert_to_disfluency_tag_simple
            elif any(["<speaker" in x for x in self.observation_tags]):
                self.convert_tag = convert_to_diact_interactive_tag
            else:
                # if only dialogue acts
                self.convert_tag = convert_to_diact_tag

        if markov_model_file:
            print "loading", markov_model_file, "Markov model"

            # print "If we have just seen 'DET', \
            # the probability of 'N' is", cpd_tags["DET"].prob("N")
            # or load from file
            mm_path = os.path.dirname(os.path.realpath(__file__)) +\
                "/models/{}_tags.pkl".format(markov_model_file)
            # if load:
            self.cfd_tags = pickle.load(open(mm_path, "rb"))
            # else:
            #    # or create this from scratch
            #    graph = convert_to_dot("../decoder/models/{}.csv".format(
            #                                                markov_model_file))
            #    # loading MM from the graph/dot representation
            #    tags = []
            #    for line in graph.split("\n"):
            #        spl = line.replace(";", "").split()
            #        if not len(spl) == 3:
            #            continue
            #        assert spl[1] == "->"
            #        tags.append((spl[0], spl[2]))
            #    self.cfd_tags += nltk.ConditionalFreqDist(tags)
        else:
            print 'No Markov model file specified, empty CFD. Needs training.'
        # whatever happens turn this into a cond prob dist:
        self.cpd_tags = nltk.ConditionalProbDist(self.cfd_tags,
                                                 nltk.MLEProbDist)

        all_outcomes = [v.keys() for v in self.cfd_tags.values()]
        self.tag_set = set(self.cfd_tags.keys() +
                           [y for x in all_outcomes for y in x])
        self.viterbi_init()  # initialize viterbi
        # print "Test: If we have just seen 'rpSM',\
        # the probability of 'f' is", self.cpd_tags["c_rpSM_c"].prob("c_f_c")
        if timing_model:
            self.timing_model = timing_model
            self.timing_model_scaler = timing_model_scaler
            # self.simple_trp_idx2label = {0 : "<cc/>",
            #                        1 : "<ct/>",
            #                        2 : "<tc/>",
            #                       3 : "<tt/>"}
            # Only use the Inbetween and Start tags
            self.simple_trp_idx2label = {0: "<c", 1: "<t"}
        else:
            print "No timing model given"

    def train_markov_model_from_file(self, corpus_path, mm_path, update=False):
        """Adds to the self.cfd_tags conditional frequency distribution
        loaded, if there is one, else starts afresh.
        Recalculate the conditional prob distribution afresh.

        args:
        --filepath : filepath to newline separated file to learn sequence
        probabilities from.
        --mm_path : filepath to markov model distribution path to write to.
        --update : whether to update the current cfd, if not start anew.
        """
        tags = []
        # expects line separated sequences
        corpus_file = open(corpus_path)
        for line in corpus_file:
            if line.strip("\n") == "":
                continue
            labels_data = line.strip("\n").split(",")
            previous = "s"
            # print "length sequence", len(labels_data)
            for i in range(len(labels_data)):
                if labels_data[i] not in self.observation_tags:
                    print labels_data[i], "not in obs tags"
                    continue
                # print labels_data[i]
                # adjust interregna
                if any(["<i" in t for t in self.observation_tags]):
                    if "<rm-" in labels_data[i]:
                        b = len(tags)-1
                        while "e" in tags[b][1] and b > 0:
                            if "i" not in tags[b][1]:
                                new_1 = tags[b][1].replace('eR', 'i').\
                                    replace('e', 'i')
                                tags[b] = (tags[b][0], new_1)
                            if "e" in tags[b][0] and "i" not in tags[b][0]:
                                new_0 = tags[b][0].replace('eR', 'i').\
                                    replace('e', 'i')
                                tags[b] = (new_0, tags[b][1])
                            b -= 1
                        previous = tags[-1][1]
                tag = self.convert_tag(previous, labels_data[i])
                tags.append((previous, tag))
                previous = tag

            if "se" in self.observation_tags:
                # add end tag
                tags.append((previous, 'se'))
        # print "If we have just seen 'DET', \
        # the probability of 'N' is", cpd_tags["DET"].prob("N")
        # assumes these are added to exisiting one
        if update:
            self.cfd_tags += nltk.ConditionalFreqDist(tags)
        else:
            self.cfd_tags = nltk.ConditionalFreqDist(tags)
        print "cfd trained, counts:"
        self.cfd_tags.tabulate()
        print "test:"
        print tabulate_cfd(self.cfd_tags)
        # save this new cfd for later use
        pickle.dump(self.cfd_tags, open(mm_path, "wb"))
        # initialize the cpd
        self.cpd_tags = nltk.ConditionalProbDist(self.cfd_tags,
                                                 nltk.MLEProbDist)
        # print "cpd summary:"
        # print self.cpd_tags.viewitems()
        print tabulate_cfd(self.cpd_tags)
        all_outcomes = [v.keys() for v in self.cfd_tags.values()]
        self.tag_set = set(self.cfd_tags.keys() +
                           [y for x in all_outcomes for y in x])
        self.viterbi_init()  # initialize viterbi

    def viterbi_init(self):
        self.best_tagsequence = []  # presume this is for a new sequence
        self.viterbi = []
        self.backpointer = []
        self.converted = []
        self.history = []
        if self.noisy_channel_source_model:
            self.noisy_channel_source_model.reset()
            self.noisy_channel = [] # history

    def add_to_history(self, viterbi, backpointer, converted):
        """We store a history of n_history steps back in case we need to
        rollback.
        """
        if len(self.history) == self.n_history:
            self.history.pop(-1)
        self.history = [{"viterbi": deepcopy(viterbi),
                         "backpointer": deepcopy(backpointer),
                         "converted": deepcopy(converted)}] + self.history

    def rollback(self, n):
        """Rolling back to n back in the history."""
        # print "rollback",n
        # print len(self.history)
        self.history = self.history[n:]
        self.viterbi = self.viterbi[:len(self.viterbi)-n]
        self.backpointer = self.backpointer[:len(self.backpointer)-n]
        self.converted = self.converted[:len(self.converted)-n]
        self.best_tagsequence = self.best_tagsequence[
            :len(self.best_tagsequence)-n]
        if self.noisy_channel_source_model:
            self.noisy_channel = self.noisy_channel[
            :len(self.best_tagsequence)-n] # history

    def viterbi_step(self, softmax, word_index, sequence_initial=False,
                     timing_data=None):
        """The principal viterbi calculation for an extension to the
        input prefix, i.e. not reseting.
        """
        # source_weight = 13 # higher for WML
        if sequence_initial:
            # first time requires initialization with the start of sequence tag
            first_viterbi = {}
            first_backpointer = {}
            first_converted = {}
            if self.noisy_channel_source_model:
                first_noisy_channel = {}
            for tag in self.observation_tags:
                # don't record anything for the START tag
                # print tag
                if tag == "s" or tag == 'se':
                    continue
                # print word_index
                # print softmax.shape
                # print self.tagToIndexDict[tag]
                # print softmax[word_index][self.tagToIndexDict[tag]]
                tag_prob = self.cpd_tags["s"].prob(self.convert_tag("s", tag))
                if tag_prob >= 0.001:  #allowing for margin
                    if self.constraint_only:
                        # TODO for now treating this like a {0,1} constraint
                        tag_prob = 1.0
                else:
                    tag_prob = 0.0

                prob = log(tag_prob) + \
                    log(softmax[word_index][self.tagToIndexDict[tag]])
                # no timing bias to start
                if self.noisy_channel_source_model:
                    # noisy channel eliminate the missing tags
                    source_tags = convert_to_source_model_tags([tag],
                                                               uttseg=True)
                    source_prob, node = self.noisy_channel_source_model.\
                                        get_log_diff_of_tag_suffix(source_tags,
                                                                   n=1)
                    first_noisy_channel[tag] = node
                    #prob = (source_weight * source_prob) + \
                    #        ((1 - source_weight) * prob)
                    prob+=(source_weight * source_prob)
                first_viterbi[tag] = prob
                first_backpointer[tag] = "s"
                first_converted[tag] = self.convert_tag("s", tag)
                assert first_converted[tag] in self.tag_set,\
                    first_converted[tag]
            # store first_viterbi (the dictionary for the first word)
            # in the viterbi list, and record that the best previous tag
            # for any first tag is "s" (start of sequence tag)
            self.viterbi.append(first_viterbi)
            self.backpointer.append(first_backpointer)
            self.converted.append(first_converted)
            if self.noisy_channel_source_model:
                self.noisy_channel.append(first_noisy_channel)
            self.add_to_history(first_viterbi, first_backpointer,
                                first_converted)
            return
        # else we're beyond the first word
        # start a new dictionary where we can store, for each tag, the prob
        # of the best tag sequence ending in that tag
        # for the current word in the sentence
        this_viterbi = {}
        # we also store the best previous converted tag
        this_converted = {}  # added for the best converted tags
        # start a new dictionary we we can store, for each tag,
        # the best previous tag
        this_backpointer = {}
        # prev_viterbi is a dictionary that stores, for each tag, the prob
        # of the best tag sequence ending in that tag
        # for the previous word in the sentence.
        # So it stores, for each tag, the probability of a tag sequence
        # up to the previous word
        # ending in that tag.
        prev_viterbi = self.viterbi[-1]
        prev_converted = self.converted[-1]
        if self.noisy_channel_source_model:
            this_noisy_channel = {}
            prev_noisy_channel = self.noisy_channel[-1]
        # for each tag, determine what the best previous-tag is,
        # and what the probability is of the best tag sequence ending.
        # store this information in the dictionary this_viterbi
        if timing_data and self.timing_model:
            # X = self.timing_model_scaler.transform(np.asarray(
            # [timing_data[word_index-2:word_index+1]]))
            # TODO may already be an array
            # print "calculating timing"
            # print timing_data
            X = self.timing_model_scaler.transform(np.asarray([timing_data]))
            softmax_timing = self.timing_model.predict_proba(X)
            # print softmax_timing
            # raw_input()
        for tag in self.observation_tags:
            # don't record anything for the START/END tag
            if tag in ["s", "se"]:
                continue
            # joint probability calculation:
            # if this tag is X and the current word is w, then
            # find the previous tag Y such that
            # the best tag sequence that ends in X
            # actually ends in Y X
            # that is, the Y that maximizes
            # prev_viterbi[ Y ] * P(X | Y) * P( w | X)
            # The following command has the same notation
            # that you saw in the sorted() command.
            best_previous = None
            best_prob = log(0.0)  # has to be -inf for log numbers
            # the inner loop which makes this quadratic complexity
            # in the size of the tag set
            for prevtag in prev_viterbi.keys():
                # the best converted tag, needs to access the previous one
                prev_converted_tag = prev_converted[prevtag]
                # TODO there could be several conversions for this tag
                converted_tag = self.convert_tag(prev_converted_tag, tag)
                assert converted_tag in self.tag_set, tag + " " + \
                    converted_tag + " prev:" + str(prev_converted_tag)
                tag_prob = self.cpd_tags[prev_converted_tag].prob(
                    converted_tag)
                if tag_prob >= 0.001:  # allowing for margin of error
                    if self.constraint_only:
                        # TODO for now treating this like a {0,1} constraint
                        tag_prob = 1.0
                    test = converted_tag.lower()
                    if "rps" in test:  # boost for start tags
                        tag_prob = tag_prob * 2.0
                    elif "rpe" in test:
                        tag_prob = tag_prob  #  * 14  # boost for end tags
                    if timing_data and self.timing_model:
                        for k, v in self.simple_trp_idx2label.items():
                            if v in tag:
                                timing_tag = k
                                found = True
                                break
                        if not found:
                            raw_input("warning")
                        # using the prob from the timing classifier
                        # array over the different classes
                        timing_prob = softmax_timing[0][timing_tag]
                        if "<t" in tag:
                            sparse_weight = 7.0
                            # results on unweighted timing classifier:
                            # 1 0.757 2 0.770 3 0.778  4. 0.781 5.0.783
                            # 6. 0.785 7. 0.784
                            timing_prob = timing_prob * sparse_weight
                        if self.constraint_only:
                            # just adapt the prob of the timing tag
                            # tag_prob = timing_prob
                            # the higher the timing weight the more influence
                            # the timing classifier has
                            tag_prob = (timing_weight * timing_prob) # + tag_prob
                            # print tag, timing_tag, timing_prob
                        else:
                            
                            tag_prob = (timing_weight * timing_prob) + tag_prob
                else:
                    tag_prob = 0.0
                # the principal joint log prob
                prob = prev_viterbi[prevtag] + log(tag_prob) + \
                    log(softmax[word_index][self.tagToIndexDict[tag]])
                if self.noisy_channel_source_model:
                    prev_n_ch_node = prev_noisy_channel[prevtag]
                    # The noisy channel model adds the score
                    # if we assume this tag and the backpointed path
                    # from the prev tag
                    # Converting all to source tags first
                    # NB this is what is slowing things down
                    # Need to go from the known index
                    # in the nc model
                    full_backtrack_method = False
                    if full_backtrack_method:
                        inc_best_tag_sequence = [prevtag]
                        # invert the list of backpointers
                        inc_backpointer = deepcopy(self.backpointer)
                        inc_backpointer.reverse()
                        # go backwards through the list of backpointers
                        # (or in this case forward, we have inverted the
                        # backpointer list)
                        inc_current_best_tag = prevtag
                        for b_count, bp in enumerate(inc_backpointer):
                            inc_best_tag_sequence.append(bp[inc_current_best_tag])
                            inc_current_best_tag = bp[inc_current_best_tag]
                            if b_count > 9:
                                break
                        inc_best_tag_sequence.reverse()
                        inc_best_tag_sequence.append(tag)  # add tag
                        source_tags = convert_to_source_model_tags(
                                                inc_best_tag_sequence[1:],
                                                uttseg=True)
                        source_prob, nc_node = self.noisy_channel_source_model.\
                                          get_log_diff_of_tag_suffix(source_tags,
                                                                   n=1)
                    else:
                        # NB these only change if there is a backward
                        # looking tag
                        if "<rm-" in tag:
                            m = re.search("<rm-([0-9]+)\/>", tag)
                            if m:
                                back = min([int(m.group(1)), len(self.backpointer)])
                                suffix = ["<e/>"] * back + ["<f/>"]
                            # to get the change in probability due to this
                            # we need to backtrack further
                            n = len(suffix)
                        
                        else:
                            suffix = convert_to_source_model_tags([tag])
                            n = 1 # just monotonic extention
                            
                                # print back, i, source_tags
                        source_prob, nc_node = self.noisy_channel_source_model.\
                                          get_log_diff_of_tag_suffix(suffix,
                                                            start_node_ID=prev_n_ch_node,
                                                                   n=n)
                         
                    
                    
                    prob+=(source_weight * source_prob)
                
                if prob >= best_prob:
                    best_converted = converted_tag
                    best_previous = prevtag
                    best_prob = prob
                    if self.noisy_channel_source_model:
                        best_n_c_node = nc_node
            # if best result is 0 do not add, pruning, could set this higher
            if best_prob > log(0.0):
            # if True:  # TODO still a mystery
                this_converted[tag] = best_converted
                this_viterbi[tag] = best_prob
                # the most likely preceding tag for this current tag
                this_backpointer[tag] = best_previous
                if self.noisy_channel_source_model:
                    this_noisy_channel[tag] = best_n_c_node
        # done with all tags in this iteration
        # so store the current viterbi step
        self.viterbi.append(this_viterbi)
        self.backpointer.append(this_backpointer)
        self.converted.append(this_converted)
        if self.noisy_channel_source_model:
            self.noisy_channel.append(this_noisy_channel)
        self.add_to_history(this_viterbi, this_backpointer, this_converted)
        return

    def get_best_n_tag_sequences(self, n, noisy_channel_source_model=None):
        # Do a breadth-first search
        # try the best final tag and its backpointers, then the second
        # best final tag etc.
        # once all final tags are done and n > len(final tags)
        # move to the second best penult tags for each tag
        # from the best to worst, then the 3rd row
        # it terminates when n is reached
        # use the history self.history = [{"viterbi": deepcopy(viterbi),
        #                 "backpointer": deepcopy(backpointer),
        #                 "converted": deepcopy(converted)}] + self.history
        # num_seq = n if not noisy_channel_source_model else 1000
        num_seq = n
        best_n = []  # the tag sequences with their probability (tuple)
        # print "len viterbi", len(self.viterbi)
        # print "len backpoint", len(self.backpointer)
        for viterbi_depth in range(len(self.viterbi)-1, -1, -1):
            if len(best_n) == num_seq:
                break
            inc_prev_viterbi = deepcopy(self.viterbi[viterbi_depth])
            # inc_best_previous = max(inc_prev_viterbi.keys(),
            #                        key=lambda prevtag:
            # inc_prev_viterbi[prevtag])
            inc_previous = sorted(inc_prev_viterbi.items(),
                                  key=lambda x: x[1], reverse=True)
            for tag, prob in inc_previous:
                # print tag, prob
                # prob = inc_prev_viterbi[inc_best_previous]
                # assert(prob != log(0)), "highest likelihood is 0!"
                if prob == log(0):
                    continue
                inc_best_tag_sequence = [tag]
                # invert the list of backpointers
                inc_backpointer = deepcopy(self.backpointer)
                inc_backpointer.reverse()
                # go backwards through the list of backpointers
                # (or in this case forward, we have inverted the
                # backpointer list)
                inc_current_best_tag = tag
                # print "backpointer..."
                d = 0
                for bp in inc_backpointer:
                    d+=1
                    # print "depth", d, "find bp for", inc_current_best_tag
                    inc_best_tag_sequence.append(bp[inc_current_best_tag])
                    inc_current_best_tag = bp[inc_current_best_tag]
                # print "..."
                inc_best_tag_sequence.reverse()
                best_n.append((inc_best_tag_sequence, prob))
                if len(best_n) == num_seq:
                    break
        best_n = sorted(best_n, key=lambda x: x[1], reverse=True)
        debug = False
        if debug:
            print "getting best n"
            for s, p in best_n:
                print s[-1], p
            print "***"
        assert(best_n[0][1] > log(0.0)), "best prob 0!"

        if not noisy_channel_source_model:
            # return inc_best_tag_sequence
            return [x[0] for x in best_n]
        # if noisy channel do the interpolation
        # need to entertain the whole beam for the channel model and source
        # model
        # channel_beam = best_n  # the tag sequences with their probability
        # source_beam = noisy_channel.get_best_n_tag_sequences(1000)
        # self.interpolate_(channel_beam, source_beam)
        channel_beam = [lambda x: (x[0], convert_to_source_model_tags(x[0]),
                                   x[1]) for x in best_n]
        best_seqs = noisy_channel_source_model.\
                    interpolate_probs_with_n_best(channel_beam,
                                                  source_beam_width=1000,
                                                  output_beam_width=n)

#     def get_best_tag_sequence(self):
#         """Returns the best tag sequence from the input so far.
#         """
#         inc_prev_viterbi = deepcopy(self.viterbi[-1])
#         inc_best_previous = max(inc_prev_viterbi.keys(),
#                                 key=lambda prevtag: inc_prev_viterbi[prevtag])
#         assert(inc_prev_viterbi[inc_best_previous]) != log(0),\
#             "highest likelihood is 0!"
#         inc_best_tag_sequence = [inc_best_previous]
#         # invert the list of backpointers
#         inc_backpointer = deepcopy(self.backpointer)
#         inc_backpointer.reverse()
#         # go backwards through the list of backpointers
#         # (or in this case forward, we have inverted the backpointer list)
#         inc_current_best_tag = inc_best_previous
#         for bp in inc_backpointer:
#             inc_best_tag_sequence.append(bp[inc_current_best_tag])
#             inc_current_best_tag = bp[inc_current_best_tag]
# 
#         inc_best_tag_sequence.reverse()
#         return inc_best_tag_sequence


    def get_best_tag_sequence(self, noisy_channel_source_model=None):
        l = self.get_best_n_tag_sequences(1, noisy_channel_source_model)

        return l[0]


    def viterbi(self, softmax, incremental_best=False):
        """Standard non incremental (sequence-level) viterbi over softmax input

        Keyword arguments:
        softmax -- the emmision probabilities of each step in the sequence,
        array of width n_classes
        incremental_best -- whether the tag sequence prefix is stored for
        each step in the sequence (slightly 'hack-remental'
        """
        incrementalBest = []
        sentlen = len(softmax)
        self.viterbi_init()

        for word_index in range(0, sentlen):
            self.viterbi_step(softmax, word_index, word_index == 0)
            # INCREMENTAL RESULTS (hack-remental. doing it post-hoc)
            # the best result we have so far, not given the next one
            if incremental_best:
                inc_best_tag_sequence = self.get_best_tag_sequence()
                incrementalBest.append(deepcopy(inc_best_tag_sequence[1:]))
        # done with all words/input in the sentence/sentence
        # find the probability of each tag having "se" next (end of utterance)
        # and use that to find the overall best sequence
        prev_converted = self.converted[-1]
        prev_viterbi = self.viterbi[-1]
        best_previous = max(prev_viterbi.keys(),
                            key=lambda prevtag: prev_viterbi[prevtag] +
                            log(self.cpd_tags[prev_converted[prevtag]].
                            prob("se")))
        self.best_tagsequence = ["se", best_previous]
        # invert the list of backpointers
        self.backpointer.reverse()
        # go backwards through the list of backpointers
        # (or in this case forward, we've inverted the backpointer list)
        # in each case:
        # the following best tag is the one listed under
        # the backpointer for the current best tag
        current_best_tag = best_previous
        for bp in self.backpointer:
            self.best_tagsequence.append(bp[current_best_tag])
            current_best_tag = bp[current_best_tag]
        self.best_tagsequence.reverse()
        if incremental_best:
            # NB also consumes the end of utterance token! Last two the same
            incrementalBest.append(self.best_tagsequence[1:-1])
            return incrementalBest
        return self.best_tagsequence[1:-1]

    def viterbi_incremental(self, soft_max, a_range=None,
                            changed_suffix_only=False, timing_data=None,
                            words=None):
        """Given a new softmax input, output the latest labels.
        Effectively incrementing/editing self.best_tagsequence.

        Keyword arguments:
        changed_suffix_only -- boolean, output the changed suffix of
        the previous output sequence of labels.
            i.e. if before this function is called the sequence is
            [1:A, 2:B, 3:C]
            and after it is
            [1:A, 2:B, 3:E, 4:D]
            then output is:
            [3:E, 4:D]
            (TODO maintaining the index/time spans is important
            to acheive this, even if only externally)
        """
        previous_best = deepcopy(self.best_tagsequence)
        # print "previous best", previous_best
        if not a_range:
            # if not specified consume the whole soft_max input
            a_range = (0, len(soft_max))
        for i in xrange(a_range[0], a_range[1]):
            if self.noisy_channel_source_model:
                self.noisy_channel_source_model.consume_word(words.pop(0))
            self.viterbi_step(soft_max, i, sequence_initial=self.viterbi == [],
                              timing_data=timing_data)
            # slice the input if multiple steps
            # get the best tag sequence we have so far
        self.best_tagsequence = self.get_best_tag_sequence()
        # print "best_tag", self.best_tagsequence
        if changed_suffix_only:
            # print "current best", self.best_tagsequence
            # only output the suffix of predictions which has changed-
            # TODO needs IDs to work
            for r in range(1, len(self.best_tagsequence)):
                if r > len(previous_best)-1 or \
                        previous_best[r] != self.best_tagsequence[r]:
                    return self.best_tagsequence[r:]
        return self.best_tagsequence[1:]

    # def adjust_incremental_viterbi_with_source_channel(self, source_channel):
    #    """This reranks the current hypotheses with the noisy channel
    #    decode, adding a weighted log prob of the language model
    #    scores from the source model to the probs in viterbi.
    #    Note this should be done before the backpointer is computed
    #    for each new tag?
    #    """



if __name__ == '__main__':
    def load_tags(filepath):
        """Returns a tag dictionary from word to a n int indicating index
        by an integer.
        """
        tag_dictionary = defaultdict(int)
        f = open(filepath)
        for line in f:
            l = line.strip('\n').split(",")
            tag_dictionary[l[1]] = int(l[0])
        f.close()
        return tag_dictionary

    tags_name = "swbd_disf1_uttseg_034"
    tags = load_tags(
        "../data/tag_representations/{}_tags.csv".format(tags_name))
    if "disf" in tags_name:
        intereg_ind = len(tags.keys())
        interreg_tag = "<i/><cc/>" if "uttseg" in tags_name else "<i/>"
        tags[interreg_tag] = intereg_ind  # add the interregnum tag
    print tags

    h = FirstOrderHMM(tags, markov_model_file=None)
    corpus_path = "../data/tag_representations/{}_tag_corpus.csv".format(
        tags_name).replace("_034", "")
    mm_path = "models/{}_tags.pkl".format(tags_name)
    h.train_markov_model_from_file(corpus_path, mm_path)
    table = tabulate_cfd(h.cpd_tags)
    test_f = open("models/{}_tags_table.csv".format(tags_name), "w")
    test_f.write(table)
    test_f.close()
