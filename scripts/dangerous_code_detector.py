#!/usr/bin/env python3
"""
Dangerous Code Pattern Detector
Scans for TODO comments, mock code, placeholder implementations, and other patterns
that could cause crashes or bugs in production.
"""
import os
import re
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Set
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """Severity levels for dangerous patterns"""
    CRITICAL = "CRITICAL"  # Will definitely crash or cause major issues
    HIGH = "HIGH"          # Likely to cause bugs
    MEDIUM = "MEDIUM"      # Could cause issues in certain conditions
    LOW = "LOW"            # Should be fixed but not urgent
    INFO = "INFO"          # Informational only


@dataclass
class DangerousPattern:
    """Represents a dangerous code pattern found"""
    file_path: str
    line_number: int
    pattern_type: str
    severity: Severity
    code_snippet: str
    description: str
    suggested_fix: str = ""


class DangerousCodeDetector:
    """Detects dangerous code patterns that could crash or bug the application"""
    
    # Pattern definitions with severity levels
    PATTERNS = {
        # CRITICAL - Will crash or fail
        'mock_function': {
            'patterns': [
                r'def\s+\w+\([^)]*\):\s*#\s*MOCK',
                r'def\s+\w+\([^)]*\):\s*#\s*TODO.*implement',
                r'return\s+["\']MOCK',
                r'return\s+None\s*#\s*TODO',
                r'raise\s+NotImplementedError',
            ],
            'severity': Severity.CRITICAL,
            'description': 'Mock or unimplemented function that will crash when called',
            'fix': 'Implement the function or remove if unused'
        },
        
        'placeholder_value': {
            'patterns': [
                r'=\s*["\']TODO["\']',
                r'=\s*["\']FIXME["\']',
                r'=\s*["\']XXX["\']',
                r'=\s*["\']PLACEHOLDER["\']',
                r'=\s*123456789\s*#.*test',
                r'=\s*["\']test_.*["\'].*#.*replace',
            ],
            'severity': Severity.CRITICAL,
            'description': 'Placeholder value that needs to be replaced',
            'fix': 'Replace with actual value or configuration'
        },
        
        # HIGH - Likely to cause bugs
        'empty_except': {
            'patterns': [
                r'except.*:\s*pass',
                r'except.*:\s*$',
                r'except Exception:\s*pass',
                r'except:\s*pass',
            ],
            'severity': Severity.HIGH,
            'description': 'Silent exception handling that hides errors',
            'fix': 'Add proper error handling or logging'
        },
        
        'hardcoded_credentials': {
            'patterns': [
                r'password\s*=\s*["\'][^"\']+["\']',
                r'api_key\s*=\s*["\'][^"\']+["\']',
                r'secret\s*=\s*["\'][^"\']+["\']',
                r'token\s*=\s*["\'][^"\']+["\']',
            ],
            'severity': Severity.HIGH,
            'description': 'Hardcoded credentials in code',
            'fix': 'Use environment variables or config files'
        },
        
        'todo_fixme': {
            'patterns': [
                r'#\s*TODO:.*critical',
                r'#\s*FIXME:.*bug',
                r'#\s*BUG:',
                r'#\s*HACK:',
                r'#\s*XXX:',
            ],
            'severity': Severity.HIGH,
            'description': 'Critical TODO or bug marker',
            'fix': 'Address the TODO or bug before deployment'
        },
        
        # MEDIUM - Could cause issues
        'debug_code': {
            'patterns': [
                r'print\([^)]*debug',
                r'console\.log',
                r'debugger;',
                r'import\s+pdb',
                r'pdb\.set_trace',
                r'breakpoint\(\)',
                r'DEBUG\s*=\s*True',
            ],
            'severity': Severity.MEDIUM,
            'description': 'Debug code left in production',
            'fix': 'Remove debug statements or use proper logging'
        },
        
        'unsafe_eval': {
            'patterns': [
                r'eval\(',
                r'exec\(',
                r'compile\(',
                r'__import__\(',
            ],
            'severity': Severity.MEDIUM,
            'description': 'Unsafe code execution',
            'fix': 'Replace with safe alternatives'
        },
        
        'infinite_loop_risk': {
            'patterns': [
                r'while\s+True:(?!.*break)',
                r'while\s+1:(?!.*break)',
                r'for\s+_\s+in\s+iter\(int,\s*1\):',
            ],
            'severity': Severity.MEDIUM,
            'description': 'Potential infinite loop without clear exit',
            'fix': 'Add explicit break condition or timeout'
        },
        
        # LOW - Should be fixed
        'commented_code': {
            'patterns': [
                r'^\s*#\s*(if|for|while|def|class|import|from)\s',
                r'"""\s*(if|for|while|def|class|import|from)\s',
            ],
            'severity': Severity.LOW,
            'description': 'Large blocks of commented code',
            'fix': 'Remove commented code or move to documentation'
        },
        
        'magic_numbers': {
            'patterns': [
                r'sleep\((?!0)[0-9]+\)',
                r'time\.sleep\((?!0)[0-9]+\)',
                r'timeout\s*=\s*[0-9]+(?!\s*#)',
                r'if\s+.+\s*[<>=]+\s*[0-9]{4,}',  # Large magic numbers in conditions
            ],
            'severity': Severity.LOW,
            'description': 'Magic numbers without explanation',
            'fix': 'Define as named constants with comments'
        },
    }
    
    # Files/directories to skip
    SKIP_PATTERNS = [
        r'test_.*\.py$',
        r'.*_test\.py$',
        r'tests/',
        r'__pycache__',
        r'\.git/',
        r'venv/',
        r'env/',
        r'node_modules/',
        r'\.pyc$',
    ]
    
    def __init__(self):
        """Initialize the detector"""
        self.issues = []
        self.files_scanned = 0
        self.total_lines = 0
        
    def should_skip_file(self, file_path: str) -> bool:
        """Check if file should be skipped"""
        for pattern in self.SKIP_PATTERNS:
            if re.search(pattern, file_path):
                return True
        return False
    
    def scan_file(self, file_path: str) -> List[DangerousPattern]:
        """Scan a single file for dangerous patterns"""
        if self.should_skip_file(file_path):
            return []
        
        issues = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except:
            return []
        
        self.files_scanned += 1
        self.total_lines += len(lines)
        
        for line_num, line in enumerate(lines, 1):
            # Check each pattern category
            for pattern_name, pattern_info in self.PATTERNS.items():
                for pattern in pattern_info['patterns']:
                    if re.search(pattern, line, re.IGNORECASE):
                        issues.append(DangerousPattern(
                            file_path=file_path,
                            line_number=line_num,
                            pattern_type=pattern_name,
                            severity=pattern_info['severity'],
                            code_snippet=line.strip()[:100],
                            description=pattern_info['description'],
                            suggested_fix=pattern_info['fix']
                        ))
                        break  # Only report once per line
        
        # Special multi-line pattern checks
        issues.extend(self._check_multiline_patterns(file_path, lines))
        
        return issues
    
    def _check_multiline_patterns(self, file_path: str, lines: List[str]) -> List[DangerousPattern]:
        """Check for patterns that span multiple lines"""
        issues = []
        
        # Check for functions with only 'pass' or 'return None'
        for i, line in enumerate(lines):
            if re.match(r'^\s*def\s+\w+\([^)]*\):', line):
                # Found function definition, check body
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line == 'pass' or next_line == 'return None':
                        issues.append(DangerousPattern(
                            file_path=file_path,
                            line_number=i + 1,
                            pattern_type='empty_function',
                            severity=Severity.HIGH,
                            code_snippet=line.strip() + ' -> ' + next_line,
                            description='Empty function implementation',
                            suggested_fix='Implement the function or remove if unused'
                        ))
        
        # Check for large blocks of TODO comments
        todo_block_start = None
        todo_count = 0
        
        for i, line in enumerate(lines, 1):
            if 'TODO' in line or 'FIXME' in line:
                if todo_block_start is None:
                    todo_block_start = i
                todo_count += 1
            else:
                if todo_count >= 3:  # 3+ consecutive TODO lines
                    issues.append(DangerousPattern(
                        file_path=file_path,
                        line_number=todo_block_start,
                        pattern_type='todo_block',
                        severity=Severity.MEDIUM,
                        code_snippet=f'{todo_count} consecutive TODO/FIXME lines',
                        description='Large block of TODOs indicates incomplete implementation',
                        suggested_fix='Complete the implementation or create proper tickets'
                    ))
                todo_block_start = None
                todo_count = 0
        
        return issues
    
    def scan_directory(self, directory: str) -> bool:
        """Scan all Python files in directory"""
        path = Path(directory)
        
        for py_file in path.rglob('*.py'):
            file_issues = self.scan_file(str(py_file))
            self.issues.extend(file_issues)
        
        return len(self.issues) == 0
    
    def print_report(self, verbose: bool = True, max_issues: int = None):
        """Print scan report"""
        print(f"\n{'='*80}")
        print(f"🔍 Dangerous Code Pattern Detection Report")
        print(f"{'='*80}")
        
        print(f"\n📊 Summary:")
        print(f"  Files scanned: {self.files_scanned}")
        print(f"  Total lines: {self.total_lines:,}")
        print(f"  Issues found: {len(self.issues)}")
        
        if not self.issues:
            print(f"\n✅ No dangerous patterns found!")
            return True
        
        # Group by severity
        by_severity = {}
        for issue in self.issues:
            if issue.severity not in by_severity:
                by_severity[issue.severity] = []
            by_severity[issue.severity].append(issue)
        
        # Print severity breakdown
        print(f"\n🚨 Issues by Severity:")
        for severity in Severity:
            count = len(by_severity.get(severity, []))
            if count > 0:
                emoji = {
                    Severity.CRITICAL: "🔴",
                    Severity.HIGH: "🟠",
                    Severity.MEDIUM: "🟡",
                    Severity.LOW: "🔵",
                    Severity.INFO: "⚪"
                }[severity]
                print(f"  {emoji} {severity.value}: {count} issues")
        
        # Print detailed issues
        if verbose:
            issue_count = 0
            for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
                if severity in by_severity:
                    print(f"\n{severity.value} Issues:")
                    print("-" * 40)
                    
                    for issue in by_severity[severity]:
                        if max_issues and issue_count >= max_issues:
                            remaining = len(self.issues) - issue_count
                            print(f"\n... and {remaining} more issues")
                            break
                        
                        issue_count += 1
                        rel_path = os.path.relpath(issue.file_path)
                        
                        print(f"\n  📁 {rel_path}:{issue.line_number}")
                        print(f"  Type: {issue.pattern_type}")
                        print(f"  Issue: {issue.description}")
                        print(f"  Code: {issue.code_snippet}")
                        if issue.suggested_fix:
                            print(f"  Fix: {issue.suggested_fix}")
                    
                    if max_issues and issue_count >= max_issues:
                        break
        
        print(f"\n{'='*80}")
        
        # Return False if critical issues found
        return not any(issue.severity == Severity.CRITICAL for issue in self.issues)
    
    def has_blocking_issues(self) -> bool:
        """Check if there are issues that should block deployment"""
        return any(issue.severity in [Severity.CRITICAL, Severity.HIGH] for issue in self.issues)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Detect dangerous code patterns like TODOs, mocks, and placeholders'
    )
    parser.add_argument(
        '--directory',
        default='src',
        help='Directory to scan (default: src)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed output'
    )
    parser.add_argument(
        '--max-issues',
        type=int,
        help='Maximum number of issues to display'
    )
    parser.add_argument(
        '--block-on',
        choices=['critical', 'high', 'medium', 'low', 'none'],
        default='high',
        help='Exit with error if issues of this severity or higher are found'
    )
    parser.add_argument(
        '--ignore-patterns',
        nargs='*',
        help='Additional file patterns to ignore'
    )
    
    args = parser.parse_args()
    
    # Create detector
    detector = DangerousCodeDetector()
    
    # Add custom ignore patterns if provided
    if args.ignore_patterns:
        detector.SKIP_PATTERNS.extend(args.ignore_patterns)
    
    # Scan directory
    print(f"Scanning {args.directory} for dangerous code patterns...")
    detector.scan_directory(args.directory)
    
    # Print report
    success = detector.print_report(
        verbose=args.verbose,
        max_issues=args.max_issues
    )
    
    # Determine exit code based on blocking level
    should_block = False
    if args.block_on != 'none':
        block_severities = {
            'critical': [Severity.CRITICAL],
            'high': [Severity.CRITICAL, Severity.HIGH],
            'medium': [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM],
            'low': [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW],
        }
        
        blocking_severities = block_severities.get(args.block_on, [])
        should_block = any(
            issue.severity in blocking_severities 
            for issue in detector.issues
        )
    
    if should_block:
        print(f"\n❌ Blocking issues found! Fix {args.block_on} severity issues before proceeding.")
        sys.exit(1)
    
    sys.exit(0)


if __name__ == '__main__':
    main()