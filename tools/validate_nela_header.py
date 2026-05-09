#!/usr/bin/env python3
"""
Validate and sync LLM-optimized NELA file headers.

This tool ensures that the index header in NELA files always stays synchronized
with actual code sections. It's designed to enforce that LLMs update the header
whenever the file structure changes.

Usage:
    python tools/validate_nela_header.py examples/wolf_game.nela [--regenerate]
    
    flags:
      --regenerate    Overwrite header with computed index (test-only)
"""

import re
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Set


class NelaHeaderValidator:
    """Validate and manage NELA file headers."""
    
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.content = self.filepath.read_text()
        self.lines = self.content.split('\n')
        
    def extract_sections(self) -> Dict[str, Tuple[int, str, List[str]]]:
        """
        Extract all @SECTION_ markers and their associated functions.
        
        Returns:
            {section_name: (start_line, end_marker, [function_names])}
        """
        sections = {}
        pattern = r'^-- ── @SECTION_(\w+) \[(\d+)-(\d+)\]'
        func_pattern = r'^def (\w+)'
        
        current_section = None
        section_funcs: Dict[str, List[str]] = {}
        
        for i, line in enumerate(self.lines, 1):
            # Check for section marker
            m = re.match(pattern, line)
            if m:
                current_section = m.group(1)
                start = int(m.group(2))
                end = int(m.group(3))
                sections[current_section] = (start, end, [])
                section_funcs[current_section] = []
            
            # Track functions in current section
            if current_section and re.match(func_pattern, line):
                func_name = re.match(func_pattern, line).group(1)
                section_funcs[current_section].append(func_name)
        
        # Attach function lists to sections
        for section in sections:
            sections[section] = (sections[section][0], sections[section][1], 
                                section_funcs.get(section, []))
        
        return sections
    
    def extract_header_index(self) -> Dict[str, Tuple[int, int, str]]:
        """
        Parse the header index to extract declared sections.
        
        Returns:
            {section_name: (start_line, end_line, function_list_str)}
        """
        header_index = {}
        # Pattern: "--   SECTION_NAME                 [ 51- 65]      func1 | func2 | func3"
        # Flexible whitespace for alignment
        pattern = r'^--\s+SECTION_(\w+)\s+\[\s*(\d+)\s*-\s*(\d+)\s*\]\s+(.+)$'
        
        in_header = False
        for line in self.lines:
            if 'SECTION INDEX FOR LLM NAVIGATION' in line:
                in_header = True
                continue
            if in_header and line.strip() == '--':
                # End of index section
                break
            
            if in_header:
                m = re.match(pattern, line)
                if m:
                    section_name = m.group(1)
                    start = int(m.group(2))
                    end = int(m.group(3))
                    funcs_str = m.group(4).strip()
                    header_index[section_name] = (start, end, funcs_str)
        
        return header_index
    
    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate that header index matches actual code structure.
        
        Returns:
            (is_valid, error_list)
        """
        errors = []
        actual = self.extract_sections()
        declared = self.extract_header_index()
        
        # Check all declared sections exist
        for section in declared:
            if section not in actual:
                errors.append(f"SECTION_{section} declared in header but not found in code")
        
        # Check all actual sections are declared
        for section in actual:
            if section not in declared:
                errors.append(f"SECTION_{section} found in code but not in header index")
        
        # Check line numbers match
        for section in declared:
            if section in actual:
                decl_start, decl_end, _ = declared[section]
                actual_start, actual_end, _ = actual[section]
                
                if decl_start != actual_start or decl_end != actual_end:
                    errors.append(
                        f"SECTION_{section}: header says [{decl_start}-{decl_end}], "
                        f"actual code is [{actual_start}-{actual_end}]"
                    )
        
        return len(errors) == 0, errors
    
    def generate_index_header(self) -> str:
        """Generate a corrected index header based on actual code."""
        sections = self.extract_sections()
        
        lines = [
            "-- ============================================================================",
            "-- NELA v0.11 — Wolfenstein raycaster + enemies + textures",
            "-- LLM-OPTIMIZED SINGLE-FILE ARCHITECTURE",
            "-- ============================================================================",
            "-- ",
            "-- SECTION INDEX FOR LLM NAVIGATION (auto-maintained):",
        ]
        
        # Sort by start line
        sorted_sections = sorted(sections.items(), key=lambda x: x[1][0])
        for section, (start, end, funcs) in sorted_sections:
            func_str = " | ".join(funcs) if funcs else "?"
            lines.append(f"--   SECTION_{section:<20} [{start:3d}-{end:3d}]      {func_str}")
        
        lines.extend([
            "--",
            "-- ARCHITECTURE:",
            "--   Mission: ALL game logic in NELA-S. Python is I/O-only (framebuffer + input).",
            "--   State: [px, py, angle] (px,py floats; angle int [0,359])",
            "--   Map: flat list, 1D index = x + y*w",
            "--   Render Output: 40x21 grid of shade IDs [0-13]:",
            "--     0=ceiling, 1=floor, 2-4=wall (shade), 5=enemy, 6-13=textured walls",
            "--   I/O Model: IOToken linear threading (io_print/io_key callbacks)",
            "--   Textures: 3x 16x16 quantized shade tables (brick/planks/metal)",
            "--   Enemies: [x, y, alert] with LOS+patrol AI, rendering as red shade-5 pixels",
            "--",
            "-- INVARIANTS (LLM enforces when editing):",
            "--   - All functions must be declared before use (no forward refs)",
            "--   - All game logic stays in NELA-S (no Python sprite code)",
            "--   - Texture lookups stay in frame_cell (no external rendering)",
            "--   - Enemy LOS uses fat-wall checks is_wall_fat (not raycasting)",
            "--   - Coordinates: floats for position, ints for grid indices, angles normalized [0,359]",
            "--   - After edits, update the index comments above to match current line numbers",
            "--",
            "-- VERSION HISTORY:",
            "--   v0.11: Enemies + minimap + textures + LLM-optimized header",
            "--   v0.10: doors/steps tracking + render_packet",
            "--   v0.9:  IOToken game_loop (all logic in NELA-S)",
            "--   v0.8:  array/aset/use_door",
            "--   v0.7:  raycasting baseline",
            "--",
            "-- COORDINATE SYSTEM:",
            "--   Position:  float units (1 cell = 1.0)",
            "--   Angles:    integer degrees [0, 359]",
            "--     angle 0   → south (+y)",
            "--     angle 90  → east  (+x)",
            "--     angle 180 → north (-y)",
            "--     angle 270 → west  (-x)",
        ])
        
        return "\n".join(lines)
    
    def regenerate_header(self) -> bool:
        """Regenerate the header (test-only; use cautiously)."""
        new_header = self.generate_index_header()
        
        # Find where the old header ends
        end_idx = 0
        for i, line in enumerate(self.lines):
            if line.startswith('-- ── @SECTION_'):
                end_idx = i
                break
        
        if end_idx == 0:
            print("ERROR: Could not find end of header (@SECTION_ marker)")
            return False
        
        new_content = new_header + "\n\n" + "\n".join(self.lines[end_idx:])
        self.filepath.write_text(new_content)
        return True


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/validate_nela_header.py <file.nela> [--regenerate]")
        sys.exit(1)
    
    filepath = sys.argv[1]
    regenerate = "--regenerate" in sys.argv
    
    validator = NelaHeaderValidator(filepath)
    is_valid, errors = validator.validate()
    
    if is_valid:
        print(f"✓ {filepath}: Header is valid and synchronized")
        sys.exit(0)
    else:
        print(f"✗ {filepath}: Header is OUT OF SYNC")
        print("\nErrors:")
        for err in errors:
            print(f"  - {err}")
        
        if regenerate:
            print("\nAttempting to regenerate header...")
            if validator.regenerate_header():
                print(f"✓ Header regenerated in {filepath}")
                sys.exit(0)
            else:
                print("✗ Failed to regenerate header")
                sys.exit(1)
        else:
            print("\nRun with --regenerate to auto-fix (test-only)")
            sys.exit(1)


if __name__ == "__main__":
    main()
