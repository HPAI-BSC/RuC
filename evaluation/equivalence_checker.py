"""
NotSoTiny Evaluation Module

This module contains evaluation functions specifically for the NotSoTiny benchmark.
It evaluates Verilog generations using the original TinyTapeout project test infrastructure
with proper separation between syntax and functionality testing.

Author: razineMG
"""

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError as e:
    pass

from .utils import as_source_list
from .equivalence_scripts import (
    SYNTHESIS_DEFINES,
    generate_yosys_script_baseonly_verilog,
    generate_yosys_script_nst_slang,
    generate_yosys_script_nst
)


def compiler_options(args, hdl):
    """
    The `-I` / `-D` flags of the syntax checkers.

    Both come off `args`, because they are properties of the project being checked and
    only the caller that read its manifest knows them. The non-agentic flow sets neither.
    """

    if hdl == "vhd":
        # Not yet implemented
        return []

    options = [f"-I{directory}" for directory in getattr(args, "include_dirs", None) or []]
    options += [f"-D{define}" for define in getattr(args, "defines", None) or []]

    return options


def execute_command(cmd, tool_name):
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        ), None

    except FileNotFoundError:
        return None, f"{tool_name} not found"

    except  subprocess.TimeoutExpired:
        return None, f"{tool_name} timed out after 600s"



def verify_syntax(file_path, args, debug: bool = False) -> Dict[str, Any]:
    """
    Run pure syntax check using Icarus Verilog without running tests.

    Args:
        file_path: Path to the HDL file to check, or the list of sources of a
            multi-file design, in the analysis order given by `ruc_project.json`
        debug: Whether to print debug information

    Returns:
        Dictionary with syntax check results
    """
    source_args = [str(source) for source in as_source_list(file_path)]
    source_label = ", ".join(source_args)

    options = compiler_options(args, args.hdl)

    try:
        result = None

        if args.hdl == "v":
            if debug:
                print(f"[INFO] Running iverilog syntax check on {source_label}")

            result, error_message = execute_command(
                [
                    "iverilog",
                    "-Wall",
                    "-Winfloop",
                    "-Wno-timescale",
                    "-g2012",
                    "-t",
                    "null",
                    *options,
                    *source_args,
                ],
                "iverilog",
            )

        elif args.hdl == "sv":
            if debug:
                print(f"[INFO] Running verilator syntax check on {source_label}")

            result, error_message = execute_command(
                [
                    "verilator",
                    "--lint-only",
                    "-Wno-fatal",
                    *options,
                    *source_args,
                ],
                "verilator",
            )

        elif args.hdl == "vhd":
            if debug:
                print(f"[INFO] Running ghdl syntax check on {source_label}")

            result, error_message = execute_command(
                [
                    "ghdl",
                    "-a",
                    *source_args,
                ],
                "ghdl",
            )

        else:
            return {
                "syntax_valid": False,
                "error_message": f"Unsupported HDL: {args.hdl}",
                "warnings": [],
            }

        if error_message:
            return {
                "syntax_valid": False,
                "error_message": error_message,
                "warnings": [],
            }

        stderr = result.stderr or ""

        syntax_valid = result.returncode == 0
        first_error = ""
        warnings = []

        for line in stderr.splitlines():
            line = line.strip()

            if not line:
                continue

            lower = line.lower()

            if "error" in lower:
                if not first_error:
                    first_error = clean_error_message(line)

            elif "warning" in lower:
                warnings.append(clean_error_message(line))

        if not syntax_valid and not first_error:
            first_error = (
                stderr.strip()
                if stderr.strip()
                else f"Compilation failed with return code {result.returncode}"
            )

        if debug:
            print(f"[INFO] Syntax valid: {syntax_valid}")

            if first_error:
                print(f"[ERROR] Error: {first_error}")

            if warnings:
                print(f"[WARNING] Warnings: {len(warnings)}")

        return {
            "syntax_valid": syntax_valid,
            "error_message": first_error,
            "warnings": warnings,
        }

    except Exception as e:
        error_msg = f"Syntax check failed: {e}"

        if debug:
            print(f"[ERROR] {error_msg}")

        return {
            "syntax_valid": False,
            "error_message": error_msg,
            "warnings": [],
        }



def clean_error_message(message: str) -> str:
    """
    Clean up error message by removing file paths and irrelevant details.
    """
    message = re.sub(r"(^|(?<=[\s(]))/\S*/(?=\S)", "", message)

    prefixes_to_remove = [
        r"^\s*[^:]*:\s*",
        r"^\s*error:\s*",
        r"^\s*warning:\s*",
        r"^\s*make\[\d+\]:\s*",
    ]

    for prefix in prefixes_to_remove:
        message = re.sub(prefix, "", message, flags=re.IGNORECASE)

    message = " ".join(message.split())

    return message.strip()


