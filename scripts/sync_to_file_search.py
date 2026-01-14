#!/usr/bin/env python3
"""
Sync codebase to Gemini File Search Store
Version 2.6.0

Features:
- Parallel uploads (8 workers)
- Defensive deduplication
- Display name as truth (not stale doc_id)
- Smart cache updates O(1)
- Atomic manifest saves
- Rate limiting with exponential backoff
- Unified ignore (.gitignore + .aiignore + IGNORE_DIRS)
"""

import os
import sys
import json
import hashlib
import time
import tempfile
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

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
MAX_WORKERS = 8
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

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
    "docs", "tools", ".claude"
}

# =============================================================================
# Gemini Client
# =============================================================================

def get_client() -> genai.Client:
    """Initialize Gemini client with API key."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable not set")
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

    # Load .aiignore
    aiignore_path = Path(".aiignore")
    if aiignore_path.exists():
        with open(aiignore_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)

    # Remove duplicates while preserving order
    seen = set()
    unique_patterns = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique_patterns.append(p)

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
                return json.load(f)
        except json.JSONDecodeError:
            print("WARNING: Corrupt manifest, starting fresh")
            return {}
    return {}

def save_manifest_atomic(manifest: dict):
    """Save manifest atomically (write to temp, then rename)."""
    # Write to temp file first
    temp_fd, temp_path = tempfile.mkstemp(suffix=".json", prefix="manifest_")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        # Atomic rename
        shutil.move(temp_path, MANIFEST_PATH)
    except Exception as e:
        # Clean up temp file on error
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise e

def compute_file_hash(path: Path) -> str:
    """Compute SHA256 hash of file contents."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]  # First 16 chars is enough

# =============================================================================
# Scan Layer - Find Files to Sync
# =============================================================================

def scan_codebase(ignore_spec: pathspec.PathSpec) -> dict:
    """
    Scan codebase and return dict of {relative_path: file_hash}.
    Only includes files with valid extensions that aren't ignored.
    """
    files = {}
    root = Path(".")

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        # Get relative path
        rel_path = path.relative_to(root)
        rel_path_str = str(rel_path).replace("\\", "/")

        # Check extension
        suffix = path.suffix.lower()
        if suffix not in SYNC_EXTENSIONS and path.name not in SYNC_EXTENSIONS:
            continue

        # Check ignore patterns
        if should_ignore(rel_path, ignore_spec):
            continue

        # Compute hash
        try:
            file_hash = compute_file_hash(path)
            files[rel_path_str] = file_hash
        except Exception as e:
            print(f"  WARN: Could not hash {rel_path_str}: {e}")

    return files

# =============================================================================
# Diff Layer - Compute Sync Plan
# =============================================================================

def compute_diff(current_files: dict, manifest: dict) -> tuple[list, list, list]:
    """
    Compare current files to manifest and return (new, modified, deleted) lists.
    """
    new_files = []
    modified_files = []
    deleted_files = []

    manifest_paths = set(manifest.keys())
    current_paths = set(current_files.keys())

    # New files (in current but not in manifest)
    for path in current_paths - manifest_paths:
        new_files.append(path)

    # Modified files (in both but hash differs)
    for path in current_paths & manifest_paths:
        if current_files[path] != manifest[path].get("hash"):
            modified_files.append(path)

    # Deleted files (in manifest but not in current)
    for path in manifest_paths - current_paths:
        deleted_files.append(path)

    return new_files, modified_files, deleted_files

# =============================================================================
# Store Operations - Interact with Gemini File Search API
# =============================================================================

def list_store_documents(client: genai.Client) -> dict:
    """
    List all documents in the store, return dict of {display_name: doc_resource_name}.
    Uses display_name as truth (not doc_id from manifest).
    """
    docs = {}
    try:
        # List documents from the store
        response = client.files.list()
        for doc in response:
            if hasattr(doc, 'display_name') and doc.display_name:
                docs[doc.display_name] = doc.name
    except Exception as e:
        print(f"  WARN: Could not list store documents: {e}")
    return docs

def delete_document(client: genai.Client, doc_name: str) -> bool:
    """Delete a document from the store."""
    try:
        client.files.delete(name=doc_name)
        return True
    except Exception as e:
        print(f"  WARN: Could not delete {doc_name}: {e}")
        return False

