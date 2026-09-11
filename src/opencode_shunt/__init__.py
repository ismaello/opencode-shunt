"""Keep bulk work off your expensive model.

A frontier model is worth its price for judgement and worthless for volume. This
package installs the machinery that enforces that split inside OpenCode: bulk
reading, file generation and repetitive edits go to a cheap model, and only the
result comes back.

The version here is the single source of truth. It is what pyproject reports,
what the install manifest records, and what tells a repository its runtime is
out of date.
"""

__version__ = "3.4.0"
