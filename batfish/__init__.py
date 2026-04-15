"""Batfish digital twin — pre-deployment config validation.

Builds snapshots from generated configs and spec topology, then runs
pybatfish queries to validate BGP sessions, reachability, and route
propagation before anything touches live devices.
"""
