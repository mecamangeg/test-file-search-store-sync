#!/usr/bin/env python3
"""
Stress Test for File Search Sync Architecture
Generates test files in nested folder structures and monitors sync performance.
"""

import os
import sys
import random
import string
import time
from pathlib import Path
from datetime import datetime, timezone

# Test configuration
TEST_ROOT = Path("src")
NESTED_DEPTH = 4  # Max folder nesting depth
FOLDERS_PER_LEVEL = 3
FILES_PER_FOLDER = 5
FILE_SIZES = [500, 1000, 2000, 5000, 10000]  # bytes

# File templates
TEMPLATES = {
    ".ts": '''// {filename}
// Generated: {timestamp}
// Module: {module}

interface {class_name}Config {{
  id: string;
  name: string;
  enabled: boolean;
  options: Record<string, unknown>;
}}

export class {class_name} {{
  private config: {class_name}Config;
  private initialized: boolean = false;

  constructor(config: {class_name}Config) {{
    this.config = config;
  }}

  async initialize(): Promise<void> {{
    if (this.initialized) return;
    console.log(`Initializing ${{this.config.name}}...`);
    this.initialized = true;
  }}

  process(data: string): string {{
    return data.toUpperCase();
  }}

  // {random_content}
}}

export default {class_name};
''',

    ".tsx": '''// {filename}
// Generated: {timestamp}

import React, {{ useState, useEffect }} from 'react';

interface {class_name}Props {{
  title: string;
  items: string[];
  onSelect: (item: string) => void;
}}

export const {class_name}: React.FC<{class_name}Props> = ({{ title, items, onSelect }}) => {{
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {{
    setLoading(false);
  }}, []);

  const handleClick = (item: string) => {{
    setSelected(item);
    onSelect(item);
  }};

  if (loading) return <div>Loading...</div>;

  return (
    <div className="container">
      <h2>{{title}}</h2>
      <ul>
        {{items.map((item, i) => (
          <li key={{i}} onClick={{() => handleClick(item)}}>
            {{item}} {{selected === item && '✓'}}
          </li>
        ))}}
      </ul>
    </div>
  );
}};

// {random_content}
''',

    ".py": '''#!/usr/bin/env python3
"""
{filename}
Generated: {timestamp}
Module: {module}
"""

from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import asyncio


@dataclass
class {class_name}Config:
    """Configuration for {class_name}."""
    id: str
    name: str
    enabled: bool = True
    options: Dict[str, Any] = None


class {class_name}:
    """Main class for {module} functionality."""

    def __init__(self, config: {class_name}Config):
        self.config = config
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the component."""
        if self._initialized:
            return
        print(f"Initializing {{self.config.name}}...")
        self._initialized = True

    def process(self, data: str) -> str:
        """Process input data."""
        return data.upper()

    def validate(self, items: List[str]) -> bool:
        """Validate items."""
        return all(item for item in items)

    # {random_content}


if __name__ == "__main__":
    config = {class_name}Config(id="test", name="Test")
    instance = {class_name}(config)
    asyncio.run(instance.initialize())
''',

    ".js": '''// {filename}
// Generated: {timestamp}
// Module: {module}

const {class_name} = {{
  config: {{
    id: '{id}',
    name: '{class_name}',
    enabled: true,
  }},

  initialized: false,

  async init() {{
    if (this.initialized) return;
    console.log(`Initializing ${{this.config.name}}...`);
    this.initialized = true;
  }},

  process(data) {{
    return data.toUpperCase();
  }},

  validate(items) {{
    return items.every(Boolean);
  }},
}};

// {random_content}

module.exports = {class_name};
''',

    ".md": '''# {class_name}

> Generated: {timestamp}

## Overview

This module provides {class_name} functionality for the {module} system.

## Installation

```bash
npm install @project/{id}
```

## Usage

```typescript
import {{ {class_name} }} from '@project/{id}';

const instance = new {class_name}({{
  id: '{id}',
  name: '{class_name}',
}});

await instance.initialize();
```

## API Reference

### `initialize()`

Initializes the {class_name} instance.

### `process(data: string): string`

Processes the input data and returns transformed output.

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| id | string | required | Unique identifier |
| name | string | required | Display name |
| enabled | boolean | true | Enable/disable flag |

---

{random_content}
''',

    ".json": '''{{
  "name": "{id}",
  "version": "1.0.0",
  "description": "{class_name} configuration",
  "generated": "{timestamp}",
  "module": "{module}",
  "config": {{
    "enabled": true,
    "debug": false,
    "timeout": 5000,
    "retries": 3
  }},
  "dependencies": {{
    "core": "^1.0.0",
    "utils": "^2.0.0"
  }},
  "metadata": {{
    "author": "stress-test",
    "tags": ["test", "generated", "{module}"]
  }}
}}
''',

    ".css": '''/* {filename}
 * Generated: {timestamp}
 * Module: {module}
 */

.{id}-container {{
  display: flex;
  flex-direction: column;
  padding: 1rem;
  margin: 0 auto;
  max-width: 1200px;
}}

.{id}-header {{
  font-size: 1.5rem;
  font-weight: 600;
  margin-bottom: 1rem;
  color: #1a1a1a;
}}

.{id}-content {{
  background: #ffffff;
  border-radius: 8px;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  padding: 1.5rem;
}}

.{id}-item {{
  padding: 0.75rem;
  border-bottom: 1px solid #e5e5e5;
  transition: background 0.2s ease;
}}

.{id}-item:hover {{
  background: #f5f5f5;
}}

/* {random_content} */
'''
}

