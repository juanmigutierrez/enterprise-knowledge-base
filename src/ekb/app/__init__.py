"""Composition root: the only place that knows about concrete options.

Entry points (``ekb connect``, ``ekb worker``, ``ekb api``) live here. They read
the configuration, build each step from the option registry and start the
service. Business code never imports a concrete option directly.
"""
