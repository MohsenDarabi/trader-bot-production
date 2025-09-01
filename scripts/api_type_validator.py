#!/usr/bin/env python3
"""
API Type Validator
Validates that code properly handles API response types according to the documented schema.
Catches bugs like using string values as numbers without conversion.
"""
import os
import re
import sys
import yaml
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass


@dataclass
class TypeViolation:
    """Represents a type handling violation"""
    file_path: str
    line_number: int
    field_name: str
    api_type: str
    expected_conversion: str
    code_snippet: str
    violation_type: str


class APITypeValidator:
    """Validates API type usage in Python code"""
    
    def __init__(self, schema_path: str):
        """Initialize with API schema"""
        self.schema_path = schema_path
        self.schema = self._load_schema()
        self.violations = []
        self.files_checked = 0
        self.total_api_usages = 0
        
    def _load_schema(self) -> Dict:
        """Load API schema from YAML file"""
        with open(self.schema_path, 'r') as f:
            return yaml.safe_load(f)
    
    def _get_all_string_fields(self) -> Dict[str, str]:
        """Extract all string-type fields that need conversion"""
        string_fields = {}
        
        for endpoint_name, endpoint_data in self.schema['endpoints'].items():
            for field in endpoint_data.get('response_fields', []):
                if field['type'] == 'string' and field.get('converts_to'):
                    field_name = field['name']
                    string_fields[field_name] = field.get('converts_to', 'float')
        
        return string_fields
    
    def _check_file(self, file_path: str) -> List[TypeViolation]:
        """Check a single Python file for type violations"""
        violations = []
        string_fields = self._get_all_string_fields()
        
        # Skip non-Python files
        if not file_path.endswith('.py'):
            return violations
        
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
        except:
            return violations
        
        self.files_checked += 1
        
        for line_num, line in enumerate(lines, 1):
            # Skip comments
            if line.strip().startswith('#'):
                continue
            
            # Check for each string field
            for field_name, converts_to in string_fields.items():
                # Pattern 1: Direct .get() without safe_float
                # Bad: market_info.get('min_amount')
                # Good: safe_float(market_info.get('min_amount'))
                direct_get_pattern = rf"\.get\(['\"]?{field_name}['\"]?"
                
                if re.search(direct_get_pattern, line):
                    self.total_api_usages += 1
                    
                    # Check if it's wrapped in safe_float/safe_int
                    safe_pattern = rf"safe_(float|int)\([^)]*\.get\(['\"]?{field_name}['\"]?"
                    
                    if not re.search(safe_pattern, line):
                        # Check for dangerous operations
                        danger_patterns = [
                            (rf"float\([^)]*\.get\(['\"]?{field_name}['\"]?", "Direct float() conversion"),
                            (rf"int\([^)]*\.get\(['\"]?{field_name}['\"]?", "Direct int() conversion"),
                            (rf"\.get\(['\"]?{field_name}['\"]?[^)]*\)\s*[\*\+\-\/]", "Math operation on string"),
                            (rf"\.get\(['\"]?{field_name}['\"]?[^)]*\)\s*[<>=!]", "Comparison on string"),
                            (rf"if\s+[^:]*\.get\(['\"]?{field_name}['\"]?[^)]*\):", "Boolean check on string"),
                        ]
                        
                        for pattern, violation_type in danger_patterns:
                            if re.search(pattern, line):
                                violations.append(TypeViolation(
                                    file_path=file_path,
                                    line_number=line_num,
                                    field_name=field_name,
                                    api_type='string',
                                    expected_conversion=f'safe_float() or safe_int()',
                                    code_snippet=line.strip(),
                                    violation_type=violation_type
                                ))
                                break
                
                # Pattern 2: Direct dictionary access
                # Bad: response['min_amount']
                direct_access_pattern = rf"\[['\"]?{field_name}['\"]?\]"
                
                if re.search(direct_access_pattern, line):
                    self.total_api_usages += 1
                    
                    # Check if it's wrapped in safe_float/safe_int
                    safe_bracket_pattern = rf"safe_(float|int)\([^)]*\[['\"]?{field_name}['\"]?\]"
                    
                    if not re.search(safe_bracket_pattern, line):
                        # Check for dangerous patterns
                        if re.search(rf"\[['\"]?{field_name}['\"]?\]\s*[\*\+\-\/<>=!]", line):
                            violations.append(TypeViolation(
                                file_path=file_path,
                                line_number=line_num,
                                field_name=field_name,
                                api_type='string',
                                expected_conversion=f'safe_float() or safe_int()',
                                code_snippet=line.strip(),
                                violation_type="Direct bracket access without conversion"
                            ))
        
        return violations
    
    def validate_directory(self, directory: str, exclude_dirs: List[str] = None) -> bool:
        """Validate all Python files in a directory"""
        exclude_dirs = exclude_dirs or ['venv', 'env', '.git', '__pycache__', 'node_modules']
        
        path = Path(directory)
        
        for py_file in path.rglob('*.py'):
            # Skip excluded directories
            if any(excluded in str(py_file) for excluded in exclude_dirs):
                continue
            
            file_violations = self._check_file(str(py_file))
            self.violations.extend(file_violations)
        
        return len(self.violations) == 0
    
    def print_report(self, verbose: bool = True):
        """Print validation report"""
        print(f"\n{'='*80}")
        print(f"API Type Validation Report")
        print(f"{'='*80}")
        print(f"Files checked: {self.files_checked}")
        print(f"Total API field usages found: {self.total_api_usages}")
        print(f"Type violations found: {len(self.violations)}")
        
        if self.violations:
            print(f"\n{'='*80}")
            print("TYPE VIOLATIONS FOUND:")
            print(f"{'='*80}")
            
            # Group violations by file
            violations_by_file = {}
            for v in self.violations:
                if v.file_path not in violations_by_file:
                    violations_by_file[v.file_path] = []
                violations_by_file[v.file_path].append(v)
            
            for file_path, file_violations in violations_by_file.items():
                # Show relative path for readability
                rel_path = os.path.relpath(file_path)
                print(f"\n📁 {rel_path}")
                print(f"   {len(file_violations)} violation(s)")
                
                for v in file_violations:
                    print(f"\n   Line {v.line_number}: {v.violation_type}")
                    print(f"   Field: '{v.field_name}' (API returns: {v.api_type})")
                    print(f"   Code: {v.code_snippet[:80]}...")
                    print(f"   Fix: Wrap with {v.expected_conversion}")
                    
                    # Suggest fix
                    if ".get(" in v.code_snippet:
                        field_access = f".get('{v.field_name}'"
                        suggested = v.code_snippet.replace(
                            field_access, 
                            f"safe_float({field_access}"
                        )
                        # Balance parentheses
                        open_count = suggested.count('(')
                        close_count = suggested.count(')')
                        if open_count > close_count:
                            suggested += ')' * (open_count - close_count)
                        print(f"   Suggested: {suggested[:80]}...")
        else:
            print(f"\n✅ No type violations found! All API fields are properly converted.")
        
        print(f"\n{'='*80}\n")
        
        return len(self.violations) == 0


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Validate API type usage in Python code')
    parser.add_argument(
        '--schema', 
        default='api_docs/coinex_api_schema.yaml',
        help='Path to API schema file'
    )
    parser.add_argument(
        '--directory',
        default='src',
        help='Directory to validate'
    )
    parser.add_argument(
        '--exclude',
        nargs='*',
        default=['venv', 'env', '.git', '__pycache__'],
        help='Directories to exclude'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Only show summary'
    )
    parser.add_argument(
        '--exit-code',
        action='store_true',
        help='Exit with non-zero code if violations found'
    )
    
    args = parser.parse_args()
    
    # Check if schema file exists
    if not os.path.exists(args.schema):
        print(f"Error: Schema file not found: {args.schema}")
        sys.exit(1)
    
    # Create validator
    validator = APITypeValidator(args.schema)
    
    # Validate directory
    success = validator.validate_directory(args.directory, args.exclude)
    
    # Print report
    validator.print_report(verbose=not args.quiet)
    
    # Exit with appropriate code
    if args.exit_code and not success:
        sys.exit(1)
    
    sys.exit(0)


if __name__ == '__main__':
    main()