def random_string(length: int) -> str:
    """Generate random string."""
    return ''.join(random.choices(string.ascii_lowercase, k=length))

def random_content(size: int) -> str:
    """Generate random content of approximate size."""
    words = []
    current_size = 0
    while current_size < size:
        word = random_string(random.randint(3, 12))
        words.append(word)
        current_size += len(word) + 1
    return ' '.join(words)

def to_class_name(name: str) -> str:
    """Convert to PascalCase class name."""
    parts = name.replace('-', '_').split('_')
    return ''.join(p.capitalize() for p in parts)

def generate_file(folder: Path, index: int, depth: int) -> Path:
    """Generate a test file with random content."""
    extensions = list(TEMPLATES.keys())
    ext = random.choice(extensions)

    base_name = f"{folder.name}_{random_string(4)}_{index}"
    filename = f"{base_name}{ext}"
    filepath = folder / filename

    template = TEMPLATES[ext]
    content = template.format(
        filename=filename,
        timestamp=datetime.now(timezone.utc).isoformat(),
        module=str(folder).replace('\\', '/'),
        class_name=to_class_name(base_name),
        id=base_name.lower().replace('_', '-'),
        random_content=random_content(random.choice(FILE_SIZES))
    )

    filepath.write_text(content, encoding='utf-8')
    return filepath

def generate_folder_structure(
    root: Path,
    current_depth: int = 0,
    stats: dict = None
) -> dict:
    """Recursively generate folder structure with files."""
    if stats is None:
        stats = {'folders': 0, 'files': 0, 'bytes': 0}

    root.mkdir(parents=True, exist_ok=True)
    stats['folders'] += 1

    # Generate files in current folder
    for i in range(FILES_PER_FOLDER):
        filepath = generate_file(root, i, current_depth)
        stats['files'] += 1
        stats['bytes'] += filepath.stat().st_size

    # Recurse into subfolders
    if current_depth < NESTED_DEPTH:
        folder_names = [
            'components', 'services', 'utils', 'hooks', 'api',
            'models', 'types', 'config', 'helpers', 'lib',
            'features', 'modules', 'core', 'shared', 'common'
        ]
        selected = random.sample(folder_names, min(FOLDERS_PER_LEVEL, len(folder_names)))

        for name in selected:
            subfolder = root / f"{name}_{random_string(3)}"
            generate_folder_structure(subfolder, current_depth + 1, stats)

    return stats

def cleanup_test_files(root: Path):
    """Remove all generated test files."""
    import shutil
    if root.exists():
        shutil.rmtree(root)
        print(f"Cleaned up: {root}")

def main():
    """Generate test files for stress testing."""
    print("=" * 60)
    print("File Search Sync - Stress Test Generator")
    print("=" * 60)

    # Check for cleanup flag
    if len(sys.argv) > 1 and sys.argv[1] == '--cleanup':
        cleanup_test_files(TEST_ROOT)
        return

    print(f"\nConfiguration:")
    print(f"  Root: {TEST_ROOT}")
    print(f"  Max depth: {NESTED_DEPTH}")
    print(f"  Folders per level: {FOLDERS_PER_LEVEL}")
    print(f"  Files per folder: {FILES_PER_FOLDER}")

    start = time.time()

    # Generate structure
    print(f"\nGenerating test files...")
    stats = generate_folder_structure(TEST_ROOT)

    duration = time.time() - start

    print(f"\nGeneration complete!")
    print(f"  Folders created: {stats['folders']}")
    print(f"  Files created: {stats['files']}")
    print(f"  Total bytes: {stats['bytes']:,}")
    print(f"  Duration: {duration:.2f}s")

    # Calculate expected structure
    expected_files = sum(
        FILES_PER_FOLDER * (FOLDERS_PER_LEVEL ** d)
        for d in range(NESTED_DEPTH + 1)
    )
    print(f"\nExpected files (formula): ~{expected_files}")

    print("\n" + "=" * 60)
    print("Run sync with: python scripts/sync_to_file_search.py")
    print("Cleanup with: python scripts/stress_test.py --cleanup")
    print("=" * 60)

if __name__ == "__main__":
    main()
