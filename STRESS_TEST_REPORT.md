# Stress Test Report - File Search Sync Architecture

**Date**: 2026-01-14
**Version**: v2.7.0
**Store**: `fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo`

---

## Test Configuration

| Parameter | Value |
|-----------|-------|
| Total Files Generated | 605 |
| Total Folders Created | 121 |
| Max Nesting Depth | 5 levels |
| Total Bytes | 2.49 MB |
| Parallel Workers | 8 |
| File Types | .ts, .tsx, .py, .js, .md, .json, .css |

---

## Performance Results

### Initial Sync (607 files)

| Metric | Value |
|--------|-------|
| **Total Duration** | 188.9s (3.1 minutes) |
| **Files Uploaded** | 607 |
| **Upload Success Rate** | 100% |
| **API Calls** | 608 |
| **API Errors** | 0 |
| **Bytes Uploaded** | 2.49 MB |
| **Throughput** | 3.2 files/second |

### Timing Breakdown

| Operation | Total Time | Avg Time | Count |
|-----------|------------|----------|-------|
| Scan Codebase | 0.333s | - | 1 |
| Hash Files | 0.148s | 0.0002s | 607 |
| Compute Diff | 0.012s | - | 1 |
| List Documents | 0.802s | - | 1 |
| Upload File | 1484.1s | **2.445s** | 607 |
| Parallel Batch | **186.3s** | - | 1 |

### Second Sync (No Changes)

| Metric | Value |
|--------|-------|
| **Total Duration** | 85.3s |
| **Files Detected as Changed** | 1 (manifest only) |
| **List Documents** | **81.0s** (95% of time!) |

### Third Sync (2 Modified Files)

| Metric | Value |
|--------|-------|
| **Total Duration** | 91.5s |
| **Modified Files Detected** | 2 |
| **Delete Operations** | 2 (avg 1.67s each) |
| **Upload Operations** | 2 (avg 2.59s each) |

---

## Identified Bottlenecks

### 1. **CRITICAL: `list_documents` API Call**

| Scenario | Documents | Time |
|----------|-----------|------|
| Initial (empty store) | 0 | 0.8s |
| Second sync | 607 | **81.0s** |
| Third sync | 608 | **81.1s** |

**Impact**: As the store grows, the `list_documents` call becomes the dominant bottleneck, taking 95% of total sync time on subsequent runs.

**Root Cause**: The Gemini Files API returns documents in a paginated manner, requiring multiple round-trips to list all documents.

### 2. **Sequential Modified File Updates**

Modified files are processed sequentially (delete then re-upload) rather than in parallel.

```
Current flow:
  for file in modified_files:
      delete(file)  # 1.67s
      upload(file)  # 2.59s
  # Total: 8.5s for 2 files
```

### 3. **Per-file Upload Latency**

| Metric | Value |
|--------|-------|
| Average upload time | 2.45s |
| Min upload time | 1.69s |
| Max upload time | 4.31s |

The API has a minimum ~1.7s latency floor per upload, regardless of file size.

---

## Friction Points

### 1. **Manifest File Gets Synced**
The `.file-search-manifest.json` file itself gets synced to the store, creating a circular dependency where every sync modifies the manifest, triggering a sync of the manifest.

**Recommendation**: Add `.file-search-manifest.json` to `.aiignore`.

### 2. **Deprecation Warnings**
Multiple `datetime.utcnow()` deprecation warnings clutter output.

### 3. **No Progress Indication During `list_documents`**
The 81-second `list_documents` call has no progress feedback, making it appear frozen.

---

## Optimization Opportunities

### High Priority

#### 1. **Skip `list_documents` When Manifest Is Trusted**

If the manifest exists and is recent, skip the expensive `list_documents` call and trust the manifest. Only fetch store state when:
- Manifest is missing
- User explicitly requests `--force-refresh`
- Manifest is older than threshold (e.g., 24 hours)

**Estimated Improvement**: 80-95% reduction in sync time for incremental syncs.

#### 2. **Parallel Modified File Processing**

Process modified files in parallel instead of sequentially:

```python
# Instead of:
for path in modified_files:
    delete_then_upload(path)

# Use:
with ThreadPoolExecutor() as executor:
    executor.map(delete_then_upload, modified_files)
```

**Estimated Improvement**: ~50% reduction in modified file sync time.

#### 3. **Exclude Manifest from Sync**

Add to `.aiignore`:
```
.file-search-manifest.json
```

### Medium Priority

#### 4. **Lazy Document Listing**

Only list store documents if deletions or deduplication is actually needed:
- If all changes are NEW files and manifest is fresh, skip listing
- Use manifest's `doc_id` field for delete operations

#### 5. **Batch Delete Operations**

If deleting multiple files, investigate if the API supports batch delete.

#### 6. **Connection Pooling**

Ensure HTTP connection pooling is enabled to reduce per-request overhead.

### Low Priority

#### 7. **Fix Deprecation Warnings**

Replace `datetime.utcnow()` with `datetime.now(datetime.UTC)`.

#### 8. **Progress Bar for Long Operations**

Add `tqdm` progress bar for `list_documents` and parallel uploads.

#### 9. **Configurable Chunk Size**

Allow chunk size to be configured per-file-type (larger for markdown, smaller for code).

---

## Deduplication Analysis

| Metric | Value |
|--------|-------|
| Dedup Checks | 607 |
| Dedup Hits | 0 (initial sync) |
| Defensive Delete | Working correctly |

The deduplication logic is working correctly:
- Files are checked against store before upload
- Duplicate display names are deleted before re-upload
- Hash-based change detection prevents unnecessary uploads

---

## Metadata Tagging

Each uploaded file now includes metadata:

```json
{
  "metadata": {
    "module": "src/types_fre/utils_hfv",
    "filename": "utils_hfv_cdkc_4.ts",
    "extension": ".ts"
  }
}
```

This enables:
- Filtering by module/folder
- File type analysis
- Better search context

---

## Async Parallel Operations

| Feature | Status |
|---------|--------|
| Parallel uploads (8 workers) | Working |
| Thread-safe metrics | Working |
| Rate limiting with backoff | Working |
| Parallel deletes | NOT IMPLEMENTED |
| Parallel modified files | NOT IMPLEMENTED |

---

## Recommendations Summary

| Priority | Optimization | Impact |
|----------|--------------|--------|
| **P0** | Skip `list_documents` with fresh manifest | 80-95% time reduction |
| **P1** | Exclude manifest from sync | Eliminates circular updates |
| **P1** | Parallel modified file processing | 50% reduction for updates |
| **P2** | Progress feedback for long operations | Better UX |
| **P2** | Lazy document listing | Reduce API calls |
| **P3** | Fix deprecation warnings | Code hygiene |

---

## Conclusion

The sync architecture successfully handles 607 files across 121 nested folders with:
- 100% upload success rate
- Correct deduplication logic
- Proper change detection (new/modified/deleted)
- Metadata tagging
- Comprehensive logging

**Primary bottleneck**: The `list_documents` API call dominates sync time (81s for 607 documents). Implementing manifest-trusted mode would reduce typical sync time from 90s to under 10s.

---

## Test Artifacts

- Log files: `logs/sync_*.log`
- Manifest: `.file-search-manifest.json`
- Test files: `src/**/*`

Cleanup: `python scripts/stress_test.py --cleanup`
