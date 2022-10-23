# Author: Chandler Squires
"""
Base class for DAGs representing Gaussian distributions (i.e. linear SEMs with Gaussian noise).
"""
# === IMPORTS: BUILT-IN ===
import operator as op
import itertools as itr
from typing import Any, Dict, Union, Set, Tuple, List

# === IMPORTS: THIRD-PARTY ===
import numpy as np
from numpy import sqrt, diag
from numpy.linalg import inv, lstsq
from scipy.linalg import cholesky
from scipy.stats import norm

# === IMPORTS: LOCAL ===
from .dag import DAG
from ..interventions import Intervention, SoftInterventionalDistribution, \
    PerfectInterventionalDistribution, PerfectIntervention, SoftIntervention, GaussIntervention, BinaryIntervention, \
    MultinomialIntervention, ConstantIntervention
from graphical_models.utils import core_utils


def cholesky_no_permutation(mat):
    u_chol = cholesky(mat, lower=True)  # compute L s.t. precision = L @ L.T
    u_chol[np.isclose(u_chol, 0)] = 0
    u_diag = np.diag(u_chol)
    d = u_diag**2
    u = u_chol / u_diag
    amat = np.eye(mat.shape[0]) - u
    return amat, d


class GaussDAG(DAG):
    def __init__(
            self,
            nodes: List,
            arcs: Union[Set[Tuple[Any, Any]], Dict[Tuple[Any, Any], float]],
            biases=None,
            variances=None
    ):
        self._weight_mat = np.zeros((len(nodes), len(nodes)))
        self._node_list = nodes
        self._node2ix = core_utils.ix_map_from_list(self._node_list)

        self._variances = np.ones(len(nodes)) if variances is None else np.array(variances, dtype=float)
        self._biases = np.zeros((len(nodes))) if biases is None else np.array(biases)

        self._precision = None
        self._covariance = None
        self._correlation = None

        super(GaussDAG, self).__init__(set(nodes), arcs)

        for node1, node2 in arcs:
            w = arcs[(node1, node2)] if isinstance(arcs, dict) else 1
            self._weight_mat[self._node2ix[node1], self._node2ix[node2]] = w

    def to_dag(self):
        """
        TODO

        Examples
        --------
        TODO
        """
        return DAG(nodes=set(self._node_list), arcs=self.arcs)

    def copy(self):
        return GaussDAG(nodes=self._nodes, arcs=self.arc_weights, biases=self._biases, variances=self._variances)

    @classmethod
    def from_amat(cls, weight_mat, nodes=None, arcs=None, biases=None, variances=None):
        """
        Return a GaussDAG with arc weights specified by weight mat.

        Parameters
        ----------
        weight_mat:
            Matrix of edge weights, with weight[i, j] being the weight on the arc i->j.
        nodes
        biases:
            Node biases.
        variances:
            Node residual variances.

        Examples
        --------
        TODO
        """
        nodes = nodes if nodes is not None else list(range(weight_mat.shape[0]))
        arcs = arcs if arcs is not None else {(i, j): w for (i, j), w in np.ndenumerate(weight_mat) if w != 0}
        return cls(nodes=nodes, arcs=arcs, biases=biases, variances=variances)

    @classmethod
    def from_covariance(cls, covariance_matrix: np.ndarray, node_order=None, check=False):
        """
        Return a GaussDAG with the specified covariance matrix and topological ordering of nodes.

        Parameters
        ----------
        covariance_matrix:
            The desired covariance matrix for the graph.
        node_order:
            The desired ordering of nodes.

        Examples
        --------
        TODO
        """
        if not core_utils.is_symmetric(covariance_matrix):
            raise ValueError('Covariance matrix is not symmetric')
        precision = inv(covariance_matrix)
        return GaussDAG.from_precision(precision, node_order, check=check)

    @classmethod
    def fit_mle(cls, dag, samples):
        n, p = samples.shape
        extended_samples = np.hstack((samples, np.ones((n, 1))))
        cov = np.cov(extended_samples, rowvar=False)
        amat = np.zeros((p, p))
        biases = np.zeros(p)
        variances = np.zeros(p)
        for node in dag.nodes:
            parents = list(dag.parents_of(node)) + [p]
            node = dag._node2ix[node]
            parents = [pa if type(pa)==int else dag._node2ix[pa] for pa in parents]
            coefs, _, _, _ = lstsq(cov[np.ix_(parents, parents)], cov[parents, node])
            bias = coefs[-1]
            variance = cov[node, node] - coefs.T @ cov[np.ix_(parents, parents)] @ coefs
            amat[parents[:-1], node] = coefs[:-1]
            biases[node] = bias
            variances[node] = variance
        return GaussDAG.from_amat(
            amat,
            nodes=dag._node_list,
            arcs=dag.arcs,
            biases=biases,
            variances=variances
        )

    def direct_effects(self):
        p = self.nnodes
        effects = np.zeros((p, p))
        A = self.weight_mat
        for k in range(p):
            effects += A
            A = A @ self.weight_mat
        return effects

    @classmethod
    def from_precision(cls, precision_matrix, node_order=None, check=False):
        """
        Return a GaussDAG with the specified precision matrix and topological ordering of nodes. Note that the precision
        matrix \Theta has the formula \Theta = (I - B)^T @ \Sigma^-1 @ (I - B), where B is the adjacency matrix s.t.
        B[i,j] \neq 0 if i->j, and \Sigma is the diagonal matrix of node variances.

        Parameters
        ----------
        precision_matrix:
            The desired precision matrix for the graph.
        node_order:
            The desired ordering of nodes.

        Examples
        --------
        TODO
        """
        if not core_utils.is_symmetric(precision_matrix):
            raise ValueError('Covariance matrix is not symmetric')

        p = precision_matrix.shape[0]
        if node_order is None:
            node_order = list(range(p))

        # === permute precision matrix into correct order for LDL
        precision_matrix_perm = precision_matrix.copy()
        rev_node_order = list(reversed(node_order))
        precision_matrix_perm = precision_matrix_perm[rev_node_order]
        precision_matrix_perm = precision_matrix_perm[:, rev_node_order]

        # === perform ldl decomposition and correct for floating point errors
        u_chol = cholesky(precision_matrix_perm, lower=True)  # compute L s.t. precision = L @ L.T
        u_chol[np.isclose(u_chol, 0)] = 0
        u_diag = np.diag(u_chol)
        d = u_diag**2
        u = u_chol / u_diag

        # === permute back
        inv_node_order = [i for i, j in sorted(enumerate(rev_node_order), key=op.itemgetter(1))]
        u_perm = u.copy()
        u_perm = u_perm[inv_node_order]
        u_perm = u_perm[:, inv_node_order]
        d_perm = d[inv_node_order]

        amat = np.eye(p) - u_perm
        variances = d_perm ** -1
        if check:
            assert (np.diag(amat) == 0).all()
            assert np.isclose((u_perm @ np.diag(d_perm) @ u_perm.T), precision_matrix).all()

        # adj_mat[np.isclose(adj_mat, 0)] = 0
        return GaussDAG.from_amat(amat, variances=variances)

    def normalize_weights(self):
        """
        Return a GaussDAG with the same skeleton and correlation matrix, normalized so that each variable has unit
        variance.

        Examples
        --------
        TODO
        """
        normalized_weight_mat = np.zeros(self.weight_mat.shape)
        node_sds = sqrt(diag(self.covariance))
        num_nodes = self.nnodes
        for i, j in itr.combinations(range(num_nodes), 2):
            if self.weight_mat[i, j] != 0:
                normalized_weight_mat[i, j] = B[i, j] * node_sds[i] / node_sds[j]
        # TODO: variance
        return GaussDAG.from_amat(normalized_weight_mat)

    def round_weights(self, decimals=2):
        rounded_weight_mat = np.around(self._weight_mat, decimals=decimals)
        return GaussDAG.from_amat(rounded_weight_mat)

    def set_arc_weight(self, i, j, val):
        """
        Change the weight of the arc i->j to val

        Parameters
        ----------
        i:
            source node of arc.
        j:
            target node of arc.
        val:
            weight of the arc `i->j`.

        Examples
        --------
        TODO
        """
        self._weight_mat[self._node2ix[i], self._node2ix[j]] = val
        if val == 0 and (i, j) in self._arcs:
            super().remove_arc(i, j)
        if val != 0 and (i, j) not in self._arcs:
            super().add_arc(i, j)

    def set_arc_weights(self, arcs):
        if len(arcs) == 0:
            return
        ixs, ws = zip(*arcs.items())
        ixs = np.array(ixs)
        ws = np.array(ws)
        amat_ixs = [(self._node2ix[i], self._node2ix[j]) for i, j in ixs]
        rows, cols = zip(*amat_ixs)
        self._weight_mat[rows, cols] = ws
        zeros = np.where(ws == 0)[0]
        nonzeros = np.where(ws != 0)[0]
        super().remove_arcs_from(ixs[zeros], ignore_error=True)
        super().add_arcs_from(ixs[nonzeros])

    def set_node_bias(self, i, bias):
        self._biases[i] = bias

    def set_node_mean(self, i, mean):
        self.set_node_bias(i, mean)
        raise DeprecationWarning("GaussDAG.set_node_mean has been changed to GaussDAG.set_node_bias")

    def set_node_variance(self, i, var):
        """
        Change the variance of node i to var

        Parameters
        ----------
        i:
            node whose variance to change.
        var:
            new variance.

        Examples
        --------
        TODO
        """
        self._variances[i] = var

    def means(self):
        """
        Return the marginal mean of each node
        """
        means = np.zeros(len(self._node_list))
        for i in range(self.nnodes):
            parent_ixs = [self._node2ix[p] for p in self._parents[i]]
            means[i] = self.weight_mat[parent_ixs, i] @ means[parent_ixs] + self._biases[i]
        return means

    def deterministic_intervention_means(self, intervention: Dict[Any, float]):
        """
        Return the marginal mean of each node after a deterministic intervention is performed
        """
        means = np.zeros(len(self._node_list))
        for i in range(self.nnodes):
            iv_value = intervention.get(i)
            if iv_value is None:
                parent_ixs = [self._node2ix[p] for p in self._parents[i]]
                means[i] = self.weight_mat[parent_ixs, i] @ means[parent_ixs] + self._biases[i]
            else:
                means[i] = iv_value
        return means

    def deterministic_intervention_counterfactuals(self, samples, intervention: Dict[Any, float]):
        counterfactuals = np.zeros(samples.shape)
        t = self.topological_sort()
        exogenous_noises = samples @ (np.eye(samples.shape[1]) - self._weight_mat)
        assert exogenous_noises.shape == samples.shape

        for node in t:
            node_ix = self._node2ix[node]
            if node in intervention:
                counterfactuals[:, node_ix] = intervention[node]
            else:
                parent_ixs = [self._node2ix[p] for p in self._parents[node]]
                parent_weights = self._weight_mat[parent_ixs, node]
                counterfactuals[:, node_ix] = counterfactuals[:, parent_ixs] @ parent_weights + exogenous_noises[:, node_ix]

        return counterfactuals

    @property
    def nodes(self):
        return list(self._nodes)

    @property
    def arc_weights(self):
        return {(i, j): self._weight_mat[i, j] for i, j in self._arcs}

    @property
    def weight_mat(self):
        return self._weight_mat.copy()

    @property
    def variances(self):
        return self._variances.copy()

    @property
    def precision(self):
        self._ensure_precision()
        return self._precision.copy()

    @property
    def covariance(self):
        self._ensure_covariance()
        return self._covariance.copy()

    def _ensure_correlation(self):
        if self._correlation is None:
            S = self.covariance
            self._correlation = S / sqrt(diag(S)) / sqrt(diag(S)).reshape([-1, 1])

    @property
    def correlation(self):
        self._ensure_correlation()
        return self._correlation

    def marginal_precision(self, nodes: list):
        p = self.precision
        comp = [node for node in self.nodes if node not in nodes]
        A = p[np.ix_(nodes, nodes)]
        B = p[np.ix_(nodes, comp)]
        C = p[np.ix_(comp, comp)]
        return A - B @ inv(C) @ B.T

    def partial_correlation(self, i, j, cond_set):
        """
        Return the partial correlation of i and j conditioned on `cond_set`.

        Parameters
        ----------
        i: first node.
        j: second node.
        cond_set: conditioning set.

        Examples
        --------
        TODO
        """
        cond_set = core_utils.to_set(cond_set)
        if len(cond_set) == 0:
            return self.correlation[i, j]
        else:
            theta = inv(self.correlation[np.ix_([i, j, *cond_set], [i, j, *cond_set])])
            return -theta[0, 1] / np.sqrt(theta[0, 0] * theta[1, 1])

    def regression_coefficients(self, target_ixs, predictor_ixs, bias=True):
        if len(predictor_ixs) == 0 and not bias:
            coefs = np.zeros(0)
        if not isinstance(target_ixs, list):
            target_ixs = [target_ixs]
        cov = self.covariance
        t_ixs, p_ixs = target_ixs, list(predictor_ixs)
        coefs = inv(cov[np.ix_(p_ixs, p_ixs)]) @ cov[np.ix_(p_ixs, t_ixs)]
        if bias:
            means = self.means()
            bias = means[t_ixs] - means[p_ixs] @ coefs
            return coefs, bias
            
        return coefs

    def add_arc(self, i, j, check_acyclic=False):
        """
        Add an arc to the graph with weight 1.

        Parameters
        ----------
        i:
        j:
        check_acyclic:

        Examples
        --------
        TODO
        """
        self.set_arc_weight(i, j, 1)

    def remove_arc(self, i, j, ignore_error=False):
        """
        Remove an arc from the graph

        Parameters
        ----------
        i:
        j:
        ignore_error:

        Examples
        --------
        TODO
        """
        self.set_arc_weight(i, j, 0)

    def add_node(self, node):
        """
        Add a node to the graph

        Parameters
        ----------
        node

        Examples
        --------
        TODO
        """
        self._node_list.append(node)
        self._weight_mat = np.zeros((len(self._node_list), len(self._node_list)))
        self._weight_mat[:-1, :-1] = None

    def remove_node(self, node, ignore_error=False):
        """
        Remove a node from the graph.

        Parameters
        ----------
        node:
        ignore_error:

        Examples
        --------
        TODO
        """
        del self._node_list[self._node2ix[node]]
        self._weight_mat = self._weight_mat[np.ix_(self._node_list, self._node_list)]
        super().remove_node(node)

    def add_arcs_from(self, arcs: Union[Dict, Set], check_acyclic=False):
        """
        TODO

        Parameters
        ----------
        arcs:
        check_acyclic:

        Examples
        --------
        TODO
        """
        # super().add_arcs_from(arcs)
        arcs = arcs if isinstance(arcs, dict) else {arc: 1 for arc in arcs}
        self.set_arc_weights(arcs)

    def add_nodes_from(self, nodes):
        raise NotImplementedError
        pass

    def reverse_arc(self, i, j, ignore_error=False, check_acyclic=False):
        raise NotImplementedError
        pass

    def arcs_in_vstructures(self):
        return super().arcs_in_vstructures()

    def reversible_arcs(self):
        return super().reversible_arcs()

    def topological_sort(self):
        return super().topological_sort()

    def shd(self, other):
        return super().shd(other)

    def descendants_of(self, node):
        return super().descendants_of(node)

    def ancestors_of(self, node):
        return super().ancestors_of(node)

    def incident_arcs(self, node):
        return super().incident_arcs(node)

    def incoming_arcs(self, node):
        return super().incoming_arcs(node)

    def outgoing_arcs(self, node):
        return super().outgoing_arcs(node)

    def outdegree_of(self, node):
        return super().outdegree_of(node)

    def indegree_of(self, node):
        return super().indegree_of(node)

    def save_gml(self, filename):
        raise NotImplementedError

    def to_amat(self):
        """
        TODO

        Examples
        --------
        TODO
        """
        return self.weight_mat

    def cpdag(self):
        return super().cpdag()

    def optimal_intervention_greedy(self, cpdag=None):
        return super().optimal_intervention_greedy(cpdag=cpdag)

    def backdoor(self, i, j):
        return super().backdoor(i, j)

    def frontdoor(self, i, j):
        return super().frontdoor(i, j)

    def dsep(self, i, j, C=None, verbose=False, certify=False):
        return super().dsep(i, j, C=C, verbose=verbose, certify=certify)

    def _ensure_precision(self):
        if self._precision is None:
            id_ = np.eye(len(self._nodes))
            a = self._weight_mat
            if (self._variances == 1).all():
                self._precision = (id_ - a) @ (id_ - a).T
            else:
                self._precision = (id_ - a) @ np.diag(self._variances ** -1) @ (id_ - a).T

    def _ensure_covariance(self):
        if self._covariance is None:
            id_ = np.eye(len(self._nodes))
            a = self._weight_mat
            id_min_a_inv = inv(id_ - a)
            if (self._variances == 1).all():
                self._covariance = id_min_a_inv.T @ id_min_a_inv
                # TODO set isclose to 0 to 0 ??
            else:
                self._covariance = id_min_a_inv.T @ np.diag(self._variances) @ id_min_a_inv

    def sample(self, nsamples: int = 1) -> np.array:
        """
        Return `nsamples` samples from the graph.

        Parameters
        ----------
        nsamples:
            Number of samples.

        Returns
        -------
        (nsamples x nnodes) matrix of samples.

        Examples
        --------
        TODO
        """
        samples = np.zeros((nsamples, len(self._nodes)))
        noise = np.zeros((nsamples, len(self._nodes)))
        for ix, (bias, var) in enumerate(zip(self._biases, self._variances)):
            noise[:, ix] = np.random.normal(loc=bias, scale=var ** .5, size=nsamples)
        t = self.topological_sort()
        for node in t:
            ix = self._node2ix[node]
            parents = self._parents[node]
            if len(parents) != 0:
                parent_ixs = [self._node2ix[p] for p in self._parents[node]]
                parent_vals = samples[:, parent_ixs]
                samples[:, ix] = np.sum(parent_vals * self._weight_mat[parent_ixs, node], axis=1) + noise[:, ix]
            else:
                samples[:, ix] = noise[:, ix]
        return samples

    def sample_interventional_perfect(self, interventions: PerfectIntervention, nsamples: int = 1) -> np.array:
        """
        Return `nsamples` samples from the graph under a perfect intervention

        Parameters
        ----------
        interventions:
        nsamples:

        Returns
        -------
        (nsamples x nnodes) matrix of samples.
        """
        samples = np.zeros((nsamples, len(self._nodes)))
        noise = np.zeros((nsamples, len(self._nodes)))

        for ix, (node, bias, var) in enumerate(zip(self._node_list, self._biases, self._variances)):
            interventional_dist = interventions.get(node)
            if interventional_dist is not None:
                noise[:, ix] = interventional_dist.sample(nsamples)
            else:
                noise[:, ix] = np.random.normal(loc=bias, scale=var ** .5, size=nsamples)

        t = self.topological_sort()
        for node in t:
            ix = self._node2ix[node]
            parents = self._parents[node]
            if node not in interventions and len(parents) != 0:
                parent_ixs = [self._node2ix[p] for p in self._parents[node]]
                parent_vals = samples[:, parent_ixs]
                samples[:, ix] = np.sum(parent_vals * self._weight_mat[parent_ixs, node], axis=1) + noise[:, ix]
            else:
                samples[:, ix] = noise[:, ix]

        return samples

    def sample_interventional_soft(self, intervention: SoftIntervention, nsamples: int = 1) -> np.array:
        """
        Return samples from the graph under a soft intervention
        """
        samples = np.zeros((nsamples, len(self._nodes)))
        noise = np.zeros((nsamples, len(self._nodes)))
        for ix, var in enumerate(self._variances):
            noise[:, ix] = np.random.normal(scale=var ** .5, size=nsamples)

        t = self.topological_sort()
        for node in t:
            ix = self._node2ix[node]
            parent_ixs = [self._node2ix[p] for p in self._parents[node]]
            parent_vals = samples[:, parent_ixs]

            interventional_dist = intervention.get(node)
            if interventional_dist is not None:
                samples[:, ix] = interventional_dist.sample(parent_vals, self, node)
            elif len(parent_ixs) != 0:
                samples[:, ix] = np.sum(parent_vals * self._weight_mat[parent_ixs, node], axis=1) + noise[:, ix]
            else:
                samples[:, ix] = noise[:, ix]

        return samples

    def sample_interventional(self, intervention: Intervention, nsamples: int = 1) -> np.ndarray:
        """
        TODO

        Parameters
        ----------
        intervention:

        Examples
        --------
        TODO
        """
        samples = np.zeros((nsamples, len(self._nodes)))
        noise = np.random.normal(size=[nsamples, len(self._nodes)])
        noise = noise * np.array(self._variances) ** .5 + self._biases

        t = self.topological_sort()
        for node in t:
            ix = self._node2ix[node]
            parent_ixs = [self._node2ix[p] for p in self._parents[node]]
            parent_vals = samples[:, parent_ixs]

            interventional_dist = intervention.get(node)
            if interventional_dist is not None:
                if isinstance(interventional_dist, SoftInterventionalDistribution):
                    samples[:, ix] = interventional_dist.sample(parent_vals, self, node)
                elif isinstance(interventional_dist, PerfectInterventionalDistribution):
                    samples[:, ix] = interventional_dist.sample(nsamples)
            elif len(parent_ixs) != 0:
                samples[:, ix] = np.sum(parent_vals * self._weight_mat[parent_ixs, ix], axis=1) + noise[:, ix]
            else:
                samples[:, ix] = noise[:, ix]

        return samples

    def interventional_covariance(self, intervened_nodes: set):
        remaining_nodes = [node for node in self._nodes if node not in intervened_nodes]

        id_ = np.eye(len(self._nodes))
        a = self._weight_mat
        a = a[np.ix_(remaining_nodes, remaining_nodes)]
        id_min_a_inv = np.linalg.inv(id_ - a)
        if (self._variances == 1).all():
            return id_min_a_inv.T @ id_min_a_inv
        else:
            return id_min_a_inv.T @ np.diag(self._variances) @ id_min_a_inv

    def interventional_dag(self, interventions: Dict[Any, Tuple[float, float]]):
        remaining_arcs = {(i, j): w for (i, j), w in self.arc_weights.items() if j not in interventions}
        new_biases = [self._biases[node] if node not in interventions else interventions[node][0] for node in self._nodes]
        new_variances = [self._variances[node] if node not in interventions else interventions[node][1] for node in self._nodes]
        return GaussDAG(nodes=self._nodes, arcs=remaining_arcs, biases=new_biases, variances=new_variances)

    # def logpdf(self, samples: np.array, interventions: Intervention = None) -> np.array:
    #     self._ensure_covariance()
    #
    #     if interventions is None:
    #         return multivariate_normal.logpdf(samples, mean=self._means, cov=self._covariance)
    #     else:
    #         intervened_nodes = set(interventions.keys())
    #         remaining_nodes = [node for node in self._nodes if node not in intervened_nodes]
    #         samples = samples[:, remaining_nodes]
    #         adjusted_means = None
    #         adjusted_cov = self.interventional_covariance(intervened_nodes)
    #         return multivariate_normal.logpdf(samples, meabn=adjusted_means, cov=adjusted_cov)

    def fast_logpdf(self, samples, nodes):
        if len(nodes) == 0:
            return np.zeros(samples.shape[0])
        cov = self.covariance[np.ix_(nodes, nodes)]
        mean = self.means()[nodes]
        vals, vecs = np.linalg.eigh(cov)
        logdet     = np.sum(np.log(vals))
        valsinv    = 1./vals
        U          = vecs * np.sqrt(valsinv)
        dim        = len(vals)
        dev        = samples[:, nodes] - mean
        maha       = np.square(np.dot(dev, U)).sum(axis=1)
        log2pi     = np.log(2 * np.pi)
        return -0.5 * (dim * log2pi + maha + logdet)
    
    def log_probability(self, samples: np.ndarray):
        return self.fast_logpdf(samples, list(range(self.nnodes)))
    
    def predict_from_parents(self, node, parent_vals):
        node_ix = self._node2ix[node]
        parent_ixs = [self._node2ix[p] for p in self.parents_of(node)]
        coefs = self._weight_mat[parent_ixs, node_ix]
        bias = self._biases[node_ix]
        return parent_vals @ coefs + bias

    def logpdf(self, samples: np.array, interventions: PerfectIntervention = None, special_node=None,
               exclude_intervention_prob=True) -> np.array:
        # TODO this is about 10x slower than using multivariate_normal.logpdf with the covariance matrix
        # TODO can I speed this up? where is the time spent?

        sorted_nodes = self.topological_sort()
        nsamples = samples.shape[0]
        log_probs = np.zeros(nsamples)

        if interventions is None:
            for node in sorted_nodes:
                node_ix = self._node2ix[node]
                parent_ixs = [self._node2ix[p] for p in self._parents[node]]
                if len(parent_ixs) != 0:
                    parent_vals = samples[:, parent_ixs]
                    correction = (parent_vals * self._weight_mat[parent_ixs, node]).sum(axis=1)
                else:
                    correction = 0
                log_probs += norm.logpdf(samples[:, node_ix] - correction, scale=self._variances[node_ix] ** .5)
        else:
            for node in sorted_nodes:
                node_ix = self._node2ix[node]
                iv = interventions.get(node)
                if iv is not None:
                    if not exclude_intervention_prob:
                        if isinstance(iv, GaussIntervention):
                            log_probs += iv.logpdf(samples[:, node_ix])
                        else:
                            log_probs += np.log(iv.pdf(samples[:, node_ix]))
                else:
                    parent_ixs = [self._node2ix[p] for p in self._parents[node]]
                    parent_vals = samples[:, parent_ixs]
                    correction = (parent_vals * self._weight_mat[parent_ixs, node]).sum(axis=1)
                    log_probs += norm.logpdf(samples[:, node_ix] - correction, scale=self._variances[node_ix] ** .5)

        return log_probs


if __name__ == '__main__':
    from graphical_models import GaussDAG, GaussIntervention

    iv = MultinomialIntervention(
        interventions=[
            ConstantIntervention(val=-1),
            ConstantIntervention(val=1),
            GaussIntervention(mean=2, variance=1),
        ],
        pvals=[.4, .4, .2]
    )

    iv = BinaryIntervention(
        intervention1=ConstantIntervention(val=-1),
        intervention2=ConstantIntervention(val=1)
    )
    import causaldag as cd

    B = np.zeros((3, 3))
    B[0, 1] = 1
    B[0, 2] = -1
    B[1, 2] = 4
    gdag = GaussDAG.from_amat(B)
    iv = GaussIntervention(mean=0, variance=.1)
    gdag.sample_interventional_perfect({0: iv}, nsamples=100)

    s = gdag.sample(1000)
    # print(gdag.arcs)
    print(s.T @ s / 1000)
    print(gdag.covariance)
    s2 = gdag.sample_interventional_perfect({1: iv}, 1000)
    print(s2.T @ s2 / 1000)

    import matplotlib.pyplot as plt

    plt.clf()
    plt.ion()
    plt.scatter(s2[:, 1], s2[:, 2])
    plt.show()
