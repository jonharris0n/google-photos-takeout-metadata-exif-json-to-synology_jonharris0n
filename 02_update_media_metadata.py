import os
import json
import argparse
import piexif
from PIL import Image
from PIL.ExifTags import TAGS
from datetime import datetime
import logging
from pathlib import Path
import shutil
import unicodedata
from pillow_heif import register_heif_opener
import re
from concurrent.futures import ThreadPoolExecutor
import threading

# google-photos-takeout-metadata-exif-json-to-synology
# https://github.com/alexmonnerie/google-photos-takeout-metadata-exif-json-to-synology/

# Enhanced by: jonharris0n
# Date: July 11, 2025
# Changes: Implemented comprehensive performance optimizations including JSON file caching,
# enhanced metadata-based matching, progressive truncation strategies, and improved
# multi-threaded processing with optimized thread pool configuration for better I/O performance.
# Added comprehensive similarity scoring, metadata validation, and fallback matching strategies
# to significantly improve JSON file discovery rates and processing speed.

# HEIC support recording
register_heif_opener()

# Supported formats
IMAGE_FORMATS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".webp",
}
VIDEO_FORMATS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}

ALL_FORMATS = {ext.lower() for ext in list(IMAGE_FORMATS) + list(VIDEO_FORMATS)}


