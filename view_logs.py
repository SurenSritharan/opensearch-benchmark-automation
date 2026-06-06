#!/usr/bin/env python3
"""
Real-time log viewer for parallel benchmark execution.
Monitors and displays logs from all running benchmark engines.
"""
import sys
import time
import argparse
from pathlib import Path
from typing import List, Optional
import subprocess
import os


class LogViewer:
    """Interactive log viewer for parallel benchmark execution."""
    
    def __init__(self, results_dir: Optional[str] = None):
        """
        Initialize log viewer.
        
        Args:
            results_dir: Path to results directory. If None, finds the latest.
        """
        if results_dir:
            self.results_dir = Path(results_dir)
        else:
            self.results_dir = self._find_latest_results()
        
        self.logs_dir = self.results_dir / "parallel-logs"
        
        if not self.logs_dir.exists():
            print(f"❌ Error: Logs directory not found: {self.logs_dir}")
            sys.exit(1)
    
    def _find_latest_results(self) -> Path:
        """Find the most recent results directory."""
        results_root = Path("./results")
        
        if not results_root.exists():
            print("❌ Error: No results directory found")
            sys.exit(1)
        
        # Find all timestamped directories
        dirs = [d for d in results_root.iterdir() if d.is_dir()]
        
        if not dirs:
            print("❌ Error: No results found in ./results")
            sys.exit(1)
        
        # Sort by modification time and get the latest
        latest = max(dirs, key=lambda d: d.stat().st_mtime)
        return latest
    
    def list_available_logs(self) -> List[Path]:
        """List all available log files."""
        log_files = sorted(self.logs_dir.glob("*.log"))
        # Filter out error logs
        log_files = [f for f in log_files if not f.name.endswith(".error.log")]
        return log_files
    
    def show_status(self):
        """Display current execution status."""
        status_file = self.logs_dir / "status.txt"
        
        print("=" * 80)
        print("📊 BENCHMARK EXECUTION STATUS")
        print("=" * 80)
        print(f"Results Directory: {self.results_dir}")
        print(f"Logs Directory: {self.logs_dir}")
        print()
        
        if status_file.exists():
            print(status_file.read_text())
        else:
            print("Status file not found")
        
        print()
        print("Available Logs:")
        print("-" * 80)
        
        log_files = self.list_available_logs()
        
        if not log_files:
            print("No log files found")
            return
        
        for log_file in log_files:
            size = log_file.stat().st_size
            size_str = self._format_size(size)
            mtime = time.strftime('%Y-%m-%d %H:%M:%S', 
                                 time.localtime(log_file.stat().st_mtime))
            
            # Check if there's an error log
            error_file = log_file.parent / f"{log_file.stem}.error.log"
            has_errors = error_file.exists() and error_file.stat().st_size > 0
            error_indicator = " ⚠️" if has_errors else ""
            
            print(f"  {log_file.name:20s} {size_str:>10s}  {mtime}{error_indicator}")
        
        print("=" * 80)
    
    def _format_size(self, size: int) -> str:
        """Format file size in human-readable format."""
        size_float = float(size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_float < 1024.0:
                return f"{size_float:.1f}{unit}"
            size_float /= 1024.0
        return f"{size_float:.1f}TB"
    
    def tail_log(self, engine: str, lines: int = 50, follow: bool = False):
        """
        Tail a specific engine's log file.
        
        Args:
            engine: Engine name (jvector, faiss, lucene)
            lines: Number of lines to display
            follow: Whether to follow the log (like tail -f)
        """
        log_file = self.logs_dir / f"{engine}.log"
        
        if not log_file.exists():
            print(f"❌ Error: Log file not found: {log_file}")
            return
        
        print(f"📄 Viewing log: {log_file.name}")
        print("=" * 80)
        
        if follow:
            # Use tail -f for live following
            try:
                subprocess.run(["tail", "-f", "-n", str(lines), str(log_file)])
            except KeyboardInterrupt:
                print("\n\nStopped following log")
        else:
            # Just display last N lines
            try:
                result = subprocess.run(
                    ["tail", "-n", str(lines), str(log_file)],
                    capture_output=True,
                    text=True
                )
                print(result.stdout)
            except Exception as e:
                print(f"❌ Error reading log: {e}")
    
    def tail_all_logs(self, lines: int = 20):
        """Display the last N lines from all log files."""
        log_files = self.list_available_logs()
        
        if not log_files:
            print("No log files found")
            return
        
        for log_file in log_files:
            print()
            print("=" * 80)
            print(f"📄 {log_file.name} (last {lines} lines)")
            print("=" * 80)
            
            try:
                result = subprocess.run(
                    ["tail", "-n", str(lines), str(log_file)],
                    capture_output=True,
                    text=True
                )
                print(result.stdout)
            except Exception as e:
                print(f"❌ Error reading log: {e}")
    
    def follow_all_logs(self):
        """Follow all logs simultaneously using multitail or tmux."""
        log_files = self.list_available_logs()
        
        if not log_files:
            print("No log files found")
            return
        
        # Check if multitail is available
        try:
            subprocess.run(["which", "multitail"], 
                         capture_output=True, check=True)
            has_multitail = True
        except subprocess.CalledProcessError:
            has_multitail = False
        
        if has_multitail:
            # Use multitail for simultaneous viewing
            cmd = ["multitail"] + [str(f) for f in log_files]
            print(f"📺 Following all logs with multitail...")
            print("   Press 'q' to quit")
            print()
            try:
                subprocess.run(cmd)
            except KeyboardInterrupt:
                print("\n\nStopped following logs")
        else:
            # Fallback: tail all files with labels
            print("💡 Tip: Install 'multitail' for better multi-log viewing")
            print("   brew install multitail  (macOS)")
            print("   apt-get install multitail  (Ubuntu/Debian)")
            print()
            print("📺 Following all logs (combined view)...")
            print("   Press Ctrl+C to stop")
            print()
            
            # Use tail -f with all files
            cmd = ["tail", "-f"] + [str(f) for f in log_files]
            try:
                subprocess.run(cmd)
            except KeyboardInterrupt:
                print("\n\nStopped following logs")
    
    def show_errors(self):
        """Display any error logs."""
        error_files = sorted(self.logs_dir.glob("*.error.log"))
        
        if not error_files:
            print("✅ No error logs found")
            return
        
        print("⚠️  Error Logs Found:")
        print("=" * 80)
        
        for error_file in error_files:
            if error_file.stat().st_size == 0:
                continue
            
            print()
            print(f"📄 {error_file.name}")
            print("-" * 80)
            print(error_file.read_text())
            print()


def main():
    """Main entry point for log viewer."""
    parser = argparse.ArgumentParser(
        description="View logs from parallel benchmark execution",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Show status of latest run
  %(prog)s --status                 # Show status of latest run
  %(prog)s --tail faiss             # Show last 50 lines of faiss log
  %(prog)s --tail faiss --follow    # Follow faiss log in real-time
  %(prog)s --tail-all               # Show last 20 lines from all logs
  %(prog)s --follow-all             # Follow all logs simultaneously
  %(prog)s --errors                 # Show any error logs
  %(prog)s --dir results/20260606-023140  # View specific run
        """
    )
    
    parser.add_argument(
        "--dir",
        help="Results directory to view (default: latest)"
    )
    
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show execution status and available logs"
    )
    
    parser.add_argument(
        "--tail",
        metavar="ENGINE",
        help="Tail log for specific engine (jvector, faiss, lucene)"
    )
    
    parser.add_argument(
        "--follow", "-f",
        action="store_true",
        help="Follow log in real-time (use with --tail)"
    )
    
    parser.add_argument(
        "--lines", "-n",
        type=int,
        default=50,
        help="Number of lines to display (default: 50)"
    )
    
    parser.add_argument(
        "--tail-all",
        action="store_true",
        help="Show last N lines from all logs"
    )
    
    parser.add_argument(
        "--follow-all",
        action="store_true",
        help="Follow all logs simultaneously"
    )
    
    parser.add_argument(
        "--errors",
        action="store_true",
        help="Show error logs"
    )
    
    args = parser.parse_args()
    
    # Create viewer
    viewer = LogViewer(args.dir)
    
    # Execute requested action
    if args.tail:
        viewer.tail_log(args.tail, lines=args.lines, follow=args.follow)
    elif args.tail_all:
        viewer.tail_all_logs(lines=args.lines)
    elif args.follow_all:
        viewer.follow_all_logs()
    elif args.errors:
        viewer.show_errors()
    else:
        # Default: show status
        viewer.show_status()


if __name__ == "__main__":
    main()

# Made with Bob
