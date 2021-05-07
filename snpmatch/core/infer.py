import numpy as np
import scipy as sp
import pandas as pd
import logging
from glob import glob
import itertools
import os.path
from . import parsers
from hmmlearn import hmm



log = logging.getLogger(__name__)

class IdentifyStrechesofHeterozygosity(object):

    def __init__(self,
        sample_gt, 
        chromosome_size, 
        avg_depth = 1.5, 
        delta_het_parents = 0.99,
        avg_sites_segregating = 0.01, 
        base_error = 0.0001, 
        recomb_rate = 3.3, 
        n_iter = 1000
    ):
        #  
        self.sample_gt = parsers.parseGT( sample_gt )
        self.params = {}
        self.params['num_markers'] = sample_gt.shape[0]
        self.params['avg_depth'] = avg_depth
        ### fraction of sites parental genomes are heterozygous
        self.params['delta_het_parents'] = delta_het_parents 
        ### fraction of sites which are segregating between parents
        self.params['avg_sites_segregating'] = avg_sites_segregating 
        ### sequencing error rate
        self.params['base_error'] = base_error 
        ## genome size 
        self.params['chromosome_size'] = chromosome_size
        ## recombination rate
        self.params['recomb_rate'] = recomb_rate
        ## number of iterations
        self.params['n_iter'] = n_iter

        self.model = self._init_hmm_streches_of_het()


    def _init_hmm_streches_of_het(self):
        """
        HMM to identify streches of homozygosity in an intercross
        ## currently: observations should only be in segregating SNPs
        Input: 
            number of markers
            avg_sites_segregating:  Average number of given sites that would be segregating between parents
            recomb_rate: assumed rate of recombination (cM per Mb)
            genome_size: size of chromosome in Mb
        """
        ### There are two hidden states
        log.info("Initialising HMM")
        states = ('aa', 'ab')
        ## you observe 
        observations = ('homo', 'het', 'NA')
        ## if a site is called Homo -- all the reads mapped to it are the same
        ## het if atleast one read is different (default GATK/bcftools callers does that)
        ##_____
        ## What is the transition probabilities? 
        ## First, I am doing it for F2 cross
        init_prob = [0.5, 0.5]
        ri = (float(self.params['chromosome_size']) / self.params['num_markers']) * self.params['recomb_rate'] / 100
        ## assuming a diploid
        transition_prob = [
                [(1 - ri)**2 + ri**2,   2 * ri * (1 - ri)   ],
                [2 * ri * (1 - ri),     (1 - ri)**2 + ri**2 ]
        ]
        ## Emission probability
        # average_depth
        # base_error 
        # avg_sites_segregating 
        ## Z -- underlying ancestry -- either AA or AB
        ## G = genotype at a given locus -- aa or ab -- depends on whether parents are segregating
        ## X = observed states (00, 01)
        p_homo_given_gaa = ((1 - self.params['base_error']) ** self.params['avg_depth']) + self.params['base_error']**self.params['avg_depth']
        p_homo_given_gab = 2 * (0.5 ** self.params['avg_depth'])
        ## What fraction of sites are heterozygoues in parental genomes. 
        ## delta_het_parents
        p_homo_given_ZAA = (self.params['delta_het_parents'] * p_homo_given_gaa ) + ((1 - self.params['delta_het_parents']) * p_homo_given_gab )
        p_homo_given_ZAB = ((1 - self.params['avg_sites_segregating']) * p_homo_given_gaa ) + ( self.params['avg_sites_segregating'] * p_homo_given_gab )

        emission_prob = [ 
            [p_homo_given_ZAA,  1 - p_homo_given_ZAA, 0.5],
            [p_homo_given_ZAB, 1 - p_homo_given_ZAB, 0.5]
        ]
        print("transition probability:\n %s" % pd.DataFrame(transition_prob) )
        print("emission probability:\n %s" % pd.DataFrame(emission_prob) )
        model = hmm.MultinomialHMM(n_components=2, n_iter = self.params['n_iter'], algorithm = "viterbi", init_params='st') 
        model.startprob_ = np.array(init_prob)
        model.transmat_ = pd.DataFrame(transition_prob)
        model.emissionprob_ = pd.DataFrame(emission_prob)
        return(model)

    def _input_obs( self, sample_gt ):
        t_obs = np.array(sample_gt)
        t_obs[t_obs == 1] = 0
        t_obs[t_obs == 2] = 1
        t_obs[t_obs == -1] = 2
        return(t_obs)

    def hmm_decode( self ):
        self._obs = self._input_obs( self.sample_gt )
        t_genotypes = self.model.decode( self._obs.reshape((-1, 1)) )
        return(t_genotypes)



