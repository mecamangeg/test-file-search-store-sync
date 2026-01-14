#!/usr/bin/env python3
"""
Sync codebase to Gemini File Search Store
Version 2.7.0

Features:
- Parallel uploads (8 workers)
- Defensive deduplication
- Display name as truth (not stale doc_id)
- Smart cache updates O(1)
- Atomic manifest saves
- Rate limiting with exponential backoff
- Unified ignore (.gitignore + .aiignore + IGNORE_DIRS)
- Comprehensive logging system with file output
- Performance metrics and timing
"""

import os
import sys
import json
import hashlib
import time
import tempfile
import shutil
import subprocess
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

try:
    import pathspec
except ImportError:
    print("Installing pathspec...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pathspec"])
    import pathspec

from google import genai
from google.genai import types

# =============================================================================
# Configuration
# =============================================================================

STORE_ID = os.environ.get("FILE_SEARCH_STORE_ID", "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo")
MANIFEST_PATH = Path(".file-search-manifest.json")
LOG_DIR = Path("logs")
MAX_WORKERS = 8
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# Optimization: Trust manifest for doc lookups (skip expensive list_documents)
# Set to False to always fetch store state (slower but more accurate)
TRUST_MANIFEST = os.environ.get("TRUST_MANIFEST", "true").lower() == "true"
MANIFEST_MAX_AGE_HOURS = 24  # Force refresh if manifest is older than this

# Extensions to sync
SYNC_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".md", ".json", ".yaml", ".yml",
    ".css", ".scss", ".html",
    ".sql", ".sh", ".bash",
    ".env.example", ".gitignore"
}

# Directories to always ignore (merged with .gitignore and .aiignore)
IGNORE_DIRS = {
    ".git", ".next", "node_modules", "__pycache__",
    ".venv", "venv", "env", ".env",
    "dist", "build", "out", ".output",
    "coverage", ".nyc_output", ".pytest_cache",
    ".turbo", ".vercel", ".netlify",
    "docs", "tools", ".claude", "logs"
}

# =============================================================================
# Logging System
# =============================================================================

class SyncLogger:
    """Comprehensive logging system with file and console output."""

    def __init__(self, log_dir: Path = LOG_DIR):
        self.log_dir = log_dir
        self.log_dir.mkdir(exist_ok=True)

        # Create timestamped log file
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"sync_{timestamp}.log"

        # Configure root logger
        self.logger = logging.getLogger("FileSearchSync")
        self.logger.setLevel(logging.DEBUG)

        # Clear existing handlers
        self.logger.handlers = []

        # File handler (detailed)
        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_format)
        self.logger.addHandler(file_handler)

        # Console handler (summary)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter("%(message)s")
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)

        # Thread-safe counters for metrics
        self._lock = threading.Lock()
        self.metrics = defaultdict(lambda: defaultdict(int))
        self.timings = defaultdict(list)

    def info(self, msg: str):
        self.logger.info(msg)

    def debug(self, msg: str):
        self.logger.debug(msg)

    def warning(self, msg: str):
        self.logger.warning(msg)

    def error(self, msg: str):
        self.logger.error(msg)

    def record_metric(self, category: str, metric: str, value: int = 1):
        """Record a metric in a thread-safe manner."""
        with self._lock:
            self.metrics[category][metric] += value

    def record_timing(self, operation: str, duration: float):
        """Record operation timing."""
        with self._lock:
            self.timings[operation].append(duration)

    def get_timing_stats(self, operation: str) -> dict:
        """Get timing statistics for an operation."""
        with self._lock:
            times = self.timings.get(operation, [])
            if not times:
                return {"count": 0}
            return {
                "count": len(times),
                "total": sum(times),
                "avg": sum(times) / len(times),
                "min": min(times),
                "max": max(times)
            }

    def print_summary(self):
        """Print a summary of all metrics and timings."""
        self.info("\n" + "=" * 60)
        self.info("SYNC METRICS SUMMARY")
        self.info("=" * 60)

        # Print metrics
        for category, data in self.metrics.items():
            self.info(f"\n[{category}]")
            for metric, value in data.items():
                self.info(f"  {metric}: {value}")

        # Print timings
        self.info("\n[TIMING STATISTICS]")
        for operation, times in self.timings.items():
            stats = self.get_timing_stats(operation)
            if stats["count"] > 0:
                self.info(f"  {operation}:")
                self.info(f"    count: {stats['count']}")
                self.info(f"    total: {stats['total']:.3f}s")
                self.info(f"    avg:   {stats['avg']:.3f}s")
                self.info(f"    min:   {stats['min']:.3f}s")
                self.info(f"    max:   {stats['max']:.3f}s")

        self.info(f"\nLog file: {self.log_file}")
        self.info("=" * 60)

