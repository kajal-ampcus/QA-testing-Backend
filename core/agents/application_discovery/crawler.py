"""
Priority-queue crawl logic (Section 9): scores each interactive element by
(a) relevance to the current requirement's keywords, (b) novelty (leads to an
unvisited state), (c) risk class (destructive actions are never auto-clicked).
Breadth-first with a relevance-weighted priority queue, not a random walk.

Also owns the large-app sharding strategy (Section 9 scaling subsection):
shard by top-level navigation section, each shard an independent crawl with
its own crawl_budget, merged via fingerprint.py's dedup key. MVP = one shard
(the whole app); Production = many, run in parallel worker tasks.

Phase 0 stub.
"""