def verify_equivalence(
    generated_file, reference_file, project_dir: Path, args, debug: bool = False
) -> Dict[str, Any]:
    """
    Run formal equivalence check using Yosys with cells coverage tracking.

    `generated_file` and `reference_file` accept either a single path, as the rule
    completion flow uses, or the list of sources of a multi-file design in the
    analysis order given by `ruc_project.json`.

    Returns:
        {
            "equiv_passed": bool,
            "error_message": str,
            "top_module": str,
            "total_cells": int or None,
            "proven_cells": int or None,
            "unproven_cells": int or None,
            "cells_coverage": float or None,
            "unproven_signals": List[str] or None,
            "yosys_time": float or None,
            "eqy_return_code": int or "timeout",
            "equiv_method": str
        }
    """
    generated_files = as_source_list(generated_file)
    reference_files = as_source_list(reference_file)

    if debug:
        print(f"[INFO]   Running equivalence check:")
        print(f"[INFO]     Generated: {', '.join(str(f) for f in generated_files)}")
        print(f"[INFO]     Reference: {', '.join(str(f) for f in reference_files)}")
        print(f"[INFO]     Project: {project_dir}")

    result = {
        "equiv_passed": False,
        "error_message": "",
        "top_module": None,
        "total_cells": None,
        "proven_cells": None,
        "unproven_cells": None,
        "cells_coverage": None,
        "unproven_signals": None,
        "yosys_time": None,
        "eqy_return_code": None,
        "equiv_method": "error",
    }

    try:
        # top_module comes from the project manifest when there is one, otherwise it is
        # the name of the single reference file after removing the extension
        top_module = getattr(args, "top_module", None) or reference_files[0].stem

        result["top_module"] = top_module
        if not top_module:
            result["error_message"] = "Could not extract top module"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        abs_generated_files = [os.path.abspath(str(f)) for f in generated_files]
        abs_reference_files = [os.path.abspath(str(f)) for f in reference_files]

        missing_generated = [f for f in abs_generated_files if not os.path.exists(f)]
        if missing_generated:
            result["error_message"] = f"Generated file not found: {missing_generated[0]}"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        missing_reference = [f for f in abs_reference_files if not os.path.exists(f)]
        if missing_reference:
            result["error_message"] = f"Reference file not found: {missing_reference[0]}"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        if any(os.path.getsize(f) == 0 for f in abs_generated_files):
            result["error_message"] = "Generated file is empty"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        gen_size = sum(os.path.getsize(f) for f in abs_generated_files)

        if debug:
            print(f"[INFO] Top module: {top_module}")
            print(f"[INFO] Generated size: {gen_size} bytes")
            print(f"[INFO] Reference size: {sum(os.path.getsize(f) for f in abs_reference_files)} bytes")
            print(f"[INFO] Timeout: 3600s")

        # Multi-file designs report into the task folder instead of the source foldergenerate_yosys_script_baseonly_verilog
        task_path = getattr(args, "task_path", None) or os.path.dirname(str(generated_files[0]))


        # Where `` `include `` resolves from, and what is defined while reading.
        include_dirs = getattr(args, "include_dirs", None) or []
        defines = getattr(args, "defines", None) or []

        # The parser has to match the script: the two flows end in different Yosys
        # commands and report their verdict with different wording. `sat -verify` prints
        # SUCCESS!/FAIL!, `equiv_status` prints the $equiv cell counts -- reading one with
        # the other's parser makes every check look unparseable, and therefore failed.
        if args.hdl == "vhd":
            result["error_message"] = "EQV check on VHDL files not available"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            return result
        elif args.hdl == "v":
            script_content = generate_yosys_script_nst(
                abs_reference_files, abs_generated_files, top_module, include_dirs, defines
            )
            parse_yosys_output = parse_yosys_output_standard
            out_path = os.path.join(task_path, "nst_equiv_output.txt")
        else:
            script_content = generate_yosys_script_nst_slang(
                abs_reference_files, abs_generated_files, top_module, include_dirs, defines
            )
            parse_yosys_output = parse_yosys_output_standard
            out_path = os.path.join(task_path, "nst_equiv_output.txt")


        if debug:
            print(f"[INFO] Yosys Script (first 15 lines):")
            for i, line in enumerate(script_content.split("\n")[:15]):
                print(f"[INFO]  {i+1}: {line}")

        temp_dir = tempfile.mkdtemp(prefix=f"yosys_check_{os.getpid()}_")

        try:
            script_file_path = os.path.join(temp_dir, "equiv_check.ys")
            with open(script_file_path, "w") as f:
                f.write(script_content)

            if debug:
                print(f"[INFO] Working directory: {temp_dir}")
                print(f"[INFO] Starting Yosys verification...")

            yosys_result = subprocess.run(
                ["yosys", "-s", script_file_path],
                capture_output=True,
                text=True,
                timeout=3600,
                cwd=temp_dir,
            )

            result["eqy_return_code"] = yosys_result.returncode
            
            parsed = parse_yosys_output(yosys_result.stdout, yosys_result.stderr, out_path, debug)

            result["total_cells"] = parsed["total_cells"]
            result["proven_cells"] = parsed["proven_cells"]
            result["unproven_cells"] = parsed["unproven_cells"]
            result["unproven_signals"] = parsed["unproven_signals"]
            result["yosys_time"] = parsed["yosys_time"]

            # Compute cells coverage
            if result["total_cells"] is not None and result["total_cells"] > 0:
                if result["proven_cells"] is not None:
                    result["cells_coverage"] = (result["proven_cells"] / result["total_cells"]) * 100.0
                else:
                    result["cells_coverage"] = 0.0
            else:
                result["cells_coverage"] = None

            if parsed["success"]:
                result["equiv_passed"] = True
                result["error_message"] = ""
                result["equiv_method"] = "proven"
                # If Yosys reports "Equivalence successfully proven!", coverage must be 100%
                result["cells_coverage"] = 100.0
                if debug:
                    print(f"[INFO] Equivalence PROVEN ({result['proven_cells']}/{result['total_cells']} cells, 100.00% coverage)")
            
            # Only the SAT flow reports this, and only on an explicit FAIL!, so a genuine
            # mismatch is not recorded as Yosys having broken by the returncode branch below
            elif parsed.get("failed"):
                result["equiv_passed"] = False
                result["error_message"] = "SAT equivalence check failed"
                result["equiv_method"] = "failed"
                result["cells_coverage"] = 0.0
                if debug:
                    print(f"[INFO] Equivalence FAILED (SAT)")

            elif parsed["unproven_cells"] and parsed["unproven_cells"] > 0:
                result["equiv_passed"] = False
                result["error_message"] = f"Found {parsed['unproven_cells']} unproven cells"
                result["equiv_method"] = "failed"
                if debug:
                    print(f"[Info] Equivalence FAILED ({parsed['unproven_cells']} unproven cells, {result['cells_coverage']:.2f}% coverage)")
                    if parsed["unproven_signals"]:
                        print(f"[INFO] Unproven signals (first 3):")
                        for sig in parsed["unproven_signals"][:3]:
                            print(f"[INFO]       {sig}")
            
            elif yosys_result.returncode != 0:
                result["equiv_passed"] = False
                result["equiv_method"] = "error"
                error_detail = get_detailed_yosys_error(yosys_result.stdout, yosys_result.stderr)
                result["error_message"] = f"Yosys failed: {error_detail}"
                if result["cells_coverage"] is None:
                    result["cells_coverage"] = 0.0
                if debug:
                    print(f"[ERROR] Yosys error: {result['error_message']}")
            
            else:
                result["equiv_passed"] = False
                result["equiv_method"] = "error"
                result["error_message"] = "Could not parse equivalence result"
                if result["cells_coverage"] is None:
                    result["cells_coverage"] = 0.0
                if debug:
                    print(f"[WARNING] Unclear result from Yosys")
            print("DONE")

        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                if debug:
                    print(f"[WARNING]     Warning: Could not remove temp dir {temp_dir}: {e}")

    except subprocess.TimeoutExpired:
        # A timeout proves nothing either way, so `--timeout_is_pass` decides. The rule
        # completion flow defaults it to True so its numbers are unchanged, the agentic flow
        # to False because multi-file designs time out far more often. Callers without the
        # option, such as the --fnc benchmark build, keep True.
        timeout_is_pass = getattr(args, "timeout_is_pass", True)

        result["error_message"] = "Verification timeout after 3600s"
        result["equiv_passed"] = timeout_is_pass
        result["eqy_return_code"] = "timeout"
        result["equiv_method"] = "timeout"
        result["cells_coverage"] = 100.0 if timeout_is_pass else 0.0
        if debug:
                verdict = "Pass (100% coverage)" if timeout_is_pass else "Fail"
                print(f"[INFO] Yosys timeout after 3600s - treating as {verdict}")

    except FileNotFoundError:
        result["error_message"] = "Yosys not found - please install yosys"
        result["equiv_passed"] = False
        result["equiv_method"] = "error"
        result["cells_coverage"] = 0.0
        if debug:
                print(f"[ERROR]     ✗ {result['error_message']}")

    except Exception as e:
        result["error_message"] = f"Equivalence check error: {str(e)}"
        result["equiv_passed"] = False
        result["equiv_method"] = "error"
        result["cells_coverage"] = 0.0
        if debug:
                print(f"[ERROR]     ✗ {result['error_message']}")

    return result


