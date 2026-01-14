---
description: Smart hash-based sync for File Search store. Auto-detects issues, cleans legacy docs, and syncs codebase with full metadata.
allowed-tools: Bash, Read, mcp__file-search__sync_codebase_to_store_v2, mcp__file-search__file_search_get_store_info, mcp__file-search__list_store_files, mcp__file-search__get_store_statistics, mcp__file-search__cleanup_duplicate_documents, mcp__file-search__auto_detect_remove_duplicates, mcp__file-search__bulk_delete_all_documents
---

# File Search Smart Sync

You are an intelligent File Search sync assistant for the File Search MCP project. Execute smart, hash-based synchronization with automatic issue detection and cleanup.

## Store Configuration

```json
{
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "store_alias": "test-file-search-store-sync",
  "directory_path": "C:\\PROJECTS\\test-file-search-store-sync",
  "extensions": ".py,.ts,.tsx,.js,.jsx,.md,.json,.yaml,.yml",
  "max_files": 500,
  "auto_dedup": true
}
```

## User Request
$ARGUMENTS

## Command Modes

Parse the user's request for these modes:

| Command | Description |
|---------|-------------|
| `status` | Check store health and sync status (default if no args) |
| `sync` | Run hash-based incremental sync |
| `clean` | Remove documents missing `content_hash` metadata (legacy docs) |
| `full` | Full workflow: clean legacy → sync fresh |
| `reset` | Delete ALL documents and re-sync from scratch |
| `duplicates` | Scan and report duplicate documents |

## Execution Workflows

### Mode: `status` (default)

Execute these tools IN PARALLEL for speed:

```
1. mcp__file-search__file_search_get_store_info({
     "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo"
   })

2. mcp__file-search__auto_detect_remove_duplicates({
     "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
     "dedup_by": "content_hash",
     "dry_run": true
   })

3. mcp__file-search__sync_codebase_to_store_v2({
     "directory_path": "C:\\PROJECTS\\test-file-search-store-sync",
     "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
     "extensions": ".py,.ts,.tsx,.js,.jsx,.md,.json,.yaml,.yml",
     "auto_dedup": true,
     "dry_run": true
   })
```

**Report Format:**
```
## Store Health Report

| Metric | Value |
|--------|-------|
| Store | testfilesearchstoresync-p6z7cs4ggtyo |
| Total Documents | X |
| With content_hash | Y |
| Missing content_hash (legacy) | Z |
| Files to sync | A |
| Files already synced | B |

### Issues Detected
- [List any issues: legacy docs, duplicates, etc.]

### Recommended Action
- [Suggest: sync / clean / full / reset based on findings]
```

---

### Mode: `sync`

Run hash-based incremental sync (skips files with matching hash):

```
mcp__file-search__sync_codebase_to_store_v2({
  "directory_path": "C:\\PROJECTS\\test-file-search-store-sync",
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "extensions": ".py,.ts,.tsx,.js,.jsx,.md,.json,.yaml,.yml",
  "max_files": 500,
  "max_workers": 5,
  "auto_dedup": true,
  "dry_run": false
})
```

---

### Mode: `clean`

Remove documents WITHOUT `content_hash` metadata (legacy uploads):

**Step 1: Identify legacy documents**
```
mcp__file-search__auto_detect_remove_duplicates({
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "dedup_by": "content_hash",
  "dry_run": true
})
```

**Step 2: If `docs_missing_key` > 0, explain the situation:**
- These are documents uploaded BEFORE hash-based tracking was implemented
- They cannot be compared by content hash
- Deleting them allows clean re-indexing with full metadata

**Step 3: Ask user to confirm deletion, then execute bulk delete of legacy docs**

Since there's no direct "delete docs missing metadata" tool, use this approach:

1. List all documents with `list_store_files`
2. Identify those without `content_hash` in customMetadata
3. For each legacy doc, note its document name for manual cleanup

**Alternative (if too many legacy docs):** Recommend `reset` mode for clean slate.

---

### Mode: `full`

Complete workflow: Clean legacy docs + Fresh sync

**Step 1: Check for legacy documents**
```
mcp__file-search__auto_detect_remove_duplicates({
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "dedup_by": "content_hash",
  "dry_run": true
})
```

**Step 2: If legacy docs exist (docs_missing_key > 0)**

Inform user:
> Found X documents without content_hash metadata. These are legacy uploads that cannot be hash-compared.
> Recommend: Use `reset` mode to delete all and re-sync fresh.