class IdentifyAncestryF2individual(object):

    def __init__(self, 
        chromosome_size,
        num_markers,
        recomb_rate = 3.5,  ### in cM/Mb
        error_p1 = 0.00001,
        error_p2 = 0.00001, 
        base_error = 0.01, 
        avg_depth = 1.5,
        n_iter = 1000
    ):
        self.ancestry = ['AA', 'AB', 'BB']
        self.geno_parents = ['00', '01', '11']
        self.observed_states = ['00', '01', '11', 'NA']
        self.params = {}
        self.params['num_markers'] = num_markers
        self.params['chromosome_size'] = chromosome_size
        self.params['recomb_rate'] = recomb_rate
        self.params['error_p1'] = error_p1
        self.params['error_p2'] = error_p2
        self.params['base_error'] = base_error
        self.params['avg_depth'] = avg_depth
        self.params['n_iter'] = n_iter
        self.prob_g_given_z = self._prob_g_given_Z(self.params['error_p1'], self.params['error_p2'] )
        self.prob_x_given_g = self._prob_x_given_G(self.params['base_error'], self.params['avg_depth'] )
        self.init_prob = [0.25, 0.5, 0.25] ### F2 individual with -- Mendelian segregation
        print("init probabilites:\n %s" % pd.Series(self.init_prob, index = self.ancestry) )
        self.transition_prob = self._transition_prob(self.params['chromosome_size'], self.params['num_markers'], self.params['recomb_rate'])
        print("transition probability:\n %s" % self.transition_prob )
        self.emission_prob =  pd.DataFrame(np.dot(self.prob_g_given_z.values, self.prob_x_given_g.values), index = self.ancestry, columns = self.observed_states)
        print("emission probability:\n %s" % self.emission_prob )
        self.model = self._model( n_iter = self.params['n_iter'] )

    def _prob_g_given_Z(self, error_p1, error_p2):
        ## Adapted from Andolfatto et al.
        conf_p1 = 1 - error_p1
        conf_p2 = 1 - error_p2
        req_prob = [
            [conf_p1**2, 2 * conf_p1 * error_p1, error_p1**2],
            [conf_p1 * error_p2, (conf_p1 * conf_p2)  + (error_p1 * error_p2), conf_p2 * error_p1],
            [error_p2**2, 2 * conf_p2 * error_p2, conf_p2**2]
        ]
        return( pd.DataFrame( req_prob, index = self.ancestry, columns = self.geno_parents ) )
    
    def _prob_x_given_G(self, base_error, avg_depth):
        req_prob = [
            [(1 - base_error)**avg_depth, 1 - base_error**avg_depth - (1 - base_error)**avg_depth, base_error**avg_depth, 0.5],
            [(0.5 * (1 - base_error))**avg_depth, 1 - 2 * ((0.5 * (1 - base_error))**avg_depth),(0.5 * (1 - base_error))**avg_depth,0.5],
            [base_error**avg_depth, 1 - base_error**avg_depth - (1 - base_error)**avg_depth, (1-base_error)**avg_depth, 0.5]
        ]
        return( pd.DataFrame( req_prob, index = self.geno_parents, columns = self.observed_states )  )

    def _transition_prob(self, chromosome_size, num_markers, recomb_rate):
        ri = (float(chromosome_size) / num_markers) * recomb_rate / 100
        ## assuming a diploid
        transition_prob = [
                [(1 - ri)**2,   2 * ri * (1 - ri), ri**2],
                [ri * (1 - ri),     (1 - ri)**2 + ri**2, ri * (1 - ri)],
                [ri**2,   2 * ri * (1 - ri), (1 - ri)**2]
        ]
        return( pd.DataFrame( transition_prob, index = self.ancestry, columns = self.ancestry ) )
    
    def _model(self, n_iter = 1000):
        log.info("initialising HMM")
        model = hmm.MultinomialHMM(n_components=3, n_iter = n_iter, algorithm = "viterbi", init_params='ste') 
        model.startprob_ = self.init_prob
        model.transmat_ = self.transition_prob
        model.emissionprob_ = self.emission_prob
        return(model)