def parse_yosys_output_standard(stdout: str, stderr: str, out_path, debug: bool = False) -> Dict:
    """Parse Yosys equiv_status output to extract results."""
    result = {
        "success": False,
        "total_cells": None,
        "proven_cells": None,
        "unproven_cells": None,
        "unproven_signals": [],
        "yosys_time": None,
    }
    
    output = stdout + "\n" + (stderr or "")
    
    total_match = re.search(r'Found\s+(\d+)\s+\$equiv\s+cells\s+in\s+miter', output)
    if total_match:
        result["total_cells"] = int(total_match.group(1))
    
    proven_match = re.search(
        r'Of\s+those\s+cells\s+(\d+)\s+are\s+proven\s+and\s+(\d+)\s+are\s+unproven',
        output
    )
    if proven_match:
        result["proven_cells"] = int(proven_match.group(1))
        result["unproven_cells"] = int(proven_match.group(2))
    
    if 'Equivalence successfully proven!' in output:
        result["success"] = True
    
    if result["unproven_cells"] and result["unproven_cells"] > 0:
        unproven_lines = re.findall(
            r'Unproven\s+\$equiv.*?:\s+(.+)',
            output
        )
        result["unproven_signals"] = unproven_lines
    
    timing_match = re.search(r'CPU:\s+user\s+([\d.]+)s', output)
    if timing_match:
        result["yosys_time"] = float(timing_match.group(1))
    
    if debug and result["total_cells"]:
            print(f"[INFO]     Parsed: {result['proven_cells']}/{result['total_cells']} cells proven")

    if debug:         print(f"[INFO] Saving at {out_path}")
    with open(out_path, "a") as op:
        op.write("=" * 30 + "\n")
        op.write(output)
        op.write("\n")
    
    return result


