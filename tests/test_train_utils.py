"""DB-free unit tests for the split and metric helpers in ml/train.py."""

import numpy as np
import pandas as pd

from ml.train import cls_metrics, month_boundary_cut, prevalence_metrics


def test_month_boundary_cut_picks_tail_closest_to_target_share():
    months = ["2017-01", "2017-02", "2017-03", "2017-04"]
    sizes = pd.Series([40, 30, 20, 10], index=months)
    # tail shares walking back: 10% -> 30% -> 60%; closest to 20% is 30% (starts 2017-03)
    assert month_boundary_cut(months, sizes, 0.20) == "2017-03"
    assert month_boundary_cut(months, sizes, 0.05) == "2017-04"


def test_prevalence_metrics_perfect_and_constant_scores():
    y = np.array([0, 0, 1, 1])
    perfect = prevalence_metrics(y, np.array([0.1, 0.2, 0.8, 0.9]), "perfect")
    assert perfect == {"name": "perfect", "auc": 1.0, "pr_auc": 1.0}
    constant = prevalence_metrics(y, np.zeros(4), "constant")
    assert constant["auc"] == 0.5
    assert constant["pr_auc"] == 0.5  # equals prevalence


def test_cls_metrics_counts_and_f1():
    y = np.array([1, 1, 0, 0, 1])
    pred = np.array([1, 0, 0, 1, 1])
    m = cls_metrics(y, pred)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 1)
    assert m["precision"] == 0.6667 and m["recall"] == 0.6667 and m["f1"] == 0.6667


def test_cls_metrics_handles_no_positive_predictions():
    m = cls_metrics(np.array([0, 1]), np.array([0, 0]))
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0
