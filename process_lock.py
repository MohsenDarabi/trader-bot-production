#!/usr/bin/env python3
"""
Process lock utility to prevent multiple instances
VM-compatible with enhanced error handling
"""
import fcntl
import os
import sys
import time
from pathlib import Path

class ProcessLock:
    """Ensures only one instance of the application runs - VM compatible"""
    
    def __init__(self, lock_file: str = "trading_bot.lock"):
        # For VM environments, use absolute path in /tmp if writable
        if os.getenv('DOCKER_CONTAINER', 'false').lower() == 'true':
            # In Docker, use /tmp for lock files
            self.lock_file = Path(f"/tmp/{lock_file}")
        else:
            # Local development, use current directory
            self.lock_file = Path(lock_file)
        self.file_handle = None
    
    def __enter__(self):
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                # Ensure parent directory exists (VM compatibility)
                self.lock_file.parent.mkdir(parents=True, exist_ok=True)
                
                # Create lock file
                self.file_handle = open(self.lock_file, 'w')
                
                # Try to acquire exclusive lock (non-blocking)
                fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                
                # Write current process ID and timestamp
                self.file_handle.write(f"{os.getpid()}\n{time.time()}")
                self.file_handle.flush()
                
                return self
                
            except IOError as e:
                # Lock is already held by another process
                if self.file_handle:
                    self.file_handle.close()
                    self.file_handle = None
                
                if attempt < max_retries - 1:
                    print(f"⚠️  Lock attempt {attempt + 1} failed, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    # Check if lock file contains stale PID (VM cleanup)
                    if self._is_stale_lock():
                        print("🧹 Detected stale lock file, cleaning up...")
                        try:
                            self.lock_file.unlink()
                            continue  # Retry after cleanup
                        except FileNotFoundError:
                            pass
                    
                    print("❌ ERROR: Another instance of the trading bot is already running!")
                    print("   Only one instance is allowed to prevent conflicts.")
                    print("   If you're sure no other instance is running, delete the lock file:")
                    print(f"   rm {self.lock_file.absolute()}")
                    sys.exit(1)
        
        return self
    
    def _is_stale_lock(self) -> bool:
        """Check if lock file contains a stale PID (VM compatibility)"""
        try:
            if not self.lock_file.exists():
                return False
                
            with open(self.lock_file, 'r') as f:
                content = f.read().strip().split('\n')
                if len(content) < 1:
                    return True  # Invalid lock file
                
                pid_str = content[0]
                if not pid_str.isdigit():
                    return True  # Invalid PID
                
                pid = int(pid_str)
                
                # Check if process is still running
                try:
                    os.kill(pid, 0)  # Send signal 0 to check if process exists
                    return False  # Process is still running
                except (OSError, ProcessLookupError):
                    return True  # Process is dead, lock is stale
                    
        except Exception:
            return True  # Any error means we should clean up
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file_handle:
            # Release lock and close file
            try:
                fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)
                self.file_handle.close()
            except Exception:
                pass  # Best effort cleanup
            
            # Remove lock file
            try:
                self.lock_file.unlink()
            except FileNotFoundError:
                pass  # File was already removed