def parse_yosys_output_sat(stdout: str, stderr: str, out_path, debug: bool = False) -> Dict:
    """Parse the SAT flow's output to extract results.

    `sat -verify` exits nonzero when the proof fails, which the returncode alone cannot
    tell apart from Yosys itself having broken. `failed` is therefore set only when the
    output says the proof did not hold, so a real mismatch is reported as a failed check
    rather than an error.
    """
    result = {
        "success": False,
        "failed": False,
        "total_cells": None,
        "proven_cells": None,
        "unproven_cells": None,
        "unproven_signals": [],
        "yosys_time": None,
    }

    output = (stdout or "") + "\n" + (stderr or "")

    # `sat -verify` aborts with this error instead of printing FAIL! when the proof does
    # not hold, so both wordings mean the same thing: the designs are not equivalent
    proof_failed = "FAIL!" in output or "Called with -verify and proof did fail" in output

    if proof_failed:
        result["success"] = False
        result["failed"] = True
        if debug:
            print(f"[INFO] SAT equivalence FAILED")

    elif "SUCCESS!" in output:
        result["success"] = True
        if debug:
            print(f"[INFO] SAT equivalence proven")

    else:
        if debug:             print(f"[WARNING] Didn't return Success nor Failed")
    
    if debug:         print(f"[INFO] Saving at {out_path}")
    with open(out_path, "a") as op:
        op.write("=" * 30 + "\n")
        op.write(output)
        op.write("\n")

    return result


def get_detailed_yosys_error(stdout: str, stderr: str) -> str:
    """Extract specific error details from Yosys output."""
    output = stdout + "\n" + (stderr or "")
    
    error_patterns = [
        (r"ERROR: (.+?)(?:\n|$)", 1),
        (r"error: (.+?)(?:\n|$)", 1),
        (r"(Module .+ not found)", 0),
        (r"(Identifier .+ is implicitly declared)", 0),
        (r"(syntax error[^\n]*)", 0),
        (r"prep: (.+?)(?:\n|$)", 1),
    ]
    
    for pattern, group in error_patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            error_text = match.group(group).strip()
            return error_text[:200]
    
    for line in output.split("\n"):
        line_lower = line.lower()
        if "error" in line_lower or "failed" in line_lower:
            cleaned = line.strip()
            if len(cleaned) > 10:
                return cleaned[:200]
    
    return "Verification failed"
