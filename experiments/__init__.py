"""Experiments package for the Paper-to-Poster paper.

Contains everything required to reproduce the experiments section: dataset
preparation, 4 baselines, 12 evaluation metrics, judges, run orchestration,
statistical analysis and plotting. Production code (``app/``) is not
imported from here except through stable interfaces (the FastAPI HTTP API
and the ``ExperimentLogger`` protocol consumed by ``app.feedback_loop``).
"""
