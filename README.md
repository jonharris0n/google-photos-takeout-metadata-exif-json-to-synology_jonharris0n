# Google Photos Takeout Metadata Processor

## Context

This project contains Python scripts for processing and organizing photos/videos, specifically for managing date metadata. When exporting from Google Photos using Google Takeout, metadata is frequently missing from photos and videos. These scripts allow you to reintegrate media metadata from `.json` files into media archives retrieved from Google Takeout. The script works on photos and videos, including iOS Live Photos and various known types.

It was initially designed for importing media to a Synology NAS, but can be used for importing to any other type of private or public photo library.

## Features

### Core Processing
- **Multi-strategy JSON matching**: Uses 6 different strategies to find JSON metadata files
- **Enhanced metadata matching**: Analyzes file content, size, camera info, and GPS data
- **Conservative mode**: High-accuracy matching with stricter validation
- **EXIF data update**: Updates image timestamps and GPS coordinates
- **System timestamp update**: Updates file creation/modification dates
- **Dry run mode**: Test operations without modifying files

### Performance & Scalability
- **Adaptive caching system**: Intelligent memory management for datasets up to 500,000+ files
- **Multi-threaded processing**: Thread-safe parallel processing optimized for multi-core systems
- **Memory-efficient design**: Scalable cache limits with priority-based sampling for very large datasets
- **Intelligent sampling**: For 100k+ files, uses priority scoring to cache most important files first
- **Dynamic optimization**: Automatically adjusts cache sizes and garbage collection based on dataset size

### Monitoring & Logging
- **Real-time progress tracking**: Live progress bars with ETA and processing speed
- **Comprehensive logging**: Multi-level logging with separate error files
- **Cache performance metrics**: Detailed statistics for cache efficiency and memory usage
- **Match confidence scoring**: Confidence levels (HIGH/MEDIUM/LOW) for advanced matching strategies
- **Processing analytics**: Detailed breakdowns of file processing and matching success rates

## User Guide

### Download Data from Google Photos

