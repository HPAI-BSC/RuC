import os
import json

from configs.rules import RULES
from .utils import read_name


RESULTS_PATH = "/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/results/slc/no_generations_full"
GEN_EQY_RESULTS = os.path.join(RESULTS_PATH, "generation_eqy_results.json")
OUTPUT_PATH = os.path.join(RESULTS_PATH, "view_samples.txt")
DATASET_PATH="/gpfs/scratch/bsc70/hpai/storage/projects/heka/chips-design/bigcode/datasets/tt-dataset-eqy-32k/"


def get_texts(full_gen_path, shuttle, project, task, rule, gen_num=1):
    # Store a segment of the original task, and the masked version
    texts = {}
    project_path = os.path.join(DATASET_PATH, shuttle, project)
    modules_path = os.path.join(project_path, "modules.v")
    mask_lines = os.path.join(project_path, "full_mask_lines.json")

    if not os.path.exists(mask_lines) or not os.path.exists(modules_path):
        return None

    with open(mask_lines, "r") as f:
        mask_data = json.load(f)

    with open(modules_path, "r") as f:
        full_text = f.read()

    line = mask_data[str(rule)][task-1]
    start, end = line

    s = max(0, start - 500)
    e = min(end + 500, len(full_text))

    texts["original"] = full_text[s:e]
    texts["masked"] = full_text[s:start] + "<MASK>" + full_text[end:e]
    # Get the model generations
    dir_name = os.path.dirname(full_gen_path)
    gen_name = "gen" + str(gen_num) + ".txt"
    gen_path = os.path.join(dir_name, gen_name)

    print(f"Og path: {full_gen_path}")
    print(f"New path: {gen_path}")

    with open(gen_path, "r") as g:
        generation = g.read()

    texts["generation"] = generation

    return texts


def write_sample(out_path, project, texts, passed, rule):  
    # Write the sample to the output file 
    with open(out_path, "a") as out_f: 
        out_f.write(f"Project: {project}\n")
        out_f.write(f"Rule: {rule}\n")
        out_f.write(f"EQY Passed: {passed}\n")
        out_f.write("-" * 80 + "\n\n")
        out_f.write("Original Text:\n")
        out_f.write("".join(texts["original"]) + "\n")
        out_f.write("-" * 80 + "\n\n")
        out_f.write("Masked Text:\n")
        out_f.write("".join(texts["masked"]) + "\n")
        out_f.write("=" * 80 + "\n\n")
        #out_f.write("Generated Text:\n")
        #out_f.write("".join(texts["generation"]) + "\n")
        #out_f.write("=" * 80 + "\n\n")



def produce_samples():
    # Produce a few passing/failing samples for each category to find inconsistencies and errors in the masking process
    passing_samples = {r: 0 for r in RULES}
    failing_samples = {r: 0 for r in RULES}

    open(OUTPUT_PATH, "w").close()

    with open(GEN_EQY_RESULTS, "r") as f:
        results = json.load(f)

    all_projects = set()
    passed_projects = set()
    for full_gen_path, eqy_result in results.items():
        rule = eqy_result["rule"]
        passed = eqy_result["eqy_passed"]
        project_name = os.path.basename(os.path.dirname(full_gen_path))
        shuttle, project, task = read_name(project_name)
        all_projects.add(project)
        if passed:
            passed_projects.add(project)
            if passing_samples[rule] < 10000:
                passing_samples[rule] += 1
            
            else: 
                continue

        else:
            if failing_samples[rule] < 10000:
                failing_samples[rule] += 1
            else:
                continue

        print(f"Looking at rule {rule}, project_name {project_name}, eqy passed {passed}")
        
        texts = get_texts(full_gen_path, shuttle, project, task, rule)
        if texts == None:
            continue
        
        write_sample(OUTPUT_PATH, project, texts, passed, rule)
    print(f"Total projects seen: {len(all_projects)}")
    print(f"Different projects passed: {len(passed_projects)}")
    

if __name__ == "__main__":
    produce_samples()
    print("Sample generation completed.")        