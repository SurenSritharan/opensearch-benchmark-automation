# Flame Graph Examples

This directory contains sample CPU flame graphs from OpenSearch Benchmark runs showing cosine similarity operations during bulk vector ingestion.

## Files

### Bulk Ingestion Flame Graphs

**jvector-bulk-ingest-cosine.html**
- **Engine**: JVector
- **Scenario**: Bulk vector ingestion (scenario-2)
- **Dataset**: MS MARCO (1024-dimensional vectors, cosine similarity)
- **Duration**: 45 seconds profiling
- **Key Observations**: Shows JVector's cosine similarity computation during index building

**lucene-bulk-ingest-cosine.html**
- **Engine**: Lucene
- **Scenario**: Bulk vector ingestion (scenario-2)
- **Dataset**: MS MARCO (1024-dimensional vectors, cosine similarity)
- **Duration**: 45 seconds profiling
- **Key Observations**: Shows Lucene's cosine similarity computation during index building

## How to View

Open any `.html` file in a web browser. The flame graphs are interactive:
- **Click** on a frame to zoom in
- **Search** for specific methods using the search box
- **Hover** over frames to see detailed timing information

## What to Look For

When analyzing these flame graphs for cosine similarity operations, look for:
- Stack traces containing "cosine", "similarity", "score", or "distance" methods
- Time spent in vector operations (dot product, normalization)
- JNI calls (for native implementations)
- Memory allocation patterns during scoring

## Generating Your Own

These flame graphs are automatically generated when `ENABLE_PROFILING=true` in the benchmark configuration. They are created using [async-profiler](https://github.com/async-profiler/async-profiler) and stored in the `profiles/` subdirectory of each benchmark run.