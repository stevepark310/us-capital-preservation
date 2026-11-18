"""
Hierarchical Risk Parity (HRP) Portfolio Optimization
Reference: Marcos López de Prado (2016), "Building Diversified Portfolios that Outperform Out-of-Sample",
The Journal of Portfolio Management, 42(4), pp. 59-69.
"""

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.spatial.distance import squareform


class HierarchicalRiskParity:
    def __init__(self, cov_matrix: pd.DataFrame):
        self.cov = cov_matrix
        self.corr = self._cov_to_corr(cov_matrix)
        self.assets = list(cov_matrix.columns)

    @staticmethod
    def _cov_to_corr(cov: pd.DataFrame) -> pd.DataFrame:
        v = np.sqrt(np.diag(cov))
        outer_v = np.outer(v, v)
        corr = cov / outer_v
        corr[corr > 1.0] = 1.0
        corr[corr < -1.0] = -1.0
        return corr

    def _correl_distance(self) -> np.ndarray:
        # Distance metric: d(i, j) = sqrt(0.5 * (1 - rho(i, j)))
        dist = np.sqrt(0.5 * np.maximum(0.0, 1.0 - self.corr.values))
        np.fill_diagonal(dist, 0.0)
        return dist

    def _tree_clustering(self) -> np.ndarray:
        dist = self._correl_distance()
        condensed_dist = squareform(dist, checks=False)
        link = linkage(condensed_dist, method="single")
        return link

    def _quasi_diagonal(self, link: np.ndarray) -> list[int]:
        root = to_tree(link, rd=False)
        order = []

        def traverse(node):
            if node.is_leaf():
                order.append(node.id)
            else:
                traverse(node.left)
                traverse(node.right)

        traverse(root)
        return order

    def _get_cluster_var(self, cluster_indices: list[int]) -> float:
        sub_cov = self.cov.iloc[cluster_indices, cluster_indices].values
        # Inverse-variance allocation within cluster
        inv_diag = 1.0 / np.maximum(1e-8, np.diag(sub_cov))
        w = inv_diag / np.sum(inv_diag)
        c_var = float(w @ sub_cov @ w)
        return max(1e-8, c_var)

    def _recursive_bisection(self, ordered_indices: list[int]) -> pd.Series:
        weights = pd.Series(1.0, index=ordered_indices)
        clusters = [ordered_indices]

        while len(clusters) > 0:
            next_clusters = []
            for cluster in clusters:
                if len(cluster) > 1:
                    mid = len(cluster) // 2
                    left = cluster[:mid]
                    right = cluster[mid:]

                    var_left = self._get_cluster_var(left)
                    var_right = self._get_cluster_var(right)

                    # Alpha allocation factor based on variance ratio
                    alpha = 1.0 - var_left / (var_left + var_right)

                    weights.loc[left] *= alpha
                    weights.loc[right] *= (1.0 - alpha)

                    next_clusters.append(left)
                    next_clusters.append(right)
            clusters = next_clusters

        return weights

    def allocate(self) -> pd.Series:
        if len(self.assets) == 1:
            return pd.Series([1.0], index=self.assets)

        link = self._tree_clustering()
        ordered_indices = self._quasi_diagonal(link)
        raw_weights = self._recursive_bisection(ordered_indices)

        # Map back to asset symbols
        asset_weights = pd.Series(index=self.assets, dtype=float)
        for idx_num, w in raw_weights.items():
            asset_weights[self.assets[idx_num]] = w

        # Normalize sum to 1.0
        asset_weights = asset_weights / asset_weights.sum()
        return asset_weights