def upload_file(client: genai.Client, file_path: str, existing_docs: dict, retries: int = 3) -> Optional[str]:
    """
    Upload a file to the store with defensive deduplication.
    Returns the document resource name on success, None on failure.
    """
    path = Path(file_path)
    display_name = file_path  # Use relative path as display name

    # Defensive dedup: check if already exists
    if display_name in existing_docs:
        # Delete existing first
        delete_document(client, existing_docs[display_name])

    for attempt in range(retries):
        try:
            # Read file content
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            # Upload to store
            response = client.files.upload(
                file=path,
                config=types.UploadFileConfig(
                    display_name=display_name,
                    mime_type="text/plain"
                )
            )

            return response.name

        except Exception as e:
            if attempt < retries - 1:
                # Exponential backoff
                wait_time = (2 ** attempt) + (time.time() % 1)
                print(f"  RETRY: {file_path} (attempt {attempt + 1}, waiting {wait_time:.1f}s)")
                time.sleep(wait_time)
            else:
                print(f"  ERROR: Failed to upload {file_path}: {e}")
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
    timestamp = datetime.utcnow().isoformat()

    # Get current store documents for deduplication
    print("\n  Fetching store documents for dedup check...")
    existing_docs = list_store_documents(client)
    print(f"  Found {len(existing_docs)} existing documents in store")

    # Track results
    uploaded = 0
    deleted = 0
    failed = 0

    # 1. Delete removed files
    if deleted_files:
        print(f"\n  Deleting {len(deleted_files)} removed files...")
        for path in deleted_files:
            if path in existing_docs:
                if delete_document(client, existing_docs[path]):
                    del manifest[path]
                    deleted += 1
                else:
                    failed += 1
            else:
                # Not in store, just remove from manifest
                del manifest[path]
                deleted += 1

    # 2. Handle modified files (delete then re-upload)
    if modified_files:
        print(f"\n  Updating {len(modified_files)} modified files...")
        for path in modified_files:
            # Delete old version
            if path in existing_docs:
                delete_document(client, existing_docs[path])

            # Upload new version
            doc_name = upload_file(client, path, {}, retries=3)
            if doc_name:
                manifest[path] = {
                    "hash": current_files[path],
                    "doc_id": doc_name,
                    "commit": commit_sha,
                    "last_synced": timestamp
                }
                uploaded += 1
                print(f"    UPDATED: {path}")
            else:
                failed += 1

    # 3. Upload new files in parallel
    if new_files:
        print(f"\n  Uploading {len(new_files)} new files (parallel, {MAX_WORKERS} workers)...")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            # Submit all upload tasks
            future_to_path = {
                executor.submit(upload_file, client, path, existing_docs): path
                for path in new_files
            }

            # Process completed uploads
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    doc_name = future.result()
                    if doc_name:
                        manifest[path] = {
                            "hash": current_files[path],
                            "doc_id": doc_name,
                            "commit": commit_sha,
                            "last_synced": timestamp
                        }
                        uploaded += 1
                        print(f"    UPLOADED: {path}")
                    else:
                        failed += 1
                except Exception as e:
                    print(f"    ERROR: {path}: {e}")
                    failed += 1

    # Summary
    print(f"\n  Sync complete: {uploaded} uploaded, {deleted} deleted, {failed} failed")

    return manifest

# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main sync orchestration."""
    print("=" * 60)
    print("Gemini File Search Sync v2.6.0")
    print("=" * 60)
    print(f"\nStore: {STORE_ID}")
    print(f"Time: {datetime.utcnow().isoformat()}")

    # Initialize client
    print("\n[1/5] Initializing Gemini client...")
    client = get_client()

    # Load ignore patterns
    print("\n[2/5] Loading ignore patterns...")
    ignore_spec = load_ignore_patterns()

    # Scan codebase
    print("\n[3/5] Scanning codebase...")
    current_files = scan_codebase(ignore_spec)
    print(f"  Found {len(current_files)} files to potentially sync")

    # Load manifest
    print("\n[4/5] Loading manifest...")
    manifest = load_manifest()
    print(f"  Manifest contains {len(manifest)} entries")

    # Compute diff
    new_files, modified_files, deleted_files = compute_diff(current_files, manifest)
    print(f"\n  Changes detected:")
    print(f"    NEW: {len(new_files)}")
    print(f"    MODIFIED: {len(modified_files)}")
    print(f"    DELETED: {len(deleted_files)}")

    # Check if anything to sync
    if not new_files and not modified_files and not deleted_files:
        print("\n  No changes to sync!")
        print("\n" + "=" * 60)
        return

    # Perform sync
    print("\n[5/5] Syncing to File Search Store...")
    manifest = sync_files(
        client,
        new_files,
        modified_files,
        deleted_files,
        manifest,
        current_files
    )

    # Save manifest atomically
    print("\n  Saving manifest...")
    save_manifest_atomic(manifest)
    print("  Manifest saved!")

    print("\n" + "=" * 60)
    print("Sync completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
