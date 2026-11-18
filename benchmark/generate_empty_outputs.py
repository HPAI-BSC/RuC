import os
import json
from configs.rules import RULES
from .utils import complete_code, find_hdl_files

"""
For each rule occurrence, create a copy of the original SystemVerilog file with that occurrence removed (Evaluate
it to observe which rule occurrences are functionally meaningful and can be selected to create the benchmark).
"""

def generate_empty_outputs(root_dir, output_dir, hdl, debug):
    count = 0
    for subdir, _, files in os.walk(root_dir):
        modules = find_hdl_files(files, hdl)

        if not modules or "all_mask_idx.json" not in files:
            continue

        if debug and len(modules) > 1:
            print(f"[WARNING] multiple HDL files in {subdir}, picking first: {modules[0]}")

        module_file = os.path.join(subdir, modules[0])
        mask_file = os.path.join(subdir, "all_mask_idx.json")
        if debug:
            print(f"[INFO] Modules path: {module_file}")

        with open(module_file, 'r') as f:
            text = f.read()


        with open(mask_file, 'r') as mf:
            mask_lines = json.load(mf)

        # Use it to save generations
        base_name = os.path.basename(subdir)

        # mask_lines is a dictionary with rule names as keys and list of (start, stop) tuples as values
        for rule in mask_lines:
            if rule not in RULES:
                continue

            for i, line in enumerate(mask_lines[rule], start=1):
                
                gen_text = ""
                filled_code = complete_code(text, line, gen_text)

                save_dir = base_name + "-task" + str(i)

                gen_name = "gen1.txt"
                full_gen_name = "full_gen1.txt"

                save_path_gen = os.path.join(output_dir, rule, save_dir, gen_name)
                save_path_full_gen = os.path.join(output_dir, rule, save_dir, full_gen_name)

                os.makedirs(os.path.dirname(save_path_gen), exist_ok=True)
                os.makedirs(os.path.dirname(save_path_full_gen), exist_ok=True)
                if debug:
                    print(f"[INFO] save_path_gen: {save_path_gen}")

                with open(save_path_gen, 'w') as out_gen:
                    out_gen.write(gen_text)
                with open(save_path_full_gen, 'w') as out_full_gen:
                    out_full_gen.write(filled_code)

                count += 1

    print(f"[INFO] Total occurrences processed: {count}")


def write_hole(save_dir, hole, source_text, hdl, generation=""):
    """
    Write one hole of a multi-file project.

    Only the source holding the hole is written out, not the whole project: every other
    source is still the golden one, so the checker rebuilds the design by swapping this
    single file into the manifest source list. `hole.json` records what was replaced, so
    the folder describes itself and the evaluator does not have to guess a layout.
    """
    filled_code = complete_code(source_text, hole["span"], generation)

    os.makedirs(save_dir, exist_ok=True)

    with open(os.path.join(save_dir, "gen1." + hdl), "w") as out_gen:
        out_gen.write(generation)

    with open(os.path.join(save_dir, "full_gen1." + hdl), "w") as out_full_gen:
        out_full_gen.write(filled_code)

    with open(os.path.join(save_dir, "hole.json"), "w") as out_hole:
        json.dump(hole, out_hole, indent=4)
