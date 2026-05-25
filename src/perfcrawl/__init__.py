"""PerfCrawl — website performance auditing tool (library layer).

Phase 1 establishes the canonical data contract: the registry tables
(tracking-param denylist, metric polarity) and the canonical URL key.
Later phases add the model, store, delta engine, measurement, crawl, AI,
and output layers on top of this stable seam.
"""

__version__ = "0.1.0"
