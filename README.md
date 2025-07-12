# Google Photos Takeout Metadata Processor

## Context

This project contains Python scripts for processing and organizing photos/videos, specifically for managing date metadata. When exporting from Google Photos using Google Takeout, metadata is frequently missing from photos and videos. These scripts allow you to reintegrate media metadata from `.json` files into media archives retrieved from Google Takeout. The script works on photos and videos, including iOS Live Photos and various known types.

It was initially designed for importing media to a Synology NAS, but can be used for importing to any other type of private or public photo library.

## Features

- **Multi-strategy JSON matching**: Uses 6 different strategies to find JSON metadata files
- **Enhanced metadata matching**: Analyzes file content, size, camera info, and GPS data
- **Conservative mode**: High-accuracy matching with stricter validation
- **Parallel processing**: Optimized for multi-core systems with thread safety
- **Progress tracking**: Real-time progress with ETA and processing speed
- **Comprehensive logging**: Debug logs, error tracking, and match confidence scoring
- **Dry run mode**: Test operations without modifying files
- **EXIF data update**: Updates image timestamps and GPS coordinates
- **System timestamp update**: Updates file creation/modification dates

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
  python 02_update_media_metadata.py <work_dir> [--conservative] [--debug] [--dry-run]  
  ```
  - `<work_dir>`: Working folder containing media files
  - `--conservative`: Use conservative matching (higher accuracy, fewer matches)
  - `--debug`: Activates verbose debug mode
  - `--dry-run`: Simulation without file modification

## Technical Explanations

- **01_extract_takeout_files.py**: This script extracts data from various Takeout archives and moves them into a single folder. The source folder must contain the zip archives downloaded from Google Takeout.

- **02_update_media_metadata.py**: This script matches metadata contained in JSON files with media (photos and videos) stored in `<work_dir>`. It contains various special cases and rules for searching JSON files associated with various media (LivePhotos, modified photos, duplicate media, truncated filename). It updates the creation and modification dates of files, as well as the EXIF metadata of images. It also updates GPS coordinates in EXIF metadata.

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

At the end of execution of the `02_update_media_metadata.py` script, the following statistics are displayed:

```
===== Final results =====
Files processed successfully: X,XXX
Files processed with warnings: XX
JSON files found in another directory: XX
JSON files not found: XX
```

- **Files processed successfully**: Media files with successful metadata updates
- **Files processed with warnings**: Files with errors during EXIF modification, but system date modification was usually successful
- **JSON files found in another directory**: Files matched using advanced strategies
- **JSON files not found**: Files without JSON matches (with list of affected files displayed)

> Note: This information can be used to check the effectiveness of processing and identify files requiring particular attention.

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

### Thread Pool Sizing
The script automatically detects your CPU and optimizes thread count:

- **2-4 CPU cores**: Uses 2x CPU cores (up to 8 threads)
- **5-8 CPU cores**: Uses 1.5x CPU cores (up to 16 threads)  
- **9+ CPU cores**: Uses CPU cores + 4 (up to 24 threads maximum)

**For I/O-intensive workloads** like this script, using 2-3x your CPU core count is optimal since threads spend time waiting for file operations rather than consuming CPU.

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

2. **Modify the script** (around line XXXX in `process_directory()` method):
   ```python
   # Replace automatic detection with manual setting
   max_workers = XX  # Set your desired thread count
   ```

3. **Guidelines for thread count:**
   - **Conservative**: 1x CPU cores (safest, slower)
   - **Balanced**: 2x CPU cores (recommended for most systems)
   - **Aggressive**: 3x CPU cores (fastest, may cause system stress)
   - **Maximum**: Never exceed 24 threads

## Supported File Formats

### Images
- JPEG (.jpg, .jpeg), PNG (.png), HEIC (.heic)
- GIF (.gif), BMP (.bmp), TIFF (.tiff, .tif), WebP (.webp)

### Videos
- MP4 (.mp4), MOV (.mov), AVI (.avi), MKV (.mkv), M4V (.m4v)

## Output Files

The script generates several log files:
- `media_processor_log_YYYYMMDD_HHMMSS.log` - Complete processing log
- `media_processor_errors_YYYYMMDD_HHMMSS.log` - Errors and warnings only
- `files_without_json_YYYYMMDD_HHMMSS.txt` - Files without JSON matches
- `missing_json_analysis_YYYYMMDD_HHMMSS.txt` - Analysis of unmatched files

## Example Workflow

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
   python3 02_update_media_metadata.py ./Takeout --conservative --debug
   ```

4. **Review results**
   - Check log files for any issues
   - Review unmatched files in summary files
   - Verify a few sample files have correct dates

## Best Practices

1. **Always use dry run first** to test on your specific archive
2. **Use conservative mode** for valuable photo collections
3. **Monitor log files** for match confidence and errors
4. **Keep backups** of original files before processing
5. **Review unmatched files** manually for important photos

## Contributing

This script is based on the original work from:
https://github.com/alexmonnerie/google-photos-takeout-metadata-exif-json-to-synology
