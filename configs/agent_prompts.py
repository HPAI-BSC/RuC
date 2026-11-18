# Comment syntax used to delimit a hole, per HDL. It lets the generation be extracted afterwards.
HOLE_MARKERS = {
    "v": ("// RUC_HOLE_BEGIN id={id}", "// RUC_HOLE_END id={id}"),
    "sv": ("// RUC_HOLE_BEGIN id={id}", "// RUC_HOLE_END id={id}"),
    "vhd": ("-- RUC_HOLE_BEGIN id={id}", "-- RUC_HOLE_END id={id}"),
}

# The one line of code the worked example in the instruction fills its hole with.
HOLE_EXAMPLE_BODY = {
    "v": "assign foo = bar & baz;",
    "sv": "assign foo = bar & baz;",
    "vhd": "foo <= bar and baz;",
}

AGENT_INSTRUCTION = """
You are a professional hardware designer working in an existing {hdl_name} project.

Several regions of this project have been removed. Each one is marked in the sources with
a pair of comments:

    {begin_marker}
    {end_marker}

Your task is to implement every missing region so that the project behaves exactly as it
did before the regions were removed. You do not have access to internet.

Follow these rules STRICTLY:

    1. Write the replacement code BETWEEN the two markers of each hole.
    2. Do NOT delete, move or reword the RUC_HOLE_BEGIN and RUC_HOLE_END comment lines.
       If you replace a region of text that contains a marker, the text you write back must 
       contain that marker as well.
       For a hole that reads:

           {begin_example}
           {end_example}

       this is the correct format:

           {begin_example}
           {example_body}
           {end_example}
    3. An edit that replaces exact text only applies if the text you give it matches the
       file character for character, including every space of indentation. If an edit fails, 
       re-read the file and copy those lines from what you just read instead of retyping them 
       from memory. Never send a failed edit again unchanged: match on something else, or use
       another tool to make the change.
    4. Do NOT change any code outside the marked regions. The rest of the project is
       correct and must be used as-is. Only the text you leave between a hole's two
       markers is collected.
    5. The code must be synthesizable {hdl_name}, and must integrate with the other
       sources of the project.
    6. Do not add code fences, language tags or markdown to the source files.
    7. The project might also ship its documentation, constraints, scripts and any sources in
       another language. Read whatever helps you understand the design. Only the
       {hdl_name} sources listed in RUC_TASK.md are edited and scored, so changing any
       other file has no effect on your result.
    8. The shared library the design is built on is in `{library_dir}/`. It is compiled
       together with the design, so read it whenever you need to. Editing it is pointless for the
       same reason as rule 4.

The project is listed in RUC_TASK.md.
You can use the tools available in this environment to inspect and check the project.
"""

TASK_README_TEMPLATE = """# RuC agentic task: {bundle_id}

The project `{project}` has {hole_count} missing regions, spread over {file_count} source
files. Implement all of them.

Each region is delimited in its source by:

```
{begin_example}
{end_example}
```

Write your code between the two markers, and leave the marker comments themselves
untouched. Both markers must still be in the file when you finish: they are how each
region is found and scored, and a region that has lost either of them scores zero.


## Top module

`{top_module}`

## Sources

The {hdl_name} files of the design. Every missing region is in one of these, and they are
the only files that are scored.

{source_list}

## Reference library

The shared modules and packages this design is built on, under `{library_dir}/`.

{library_list}

## Reference files

Everything else the project ships. Read anything that helps you understand what the design
is meant to do. These files are never edited or scored.

{reference_list}
"""

HDL_NAMES = {
    "v": "Verilog",
    "sv": "SystemVerilog",
    "vhd": "VHDL",
}

# The working directory Harbor's Singularity backend gives the agent, and therefore where
# the project lands: the backend passes `--pwd /app` and uploads `environment/` there.
WORKSPACE_DIR = "/app"

# Harbor bind mounts this from the host, one directory per trial, and reads the reward from
# it. It is the only writable host path a task gets, and the reason RuC needs none of its
# own: the test script writes the finished workspace here and it is already on the host.
VERIFIER_DIR = "/logs/verifier"

# Subdirectory of the verifier mount that the collect stage reads back.
EXPORT_SUBDIR = "ruc"

