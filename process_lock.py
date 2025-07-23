#!/usr/bin/env python3
"""
Process lock utility to prevent multiple instances
"""
import fcntl
import os
import sys
from pathlib import Path

class ProcessLock:
    """Ensures only one instance of the application runs"""
    
    def __init__(self, lock_file: str = "trading_bot.lock"):
        self.lock_file = Path(lock_file)
        self.file_handle = None
    
    def __enter__(self):
        try:
            # Create lock file
            self.file_handle = open(self.lock_file, 'w')
            
            # Try to acquire exclusive lock (non-blocking)
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # Write current process ID
            self.file_handle.write(str(os.getpid()))
            self.file_handle.flush()
            
            return self
            
        except IOError:
            # Lock is already held by another process
            if self.file_handle:
                self.file_handle.close()
            
            print("❌ ERROR: Another instance of the trading bot is already running!")
            print("   Only one instance is allowed to prevent conflicts.")
            print("   If you're sure no other instance is running, delete the lock file:")
            print(f"   rm {self.lock_file.absolute()}")
            sys.exit(1)
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file_handle:
            # Release lock and close file
            fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)
            self.file_handle.close()
            
            # Remove lock file
            try:
                self.lock_file.unlink()
            except FileNotFoundError:
                pass  # File was already removed