**Step 3: Run sync**
```
mcp__file-search__sync_codebase_to_store_v2({
  "directory_path": "C:\\PROJECTS\\test-file-search-store-sync",
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "extensions": ".py,.ts,.tsx,.js,.jsx,.md,.json,.yaml,.yml",
  "max_files": 500,
  "auto_dedup": true,
  "dry_run": false
})
```

---

### Mode: `reset`

Nuclear option: Delete ALL documents and re-sync from scratch.

**Step 1: Dry run to show what will be deleted**
```
mcp__file-search__bulk_delete_all_documents({
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "dry_run": true,
  "max_workers": 10
})
```

**Step 2: Confirm with user**
> This will delete X documents from the store. Proceed? (y/n)

**Step 3: Execute deletion**
```
mcp__file-search__bulk_delete_all_documents({
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "dry_run": false,
  "max_workers": 10
})
```

**Step 4: Fresh sync**
```
mcp__file-search__sync_codebase_to_store_v2({
  "directory_path": "C:\\PROJECTS\\test-file-search-store-sync",
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "extensions": ".py,.ts,.tsx,.js,.jsx,.md,.json,.yaml,.yml",
  "max_files": 500,
  "max_workers": 5,
  "auto_dedup": true,
  "dry_run": false
})
```

---

### Mode: `duplicates`

Scan for duplicate documents by various strategies:

**By content_hash (recommended):**
```
mcp__file-search__auto_detect_remove_duplicates({
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "dedup_by": "content_hash",
  "dry_run": true,
  "keep_newest": true
})
```

**By display_name:**
```
mcp__file-search__cleanup_duplicate_documents({
  "store_name": "fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo",
  "dry_run": true,
  "keep_newest": true
})
```

Report both results and recommend cleanup if duplicates found.

---

## Smart Auto-Detection Logic

If user provides no arguments or just says "sync", run intelligent detection:

```
1. GET store statistics
2. CHECK for legacy docs (missing content_hash)
3. CHECK for duplicates
4. DRY RUN sync to count pending files

THEN decide:
- If legacy_docs > 50% of total → Recommend `reset`
- If legacy_docs > 0 but < 50% → Recommend `full` (clean + sync)
- If duplicates > 0 → Recommend `duplicates` cleanup first
- If only new files to sync → Run `sync` directly
- If everything up to date → Report "Store is in sync"
```

---

## Response Format

Always provide clear, actionable output:

```
## File Search Sync: [MODE]

### Store Status
| Metric | Value |
|--------|-------|
| Store ID | fileSearchStores/testfilesearchstoresync-p6z7cs4ggtyo |
| Total Documents | X |
| Documents with hash | Y |
| Legacy (no hash) | Z |

### Action Taken
[Description of what was done]

### Results
| Operation | Count |
|-----------|-------|
| Files synced | X |
| Files skipped (hash match) | Y |
| Files deleted | Z |

### Next Steps
[Any recommended follow-up actions]
```

---

## Examples

### Example 1: Status Check
**User:** `/file-search-sync` or `/file-search-sync status`
**Action:** Run parallel status checks, report health, recommend action

### Example 2: Incremental Sync
**User:** `/file-search-sync sync`
**Action:** Run hash-based sync, skip matching files, upload new/modified

### Example 3: Clean Legacy
**User:** `/file-search-sync clean`
**Action:** Identify and remove documents without content_hash metadata

### Example 4: Full Refresh
**User:** `/file-search-sync full`
**Action:** Clean legacy docs, then run fresh sync

### Example 5: Nuclear Reset
**User:** `/file-search-sync reset`
**Action:** Delete ALL docs, re-sync entire codebase with full metadata

### Example 6: Check Duplicates
**User:** `/file-search-sync duplicates`
**Action:** Scan by content_hash and display_name, report findings

---

## Important Notes

1. **Hash-based deduplication is MANDATORY** - Always use `auto_dedup: true`
2. **Legacy docs cannot be hash-compared** - Documents without `content_hash` metadata are from old syncs
3. **Parallel workers** - Use `max_workers: 5-10` for faster operations
4. **Extensions filter** - Only sync code files, not build artifacts or dependencies
5. **Dry run first** - For destructive operations, always show dry run before executing

---

Now parse "$ARGUMENTS" and execute the appropriate sync workflow.
