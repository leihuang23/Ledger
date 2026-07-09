"""Control-plane run orchestration domain (PRD Phase 3, FR-8..FR-11).

Owns the ``POST /runs`` control-plane surface, the run status lifecycle state
machine, and the Celery task that drives a run to completion by reusing the
Project 1 investigation executor. Execution primitives (claim, resolve version,
run the workflow, finalize, timeout self-heal) are reused from
``app.agent.service``; this domain adds the control-plane API + lifecycle
validation on top.
"""
