import json
import re
from pathlib import Path


# Filenames of the rule-completion results. Shared between the evaluator that writes
# them and the benchmark builder that reads them back during functional validation,
# so the two cannot drift apart again.
RESULTS_FILENAME = "generation_eqy_results.json"
SUMMARY_FILENAME = "summary_results.json"


def read_path(project_name):
    """ Extract the information from the project name """
    project, task_part = project_name.rsplit("-task", 1)
    task = int(task_part)

    return project, task


def save_generation_eqy_results(results, output_path):
    """ Used to analyze passed and failed samples """
    with open(output_path, "w") as f:
        json.dump(results, f, indent=4)


def find_hdl_files(files, hdl):
    return [f for f in files if f.endswith("." + hdl)]


def as_source_list(paths):
    """ Normalize a single path or a list of paths into a list of Path objects.

    Single-file callers keep passing one path and get a one-element list back, so
    the behaviour of the rule-completion flow is unchanged.
    """
    if isinstance(paths, (str, Path)):
        return [Path(paths)]

    return [Path(p) for p in paths]


def str_to_bool(value):
    """ Parse a boolean coming from the YAML config through the command line.

    argparse's `type=bool` maps any non-empty string to True, so the config value
    `false` has to be handled explicitly.
    """
    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in ("1", "true", "yes", "y")
