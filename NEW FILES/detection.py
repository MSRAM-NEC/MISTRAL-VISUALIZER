import pandas as pd
from sklearn.cluster import DBSCAN
import numpy as np

class HumanDetector:
    """
    Processes a point cloud DataFrame to cluster points and identify human-like objects.
    """
    def __init__(self, eps=0.5, min_samples=5, min_points_human=10,
                 max_human_width=1.2, min_human_height=0.8, max_human_height=2.0):
        """
        Initializes the detector with parameters for clustering and classification.
        """
        # --- Tunable Parameters ---
        self.eps = eps
        self.min_samples = min_samples
        self.min_points_human = min_points_human
        self.max_human_width = max_human_width
        self.min_human_height = min_human_height
        self.max_human_height = max_human_height
        self.movement_threshold = 0.1 # m/s to distinguish static vs moving

    def _is_human_cluster(self, cluster: pd.DataFrame) -> bool:
        """
        Checks if a given cluster of points meets the criteria for being a human.
        """
        if len(cluster) < self.min_points_human:
            return False

        min_coords = cluster[['x', 'y', 'z']].min()
        max_coords = cluster[['x', 'y', 'z']].max()

        width_x = max_coords.x - min_coords.x
        width_y = max_coords.y - min_coords.y
        height = max_coords.z - min_coords.z

        horizontal_spread = np.sqrt(width_x**2 + width_y**2)

        if not (self.min_human_height < height < self.max_human_height):
            return False
        if horizontal_spread > self.max_human_width:
            return False

        return True

    def process(self, df: pd.DataFrame):
        """
        Takes a raw point cloud DataFrame and returns a processed DataFrame with
        object labels and a list of detected human details.
        """
        if df.empty:
            return df, []

        coords = df[['x', 'y', 'z']].values
        db = DBSCAN(eps=self.eps, min_samples=self.min_samples).fit(coords)
        df['cluster_id'] = db.labels_

        df['label'] = 'Clutter'
        human_info = []

        unique_clusters = df[df['cluster_id'] != -1]['cluster_id'].unique()

        for cid in unique_clusters:
            cluster_df = df[df['cluster_id'] == cid]

            if self._is_human_cluster(cluster_df):
                df.loc[df['cluster_id'] == cid, 'label'] = 'Human'
                centroid = cluster_df[['x', 'y', 'z']].mean().to_dict()
                human_info.append({'id': cid, 'centroid': centroid, 'points': len(cluster_df)})
            else:
                avg_velocity = cluster_df['velocity'].abs().mean()
                if avg_velocity > self.movement_threshold:
                    df.loc[df['cluster_id'] == cid, 'label'] = 'Moving'
                else:
                    df.loc[df['cluster_id'] == cid, 'label'] = 'Static'

        return df, human_info