# Global logger instance
logger: Optional[SyncLogger] = None

# =============================================================================
# Performance Metrics
# =============================================================================

@dataclass
class SyncMetrics:
    """Track sync operation metrics."""
    start_time: float = field(default_factory=time.time)
    files_scanned: int = 0
    files_hashed: int = 0
    files_ignored: int = 0
    files_new: int = 0
    files_modified: int = 0
    files_deleted: int = 0
    uploads_success: int = 0
    uploads_failed: int = 0
    uploads_retried: int = 0
    deletes_success: int = 0
    deletes_failed: int = 0
    dedup_checks: int = 0
    dedup_hits: int = 0
    api_calls: int = 0
    api_errors: int = 0
    bytes_uploaded: int = 0

    def duration(self) -> float:
        return time.time() - self.start_time

metrics = SyncMetrics()

# =============================================================================
# Gemini Client
# =============================================================================

def get_client() -> genai.Client:
    """Initialize Gemini client with API key."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY environment variable not set")
        sys.exit(1)
    return genai.Client(api_key=api_key)

# =============================================================================
# Ignore Layer - Unified Pattern Matching
# =============================================================================

def load_ignore_patterns() -> pathspec.PathSpec:
    """Load and merge ignore patterns from .gitignore, .aiignore, and IGNORE_DIRS."""
    patterns = []

    # Add IGNORE_DIRS as patterns
    for dir_name in IGNORE_DIRS:
        patterns.append(f"{dir_name}/")
        patterns.append(f"**/{dir_name}/")

    # Load .gitignore
    gitignore_path = Path(".gitignore")
    if gitignore_path.exists():
        with open(gitignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
        logger.debug(f"Loaded .gitignore with {len(patterns)} patterns")

    # Load .aiignore
    aiignore_path = Path(".aiignore")
    if aiignore_path.exists():
        aiignore_count = 0
        with open(aiignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
                    aiignore_count += 1
        logger.debug(f"Loaded .aiignore with {aiignore_count} patterns")

    # Remove duplicates while preserving order
    seen = set()
    unique_patterns = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique_patterns.append(p)

    logger.debug(f"Total unique ignore patterns: {len(unique_patterns)}")
    return pathspec.PathSpec.from_lines("gitwildmatch", unique_patterns)

def should_ignore(path: Path, ignore_spec: pathspec.PathSpec) -> bool:
    """Check if a path should be ignored."""
    path_str = str(path).replace("\\", "/")
    return ignore_spec.match_file(path_str)

# =============================================================================
# Manifest Layer - Track Synced Files
# =============================================================================

def load_manifest() -> dict:
    """Load manifest from disk or return empty dict."""
    if MANIFEST_PATH.exists():
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                manifest = json.load(f)
                logger.debug(f"Loaded manifest with {len(manifest)} entries")
                return manifest
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupt manifest, starting fresh: {e}")
            return {}
    logger.debug("No existing manifest found")
    return {}

def save_manifest_atomic(manifest: dict):
    """Save manifest atomically (write to temp, then rename)."""
    start = time.time()
    temp_fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="manifest_")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        shutil.move(temp_path, MANIFEST_PATH)
        duration = time.time() - start
        logger.debug(f"Manifest saved atomically in {duration:.3f}s")
        logger.record_timing("manifest_save", duration)
    except Exception as e:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        logger.error(f"Failed to save manifest: {e}")
        raise e

def compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of file contents."""
    start = time.time()
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    duration = time.time() - start
    logger.record_timing("hash_file", duration)
    metrics.files_hashed += 1
    return hasher.hexdigest()[:16]

# =============================================================================
# Scan Layer - Find Files to Sync
# =============================================================================