1. **Access Google Takeout:**
   - Go to [Google Takeout](https://takeout.google.com/)
   - Log in to your Google account

2. **Select data for export:**
   - By default, all Google services are selected
   - Click on "Deselect all"
   - Check "Google Photos" only

3. **Configure export:**
   - Choose export frequency (single export recommended in our case)
   - Select file format (zip recommended)
   - Choose maximum archive size (recommended: 4 GB)
   - Click on "Create export"

4. **Download archive:**
   - Google will prepare your data (may take several hours depending on size)
   - You will receive an email when the export is ready
   - Download the archive via the links provided or from https://takeout.google.com/manage

> Note: Google Takeout archives can be very large. Make sure you have enough free disk space before you start downloading.

### Environment Preparation

1. **Download Python:**
   - Go to [python.org](https://www.python.org)
   - Download the latest stable version of Python 3.x

2. **Check installation:**
   ```bash
   python --version # Show Python version
   python3 --version 
   ```

3. **Create a Python virtual environment:**
   ```bash
   python3 -m venv venvmedia
   source venvmedia/bin/activate # On Windows, use venv\Scripts\activate
   ```
   To exit virtual environment: `deactivate`

4. **Install dependencies:**
   ```bash
   pip install -r requirements.txt --verbose
   ```

### Running Scripts

- **Extracting and moving files:**
  ```bash
  python 01_extract_takeout_files.py <source_dir> <work_dir>  
  ```
  - `<source_dir>`: Source folder containing Takeout archives
  - `<work_dir>`: Destination folder for extracted folders and files, then used as work_dir

- **Updating media metadata:**
  ```bash
  python 02_update_media_metadata.py <work_dir> [--conservative] [--debug] [--dry-run] [--no-progress]  
  ```
  - `<work_dir>`: Working folder containing media files
  - `--conservative`: Use conservative matching (higher accuracy, fewer matches)
  - `--debug`: Activates verbose debug mode
  - `--dry-run`: Simulation without file modification
  - `--no-progress`: Disable progress bars (useful for automated scripts or logging)

## Technical Explanations

- **01_extract_takeout_files.py**: This script extracts data from various Takeout archives and moves them into a single folder. The source folder must contain the zip archives downloaded from Google Takeout.

- **02_update_media_metadata.py**: This script matches metadata contained in JSON files with media (photos and videos) stored in `<work_dir>`. Features an advanced multi-threaded processing engine with intelligent caching for datasets up to 500,000+ files. It contains various special cases and rules for searching JSON files associated with various media (LivePhotos, modified photos, duplicate media, truncated filenames). It updates the creation and modification dates of files, as well as the EXIF metadata of images. It also updates GPS coordinates in EXIF metadata.

### Advanced Features
- **Thread-safe processing**: Concurrent file processing with race condition prevention
- **Intelligent file prioritization**: Priority scoring based on file age, patterns, and metadata richness  
- **Adaptive memory management**: Dynamic cache sizing based on dataset characteristics
- **Enhanced error handling**: Comprehensive error tracking with detailed diagnostic information
- **Progress monitoring**: Real-time processing statistics with ETA and throughput metrics

## Matching Strategies

The script uses a 6-tier matching strategy to find JSON metadata files:

1. **Exact filename matching** - `photo.jpg` → `photo.jpg.json` (99%+ accuracy ✅)
2. **Supplemental metadata matching** - Handles Google Photos naming conventions (95%+ accuracy ✅)
3. **Numbered duplicate handling** - `IMG_XXXX(2).JPG` → `IMG_XXXX.JPG.supplemental-metadata(2).json` (90%+ accuracy ✅)
4. **Cleaned filename + truncation** - Removes editing suffixes and progressive matching (70-85% accuracy ⚠️)
5. **Metadata-based search** - Analyzes JSON content for title, size, camera, GPS data (60-80% accuracy ⚠️)
6. **Last resort matching** - Very loose similarity matching, skipped in conservative mode (50-65% accuracy ❌)

### Conservative vs Normal Mode

- **Conservative Mode (`--conservative`)**: Higher accuracy, fewer false positives, stricter thresholds (recommended for valuable collections)
- **Normal Mode**: More matches with reasonable accuracy, includes all strategies (recommended for testing)

## Results

At the end of execution of the `02_update_media_metadata.py` script, comprehensive statistics are displayed:

### Processing Summary
```
===== Final results =====
Files processed successfully: X,XXX
Files processed with warnings: XX
JSON files found in another directory: XX
JSON files not found: XX

Processing completed in XX:XX (H:MM)
Average speed: XXX files/minute
Peak memory usage: XXX MB
Cache efficiency: XX.X% files cached
```

### Cache Performance (for large datasets)
```
Very Large Dataset Cache Summary:
  • JSON files found: 98,661
  • JSON files processed: 59,226/98,661 (60.0% files cached)
  • JSON cache entries: 118,393 (multiple keys per file for fast lookup)
  • Metadata cache entries: 25,000/100,000
  • Fast lookup cache: 59,226 entries
  • Stem lookup cache: 59,226 entries
  • Memory optimization: GC interval set to 4,933 files
  • Intelligent sampling: Using priority-based caching for optimal performance
```

### Explanation of Statistics
- **Files processed successfully**: Media files with successful metadata updates
- **Files processed with warnings**: Files with errors during EXIF modification, but system date modification was usually successful
- **JSON files found in another directory**: Files matched using advanced strategies
- **JSON files not found**: Files without JSON matches (with list of affected files displayed)
- **Cache efficiency**: Percentage of JSON files that were cached for fast lookup vs. direct file access
- **Processing speed**: Files processed per minute, useful for estimating time for similar datasets

> Note: This information can be used to check the effectiveness of processing and identify files requiring particular attention. For very large datasets (100k+ files), cache efficiency of 60-80% is normal and provides optimal memory/performance balance.

## Dry Run Mode

The `--dry-run` mode allows to:
- Simulate all operations without modifying files
- Check actions that would otherwise be performed
- Test detection of JSON files
- Validate processes before modifying files

## Additional Features

At the end of execution of the `02_update_media_metadata.py` script, different options are available:

1. **Manual date assignment**
   - For files without associated and located JSON, manual date assignment possible
   - Expected format: YYYY-MM-DD HH:MM
   - You can skip a file by pressing Enter

2. **Clean JSON files** (currently disabled)
   - Option to delete all JSON files after processing
   - Useful for freeing up space after metadata processing
   - Use `cd <work_dir>; find . -type f -name "*.json" -delete` for deleting JSON files

3. **Clean empty directories** (currently disabled)
   - Option to delete all empty directories after processing

## Performance Optimization

### Adaptive Caching System
The script features an intelligent caching system that automatically scales based on your dataset size:

#### Cache Sizing Strategy
- **Small datasets (< 50k files)**: Base cache limits with full file coverage
- **Medium datasets (50k-100k files)**: Scaled cache with 20% buffer for complete coverage
- **Large datasets (100k+ files)**: Intelligent sampling with priority-based caching

#### Memory Management
- **Base limits**: 50,000 JSON cache entries / 25,000 metadata entries
- **Maximum limits**: 500,000 JSON cache entries / 100,000 metadata entries (memory safety)
- **Priority scoring**: Files are ranked by modification date, filename patterns, and metadata richness
- **Adaptive garbage collection**: Frequency automatically adjusts based on dataset size

#### Cache Performance Indicators
```
Very Large Dataset Cache Summary:
  • JSON files found: 98,661
  • JSON files processed: 59,226/98,661 (60.0% files cached)
  • JSON cache entries: 118,393 (multiple keys per file for fast lookup)
  • Metadata cache entries: 25,000/100,000
  • Fast lookup cache: 59,226 entries
  • Stem lookup cache: 59,226 entries
  • Memory optimization: GC interval set to 4,933 files
  • Intelligent sampling: Using priority-based caching for optimal performance
```

### Thread Pool Optimization
The script automatically detects your CPU and optimizes thread count:

- **2-4 CPU cores**: Uses 2x CPU cores (up to 8 threads)
- **5-8 CPU cores**: Uses 1.5x CPU cores (up to 16 threads)  
- **9+ CPU cores**: Uses CPU cores + 4 (up to 24 threads maximum)

**For I/O-intensive workloads** like this script, using 2-3x your CPU core count is optimal since threads spend time waiting for file operations rather than consuming CPU.

### Thread Safety Features
- **Race condition prevention**: Double-checked locking patterns for cache building
- **Lock-free processing**: Cache updates disabled during threaded processing to avoid contention
- **Thread-safe statistics**: Protected counters and file lists with proper synchronization
- **Memory safety**: Explicit cleanup and garbage collection to prevent memory leaks

### Custom Thread Count
If you want to manually adjust thread count for your specific system:

1. **Check your CPU cores:**
   ```bash
   # On macOS/Linux
   sysctl hw.ncpu
   # Or
   nproc
   
   # On Windows
   echo %NUMBER_OF_PROCESSORS%
   ```

2. **Modify the script** (around line 1950 in `process_directory()` method):
   ```python
   # Replace automatic detection with manual setting
   max_workers = XX  # Set your desired thread count
   ```

3. **Guidelines for thread count:**
   - **Conservative**: 1x CPU cores (safest, slower)
   - **Balanced**: 2x CPU cores (recommended for most systems)
   - **Aggressive**: 3x CPU cores (fastest, may cause system stress)
   - **Maximum**: Never exceed 24 threads

### Performance Best Practices
- **SSD storage recommended**: Significantly improves file I/O performance
- **Sufficient RAM**: 8GB+ recommended for large datasets (100k+ files)
- **Monitor cache efficiency**: Check log output for cache coverage percentages
- **Use conservative mode**: For valuable collections where accuracy > speed

## Supported File Formats

### Images
- JPEG (.jpg, .jpeg), PNG (.png), HEIC (.heic)
- GIF (.gif), BMP (.bmp), TIFF (.tiff, .tif), WebP (.webp)

### Videos
- MP4 (.mp4), MOV (.mov), AVI (.avi), MKV (.mkv), M4V (.m4v)

## Output Files

The script generates several log files with detailed processing information:

### Log Files
- `media_processor_log_YYYYMMDD_HHMMSS.log` - Complete processing log with cache statistics
- `media_processor_errors_YYYYMMDD_HHMMSS.log` - Errors and warnings only
- `files_without_json_YYYYMMDD_HHMMSS.txt` - Files without JSON matches
- `missing_json_analysis_YYYYMMDD_HHMMSS.txt` - Analysis of unmatched files with pattern breakdown

### Log Content Examples
```
2025-07-13 14:00:35,526 - INFO - Adaptive cache sizing: Found 98,661 JSON files, set cache limits to 118,393/59,196 (GC interval: 1000)
2025-07-13 14:01:01,349 - WARNING - JSON cache limit (118,393) reached after processing 59,226/98,661 files (60.0% files cached)
2025-07-13 14:05:15,142 - INFO - JSON file found via enhanced metadata matching: /path/to/file.json
2025-07-13 14:05:15,143 - INFO - Match confidence: HIGH (score: 0.87, components: {'title': 0.95, 'type_compatible': True})
```

### Analysis Files
The analysis files provide detailed breakdowns of:
- File extension distributions for unmatched files
- Common filename patterns that failed to match
- Suggested improvements for matching strategies
- Performance metrics and processing efficiency

## Example Workflow

### Basic Workflow
1. **Extract Google Photos Takeout**
   ```bash
   unzip takeout-YYYYMMDD-THHMMSS-001.zip
   ```

2. **Test with dry run**
   ```bash
   python3 02_update_media_metadata.py ./Takeout --conservative --dry-run --debug
   ```

3. **Process files**
   ```bash
   python3 02_update_media_metadata.py ./Takeout --conservative
   ```

### Large Dataset Workflow (100k+ files)
1. **Initial assessment with progress disabled**
   ```bash
   python3 02_update_media_metadata.py ./Takeout --dry-run --no-progress > assessment.log 2>&1
   ```

2. **Check cache efficiency in logs**
   ```bash
   grep -i "cache" assessment.log
   grep -i "files cached" assessment.log
   ```

3. **Process with optimized settings**
   ```bash
   python3 02_update_media_metadata.py ./Takeout --conservative --no-progress > processing.log 2>&1 &
   ```

4. **Monitor progress**
   ```bash
   tail -f processing.log | grep -E "(INFO|WARNING|ERROR)"
   ```

### Post-Processing Review
1. **Check processing summary**
   ```bash
   grep -A 10 "Final results" processing.log
   ```

2. **Review cache performance**
   ```bash
   grep -A 15 "Cache Summary" processing.log
   ```

3. **Analyze unmatched files**
   ```bash
   ls -la *missing_json_analysis*.txt
   ls -la *files_without_json*.txt
   ```

4. **Verify sample files**
   - Check a few sample files have correct dates
   - Verify EXIF data was properly updated
   - Confirm GPS coordinates if applicable

## Best Practices

### For All Datasets
1. **Always use dry run first** to test on your specific archive
2. **Use conservative mode** for valuable photo collections
3. **Monitor log files** for match confidence and errors
4. **Keep backups** of original files before processing
5. **Review unmatched files** manually for important photos

### For Large Datasets (50k+ files)
6. **Ensure sufficient RAM** (8GB+ recommended for 100k+ files)
7. **Use SSD storage** for significantly improved performance
8. **Monitor cache efficiency** in log output - 60-80% is optimal for very large datasets
9. **Consider processing in batches** if memory is limited
10. **Allow extra time** for initial cache building on very large datasets

### Performance Optimization
11. **Disable progress bars** (`--no-progress`) for automated scripts or when logging to files
12. **Use debug mode sparingly** - only when troubleshooting specific issues
13. **Close other applications** during processing of very large datasets
14. **Monitor system resources** during processing to ensure stability

## Contributing

This script is based on the original work from:
https://github.com/alexmonnerie/google-photos-takeout-metadata-exif-json-to-synology
