"""
Machine-specific settings -- NOT read directly. Copy this file to
`local_settings.py` (same folder) and fill in your own path.

`local_settings.py` is gitignored: it never gets committed, so wherever your
real data actually lives on disk never ends up in shared source. Every
collaborator running this pipeline makes their own copy, pointing at their
own machine.
"""

# The directory containing Level_1/, Level_1.5/, Level_2/, plots/, etc. --
# run_pipeline.py chdirs here; edit_l3.py reads Level_2QC from here.
DATA_ROOT = r"/path/to/your/data/root"
