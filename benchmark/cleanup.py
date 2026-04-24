import json, os

"""
Delete specific files from the dataset.
"""


def delete_mask_lines(root_dir):
    """Delete all mask_lines.json files in the dataset."""
    for subdir, _, files in os.walk(root_dir):
        if "mask_lines.json" in files and "modules.v" in files:
            os.remove(os.path.join(subdir, "mask_lines.json"))


def delete_mask_lines_rules(root_dir, rules):
    """Delete specific rules from mask_lines.json files in the dataset."""
    for subdir, _, files in os.walk(root_dir):
        if "mask_lines.json" in files:
            file_path = os.path.join(subdir, "mask_lines.json")
            with open(file_path, "r") as f:
                lines = json.load(f)
            modified = False
            for rule in rules:
                if rule in lines:
                    del lines[rule]
                    modified = True
            if modified:
                with open(file_path, "w") as f:
                    json.dump(lines, f)


if __name__ == "__main__":
    dataset = "tt-dataset-eqy-32k"
    shuttles = ["tt07"]
                
    for sh in shuttles:
        print(f"Processing shuttle: {sh}")
        shuttle_dir = "/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/datasets/" + dataset + "/" + sh
        # delete_mask_lines_rules(shuttle_dir, ["procedural_timing_control_statement"])
        delete_mask_lines(shuttle_dir)
