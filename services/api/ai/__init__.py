"""AI enrichment for mistake diagnoses.

The model never originates a cause. The deterministic rules produce a candidate
set with citable evidence; this layer only ranks within that set and writes the
prose. A response naming a cause outside the candidates, or citing evidence the
packet does not carry, is rejected and the rules-only diagnosis stands.

That constraint is enforced in :mod:`services.api.ai.client`, not in the prompt.
A prompt regression degrades output to rules-only rather than producing a
confident invention.
"""
