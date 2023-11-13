# coding=utf-8
import numpy as np
from scipy.spatial.distance import cdist
import logging
import os

logger = logging.getLogger(__name__)

class Retrieval(object):
    """Calculate Retrieval AP."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.K_list = cfg.EVAL.RETRIEVAL_KS
        self.dist_type = cfg.EVAL.KENDALLS_TAU_DISTANCE
        self.stride = 1
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S',filename=os.path.join(cfg.LOGDIR,'ap.log'))

    def evaluate(self, dataset, cur_epoch, summary_writer,split):
        """Labeled evaluation."""
        val_embs = dataset['embs']
        val_labels = dataset['labels']
        val_APs = []
        for K in self.K_list:
            val_APs.append(self.get_AP(val_embs, val_labels, K, 
                            cur_epoch, summary_writer,split, visualize=True))
        return val_APs[0]
    
    def get_AP(self, embs_list, label_list, K, cur_epoch, summary_writer, split, visualize=False):
        """Get topK in embedding space and calculate average precision."""
        num_seqs = len(embs_list)
        precisions = np.zeros(num_seqs)
        idx = 0
        for i in range(num_seqs):
            query_feats = embs_list[i][::self.stride]
            query_label = label_list[i][::self.stride]

            candidate_feats = []
            candidate_label = []
            for j in range(num_seqs):
                if i != j:
                    candidate_feats.append(embs_list[j][::self.stride])
                    candidate_label.append(label_list[j][::self.stride])
            candidate_feats = np.concatenate(candidate_feats, axis=0)
            candidate_label = np.concatenate(candidate_label, axis=0)
            dists = cdist(query_feats, candidate_feats, self.dist_type)
            topk = np.argsort(dists, axis=1)[:, :K]
<<<<<<< HEAD

            # logger.info(f"Index :{i}")
            # logger.info(f"candidate_label: {candidate_label}")
            # logger.info(f"dists: {dists}")
            # logger.info(f"topk: {topk}")

=======
>>>>>>> 47fcb3a6ee4422a4b608b29e8779874a74efa406
            ap = 0
            for t in range(len(query_feats)):
                ap += np.mean(int(query_label[t]) == candidate_label[topk[t]])
            precisions[idx] = ap / len(query_feats)
            idx += 1
        # Remove NaNs.
        precisions = precisions[~np.isnan(precisions)]
        precision = np.mean(precisions)

        logger.info('epoch[{}/{}] {} set AP@{} precision: {:.2%}'.format(
            cur_epoch, self.cfg.TRAIN.MAX_EPOCHS, split, K, precision))

        summary_writer.add_scalar(f'AP/{split} set {K}_align_precision', precision, cur_epoch)
        return precision