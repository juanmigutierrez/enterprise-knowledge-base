"""1 · Mock source systems (outside the product).

Stand-ins for the department tools the knowledge base reads from. ``src/ekb``
never imports this package (an import-linter contract enforces it); the product
reaches the mocks only over the network, exactly as it would reach the real tools.
"""
