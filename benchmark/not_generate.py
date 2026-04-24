import os
import json
from configs.rules import RULES
from .utils import complete_code, find_hdl_files

"""
For each rule occurrence, create a copy of the original SystemVerilog file with that occurrence removed (Evaluate it to observe which rule occurrences are functionally meaningful and can be selected to create the benchmark).
"""

def not_generate(root_dir, output_dir, hdl, debug):
    count = 0
    for subdir, _, files in os.walk(root_dir):
        modules = find_hdl_files(files, hdl)

        if not modules or "all_mask_idx.json" not in files:
            continue

        if len(modules) > 1:
            print(f"Warning: multiple HDL files in {subdir}, picking first: {modules[0]}")

        module_file = os.path.join(subdir, modules[0])
        mask_file = os.path.join(subdir, "all_mask_idx.json")
        if debug:
            print(f"Modules path: {module_file}")

        with open(module_file, 'r') as f:
            text = f.read()

        #print("\n\nOriginal text:")
        #print(text)

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
                    print(f"save_path_gen: {save_path_gen}")

                with open(save_path_gen, 'w') as out_gen:
                    out_gen.write(gen_text)
                with open(save_path_full_gen, 'w') as out_full_gen:
                    out_full_gen.write(filled_code)

                count += 1

    print(f"Total occurrences processed: {count}")
                    
            