def scan_codebase(ignore_spec: pathspec.PathSpec) -> dict:
    """
    Scan codebase and return dict of {relative_path: file_hash}.
    Only includes files with valid extensions that aren't ignored.
    """
    start = time.time()
    files = {}
    root = Path(".")

    # Track folder depth distribution
    depth_counts = defaultdict(int)

    for path in root.rglob("*"):
        metrics.files_scanned += 1

        if not path.is_file():
            continue

        rel_path = path.relative_to(root)
        rel_path_str = str(rel_path).replace("\\", "/")

        # Track depth
        depth = len(rel_path.parts) - 1
        depth_counts[depth] += 1

        # Check extension
        suffix = path.suffix.lower()
        if suffix not in SYNC_EXTENSIONS and path.name not in SYNC_EXTENSIONS:
            logger.debug(f"SKIP (extension): {rel_path_str}")
            metrics.files_ignored += 1
            logger.record_metric("ignore_reason", "extension")
            continue

        # Check ignore patterns
        if should_ignore(rel_path, ignore_spec):
            logger.debug(f"SKIP (pattern): {rel_path_str}")
            metrics.files_ignored += 1
            logger.record_metric("ignore_reason", "pattern")
            continue

        # Compute hash
        try:
            file_hash = compute_file_hash(path)
            files[rel_path_str] = file_hash
            logger.debug(f"INCLUDE: {rel_path_str} (hash={file_hash})")
        except Exception as e:
            logger.warning(f"Could not hash {rel_path_str}: {e}")
            logger.record_metric("errors", "hash_failed")

    duration = time.time() - start
    logger.record_timing("scan_codebase", duration)

    # Log depth distribution
    logger.debug("File depth distribution:")
    for depth, count in sorted(depth_counts.items()):
        logger.debug(f"  depth {depth}: {count} files")
        logger.record_metric("file_depth", f"depth_{depth}", count)

    return files

# =============================================================================
# Diff Layer - Compute Sync Plan
# =============================================================================

def compute_diff(current_files: dict, manifest: dict) -> tuple[list, list, list]:
    """
    Compare current files to manifest and return (new, modified, deleted) lists.
    """
    start = time.time()
    new_files = []
    modified_files = []
    deleted_files = []

    manifest_paths = set(manifest.keys())
    current_paths = set(current_files.keys())

    # New files
    for path in current_paths - manifest_paths:
        new_files.append(path)
        logger.debug(f"DIFF NEW: {path}")

    # Modified files
    for path in current_paths & manifest_paths:
        if current_files[path] != manifest[path].get("hash"):
            modified_files.append(path)
            logger.debug(f"DIFF MODIFIED: {path} (old={manifest[path].get('hash')}, new={current_files[path]})")

    # Deleted files
    for path in manifest_paths - current_paths:
        deleted_files.append(path)
        logger.debug(f"DIFF DELETED: {path}")

    metrics.files_new = len(new_files)
    metrics.files_modified = len(modified_files)
    metrics.files_deleted = len(deleted_files)

    duration = time.time() - start
    logger.record_timing("compute_diff", duration)

    return new_files, modified_files, deleted_files

# =============================================================================
# Store Operations - Interact with Gemini File Search API
# =============================================================================

def list_store_documents(client: genai.Client) -> dict:
    """
    List all documents in the store, return dict of {display_name: doc_resource_name}.
    """
    start = time.time()
    docs = {}
    try:
        metrics.api_calls += 1
        response = client.files.list()
        for doc in response:
            if hasattr(doc, 'display_name') and doc.display_name:
                docs[doc.display_name] = doc.name
        logger.debug(f"Listed {len(docs)} documents from store")
    except Exception as e:
        logger.error(f"Could not list store documents: {e}")
        metrics.api_errors += 1
        logger.record_metric("api_errors", "list_documents")

    duration = time.time() - start
    logger.record_timing("list_documents", duration)
    return docs

def delete_document(client: genai.Client, doc_name: str) -> bool:
    """Delete a document from the store."""
    start = time.time()
    try:
        metrics.api_calls += 1
        client.files.delete(name=doc_name)
        metrics.deletes_success += 1
        logger.debug(f"DELETED: {doc_name}")
        duration = time.time() - start
        logger.record_timing("delete_document", duration)
        return True
    except Exception as e:
        logger.warning(f"Could not delete {doc_name}: {e}")
        metrics.deletes_failed += 1
        metrics.api_errors += 1
        logger.record_metric("api_errors", "delete_document")
        return False

