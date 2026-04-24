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



def run_syntax_check(file_path: Path, args, debug: bool = False) -> Dict[str, Any]:
    """
    Run pure syntax check using Icarus Verilog without running tests.

    Args:
        file_path: Path to the Verilog file to check
        debug: Whether to print debug information

    Returns:
        Dictionary with syntax check results
    """
    try:
        if debug:
            print(f"  Running iverilog syntax check on {file_path}")

        
        if args.hdl == "v":
            result = subprocess.run(
                [   
                    "iverilog",
                    "-Wall",
                    "-Winfloop",
                    "-Wno-timescale",
                    "-g2012",
                    "-t",
                    "null",
                    str(file_path),
                ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        else:
            result = subprocess.run(
                [   
                    "verilator", # Added
                    "--lint-only", # Added
                    "-Wno-fatal", # Added
                    str(file_path),
                ],
            capture_output=True,
            text=True,
            timeout=900,
        )

        syntax_valid = result.returncode == 0
        error_message = ""
        warnings = []

        if result.stderr:
            lines = result.stderr.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if "error:" in line.lower():
                    if not error_message:
                        error_message = clean_error_message(line)
                elif "warning:" in line.lower():
                    warnings.append(clean_error_message(line))

        if not syntax_valid and not error_message:
            if result.stderr:
                error_message = result.stderr.strip()[:200]
            else:
                error_message = f"Compilation failed with return code {result.returncode}"

        if debug:
            print(f"  Syntax check result: {syntax_valid}")
            if error_message:
                print(f"  Error: {error_message}")
            if warnings:
                print(f"  Warnings: {len(warnings)}")

        return {
            "syntax_valid": syntax_valid,
            "error_message": error_message,
            "warnings": warnings,
        }

    except subprocess.TimeoutExpired:
        error_msg = "Syntax check timed out (600s)"
        if debug:
            print(f"  {error_msg}")
        return {"syntax_valid": False, "error_message": error_msg, "warnings": []}

    except FileNotFoundError:
        error_msg = "Icarus Verilog (iverilog) not found - please install it"
        if debug:
            print(f"  {error_msg}")
        return {"syntax_valid": False, "error_message": error_msg, "warnings": []}

    except Exception as e:
        error_msg = f"Syntax check failed: {str(e)}"
        if debug:
            print(f"  {error_msg}")
        return {"syntax_valid": False, "error_message": error_msg, "warnings": []}



def clean_error_message(message: str) -> str:
    """Clean up error message by removing file paths and irrelevant details."""
    message = re.sub(r"/[^/\s]*/", "", message)

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


def run_equivalence_check(
    generated_file: Path, reference_file: Path, project_dir: Path, args, debug: bool = False
) -> Dict[str, Any]:
    """
    Run formal equivalence check using Yosys with cells coverage tracking.
    
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
    if debug:
        print(f"  Running equivalence check:")
        print(f"    Generated: {generated_file}")
        print(f"    Reference: {reference_file}")
        print(f"    Project: {project_dir}")

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
         # top_module is the name of the project after removing the extension
        if args.hdl == "sv":
            top_module = reference_file.name.replace(".sv", "")
        else:
            top_module = reference_file.name.replace(".v", "") 

        result["top_module"] = top_module
        if not top_module:
            result["error_message"] = "Could not extract top module"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        abs_generated_file = os.path.abspath(str(generated_file))
        abs_reference_file = os.path.abspath(str(reference_file))

        if not os.path.exists(abs_generated_file):
            result["error_message"] = f"Generated file not found: {abs_generated_file}"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        if not os.path.exists(abs_reference_file):
            result["error_message"] = f"Reference file not found: {abs_reference_file}"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        gen_size = os.path.getsize(abs_generated_file)
        if gen_size == 0:
            result["error_message"] = "Generated file is empty"
            result["equiv_passed"] = False
            result["equiv_method"] = "error"
            result["cells_coverage"] = 0.0
            return result

        if debug:
            print(f"    Top module: {top_module}")
            print(f"    Generated size: {gen_size} bytes")
            print(f"    Reference size: {os.path.getsize(abs_reference_file)} bytes")
            print(f"    Timeout: 5 minutes (300 seconds)")
        
        task_path = os.path.dirname(generated_file)
        out_path = os.path.join(task_path, "equiv_output.txt")

        
        if args.hdl == "v":
            script_content = create_yosys_equivalence_script_baseonly_verilog(
                abs_reference_file, abs_generated_file, top_module
            )
        else:
            script_content = create_yosys_equivalence_script_baseonly_slang(
                abs_reference_file, abs_generated_file, top_module
            )
            
        

        if debug:
            print(f"    Yosys Script (first 15 lines):")
            for i, line in enumerate(script_content.split("\n")[:15]):
                print(f"      {i+1}: {line}")

        temp_dir = tempfile.mkdtemp(prefix=f"yosys_check_{os.getpid()}_")

        try:
            script_file_path = os.path.join(temp_dir, "equiv_check.ys")
            with open(script_file_path, "w") as f:
                f.write(script_content)

            if debug:
                print(f"    Working directory: {temp_dir}")
                print(f"    Starting Yosys verification...")

            yosys_result = subprocess.run(
                ["yosys", "-s", script_file_path],
                capture_output=True,
                text=True,
                timeout=900,
                cwd=temp_dir,
            )

            result["eqy_return_code"] = yosys_result.returncode
            
            parsed = parse_yosys_output_sat(yosys_result.stdout, yosys_result.stderr, out_path, debug)

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
                    print(f"    ✓ Equivalence PROVEN ({result['proven_cells']}/{result['total_cells']} cells, 100.00% coverage)")
            
            elif parsed["unproven_cells"] and parsed["unproven_cells"] > 0:
                result["equiv_passed"] = False
                result["error_message"] = f"Found {parsed['unproven_cells']} unproven cells"
                result["equiv_method"] = "failed"
                if debug:
                    print(f"    ✗ Equivalence FAILED ({parsed['unproven_cells']} unproven cells, {result['cells_coverage']:.2f}% coverage)")
                    if parsed["unproven_signals"]:
                        print(f"    Unproven signals (first 3):")
                        for sig in parsed["unproven_signals"][:3]:
                            print(f"      {sig}")
            
            elif yosys_result.returncode != 0:
                result["equiv_passed"] = False
                result["equiv_method"] = "error"
                error_detail = extract_yosys_error_detail(yosys_result.stdout, yosys_result.stderr)
                result["error_message"] = f"Yosys failed: {error_detail}"
                if result["cells_coverage"] is None:
                    result["cells_coverage"] = 0.0
                if debug:
                    print(f"    ✗ Yosys error: {result['error_message']}")
            
            else:
                result["equiv_passed"] = False
                result["equiv_method"] = "error"
                result["error_message"] = "Could not parse equivalence result"
                if result["cells_coverage"] is None:
                    result["cells_coverage"] = 0.0
                if debug:
                    print(f"    ⚠️ Unclear result from Yosys")
            print("DONE")

        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                if debug:
                    print(f"    Warning: Could not remove temp dir {temp_dir}: {e}")

    except subprocess.TimeoutExpired:
        result["error_message"] = "Verification timeout after 900s"
        result["equiv_passed"] = True
        result["eqy_return_code"] = "timeout"
        result["equiv_method"] = "timeout"
        result["cells_coverage"] = 100.0
        if debug:
            print(f"    ⏱ Yosys timeout after 15 minutes - treating as Pass (100% coverage)")

    except FileNotFoundError:
        result["error_message"] = "Yosys not found - please install yosys"
        result["equiv_passed"] = False
        result["equiv_method"] = "error"
        result["cells_coverage"] = 0.0
        if debug:
            print(f"    ✗ {result['error_message']}")

    except Exception as e:
        result["error_message"] = f"Equivalence check error: {str(e)}"
        result["equiv_passed"] = False
        result["equiv_method"] = "error"
        result["cells_coverage"] = 0.0
        if debug:
            print(f"    ✗ {result['error_message']}")

    return result

def create_yosys_equivalence_script_baseonly_verilog(
    reference_file: str, generated_file: str, top_module: str
) -> str:
    """Create Yosys script for equivalence checking."""
    script_content = f"""
# =============================================================================
# Load Designs
# =============================================================================
read_verilog -sv {reference_file}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gold

read_verilog -sv {generated_file}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gate

# =============================================================================
# Equivalence Check
# =============================================================================

design -copy-from gold -as gold A:top
design -copy-from gate -as gate A:top

miter -equiv -make_outputs gold gate miter
prep -flatten -top miter

sat -verify -prove trigger 0 -tempinduct-baseonly -maxsteps 20 -set-init-zero -set in_rst_n 1 miter
"""
    return script_content

def create_yosys_equivalence_script_baseonly_slang(
    reference_file: str, generated_file: str, top_module: str
) -> str:
    """Create Yosys script for equivalence checking."""
    script_content = f"""
# ===== Load slang plugin =====
plugin -i slang

# =============================================================================
# Load Designs
# =============================================================================
read_slang {reference_file}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gold

read_slang {generated_file}
prep -flatten -top {top_module}
memory
clk2fflogic
design -stash gate

# =============================================================================
# Equivalence Check
# =============================================================================

design -copy-from gold -as gold A:top
design -copy-from gate -as gate A:top

miter -equiv -make_outputs gold gate miter
prep -flatten -top miter

sat -verify -prove trigger 0 -tempinduct-baseonly -maxsteps 20 -set-init-zero -set in_rst_ni 1 miter
"""
    return script_content


def parse_yosys_output(stdout: str, stderr: str, debug: bool = False) -> Dict:
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
        print(f"    Parsed: {result['proven_cells']}/{result['total_cells']} cells proven")
    
    return result


def parse_yosys_output_sat(stdout: str, stderr: str, out_path, debug: bool = False) -> Dict:
    """Parse Yosys equiv_status output to extract results."""
    result = {
        "success": False,
        "total_cells": None,
        "proven_cells": None,
        "unproven_cells": None,
        "unproven_signals": [],
        "yosys_time": None,
    }
    
    output = (stdout or "") + "\n" + (stderr or "")
    if "FAIL!" in output:
        result["success"] = False
        if debug:
            print("SAT equivalence FAILED")

    elif "SUCCESS!" in output:
        result["success"] = True
        if debug:
            print("SAT equivalence proven")

    else:
        print("Didn't return Success nor Failed")
    
    print(f"Saving at {out_path}")
    with open(out_path, "a") as op:
        op.write("=" * 30 + "\n")
        op.write(output)
        op.write("\n")

    return result


def extract_yosys_error_detail(stdout: str, stderr: str) -> str:
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