# Written into the workspace by `tests/test.sh`, one path per line, listing what the agent
# changed outside its holes. Informational: none of it reaches scoring. Empty file means
# the workspace was clean, no file at all means the check could not run.
STRAY_REPORT_NAME = "RUC_STRAY_EDITS.txt"

# Harbor reads the task configuration from task.toml.
#
# `docker_image` is what selects the image -- the Singularity backend reads it from here
# and nowhere else, and raises if it is unset. There is no command line flag for it.
# `workdir` is where `environment/` is uploaded after the container starts.
TASK_TOML_TEMPLATE = """version = "1.0"

[metadata]
difficulty = "hard"
category = "hardware-design"
tags = [{tags}]

[metadata.ruc]
project = "{project}"
bundle = "{bundle}"
top_module = "{top_module}"
hdl = "{hdl}"
holes = {hole_count}
files = {file_count}

[agent]
timeout_sec = {agent_timeout_s}

[verifier]
timeout_sec = {verifier_timeout_s}

[environment]
build_timeout_sec = 600
docker_image = "{base_image}"
workdir = "{workdir}"
"""

# Runs inside the container once the agent is done.
TEST_SCRIPT_TEMPLATE = """#!/bin/bash
# Generated by RuC. Verifies the agent's work and exports the workspace for scoring.

WORKSPACE="${{RUC_WORKSPACE:-{workspace_dir}}}"
VERIFIER_DIR="${{RUC_VERIFIER_DIR:-{verifier_dir}}}"
TASK_NAME="{task_name}"

HOLE_IDS=({hole_ids})
HOLE_FILES=({hole_files})
SOURCES=({sources})
REFERENCES=({references})
INCLUDE_DIRS=({include_dirs})
# The workspace as it was emitted. SOURCE_SHA is hashed with the hole bodies blanked, so
# writing between the markers is not a change;
# OTHER_SHA is every other file, byte for byte. Only used to report where the agent worked.
SOURCE_SHA=({source_sha})
OTHER_SHA=({other_sha})
LINT=({lint})

status=0

fail() {{
    echo "[FAIL] $1"
    status=1
}}

# `id=h1` is a prefix of `id=h10`, so a marker match that is not delimited lets a
# bundle's tenth hole answer for its first: the check would read the wrong file, and a
# deleted or empty h1 would pass on the strength of h10. This is the shell and awk
# spelling of the `\\b` the collect stage matches the same markers with.
id_pattern() {{
    printf '%s id=%s([^A-Za-z0-9_]|$)' "$1" "$2"
}}

# 1. Every source of the project is still there
for source in "${{SOURCES[@]}}"; do
    if [ ! -f "$WORKSPACE/$source" ]; then
        fail "$source was deleted"
    fi
done

# 2. The markers survived, which is what makes a hole collectable at all
for hole_id in "${{HOLE_IDS[@]}}"; do
    for marker in RUC_HOLE_BEGIN RUC_HOLE_END; do
        found=0
        for source in "${{HOLE_FILES[@]}}"; do
            if grep -qE "$(id_pattern "$marker" "$hole_id")" "$WORKSPACE/$source" 2>/dev/null; then
                found=1
                break
            fi
        done
        if [ "$found" -eq 0 ]; then
            fail "$marker of $hole_id is gone"
        fi
    done
done

# 3. Every hole has something written between its markers
for hole_id in "${{HOLE_IDS[@]}}"; do
    begin_pattern=$(id_pattern RUC_HOLE_BEGIN "$hole_id")
    end_pattern=$(id_pattern RUC_HOLE_END "$hole_id")

    hole_file=""
    for source in "${{HOLE_FILES[@]}}"; do
        if grep -qE "$begin_pattern" "$WORKSPACE/$source" 2>/dev/null; then
            hole_file="$WORKSPACE/$source"
            break
        fi
    done

    # Missing markers are already reported by the previous check
    if [ -z "$hole_file" ]; then
        continue
    fi

    # The patterns go in through -v rather than the program text, so a hole id never
    # has to survive two rounds of quoting on its way into the regex.
    body=$(awk -v b="$begin_pattern" -v e="$end_pattern" \\
        '$0 ~ b {{flag=1; next}} $0 ~ e {{flag=0}} flag' "$hole_file")

    if [ -z "$(echo "$body" | tr -d '[:space:]')" ]; then
        fail "hole $hole_id was left empty"
    fi
done

# 4. Where the agent worked outside its holes.
stray=()

# The blanked form the digests were taken over. Holes never nest, so one flag is enough.
blank_holes() {{
    awk '/RUC_HOLE_BEGIN/ {{print; skip=1; next}}
         /RUC_HOLE_END/   {{print; skip=0; next}}
         !skip            {{print}}' "$1"
}}

if command -v sha256sum >/dev/null 2>&1; then
    for entry in "${{SOURCE_SHA[@]}}"; do
        expected="${{entry%% *}}"
        relative="${{entry#* }}"

        if [ ! -f "$WORKSPACE/$relative" ]; then
            stray+=("$relative (deleted)")
            continue
        fi

        # Command substitution eats trailing newlines and printf puts exactly one back,
        # which is the normalisation the emitted digest was taken with
        actual=$(printf '%s\\n' "$(blank_holes "$WORKSPACE/$relative")" \\
            | sha256sum | cut -d' ' -f1)

        if [ "$actual" != "$expected" ]; then
            stray+=("$relative (edited outside its holes)")
        fi
    done

    for entry in "${{OTHER_SHA[@]}}"; do
        expected="${{entry%% *}}"
        relative="${{entry#* }}"

        if [ ! -f "$WORKSPACE/$relative" ]; then
            stray+=("$relative (deleted)")
            continue
        fi

        actual=$(sha256sum "$WORKSPACE/$relative" | cut -d' ' -f1)

        if [ "$actual" != "$expected" ]; then
            stray+=("$relative (edited)")
        fi
    done

    # Written into the workspace, so it is exported with it and survives next to the
    # sources for whoever reads the run afterwards. Always written, empty when clean, so
    # its absence means the check never ran rather than that there was nothing to say.
    : > "$WORKSPACE/{stray_report}"

    if [ "${{#stray[@]}}" -eq 0 ]; then
        echo "[INFO] nothing was edited outside the holes"
    else
        echo "[INFO] ${{#stray[@]}} path(s) edited outside the holes, which scoring ignores:"
        for entry in "${{stray[@]}}"; do
            echo "$entry" >> "$WORKSPACE/{stray_report}"
            echo "[INFO]   $entry"
        done
    fi
else
    echo "[WARNING] sha256sum not found, edits outside the holes left unreported"
fi

# 5. The project still parses. Equivalence is proved later, outside the agent's clock.
#    The library is compiled with the design and comes first, since it declares the
#    packages the design imports.
lint_sources=()
for reference in "${{REFERENCES[@]}}"; do
    lint_sources+=("$WORKSPACE/$reference")
done
for source in "${{SOURCES[@]}}"; do
    lint_sources+=("$WORKSPACE/$source")
done

lint_flags=()
for directory in "${{INCLUDE_DIRS[@]}}"; do
    lint_flags+=("-I$WORKSPACE/$directory")
done

if ! "${{LINT[@]}}" "${{lint_flags[@]}}" "${{lint_sources[@]}}"; then
    fail "the project no longer parses"
fi

# 6. Export the workspace. Always runs, including after a failed check: a half finished
#    bundle still has holes worth scoring, and an empty hole scores as a failed hole.
#
#    VERIFIER_DIR is bind mounted from the host by Harbor, one directory per trial, so
#    writing here puts the workspace on the host without RuC binding anything itself.
destination="$VERIFIER_DIR/{export_subdir}/$TASK_NAME/workspace"

mkdir -p "$destination"
cp -r "$WORKSPACE/." "$destination/" && echo "[INFO] Workspace exported to $destination"

# 7. The reward. Harbor fails the trial outright if this file is missing, so it is written
#    last and unconditionally. The checks above are the reward: RuC scores the holes itself,
#    from the exported workspace, so this only says whether the bundle came back usable.
if [ "$status" -eq 0 ]; then
    echo "1" > "$VERIFIER_DIR/reward.txt"
else
    echo "0" > "$VERIFIER_DIR/reward.txt"
fi

# Exit 0 even on a failed check: the reward file is what reports the outcome, and a nonzero
# verifier exit would be recorded as the verifier itself having broken.
exit 0
"""