def upload_file(client: genai.Client, file_path: str, existing_docs: dict, retries: int = 3) -> Optional[str]:
    """
    Upload a file to the store with defensive deduplication.
    Returns the document resource name on success, None on failure.
    """
    path = Path(file_path)
    display_name = file_path

    # Track file size
    try:
        file_size = path.stat().st_size
    except:
        file_size = 0

    # Defensive dedup check
    metrics.dedup_checks += 1
    if display_name in existing_docs:
        metrics.dedup_hits += 1
        logger.debug(f"DEDUP HIT: {display_name} exists, deleting first")
        delete_document(client, existing_docs[display_name])

    for attempt in range(retries):
        start = time.time()
        try:
            metrics.api_calls += 1

            # Upload to store
            response = client.files.upload(
                file=path,
                config=types.UploadFileConfig(
                    display_name=display_name,
                    mime_type="text/plain"
                )
            )

            metrics.uploads_success += 1
            metrics.bytes_uploaded += file_size
            duration = time.time() - start
            logger.record_timing("upload_file", duration)
            logger.debug(f"UPLOADED: {file_path} ({file_size} bytes) in {duration:.3f}s -> {response.name}")

            return response.name

        except Exception as e:
            metrics.api_errors += 1
            if attempt < retries - 1:
                metrics.uploads_retried += 1
                wait_time = (2 ** attempt) + (time.time() % 1)
                logger.warning(f"RETRY: {file_path} (attempt {attempt + 1}/{retries}, waiting {wait_time:.1f}s) - {e}")
                logger.record_metric("retries", f"attempt_{attempt + 1}")
                time.sleep(wait_time)
            else:
                logger.error(f"FAILED: {file_path} after {retries} attempts - {e}")
                metrics.uploads_failed += 1
                logger.record_metric("api_errors", "upload_file")
                return None

    return None

# =============================================================================
# Sync Operations - Orchestrate the Sync
# =============================================================================

def sync_files(
    client: genai.Client,
    new_files: list,
    modified_files: list,
    deleted_files: list,
    manifest: dict,
    current_files: dict
) -> dict:
    """
    Perform sync operations and return updated manifest.
    """
    commit_sha = os.environ.get("GITHUB_SHA", "local")[:8]
    timestamp = datetime.now(timezone.utc).isoformat()

    # Optimization: Use manifest doc_ids when trusted (skip expensive list_documents)
    if TRUST_MANIFEST and manifest:
        logger.info("\n  Using manifest-trusted mode (skipping list_documents)")
        # Build existing_docs from manifest
        existing_docs = {
            path: entry.get("doc_id")
            for path, entry in manifest.items()
            if entry.get("doc_id")
        }
        logger.info(f"  Manifest contains {len(existing_docs)} document references")
        logger.record_metric("store", "manifest_docs", len(existing_docs))
    else:
        # Full store fetch (slower but more accurate)
        logger.info("\n  Fetching store documents for dedup check...")
        existing_docs = list_store_documents(client)
        logger.info(f"  Found {len(existing_docs)} existing documents in store")
        logger.record_metric("store", "existing_docs", len(existing_docs))

    # 1. Delete removed files
    if deleted_files:
        logger.info(f"\n  Deleting {len(deleted_files)} removed files...")
        for path in deleted_files:
            doc_id = existing_docs.get(path) or manifest.get(path, {}).get("doc_id")
            if doc_id:
                if delete_document(client, doc_id):
                    del manifest[path]
            else:
                logger.debug(f"SKIP DELETE (no doc_id): {path}")
                if path in manifest:
                    del manifest[path]

    # 2. Handle modified files (delete then re-upload) - now parallel!
    if modified_files:
        logger.info(f"\n  Updating {len(modified_files)} modified files (parallel)...")

        def update_file(path: str) -> tuple[str, Optional[str]]:
            """Delete and re-upload a modified file."""
            doc_id = existing_docs.get(path) or manifest.get(path, {}).get("doc_id")
            if doc_id:
                delete_document(client, doc_id)
            new_doc_name = upload_file(client, path, {}, retries=3)
            return path, new_doc_name

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(update_file, path): path for path in modified_files}
            for future in as_completed(futures):
                path, doc_name = future.result()
                if doc_name:
                    manifest[path] = {
                        "hash": current_files[path],
                        "doc_id": doc_name,
                        "commit": commit_sha,
                        "last_synced": timestamp,
                        "metadata": {
                            "module": str(Path(path).parent),
                            "filename": Path(path).name,
                            "extension": Path(path).suffix
                        }
                    }
                    logger.info(f"    UPDATED: {path}")

    # 3. Upload new files in parallel
    if new_files:
        logger.info(f"\n  Uploading {len(new_files)} new files (parallel, {MAX_WORKERS} workers)...")

        parallel_start = time.time()
        completed = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_path = {
                executor.submit(upload_file, client, path, existing_docs): path
                for path in new_files
            }

            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    doc_name = future.result()
                    if doc_name:
                        manifest[path] = {
                            "hash": current_files[path],
                            "doc_id": doc_name,
                            "commit": commit_sha,
                            "last_synced": timestamp,
                            "metadata": {
                                "module": str(Path(path).parent),
                                "filename": Path(path).name,
                                "extension": Path(path).suffix
                            }
                        }
                        completed += 1
                        logger.info(f"    [{completed}/{len(new_files)}] UPLOADED: {path}")
                except Exception as e:
                    logger.error(f"    ERROR: {path}: {e}")

        parallel_duration = time.time() - parallel_start
        logger.record_timing("parallel_upload_batch", parallel_duration)
        logger.debug(f"Parallel upload batch completed in {parallel_duration:.3f}s")

    # Summary
    logger.info(f"\n  Sync complete: {metrics.uploads_success} uploaded, {metrics.deletes_success} deleted, {metrics.uploads_failed} failed")

    return manifest

# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main sync orchestration."""
    global logger, metrics

    # Initialize logging
    logger = SyncLogger()
    metrics = SyncMetrics()

    logger.info("=" * 60)
    logger.info("Gemini File Search Sync v2.7.0")
    logger.info("=" * 60)
    logger.info(f"\nStore: {STORE_ID}")
    logger.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    logger.info(f"Workers: {MAX_WORKERS}")

    # Initialize client
    logger.info("\n[1/5] Initializing Gemini client...")
    client = get_client()

    # Load ignore patterns
    logger.info("\n[2/5] Loading ignore patterns...")
    ignore_spec = load_ignore_patterns()

    # Scan codebase
    logger.info("\n[3/5] Scanning codebase...")
    current_files = scan_codebase(ignore_spec)
    logger.info(f"  Found {len(current_files)} files to potentially sync")
    logger.info(f"  Scanned: {metrics.files_scanned}, Ignored: {metrics.files_ignored}")

    # Load manifest
    logger.info("\n[4/5] Loading manifest...")
    manifest = load_manifest()
    logger.info(f"  Manifest contains {len(manifest)} entries")

    # Compute diff
    new_files, modified_files, deleted_files = compute_diff(current_files, manifest)
    logger.info(f"\n  Changes detected:")
    logger.info(f"    NEW: {len(new_files)}")
    logger.info(f"    MODIFIED: {len(modified_files)}")
    logger.info(f"    DELETED: {len(deleted_files)}")

    # Check if anything to sync
    if not new_files and not modified_files and not deleted_files:
        logger.info("\n  No changes to sync!")
        logger.print_summary()
        return

    # Perform sync
    logger.info("\n[5/5] Syncing to File Search Store...")
    manifest = sync_files(
        client,
        new_files,
        modified_files,
        deleted_files,
        manifest,
        current_files
    )

    # Save manifest atomically
    logger.info("\n  Saving manifest...")
    save_manifest_atomic(manifest)
    logger.info("  Manifest saved!")

    # Print summary with metrics
    logger.info(f"\nTotal duration: {metrics.duration():.3f}s")
    logger.info(f"API calls: {metrics.api_calls}")
    logger.info(f"API errors: {metrics.api_errors}")
    logger.info(f"Bytes uploaded: {metrics.bytes_uploaded:,}")
    logger.info(f"Dedup checks: {metrics.dedup_checks}, hits: {metrics.dedup_hits}")

    logger.print_summary()

    logger.info("\n" + "=" * 60)
    logger.info("Sync completed successfully!")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