class MediaProcessor:
    def __init__(self, work_dir, debug=False, dry_run=False, conservative=False):
        self.work_dir = Path(work_dir)
        self.dry_run = dry_run
        self.conservative = conservative
        self.stats = {
            "success": 0,
            "warnings": 0,
            "json_not_found": 0,
            "ignored_with_exif": 0,
            "json_found_in_other_dir": 0,
        }
        self.files_without_json = []
        self.files_with_warnings = []

        # Performance optimization: Cache JSON files for faster lookup
        self._json_cache = {}
        self._json_metadata_cache = {}
        self._json_cache_built = False

        # Thread safety for concurrent processing
        self._stats_lock = threading.Lock()
        self._files_lock = threading.Lock()

        # Set up logging to both console and file
        log_level = logging.DEBUG if debug else logging.INFO

        # Create log file in the work directory
        log_file = (
            self.work_dir
            / f"media_processor_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )

        # Configure logging
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[
                logging.StreamHandler(),  # Console output
                logging.FileHandler(log_file, encoding="utf-8"),  # File output
            ],
        )

        # Create separate error/warning log file
        self.error_log_file = (
            self.work_dir
            / f"media_processor_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )

        # Set up file handler for warnings and errors only
        error_handler = logging.FileHandler(self.error_log_file, encoding="utf-8")
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

        # Add error handler to root logger
        logging.getLogger().addHandler(error_handler)

        # Logging for PIL (EXIF editing)
        logging.getLogger("PIL").setLevel(logging.WARNING)

        # Log startup information
        logging.info(f"Starting media processing in: {self.work_dir}")
        logging.info(f"Full log file: {log_file}")
        logging.info(f"Error/Warning log file: {self.error_log_file}")
        logging.info(f"Dry run mode: {self.dry_run}")
        logging.info(f"Debug mode: {debug}")

        # Precompile regex patterns for filename cleaning
        self._cleaning_patterns = [
            re.compile(r"-edited.*$"),
            re.compile(r"-modifi[eé].*$"),
            re.compile(r"-modified.*$"),
            re.compile(r"-copy.*$"),
            re.compile(r"-original.*$"),
            re.compile(r"-backup.*$"),
            re.compile(r"_edited.*$"),
            re.compile(r"_copy.*$"),
            re.compile(r"_original.*$"),
            re.compile(r"_backup.*$"),
            re.compile(r"\(\d+\)$"),
            re.compile(r"_\d+$"),
            re.compile(r"-\d+$"),
        ]

    def _build_json_cache(self):
        """Build a cache of all JSON files for faster lookup"""
        if self._json_cache_built:
            return

        logging.info("Building JSON file cache for faster processing...")

        # Also create a metadata cache for content-based matching
        self._json_metadata_cache = {}

        for json_file in self.work_dir.rglob("*.json"):
            # Store multiple keys for each JSON file to speed up lookups
            json_name = json_file.name
            json_stem = json_file.stem

            # Store by exact filename
            self._json_cache[json_name] = json_file

            # Store by stem for truncated matching
            if len(json_stem) >= 10:  # Only for reasonably long stems
                self._json_cache[json_stem] = json_file

            # Store by supplemental metadata patterns
            if "supplemental-metadata" in json_name:
                base_name = json_name.replace(".supplemental-metadata.json", "")
                self._json_cache[f"{base_name}_supplemental"] = json_file
            elif "supplemental-metadat" in json_name:
                base_name = json_name.replace(".supplemental-metadat.json", "")
                self._json_cache[f"{base_name}_supplemental"] = json_file
            elif "supplemental-metad" in json_name:
                base_name = json_name.replace(".supplemental-metad.json", "")
                self._json_cache[f"{base_name}_supplemental"] = json_file

            # Cache metadata content for content-based matching
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    json_data = json.load(f)
                    title = json_data.get("title", "")
                    timestamp = json_data.get("photoTakenTime", {}).get("timestamp", "")

                    # Extract additional metadata fields for enhanced matching
                    description = json_data.get("description", "")
                    creation_time = json_data.get("creationTime", {}).get(
                        "timestamp", ""
                    )
                    modification_time = json_data.get("modificationTime", {}).get(
                        "timestamp", ""
                    )

                    # Extract image/video dimensions if available
                    image_views = json_data.get("imageViews", [])
                    video_status = json_data.get("videoStatus", "")

                    # Extract file size and mime type
                    file_size = str(
                        json_data.get("photoMetadata", {})
                        .get("photoMetadataExtension", {})
                        .get("fileSize", "")
                    )
                    mime_type = json_data.get("mimeType", "")

                    # Extract camera/device info
                    camera_make = (
                        json_data.get("photoMetadata", {})
                        .get("cameraMetadata", {})
                        .get("cameraMake", "")
                    )
                    camera_model = (
                        json_data.get("photoMetadata", {})
                        .get("cameraMetadata", {})
                        .get("cameraModel", "")
                    )

                    # Extract GPS coordinates if available
                    geo_data = json_data.get("geoData", {})
                    latitude = geo_data.get("latitude", 0) if geo_data else 0
                    longitude = geo_data.get("longitude", 0) if geo_data else 0

                    # Extract people/faces data if available
                    people_in_photo = json_data.get("people", [])

                    if title and timestamp:
                        # Store comprehensive metadata for faster access
                        metadata_entry = {
                            "title": title.lower().strip(),
                            "timestamp": timestamp,
                            "file": json_file,
                            "description": (
                                description.lower().strip() if description else ""
                            ),
                            "creation_time": creation_time,
                            "modification_time": modification_time,
                            "file_size": file_size,
                            "mime_type": mime_type.lower() if mime_type else "",
                            "camera_make": camera_make.lower() if camera_make else "",
                            "camera_model": (
                                camera_model.lower() if camera_model else ""
                            ),
                            "has_geo": bool(latitude or longitude),
                            "latitude": latitude,
                            "longitude": longitude,
                            "has_people": bool(people_in_photo),
                            "people_count": (
                                len(people_in_photo) if people_in_photo else 0
                            ),
                            "video_status": (
                                video_status.lower() if video_status else ""
                            ),
                            "image_views_count": len(image_views) if image_views else 0,
                        }

                        self._json_metadata_cache[str(json_file)] = metadata_entry

                        # Also store by title for quick lookup
                        title_key = f"title_{title.lower().strip()}"
                        if title_key not in self._json_cache:
                            self._json_cache[title_key] = json_file

                        # Store truncated titles for matching
                        if len(title) > 20:
                            for length in [50, 40, 30, 20]:
                                if len(title) > length:
                                    truncated_title = title[:length].lower().strip()
                                    truncated_key = f"title_trunc_{truncated_title}"
                                    if truncated_key not in self._json_cache:
                                        self._json_cache[truncated_key] = json_file

            except (json.JSONDecodeError, IOError, KeyError) as e:
                logging.debug(f"Error reading JSON metadata for {json_file}: {e}")
                continue

        self._json_cache_built = True
        logging.info(f"JSON cache built with {len(self._json_cache)} entries")
        metadata_count = len(getattr(self, "_json_metadata_cache", {}))
        logging.info(f"JSON metadata cache built with {metadata_count} entries")

        if metadata_count > 0:
            logging.info("Metadata-based matching is available for truncated filenames")

    def find_json_file(self, media_path):
        """Find associated JSON file using multiple strategies with caching"""

        # Build cache on first use
        self._build_json_cache()

        def get_potential_json_names(base_path):
            """Generate various possible names for locating JSON file"""
            potential_names = [
                # Standard cases
                f"{base_path.name}.json",  # image.jpg.json
                f"{base_path.stem}.json",  # image.json
            ]

            # Case: LivePhotos iOS
            if base_path.suffix.upper() == ".MP4":
                potential_names.extend(
                    [
                        f"{base_path.stem}.HEIC.json",
                        f"{base_path.stem}.JPG.json",
                        # LivePhotos case and duplicate file
                        f"{base_path.stem.split('(')[0]}.HEIC.json",
                        f"{base_path.stem.split('(')[0]}.JPG.json",
                    ]
                )

            # Enhanced matching patterns
            potential_names.extend(
                [
                    # Case: duplicated files (x)
                    f"{base_path.stem.split('(')[0]}{base_path.suffix}.json",
                    # Case: JSON without "-modified" suffix
                    f"{base_path.name.replace('-modifié', '')}.json",
                    f"{base_path.name.replace('-modified', '')}.json",
                    f"{base_path.name.replace('-edited', '')}.json",
                    f"{base_path.name.replace('_edited', '')}.json",
                    f"{base_path.name.replace('-copy', '')}.json",
                    f"{base_path.name.replace('_copy', '')}.json",
                    # Case: supplemental metadata (various formats)
                    f"{base_path.name}.supplemental-metadata.json",
                    f"{base_path.name}.supplemental-metadat.json",
                    f"{base_path.name}.supplemental-metad.json",
                    # Case: supplemental metadata without extension
                    f"{base_path.stem}.supplemental-metadata.json",
                    f"{base_path.stem}.supplemental-metadat.json",
                    f"{base_path.stem}.supplemental-metad.json",
                    # Case: cleaned filename variations
                    f"{self._clean_filename_for_matching(base_path.stem)}.json",
                    f"{self._clean_filename_for_matching(base_path.stem)}.supplemental-metadata.json",
                    f"{self._clean_filename_for_matching(base_path.stem)}.supplemental-metadat.json",
                    f"{self._clean_filename_for_matching(base_path.stem)}.supplemental-metad.json",
                ]
            )

            # Case: Remove common numbering patterns
            stem_no_numbers = re.sub(r"[_-]\d+$", "", base_path.stem)
            if stem_no_numbers != base_path.stem:
                potential_names.extend(
                    [
                        f"{stem_no_numbers}.json",
                        f"{stem_no_numbers}{base_path.suffix}.json",
                        f"{stem_no_numbers}.supplemental-metadata.json",
                        f"{stem_no_numbers}.supplemental-metadat.json",
                        f"{stem_no_numbers}.supplemental-metad.json",
                    ]
                )

            # Special case: Handle Google Photos Takeout numbered duplicates
            # E.g., IMG_0947(2).JPG -> IMG_0947.JPG.supplemental-metadata(2).json
            parentheses_match = re.search(r"\((\d+)\)$", base_path.stem)
            if parentheses_match:
                number = parentheses_match.group(1)
                base_stem = base_path.stem[: parentheses_match.start()]

                logging.debug(
                    f"Found numbered duplicate: {base_path.name} -> base: {base_stem}, number: {number}"
                )

                # Generate Google Photos Takeout style JSON names
                potential_names.extend(
                    [
                        f"{base_stem}{base_path.suffix}.supplemental-metadata({number}).json",
                        f"{base_stem}{base_path.suffix}.supplemental-metadat({number}).json",
                        f"{base_stem}{base_path.suffix}.supplemental-metad({number}).json",
                        f"{base_stem}{base_path.suffix}.json({number})",
                        f"{base_stem}.json({number})",
                        # Alternative formats
                        f"{base_stem}({number}){base_path.suffix}.json",
                        f"{base_stem}({number}).json",
                        f"{base_stem}({number}){base_path.suffix}.supplemental-metadata.json",
                        f"{base_stem}({number}){base_path.suffix}.supplemental-metadat.json",
                        f"{base_stem}({number}){base_path.suffix}.supplemental-metad.json",
                    ]
                )

            return potential_names

        # Step 1: Quick cache lookup for exact matches
        potential_names = get_potential_json_names(media_path)
        for name in potential_names:
            if name in self._json_cache:
                json_file = self._json_cache[name]
                logging.debug(f"JSON file found in cache: {json_file}")
                return json_file

        # Step 2: Check for supplemental metadata in cache
        supplemental_key = f"{media_path.stem}_supplemental"
        if supplemental_key in self._json_cache:
            json_file = self._json_cache[supplemental_key]
            logging.info(f"JSON file found via supplemental cache: {json_file}")
            with self._stats_lock:
                self.stats["json_found_in_other_dir"] += 1
            return json_file

        # Step 3: Fallback to file system search for complex cases
        return self._fallback_json_search(media_path)

    def _fallback_json_search(self, media_path):
        """Fallback search for complex JSON matching cases"""
        media_stem = media_path.stem
        media_name = media_path.name

        # Special handling for files with numbered duplicates
        parentheses_match = re.search(r"\((\d+)\)$", media_stem)
        if parentheses_match:
            number = parentheses_match.group(1)
            base_stem = media_stem[: parentheses_match.start()]

            logging.debug(
                f"Fallback search for numbered duplicate: {media_name} -> base: {base_stem}, number: {number}"
            )

            # Search for Google Photos Takeout style JSON files
            for json_file in set(self._json_cache.values()):
                json_name = json_file.name

                # Check for patterns like: IMG_0947.JPG.supplemental-metadata(2).json
                if (
                    f"{base_stem}{media_path.suffix}.supplemental-metadata({number}).json"
                    in json_name
                    or f"{base_stem}{media_path.suffix}.supplemental-metadat({number}).json"
                    in json_name
                    or f"{base_stem}{media_path.suffix}.supplemental-metad({number}).json"
                    in json_name
                ):
                    logging.info(
                        f"JSON file found via numbered duplicate matching: {json_file}"
                    )
                    with self._stats_lock:
                        self.stats["json_found_in_other_dir"] += 1
                    return json_file

                # Check for exact matches in the JSON filename
                if (
                    json_name
                    == f"{base_stem}{media_path.suffix}.supplemental-metadata({number}).json"
                    or json_name
                    == f"{base_stem}{media_path.suffix}.supplemental-metadat({number}).json"
                    or json_name
                    == f"{base_stem}{media_path.suffix}.supplemental-metad({number}).json"
                ):
                    logging.info(
                        f"JSON file found via exact numbered duplicate matching: {json_file}"
                    )
                    with self._stats_lock:
                        self.stats["json_found_in_other_dir"] += 1
                    return json_file

        # Search through cached JSON files for fuzzy matches
        for json_file in set(self._json_cache.values()):
            json_name = json_file.name
            json_stem = json_file.stem

            # Strategy 1: Check if JSON name contains the media file name and supplemental metadata patterns
            if media_stem in json_name and (
                "supplemental-metadata" in json_name
                or "supplemental-metadat" in json_name
                or "supplemental-metad" in json_name
            ):
                logging.info(f"JSON file found via fuzzy matching {json_file}")
                with self._stats_lock:
                    self.stats["json_found_in_other_dir"] += 1
                return json_file

            # Strategy 2: Check for truncated filenames
            if len(json_stem) >= 10 and (  # Only check if JSON stem is reasonably long
                media_stem.startswith(json_stem)
                or json_stem.startswith(media_stem[: len(json_stem)])
            ):
                # Additional check: ensure it's likely a truncated supplemental metadata file
                if (
                    "supplemental-metadata" in json_name
                    or "supplemental-metadat" in json_name
                    or "supplemental-metad" in json_name
                    or json_name.endswith(".json")
                ):
                    logging.info(
                        f"JSON file found via truncated filename matching {json_file}"
                    )
                    with self._stats_lock:
                        self.stats["json_found_in_other_dir"] += 1
                    return json_file

            # Strategy 3: More aggressive partial matching for edited filenames
            # Remove common editing suffixes and try again
            cleaned_media_stem = self._clean_filename_for_matching(media_stem)
            cleaned_json_stem = self._clean_filename_for_matching(json_stem)

            if (
                len(cleaned_json_stem) >= 8
                and len(cleaned_media_stem) >= 8
                and (
                    cleaned_media_stem.startswith(cleaned_json_stem)
                    or cleaned_json_stem.startswith(cleaned_media_stem)
                    or cleaned_media_stem in cleaned_json_stem
                    or cleaned_json_stem in cleaned_media_stem
                )
            ):

                # Additional validation: check for supplemental metadata or reasonable similarity
                similarity_score = self._calculate_similarity(
                    cleaned_media_stem, cleaned_json_stem
                )
                if similarity_score > 0.7 and (  # 70% similarity threshold
                    "supplemental" in json_name or json_name.endswith(".json")
                ):
                    logging.info(
                        f"JSON file found via cleaned filename matching (similarity: {similarity_score:.2f}): {json_file}"
                    )
                    with self._stats_lock:
                        self.stats["json_found_in_other_dir"] += 1
                    return json_file

        # Strategy 4: Try progressive truncation with cleaned names
        cleaned_media_stem = self._clean_filename_for_matching(media_stem)
        for i in range(1, 11):  # Reduced from 20 to 11 for better performance
            truncated = cleaned_media_stem[:-(i)]
            if len(truncated) < 8:  # Don't try very short names
                break

            for suffix in [
                ".json",
                ".supplemental-metadata.json",
                ".supplemental-metadat.json",
                ".supplemental-metad.json",
            ]:
                truncated_name = f"{truncated}{suffix}"
                if truncated_name in self._json_cache:
                    json_file = self._json_cache[truncated_name]
                    logging.info(
                        f"JSON file found via cleaned truncated pattern: {json_file}"
                    )
                    with self._stats_lock:
                        self.stats["json_found_in_other_dir"] += 1
                    return json_file

        # Strategy 5: Metadata-based search using JSON title and timestamp
        metadata_match = self._metadata_based_json_search(media_path)
        if metadata_match:
            return metadata_match

        # Strategy 6: Last resort - look for any JSON with partial filename match
        return self._last_resort_json_search(media_path)

    def _last_resort_json_search(self, media_path):
        """Last resort search for JSON files with very loose matching"""
        # Skip last resort matching in conservative mode
        if self.conservative:
            return None

        media_stem = media_path.stem
        media_name = media_path.name

        # Remove parentheses for matching
        base_stem = re.sub(r"\(\d+\)$", "", media_stem)

        # Try very loose matching with conservative threshold
        for json_file in set(self._json_cache.values()):
            json_name = json_file.name
            json_stem = json_file.stem

            # Skip if file extensions are incompatible
            if not self._check_loose_file_compatibility(media_path, json_file):
                continue

            # Check for any partial match
            if len(base_stem) >= 8 and len(json_stem) >= 8:
                # Remove common JSON suffixes for comparison
                clean_json_stem = re.sub(r"\.supplemental-metadata.*$", "", json_stem)
                clean_json_stem = re.sub(r"\.json.*$", "", clean_json_stem)

                # Conservative similarity check (70% threshold)
                similarity = self._calculate_similarity(
                    base_stem.lower(), clean_json_stem.lower()
                )
                if similarity > 0.7:
                    logging.warning(
                        f"JSON file found via last resort matching "
                        f"(similarity: {similarity:.2f}, LOW confidence): {json_file}"
                    )
                    with self._stats_lock:
                        self.stats["json_found_in_other_dir"] += 1
                    return json_file

        return None

    def _check_loose_file_compatibility(self, media_path, json_file):
        """Check loose file compatibility for last resort matching"""
        media_ext = media_path.suffix.lower()
        json_name = json_file.name.lower()

        # Basic type checking - avoid obvious mismatches
        if media_ext in VIDEO_FORMATS:
            # Don't match video files with JSON files that clearly reference images
            if any(
                img_ext in json_name
                for img_ext in [".jpg.", ".jpeg.", ".png.", ".heic.", ".gif."]
            ):
                return False
        elif media_ext in IMAGE_FORMATS:
            # Don't match image files with JSON files that clearly reference videos
            if any(
                vid_ext in json_name for vid_ext in [".mp4.", ".mov.", ".avi.", ".mkv."]
            ):
                return False

        return True

    def _clean_filename_for_matching(self, filename):
        """Clean filename by removing common editing suffixes and variations"""
        cleaned = filename.lower()

        # Apply most patterns but be careful with numbered duplicates
        for pattern in self._cleaning_patterns[
            :-3
        ]:  # Skip the last 3 patterns that handle numbers
            cleaned = pattern.sub("", cleaned)

        # Handle numbered patterns more intelligently
        # Only remove trailing numbers if they're not in parentheses (Google Photos format)
        if not re.search(r"\(\d+\)$", cleaned):
            # Remove trailing numbers only if no parentheses
            cleaned = re.sub(r"_\d+$", "", cleaned)
            cleaned = re.sub(r"-\d+$", "", cleaned)

        # Remove extra spaces and normalize
        cleaned = re.sub(r"\s+", "", cleaned)
        cleaned = re.sub(r"[_-]+", "", cleaned)

        return cleaned

    def _calculate_similarity(self, str1, str2):
        """Calculate similarity between two strings using a simple algorithm"""
        if not str1 or not str2:
            return 0.0

        # Simple similarity based on common substring length
        longer = str1 if len(str1) > len(str2) else str2
        shorter = str2 if len(str1) > len(str2) else str1

        if len(longer) == 0:
            return 1.0

        # Find longest common substring
        longest_match = 0
        for i in range(len(shorter)):
            for j in range(i + 1, len(shorter) + 1):
                substr = shorter[i:j]
                if substr in longer and len(substr) > longest_match:
                    longest_match = len(substr)

        return longest_match / len(longer)

    def _metadata_based_json_search(self, media_path):
        """Search for JSON files using comprehensive metadata content when filename matching fails"""
        media_name = media_path.name
        media_stem = media_path.stem
        media_ext = media_path.suffix.lower()

        logging.debug(f"Attempting metadata-based search for: {media_name}")

        # Extract potential title variations from the media filename
        potential_titles = [
            media_stem,
            media_name,
            # Remove numbers in parentheses for matching
            re.sub(r"\(\d+\)$", "", media_stem),
            # Remove common suffixes
            re.sub(
                r"[-_](edited|modified|copy|original|backup).*$",
                "",
                media_stem,
                flags=re.IGNORECASE,
            ),
            # Clean filename for matching
            self._clean_filename_for_matching(media_stem),
        ]

        # Also try truncated versions (common in Google Photos)
        for title in potential_titles[:]:
            if len(title) > 20:
                for length in [50, 40, 30, 20, 15]:
                    if len(title) > length:
                        potential_titles.append(title[:length])

        # Remove duplicates and empty strings
        potential_titles = list(set(filter(None, potential_titles)))

        logging.debug(
            f"Generated {len(potential_titles)} potential titles for metadata matching"
        )

        # First, try quick cache lookup by title
        for potential_title in potential_titles:
            title_key = f"title_{potential_title.lower().strip()}"
            if title_key in self._json_cache:
                json_file = self._json_cache[title_key]
                logging.info(f"JSON file found via cached title match: {json_file}")
                with self._stats_lock:
                    self.stats["json_found_in_other_dir"] += 1
                return json_file

            # Try truncated title cache
            truncated_key = f"title_trunc_{potential_title.lower().strip()}"
            if truncated_key in self._json_cache:
                json_file = self._json_cache[truncated_key]
                logging.info(
                    f"JSON file found via cached truncated title match: {json_file}"
                )
                with self._stats_lock:
                    self.stats["json_found_in_other_dir"] += 1
                return json_file

        # Enhanced metadata search using multiple fields
        best_match = None
        best_score = 0
        best_match_details = {}

        # Try to extract file characteristics from the media file for better matching
        media_characteristics = self._extract_media_characteristics(media_path)

        for json_file_path, metadata in self._json_metadata_cache.items():
            json_file = metadata["file"]

            # Calculate comprehensive similarity score
            total_score = 0
            score_components = {}

            # 1. Title matching (primary factor)
            title_score = self._calculate_title_similarity(
                potential_titles, metadata["title"]
            )
            if title_score > 0:
                total_score += title_score * 0.6  # 60% weight for title
                score_components["title"] = title_score

            # 2. File type compatibility (essential)
            type_compatibility = self._check_file_type_compatibility(
                media_ext, metadata
            )
            if not type_compatibility:
                continue  # Skip incompatible files
            score_components["type_compatible"] = True

            # 3. File size correlation (if available)
            if media_characteristics.get("file_size") and metadata.get("file_size"):
                size_similarity = self._calculate_size_similarity(
                    media_characteristics["file_size"],
                    (
                        int(metadata["file_size"])
                        if metadata["file_size"].isdigit()
                        else 0
                    ),
                )
                if size_similarity > 0.8:  # Strong size correlation
                    total_score += size_similarity * 0.15  # 15% weight
                    score_components["size"] = size_similarity

            # 4. Camera/device matching (if available)
            if (
                media_characteristics.get("camera_make")
                and metadata.get("camera_make")
                and media_characteristics.get("camera_model")
                and metadata.get("camera_model")
            ):
                if (
                    media_characteristics["camera_make"].lower()
                    == metadata["camera_make"]
                    and media_characteristics["camera_model"].lower()
                    == metadata["camera_model"]
                ):
                    total_score += 0.1  # 10% bonus for same camera
                    score_components["camera_match"] = True

            # 5. Description matching (secondary)
            if metadata.get("description"):
                desc_score = self._calculate_title_similarity(
                    potential_titles, metadata["description"]
                )
                if desc_score > 0.5:
                    total_score += desc_score * 0.1  # 10% weight for description
                    score_components["description"] = desc_score

            # 6. Timestamp reasonableness check
            if not self._validate_timestamp(metadata["timestamp"]):
                continue  # Skip files with invalid timestamps

            # 7. Geo-location correlation (bonus if both have or both don't have geo data)
            media_has_geo = media_characteristics.get("has_geo", False)
            json_has_geo = metadata.get("has_geo", False)
            if media_has_geo == json_has_geo:
                total_score += 0.05  # 5% bonus for geo consistency
                score_components["geo_consistency"] = True

            # Update best match if this is better
            min_threshold = 0.7 if self.conservative else 0.6
            if total_score > best_score and total_score > min_threshold:
                best_match = json_file
                best_score = total_score
                best_match_details = {
                    "score": total_score,
                    "components": score_components,
                    "metadata": metadata,
                }

                logging.debug(
                    f"New best metadata match: {json_file} "
                    f"(score: {total_score:.2f}, components: {score_components})"
                )

        min_threshold = 0.7 if self.conservative else 0.6
        if best_match and best_score > min_threshold:
            confidence = (
                "HIGH" if best_score > 0.8 else "MEDIUM" if best_score > 0.7 else "LOW"
            )
            logging.info(
                f"JSON file found via enhanced metadata matching: {best_match}"
            )
            logging.info(
                f"Match confidence: {confidence} (score: {best_score:.2f}, components: {best_match_details['components']})"
            )
            with self._stats_lock:
                self.stats["json_found_in_other_dir"] += 1
            return best_match

        return None

    def _extract_media_characteristics(self, media_path):
        """Extract characteristics from media file for matching"""
        characteristics = {}

        try:
            # Get file size
            characteristics["file_size"] = media_path.stat().st_size

            # For images, try to extract EXIF data
            if media_path.suffix.lower() in IMAGE_FORMATS:
                try:
                    with Image.open(media_path) as img:
                        exif_data = img._getexif()
                        if exif_data:
                            for tag_id, value in exif_data.items():
                                tag = TAGS.get(tag_id, tag_id)
                                if tag == "Make":
                                    characteristics["camera_make"] = str(value).lower()
                                elif tag == "Model":
                                    characteristics["camera_model"] = str(value).lower()
                                elif tag == "GPSInfo":
                                    characteristics["has_geo"] = True
                except Exception:
                    pass  # EXIF extraction failed, continue without it

        except Exception as e:
            logging.debug(
                f"Error extracting media characteristics from {media_path}: {e}"
            )

        return characteristics

    def _calculate_title_similarity(self, potential_titles, json_title):
        """Calculate the best similarity score between potential titles and JSON title"""
        if not json_title:
            return 0

        best_similarity = 0
        for potential_title in potential_titles:
            potential_title_clean = potential_title.lower().strip()
            if not potential_title_clean:
                continue

            # Direct match
            if json_title == potential_title_clean:
                return 1.0

            # Prefix match (for truncated files)
            if len(potential_title_clean) >= 10 and (
                json_title.startswith(potential_title_clean)
                or potential_title_clean.startswith(json_title)
            ):
                similarity = self._calculate_similarity(
                    potential_title_clean, json_title
                )
                if similarity > best_similarity:
                    best_similarity = similarity

            # General similarity
            elif len(potential_title_clean) >= 8 and len(json_title) >= 8:
                similarity = self._calculate_similarity(
                    potential_title_clean, json_title
                )
                if similarity > best_similarity:
                    best_similarity = similarity

        return best_similarity

    def _check_file_type_compatibility(self, media_ext, metadata):
        """Check if media file type is compatible with JSON metadata"""
        mime_type = metadata.get("mime_type", "")

        # Video files
        if media_ext in VIDEO_FORMATS:
            return (
                mime_type.startswith("video/")
                or metadata.get("video_status")
                or any(vid_ext in mime_type for vid_ext in ["mp4", "mov", "avi", "mkv"])
            )

        # Image files
        elif media_ext in IMAGE_FORMATS:
            return (
                mime_type.startswith("image/")
                or not metadata.get("video_status")
                or any(
                    img_ext in mime_type
                    for img_ext in ["jpeg", "jpg", "png", "heic", "gif"]
                )
            )

        return True  # Default to compatible if unsure

    def _calculate_size_similarity(self, size1, size2):
        """Calculate similarity between two file sizes"""
        if not size1 or not size2 or size1 <= 0 or size2 <= 0:
            return 0

        # Calculate relative difference
        larger = max(size1, size2)
        smaller = min(size1, size2)

        # Allow for some variation (different compression, etc.)
        ratio = smaller / larger

        # Return similarity score (1.0 for identical, decreasing with difference)
        return ratio

    def _validate_timestamp(self, timestamp):
        """Validate that timestamp is reasonable"""
        try:
            ts = int(timestamp)
            dt = datetime.fromtimestamp(ts)
            # Reasonable date range (1990-2030)
            return 1990 <= dt.year <= 2030
        except (ValueError, OverflowError):
            return False

    def _validate_metadata_match_from_cache(self, json_file, media_path, metadata):
        """Validate that a metadata-based match is reasonable using cached data"""
        try:
            # Check if timestamp is reasonable (not too old/new)
            try:
                timestamp = int(metadata["timestamp"])
                dt = datetime.fromtimestamp(timestamp)

                # Reasonable date range (1990-2030)
                if dt.year < 1990 or dt.year > 2030:
                    return False

            except (ValueError, OverflowError):
                return False

            # Check if file extensions are compatible
            json_name = json_file.name.lower()
            media_ext = media_path.suffix.lower()

            # For video files, ensure JSON doesn't explicitly mention image formats
            if media_ext in VIDEO_FORMATS:
                if any(
                    img_ext in json_name
                    for img_ext in [".jpg", ".jpeg", ".png", ".heic", ".gif"]
                ):
                    return False

            # For image files, ensure JSON doesn't explicitly mention video formats
            elif media_ext in IMAGE_FORMATS:
                if any(
                    vid_ext in json_name for vid_ext in [".mp4", ".mov", ".avi", ".mkv"]
                ):
                    return False

            return True

        except Exception as e:
            logging.debug(f"Error validating metadata match: {e}")
            return False

    def update_file_dates(self, file_path, timestamp):
        """Update system dates in media"""

        if self.dry_run:
            newdate_modified = datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            logging.debug(
                f"[DRY RUN] Simulated updating system dates for {file_path} : {newdate_modified}"
            )
            return

        # Update access (atime) and modification (mtime) dates
        os.utime(file_path, (timestamp, timestamp))

        # Update last modification date
        Path(file_path).touch(exist_ok=True)
        os.utime(file_path, (timestamp, timestamp))

        logging.debug(f"Updated system dates for {file_path}")

    def update_image_exif(self, image_path, json_data):
        """Update an picture's EXIF metadata"""
        if image_path.suffix.lower() not in IMAGE_FORMATS:
            return

        try:
            timestamp = int(json_data["photoTakenTime"]["timestamp"])
            date_time = datetime.fromtimestamp(timestamp).strftime("%Y:%m:%d %H:%M:%S")

            if self.dry_run:
                logging.debug(
                    f"[DRY RUN] Simulated updating EXIF metadata for {image_path}"
                )
                return

            # Pre-build EXIF dictionary for better performance
            exif_dict = {
                "0th": {},
                "Exif": {
                    piexif.ExifIFD.DateTimeOriginal: date_time.encode("ascii"),
                    piexif.ExifIFD.DateTimeDigitized: date_time.encode("ascii"),
                    piexif.ExifIFD.SubSecTime: b"00",
                    piexif.ExifIFD.SubSecTimeOriginal: b"00",
                    piexif.ExifIFD.SubSecTimeDigitized: b"00",
                    piexif.ExifIFD.OffsetTime: b"+00:00",
                    piexif.ExifIFD.OffsetTimeOriginal: b"+00:00",
                    piexif.ExifIFD.OffsetTimeDigitized: b"+00:00",
                },
                "GPS": {},
            }

            # GPS data preparation (only if GPS data exists)
            gps_data = json_data.get("geoDataExif")
            if gps_data:
                lat = gps_data.get("latitude", 0)
                lon = gps_data.get("longitude", 0)

                if lat != 0 and lon != 0:
                    exif_dict["GPS"] = self.create_gps_dict(lat, lon)

            exif_bytes = piexif.dump(exif_dict)

            # More efficient image processing
            try:
                with Image.open(image_path) as img:
                    # Save directly without creating intermediate objects
                    img.save(image_path, exif=exif_bytes, optimize=True)
            except Exception as img_error:
                # Fallback without optimization if there's an issue
                with Image.open(image_path) as img:
                    img.save(image_path, exif=exif_bytes)

        except Exception as e:
            logging.warning(
                f"An error occurred during EXIF update of {image_path}: {str(e)}"
            )
            with self._stats_lock:
                self.stats["warnings"] += 1
            with self._files_lock:
                self.files_with_warnings.append((str(image_path), str(e)))

    def create_gps_dict(self, lat, lon):
        """Create GPS dictionary for EXIF data"""
        lat_deg = self.convert_to_degrees(abs(lat))
        lon_deg = self.convert_to_degrees(abs(lon))

        return {
            piexif.GPSIFD.GPSLatitudeRef: "N".encode() if lat >= 0 else "S".encode(),
            piexif.GPSIFD.GPSLatitude: lat_deg,
            piexif.GPSIFD.GPSLongitudeRef: "E".encode() if lon >= 0 else "W".encode(),
            piexif.GPSIFD.GPSLongitude: lon_deg,
        }

    @staticmethod
    def convert_to_degrees(value):
        """Converts decimal value into degrees for EXIF format"""
        d = int(value)
        m = int((value - d) * 60)
        s = int(((value - d) * 60 - m) * 60)
        return ((d, 1), (m, 1), (s, 1))

    def process_media_file(self, media_path):
        """Processes a media file, main processing function"""
        json_path = self.find_json_file(media_path)

        if not json_path:
            logging.warning(f"JSON file not found for {media_path}")
            with self._stats_lock:
                self.stats["json_not_found"] += 1
            with self._files_lock:
                self.files_without_json.append(str(media_path))
            return

        try:
            logging.debug(f"JSON file used {json_path}")

            # More efficient JSON loading
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)

            # Quick validation of required fields
            if (
                "photoTakenTime" not in json_data
                or "timestamp" not in json_data["photoTakenTime"]
            ):
                raise ValueError("Missing required timestamp in JSON metadata")

            # Get timestamp once for reuse
            timestamp = int(json_data["photoTakenTime"]["timestamp"])

            # EXIF update except for HEIC images (check format first for efficiency)
            if (
                media_path.suffix.lower() in IMAGE_FORMATS
                and media_path.suffix.lower() != ".heic"
            ):
                self.update_image_exif(media_path, json_data)

            # Update system dates
            self.update_file_dates(media_path, timestamp)

            # Success logging
            newdate = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            with self._stats_lock:
                self.stats["success"] += 1
            logging.debug(f"Successfully processed {media_path} - new date: {newdate}")

        except Exception as e:
            logging.error(f"An error occurred when processing {media_path}: {str(e)}")
            with self._stats_lock:
                self.stats["warnings"] += 1
            with self._files_lock:
                self.files_with_warnings.append((str(media_path), str(e)))

    def process_directory(self):
        """Processes all media files stored in directory"""
        # Pre-collect all media files for better progress tracking
        media_files = []
        logging.info("Scanning for media files...")

        for file_path in self.work_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALL_FORMATS:
                media_files.append(file_path)

        total_files = len(media_files)
        logging.info(f"Found {total_files} media files to process")

        if total_files == 0:
            logging.info("No media files found to process")
            return

        # Progress tracking for parallel processing
        processed_count = 0
        progress_lock = threading.Lock()
        start_time = datetime.now()

        # Determine progress reporting interval
        if total_files <= 10:
            progress_interval = 1
        elif total_files <= 100:
            progress_interval = 10
        elif total_files <= 1000:
            progress_interval = 50
        else:
            progress_interval = 100

        def process_file(file_path):
            nonlocal processed_count
            logging.debug(f"Processing file: {file_path}")
            self.process_media_file(file_path)

            # Update progress
            with progress_lock:
                processed_count += 1

                # Show progress at regular intervals or for milestones
                if (
                    processed_count % progress_interval == 0
                    or processed_count == total_files
                    or processed_count in [1, 5, 10, 25, 50]
                ):

                    elapsed_time = datetime.now() - start_time
                    elapsed_seconds = elapsed_time.total_seconds()

                    if processed_count > 0 and elapsed_seconds > 0:
                        files_per_second = processed_count / elapsed_seconds
                        remaining_files = total_files - processed_count
                        eta_seconds = (
                            remaining_files / files_per_second
                            if files_per_second > 0
                            else 0
                        )
                        eta_str = (
                            f", ETA: {int(eta_seconds//60)}m {int(eta_seconds%60)}s"
                            if eta_seconds > 0
                            else ""
                        )

                        logging.info(
                            f"Progress: {processed_count}/{total_files} files processed "
                            f"({processed_count/total_files*100:.1f}%) "
                            f"- {files_per_second:.1f} files/sec{eta_str}"
                        )
                    else:
                        logging.info(
                            f"Progress: {processed_count}/{total_files} files processed "
                            f"({processed_count/total_files*100:.1f}%)"
                        )

        # Use ThreadPoolExecutor with optimal number of threads
        cpu_count = os.cpu_count() or 1

        # Optimize for I/O-intensive workloads (file reading, JSON parsing, EXIF writing)
        # For mixed I/O and CPU work, use 2-3x CPU cores but cap at reasonable limits
        if cpu_count <= 4:
            max_workers = min(8, cpu_count * 2)
        elif cpu_count <= 8:
            max_workers = min(16, int(cpu_count * 1.5))
        else:
            max_workers = min(24, cpu_count + 4)

        # Additional optimization for AMD Ryzen Embedded R1600 (2 cores/4 threads)
        if cpu_count == 4:  # R1600 reports 4 logical cores
            max_workers = (
                8  # Use 8 threads for optimal I/O performance on 2-core/4-thread CPU
            )

        logging.info(
            f"CPU cores detected: {cpu_count}, using {max_workers} threads for parallel processing"
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(process_file, media_files)

        # Final progress report
        total_time = datetime.now() - start_time
        total_seconds = total_time.total_seconds()
        avg_files_per_second = total_files / total_seconds if total_seconds > 0 else 0

        logging.info(
            f"Media processing completed in {int(total_seconds//60)}m {int(total_seconds%60)}s"
        )
        logging.info(
            f"Average processing speed: {avg_files_per_second:.1f} files/second"
        )

    def print_stats(self):
        """Shows processing statistics"""
        stats_summary = [
            "\n===== Final results =====",
            f"Files processed successfully: {self.stats['success']}",
            f"Files processed with warnings: {self.stats['warnings']}",
            f"JSON files found in another directory: {self.stats['json_found_in_other_dir']}",
            f"JSON files not found: {self.stats['json_not_found']}",
        ]

        # Print to console
        for line in stats_summary:
            print(line)

        # Also log the summary
        for line in stats_summary:
            logging.info(line)

        # Write detailed error/warning summary to both console and error log
        if self.files_with_warnings:
            warning_summary = ["\n===== WARNING FILES SUMMARY ====="]
            for file, error in self.files_with_warnings:
                warning_summary.extend([f"File: {file}", f"Error: {error}", "-" * 50])

            # Print to console
            print("\nWarning files:")
            for file, error in self.files_with_warnings:
                print(f"- {file}")
                print(f"  Error details: {error}")

            # Log to error file
            for line in warning_summary:
                logging.error(line)

        if self.files_without_json:
            missing_json_summary = ["\n===== FILES WITHOUT JSON SUMMARY ====="]
            for file in self.files_without_json:
                missing_json_summary.append(f"Missing JSON for: {file}")

            # Print to console
            print("\nJSON files not found:")
            for file in self.files_without_json:
                print(f"- {file}")

            # Log to error file
            for line in missing_json_summary:
                logging.warning(line)

            # Write summary files
            self._write_summary_files()

            # In rare cases, if JSON can't be found and error processing occurs, it's possible to manually set date and time for media by referring to the file name or album name in which it's located.
            reponse = input(
                "\nDo you want manually set date and time for files without JSON? (y/n) "
            )
            if reponse.lower() == "y":
                for file in self.files_without_json:
                    date_str = input(
                        f"\nEnter date and time for {file} \nFormat YYYY-MM-DD HH:MM (or press Enter to skip) : "
                    )
                    if date_str.strip():  # If user has input date time
                        try:
                            timestamp = int(
                                datetime.strptime(
                                    date_str, "%Y-%m-%d %H:%M"
                                ).timestamp()
                            )
                            self.update_file_dates(Path(file), timestamp)
                            logging.info(f"Successfully set date and time for {file}")
                        except ValueError:
                            logging.error(
                                f"Invalid date time format for {file}, ignored"
                            )

    def _write_summary_files(self):
        """Write summary files for easy review"""
        try:
            # Write files without JSON to a text file
            if self.files_without_json:
                missing_json_file = (
                    self.work_dir
                    / f"files_without_json_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                )
                analysis_file = (
                    self.work_dir
                    / f"missing_json_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                )

                with open(missing_json_file, "w", encoding="utf-8") as f:
                    f.write("Files without JSON metadata:\n")
                    f.write("=" * 50 + "\n\n")
                    for file in self.files_without_json:
                        f.write(f"{file}\n")

                # Write analysis file
                self._write_missing_json_analysis(analysis_file)

                logging.info(f"Files without JSON written to: {missing_json_file}")
                logging.info(f"Missing JSON analysis written to: {analysis_file}")

            # Write files with warnings to a text file
            if self.files_with_warnings:
                warnings_file = (
                    self.work_dir
                    / f"files_with_warnings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                )
                with open(warnings_file, "w", encoding="utf-8") as f:
                    f.write("Files with warnings/errors:\n")
                    f.write("=" * 50 + "\n\n")
                    for file, error in self.files_with_warnings:
                        f.write(f"File: {file}\n")
                        f.write(f"Error: {error}\n")
                        f.write("-" * 30 + "\n\n")
                logging.info(f"Files with warnings written to: {warnings_file}")

        except Exception as e:
            logging.error(f"Error writing summary files: {str(e)}")

    def _write_missing_json_analysis(self, analysis_file):
        """Analyze patterns in files without JSON to help improve matching"""
        try:
            from collections import defaultdict
            import re

            # Analyze patterns
            extensions = defaultdict(int)
            filename_patterns = defaultdict(int)
            path_patterns = defaultdict(int)
            length_distribution = defaultdict(int)

            for file_path in self.files_without_json:
                path = Path(file_path)

                # Extension analysis
                extensions[path.suffix.lower()] += 1

                # Filename pattern analysis
                stem = path.stem

                # Check for common patterns
                if re.match(r"IMG_\d{8}_\d{6}", stem):
                    filename_patterns["IMG_YYYYMMDD_HHMMSS"] += 1
                elif re.match(r"MVIMG_\d{8}_\d{6}", stem):
                    filename_patterns["MVIMG_YYYYMMDD_HHMMSS"] += 1
                elif re.match(r"VID_\d{8}_\d{6}", stem):
                    filename_patterns["VID_YYYYMMDD_HHMMSS"] += 1
                elif re.match(r"PXL_\d{8}_\d{6}", stem):
                    filename_patterns["PXL_YYYYMMDD_HHMMSS"] += 1
                elif "screenshot" in stem.lower():
                    filename_patterns["Screenshot"] += 1
                elif "photo" in stem.lower():
                    filename_patterns["Photo"] += 1
                elif re.match(r".*\(\d+\)", stem):
                    filename_patterns["Duplicated_files_(N)"] += 1
                elif any(
                    suffix in stem.lower() for suffix in ["edited", "modified", "copy"]
                ):
                    filename_patterns["Edited_files"] += 1
                else:
                    filename_patterns["Other"] += 1

                # Path analysis (folder structure)
                parent_name = path.parent.name.lower()
                if "takeout" in parent_name:
                    path_patterns["Takeout_folder"] += 1
                elif "photo" in parent_name:
                    path_patterns["Photos_folder"] += 1
                elif "archive" in parent_name:
                    path_patterns["Archive_folder"] += 1
                else:
                    path_patterns["Other_folder"] += 1

                # Length distribution
                length_range = f"{(len(stem)//10)*10}-{(len(stem)//10)*10+9}"
                length_distribution[length_range] += 1

            # Write analysis
            with open(analysis_file, "w", encoding="utf-8") as f:
                f.write("ANALYSIS OF FILES WITHOUT JSON METADATA\n")
                f.write("=" * 50 + "\n\n")

                f.write(f"Total files without JSON: {len(self.files_without_json)}\n\n")

                f.write("FILE EXTENSIONS:\n")
                f.write("-" * 20 + "\n")
                for ext, count in sorted(extensions.items()):
                    f.write(f"{ext}: {count}\n")

                f.write("\nFILENAME PATTERNS:\n")
                f.write("-" * 20 + "\n")
                for pattern, count in sorted(
                    filename_patterns.items(), key=lambda x: x[1], reverse=True
                ):
                    f.write(f"{pattern}: {count}\n")

                f.write("\nFOLDER PATTERNS:\n")
                f.write("-" * 20 + "\n")
                for pattern, count in sorted(
                    path_patterns.items(), key=lambda x: x[1], reverse=True
                ):
                    f.write(f"{pattern}: {count}\n")

                f.write("\nFILENAME LENGTH DISTRIBUTION:\n")
                f.write("-" * 30 + "\n")
                for length_range, count in sorted(length_distribution.items()):
                    f.write(f"{length_range} characters: {count}\n")

                # Sample files for manual inspection
                f.write("\nSAMPLE FILES (first 20):\n")
                f.write("-" * 25 + "\n")
                for file_path in self.files_without_json[:20]:
                    f.write(f"{file_path}\n")

                if len(self.files_without_json) > 20:
                    f.write(
                        f"\n... and {len(self.files_without_json) - 20} more files\n"
                    )

                # Suggestions
                f.write("\nSUGGESTIONS FOR IMPROVEMENT:\n")
                f.write("-" * 30 + "\n")
                f.write(
                    "1. Check if these files are screenshots or system-generated images\n"
                )
                f.write(
                    "2. Look for JSON files with similar naming patterns in subdirectories\n"
                )
                f.write(
                    "3. Consider if these files were added after the Google Photos export\n"
                )
                f.write(
                    "4. Check if JSON files exist with slightly different names (typos, encoding issues)\n"
                )
                f.write(
                    "5. Verify if these are duplicate files that should reference existing JSON\n"
                )

        except Exception as e:
            logging.error(f"Error writing missing JSON analysis: {str(e)}")

            # ask_delete_json = input("\nDo you want purge all JSON files? (y/n) ")
            # if ask_delete_json.lower() == 'y':
            #     logging.info("Deleting JSON files...")
            #     for json_file in self.work_dir.rglob('*.json'):
            #         if not self.dry_run:
            #             try:
            #                 json_file.unlink()
            #                 logging.debug(f"JSON file deleted : {json_file}")
            #             except Exception as e:
            #                 logging.error(f"An error occurred during deleting {json_file}: {str(e)}")
            #         else:
            #             logging.debug(f"[DRY RUN] Simulated deletion of JSON file : {json_file}")

            # ask_delete_emptydir = input("\nDo you want delete all empty directories? (y/n) ")
            # if ask_delete_emptydir.lower() == 'y':
            #     logging.info("Deleting empty directories...")
            #     for dirpath, dirnames, filenames in os.walk(self.work_dir, topdown=False):
            #         if not dirnames and not filenames:
            #             if not self.dry_run:
            #                 try:
            #                     os.rmdir(dirpath)
            #                     logging.debug(f"Empty directory deleted : {dirpath}")
            #                 except Exception as e:
            #                     logging.error(f"An error occurred during deleting empty directory {dirpath}: {str(e)}")
            #             else:
            #                 logging.debug(f"[DRY RUN] Simulated deletion of empty directory : {dirpath}")


def main():
    parser = argparse.ArgumentParser(description="Update media file metadata")
    parser.add_argument("work_dir", help="Work directory with folders and media files")
    parser.add_argument(
        "--debug", action="store_true", help="Activate verbose debug mode"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate proccessing without modifying files",
    )
    parser.add_argument(
        "--conservative",
        action="store_true",
        help="Use conservative matching (higher thresholds, skip risky matches)",
    )

    args = parser.parse_args()

    processor = MediaProcessor(
        args.work_dir, args.debug, args.dry_run, args.conservative
    )
    processor.process_directory()
    processor.print_stats()


if __name__ == "__main__":
    main()
