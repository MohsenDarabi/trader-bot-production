#!/usr/bin/env python3
"""
API Usage Scanner
Scans the codebase to find all API field usages and generates a report.
Helps maintain accurate API documentation and find undocumented fields.
"""
import os
import re
import sys
import json
import yaml
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict


class APIUsageScanner:
    """Scans codebase for API field usage patterns"""
    
    def __init__(self, schema_path: str = None):
        """Initialize scanner with optional schema"""
        self.schema_path = schema_path
        self.schema = self._load_schema() if schema_path else None
        self.api_field_usages = defaultdict(lambda: defaultdict(list))
        self.conversion_patterns = defaultdict(int)
        self.files_scanned = 0
        
    def _load_schema(self) -> Dict:
        """Load API schema from YAML file"""
        if self.schema_path and os.path.exists(self.schema_path):
            with open(self.schema_path, 'r') as f:
                return yaml.safe_load(f)
        return {}
    
    def _extract_api_fields(self, line: str, file_path: str, line_num: int) -> List[Tuple[str, str, str]]:
        """Extract API field accesses from a line of code"""
        fields = []
        
        # Pattern 1: .get('field_name') or .get("field_name")
        get_pattern = r"\.get\(['\"]([^'\"]+)['\"][^)]*\)"
        for match in re.finditer(get_pattern, line):
            field_name = match.group(1)
            context = self._determine_conversion_context(line, field_name)
            fields.append((field_name, 'get', context))
        
        # Pattern 2: ['field_name'] or ["field_name"]
        bracket_pattern = r"\[['\"]([^'\"]+)['\"]\]"
        for match in re.finditer(bracket_pattern, line):
            field_name = match.group(1)
            # Check if this looks like an API response access
            if any(api_var in line for api_var in ['response', 'data', 'result', 'market_info', 
                                                    'position', 'order', 'balance', 'ticker']):
                context = self._determine_conversion_context(line, field_name)
                fields.append((field_name, 'bracket', context))
        
        return fields
    
    def _determine_conversion_context(self, line: str, field_name: str) -> str:
        """Determine how a field is being converted/used"""
        # Check for safe_float conversion
        if f"safe_float(" in line and field_name in line:
            return "safe_float"
        
        # Check for safe_int conversion
        if f"safe_int(" in line and field_name in line:
            return "safe_int"
        
        # Check for direct float conversion (dangerous)
        if f"float(" in line and field_name in line:
            return "DANGER: float()"
        
        # Check for direct int conversion (dangerous)
        if f"int(" in line and field_name in line:
            return "DANGER: int()"
        
        # Check for string operations
        if re.search(rf"['\"].*{field_name}", line) or re.search(rf"{field_name}.*['\"]", line):
            return "string_operation"
        
        # Check for math operations (dangerous if string)
        if re.search(rf"{field_name}[^)]*[\*\+\-\/]", line) or re.search(rf"[\*\+\-\/][^(]*{field_name}", line):
            return "DANGER: math_operation"
        
        # Check for comparisons (dangerous if string)
        if re.search(rf"{field_name}[^)]*[<>=!]", line) or re.search(rf"[<>=!][^(]*{field_name}", line):
            return "DANGER: comparison"
        
        # Check for boolean context
        if re.search(rf"if\s+.*{field_name}", line) or re.search(rf"while\s+.*{field_name}", line):
            return "boolean_context"
        
        # Check for assignment
        if "=" in line and line.index("=") > line.index(field_name) if field_name in line else False:
            return "assignment"
        
        return "unknown"
    
    def _scan_file(self, file_path: str) -> Dict:
        """Scan a single Python file for API usage"""
        if not file_path.endswith('.py'):
            return {}
        
        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
        except:
            return {}
        
        self.files_scanned += 1
        file_usages = defaultdict(list)
        
        for line_num, line in enumerate(lines, 1):
            # Skip comments
            if line.strip().startswith('#'):
                continue
            
            # Extract API fields
            fields = self._extract_api_fields(line, file_path, line_num)
            
            for field_name, access_type, context in fields:
                # Record usage
                self.api_field_usages[field_name][context].append({
                    'file': os.path.relpath(file_path),
                    'line': line_num,
                    'access_type': access_type,
                    'code': line.strip()[:100]  # First 100 chars
                })
                
                # Count conversion patterns
                self.conversion_patterns[context] += 1
                
                file_usages[field_name].append((line_num, context))
        
        return file_usages
    
    def scan_directory(self, directory: str, exclude_dirs: List[str] = None):
        """Scan all Python files in a directory"""
        exclude_dirs = exclude_dirs or ['venv', 'env', '.git', '__pycache__', 'node_modules']
        
        path = Path(directory)
        
        for py_file in path.rglob('*.py'):
            # Skip excluded directories
            if any(excluded in str(py_file) for excluded in exclude_dirs):
                continue
            
            self._scan_file(str(py_file))
    
    def generate_report(self) -> Dict:
        """Generate comprehensive usage report"""
        report = {
            'summary': {
                'files_scanned': self.files_scanned,
                'unique_fields': len(self.api_field_usages),
                'total_usages': sum(len(usages) for contexts in self.api_field_usages.values() 
                                  for usages in contexts.values()),
                'conversion_patterns': dict(self.conversion_patterns)
            },
            'fields': {},
            'dangerous_usages': [],
            'undocumented_fields': []
        }
        
        # Get documented fields from schema
        documented_fields = set()
        if self.schema:
            for endpoint in self.schema.get('endpoints', {}).values():
                for field in endpoint.get('response_fields', []):
                    documented_fields.add(field['name'])
        
        # Analyze each field
        for field_name, contexts in self.api_field_usages.items():
            field_info = {
                'total_usages': sum(len(usages) for usages in contexts.values()),
                'conversion_methods': {},
                'dangerous': False
            }
            
            # Check each context
            for context, usages in contexts.items():
                field_info['conversion_methods'][context] = len(usages)
                
                # Flag dangerous usages
                if 'DANGER' in context:
                    field_info['dangerous'] = True
                    for usage in usages[:3]:  # First 3 examples
                        report['dangerous_usages'].append({
                            'field': field_name,
                            'issue': context,
                            'file': usage['file'],
                            'line': usage['line'],
                            'code': usage['code']
                        })
            
            # Check if field is documented
            if field_name not in documented_fields and self.schema:
                report['undocumented_fields'].append(field_name)
            
            report['fields'][field_name] = field_info
        
        return report
    
    def print_report(self, report: Dict, verbose: bool = True):
        """Print formatted report"""
        print(f"\n{'='*80}")
        print(f"API Usage Scan Report")
        print(f"{'='*80}")
        
        # Summary
        print(f"\n📊 Summary:")
        print(f"  Files scanned: {report['summary']['files_scanned']}")
        print(f"  Unique API fields found: {report['summary']['unique_fields']}")
        print(f"  Total field usages: {report['summary']['total_usages']}")
        
        # Conversion patterns
        print(f"\n🔄 Conversion Patterns:")
        for pattern, count in sorted(report['summary']['conversion_patterns'].items(), 
                                    key=lambda x: x[1], reverse=True):
            emoji = "✅" if pattern.startswith("safe_") else "⚠️" if "DANGER" in pattern else "ℹ️"
            print(f"  {emoji} {pattern}: {count} usages")
        
        # Dangerous usages
        if report['dangerous_usages']:
            print(f"\n⚠️ DANGEROUS USAGES FOUND:")
            print(f"  {len(report['dangerous_usages'])} potentially buggy field accesses")
            
            if verbose:
                for i, danger in enumerate(report['dangerous_usages'][:10], 1):
                    print(f"\n  {i}. Field '{danger['field']}' - {danger['issue']}")
                    print(f"     File: {danger['file']}:{danger['line']}")
                    print(f"     Code: {danger['code']}")
                
                if len(report['dangerous_usages']) > 10:
                    print(f"\n  ... and {len(report['dangerous_usages']) - 10} more")
        
        # Undocumented fields
        if report['undocumented_fields']:
            print(f"\n📝 Undocumented Fields:")
            print(f"  Found {len(report['undocumented_fields'])} fields not in schema:")
            for field in sorted(report['undocumented_fields'])[:20]:
                print(f"    - {field}")
            
            if len(report['undocumented_fields']) > 20:
                print(f"    ... and {len(report['undocumented_fields']) - 20} more")
        
        # Top used fields
        if verbose:
            print(f"\n🔝 Top 10 Most Used Fields:")
            sorted_fields = sorted(report['fields'].items(), 
                                 key=lambda x: x[1]['total_usages'], 
                                 reverse=True)
            for field_name, info in sorted_fields[:10]:
                danger_flag = "⚠️" if info['dangerous'] else "✅"
                print(f"  {danger_flag} {field_name}: {info['total_usages']} usages")
                
                # Show conversion breakdown
                for method, count in sorted(info['conversion_methods'].items(), 
                                          key=lambda x: x[1], reverse=True):
                    print(f"      {method}: {count}")
        
        print(f"\n{'='*80}\n")
    
    def save_report(self, report: Dict, output_path: str):
        """Save report to JSON file"""
        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to: {output_path}")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Scan codebase for API field usage')
    parser.add_argument(
        '--schema',
        default='api_docs/coinex_api_schema.yaml',
        help='Path to API schema file (optional)'
    )
    parser.add_argument(
        '--directory',
        default='src',
        help='Directory to scan'
    )
    parser.add_argument(
        '--exclude',
        nargs='*',
        default=['venv', 'env', '.git', '__pycache__'],
        help='Directories to exclude'
    )
    parser.add_argument(
        '--output',
        help='Save report to JSON file'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Minimal output'
    )
    
    args = parser.parse_args()
    
    # Create scanner
    scanner = APIUsageScanner(args.schema if os.path.exists(args.schema) else None)
    
    # Scan directory
    print(f"Scanning {args.directory} for API usage...")
    scanner.scan_directory(args.directory, args.exclude)
    
    # Generate report
    report = scanner.generate_report()
    
    # Print report
    scanner.print_report(report, verbose=not args.quiet)
    
    # Save report if requested
    if args.output:
        scanner.save_report(report, args.output)
    
    # Exit with error if dangerous usages found
    if report['dangerous_usages']:
        sys.exit(1)
    
    sys.exit(0)


if __name__ == '__main__':
    main()