#!/usr/bin/env python3
import argparse
import os
import re
import requests
import sys
import time
import mimetypes
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import hashlib

# Define binary file extensions that typically trigger downloads
BINARY_EXTENSIONS = {
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',  # Office documents
    'pdf',  # PDF files
    'zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'tar.gz', 'bz2',  # Archives
    'exe', 'dll', 'bin', 'iso', 'img', 'dmg', 'apk', 'msi',  # Executables/images
    'db', 'sqlite', 'bak', 'backup',  # Database files
}

def get_domain_links(domain):
    """Fetch all links for a domain from web.archive.org"""
    print(f"[+] Fetching links for {domain}...")
    url = "https://web.archive.org/cdx/search/cdx"
    params = {
        "url": f"*.{domain}/*",
        "collapse": "urlkey",
        "output": "text",
        "fl": "original"
    }
    
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.text.splitlines()
        else:
            print(f"[!] Error fetching links: HTTP {response.status_code}")
            return []
    except Exception as e:
        print(f"[!] Error fetching links: {str(e)}")
        return []

def filter_links_by_extension(links, extensions):
    """Filter links by specific extensions"""
    filtered_links = []
    extension_pattern = '|'.join(extensions)
    pattern = re.compile(f'.*({extension_pattern})$', re.IGNORECASE)
    
    for link in links:
        if pattern.match(link):
            filtered_links.append(link)
    
    return filtered_links

def get_extension(url):
    """Extract extension from URL"""
    path = urlparse(url).path
    extension = Path(path).suffix.lower()
    if extension.startswith('.'):
        extension = extension[1:]  # Remove leading dot
    
    # Handle special cases like .tar.gz
    if path.endswith('.tar.gz'):
        extension = 'tar.gz'
    elif path.endswith('.7z'):
        extension = '7z'
    
    return extension

def get_wayback_snapshot(url):
    """Get the latest snapshot URL from web.archive.org"""
    archive_url = f"https://web.archive.org/web/timemap/link/{url}"
    
    try:
        response = requests.get(archive_url)
        if response.status_code == 200:
            # Parse the timemap to find the latest snapshot
            lines = response.text.splitlines()
            for line in reversed(lines):  # Start from newest
                if '; rel="memento"' in line:
                    snapshot_url = line.split('<')[1].split('>')[0]
                    return snapshot_url
        return None
    except Exception:
        return None

def verify_binary_file(data, extension):
    """Check if file content matches expected binary signature"""
    # File signatures (magic numbers) for some common binary formats
    signatures = {
        'pdf': b'%PDF',
        'zip': b'PK\x03\x04',
        'rar': b'Rar!\x1a\x07',
        'doc': b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1',
        'xls': b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1',
        'docx': b'PK\x03\x04',  # DOCX is a ZIP-based format
        'xlsx': b'PK\x03\x04',  # XLSX is a ZIP-based format
    }
    
    # If we don't have a specific signature check, just verify it's not HTML/text
    if extension in signatures:
        return data.startswith(signatures[extension])
    elif extension in BINARY_EXTENSIONS:
        # For other binary types, just make sure it's not HTML or plain text
        try:
            first_chars = data[:20].decode('utf-8', errors='strict').lower()
            if '<!doctype html' in first_chars or '<html' in first_chars:
                return False
            return True
        except UnicodeDecodeError:
            # If it can't be decoded as text, it's likely binary
            return True
    
    return True  # Default to accepting the file

def generate_filename(url, extension):
    """Generate a unique filename based on URL"""
    base = os.path.basename(urlparse(url).path)
    if base and '.' in base:
        return base
    
    # Generate a hash-based filename if original is not usable
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    return f"{url_hash}.{extension}"

def download_content(url, output_dir, extension):
    """Download content from URL or its archive snapshot"""
    filename = generate_filename(url, extension)
    output_path = os.path.join(output_dir, extension, filename)
    
    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # Try to download directly
    try:
        response = requests.get(url, stream=True, timeout=15)
        if response.status_code == 200:
            # For binary files, read some content to verify file type
            if extension in BINARY_EXTENSIONS:
                content_preview = next(response.iter_content(chunk_size=1024), b'')
                if not verify_binary_file(content_preview, extension):
                    # File doesn't match expected format, might be an error page
                    raise ValueError("Content doesn't match expected binary format")
                
                # Reset the stream and download the full content
                response = requests.get(url, stream=True, timeout=15)
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Verify file size (skip empty files)
            if os.path.getsize(output_path) < 100:  # Arbitrary minimum size
                with open(output_path, 'rb') as f:
                    content = f.read()
                    if b'404' in content or b'not found' in content.lower():
                        os.remove(output_path)
                        raise ValueError("File too small or contains error message")
            
            return True, "direct", output_path
    except Exception as e:
        # Remove failed download if file was created
        if os.path.exists(output_path):
            os.remove(output_path)
    
    # Try archive.org snapshot
    snapshot_url = get_wayback_snapshot(url)
    if snapshot_url:
        try:
            # Special handling for Wayback Machine archived files
            # For binary files in Wayback, we need to use the original, not the HTML snapshot
            if extension in BINARY_EXTENSIONS:
                # Modify the snapshot URL to get the original file
                response = requests.get(snapshot_url, stream=True, timeout=15, allow_redirects=True)
                
                # Check if it's a real binary file or just an error page
                content_preview = next(response.iter_content(chunk_size=1024), b'')
                if not verify_binary_file(content_preview, extension):
                    raise ValueError("Archived content doesn't match expected binary format")
                
                # Reset stream and download
                response = requests.get(snapshot_url, stream=True, timeout=15)
                
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                # Double check file size and content
                if os.path.getsize(output_path) < 100:
                    with open(output_path, 'rb') as f:
                        content = f.read()
                        if b'404' in content or b'not found' in content.lower():
                            os.remove(output_path)
                            raise ValueError("Archived file too small or contains error message")
                
                return True, "archive", output_path
        except Exception as e:
            # Remove failed download if file was created
            if os.path.exists(output_path):
                os.remove(output_path)
    
    return False, None, None

def process_link(link, output_dir):
    """Process a single link"""
    extension = get_extension(link)
    if not extension:
        return link, None, (False, None, None)
    
    success, source, filepath = download_content(link, output_dir, extension)
    file_info = None
    
    if success:
        try:
            # Get file size
            size = os.path.getsize(filepath)
            file_info = {
                "size": size,
                "size_readable": f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/1024/1024:.1f} MB",
                "path": filepath
            }
        except:
            pass
            
    return link, extension, (success, source, file_info)

def main():
    parser = argparse.ArgumentParser(description="Wayback Machine RECON Tool")
    parser.add_argument("domain", help="Target domain (e.g., example.com)")
    parser.add_argument("-o", "--output", default="recon_output", help="Output directory")
    parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads")
    parser.add_argument("-f", "--filter", action="store_true", help="Only show summary, don't download files")
    parser.add_argument("-e", "--extensions", help="Comma-separated list of extensions to search for")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output")
    args = parser.parse_args()
    
    # Standard extensions to look for
    default_extensions = [
        'xls', 'xml', 'xlsx', 'json', 'pdf', 'sql', 'doc', 'docx', 'pptx', 
        'txt', 'zip', 'tar.gz', 'tgz', 'bak', '7z', 'rar', 'log', 'cache', 
        'secret', 'db', 'backup', 'yml', 'gz', 'config', 'csv', 'yaml', 'md', 
        'md5', 'exe', 'dll', 'bin', 'ini', 'bat', 'sh', 'tar', 'deb', 'rpm', 
        'iso', 'img', 'apk', 'msi', 'dmg', 'tmp', 'crt', 'pem', 'key', 'pub', 'asc'
    ]
    
    # Use custom extensions if provided
    if args.extensions:
        extensions = [ext.strip() for ext in args.extensions.split(',')]
    else:
        extensions = default_extensions
    
    print(f"[+] Starting recon on {args.domain}")
    print(f"[+] Looking for files with extensions: {', '.join(extensions)}")
    
    # Get all links for domain
    links = get_domain_links(args.domain)
    if not links:
        print("[!] No links found. Exiting...")
        sys.exit(1)
    
    print(f"[+] Found {len(links)} total links")
    
    # Filter links by extension
    filtered_links = filter_links_by_extension(links, extensions)
    print(f"[+] Found {len(filtered_links)} links with target extensions")
    
    # Count extensions
    extension_counter = Counter()
    for link in filtered_links:
        extension = get_extension(link)
        if extension:
            extension_counter[extension] += 1
    
    # Print extension summary
    print("\n=== Extension Summary ===")
    for ext, count in sorted(extension_counter.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            binary_note = " (binary)" if ext in BINARY_EXTENSIONS else ""
            print(f"{ext}{binary_note}: {count}")
    
    if args.filter:
        print("\n[+] Filter mode enabled - skipping downloads")
        sys.exit(0)
    
    # Download files
    print(f"\n[+] Downloading files to {args.output}...")
    os.makedirs(args.output, exist_ok=True)
    
    results = {
        "success": {"direct": 0, "archive": 0},
        "failed": 0,
        "total_size": 0,
        "files": []
    }
    
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = []
        for link in filtered_links:
            futures.append(executor.submit(process_link, link, args.output))
        
        total = len(futures)
        completed = 0
        
        for future in futures:
            link, extension, result = future.result()
            completed += 1
            
            success, source, file_info = result
            
            if success:
                results["success"][source] += 1
                results["total_size"] += file_info["size"]
                results["files"].append({
                    "link": link,
                    "extension": extension,
                    "size": file_info["size_readable"],
                    "path": file_info["path"]
                })
                
                status = f"[DOWNLOADED-{source.upper()}]"
                if args.verbose:
                    print(f"[{completed}/{total}] {status} {link} ({file_info['size_readable']})")
                else:
                    print(f"[{completed}/{total}] {status} {os.path.basename(file_info['path'])} ({file_info['size_readable']})")
            else:
                results["failed"] += 1
                status = "[FAILED]"
                print(f"[{completed}/{total}] {status} {link}")
    
    # Print summary
    print("\n=== Download Summary ===")
    print(f"Downloaded (direct): {results['success']['direct']}")
    print(f"Downloaded (archive): {results['success']['archive']}")
    print(f"Failed: {results['failed']}")
    print(f"Total files: {len(results['files'])}")
    print(f"Total size: {results['total_size']/1024/1024:.2f} MB")
    print(f"\nFiles saved to: {os.path.abspath(args.output)}")
    
    # Generate summary report
    report_path = os.path.join(args.output, "summary_report.txt")
    with open(report_path, 'w') as f:
        f.write(f"RECON SUMMARY FOR {args.domain}\n")
        f.write("="*50 + "\n\n")
        f.write(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total links found: {len(links)}\n")
        f.write(f"Filtered links: {len(filtered_links)}\n\n")
        
        f.write("=== Extension Summary ===\n")
        for ext, count in sorted(extension_counter.items(), key=lambda x: x[1], reverse=True):
            if count > 0:
                binary_note = " (binary)" if ext in BINARY_EXTENSIONS else ""
                f.write(f"{ext}{binary_note}: {count}\n")
        
        f.write("\n=== Download Summary ===\n")
        f.write(f"Downloaded (direct): {results['success']['direct']}\n")
        f.write(f"Downloaded (archive): {results['success']['archive']}\n")
        f.write(f"Failed: {results['failed']}\n")
        f.write(f"Total files: {len(results['files'])}\n")
        f.write(f"Total size: {results['total_size']/1024/1024:.2f} MB\n\n")
        
        f.write("=== Downloaded Files ===\n")
        for file in sorted(results['files'], key=lambda x: x['extension']):
            f.write(f"{file['extension']}: {os.path.basename(file['path'])} ({file['size']})\n")
            f.write(f"  Source: {file['link']}\n")
        
    print(f"[+] Summary report saved to {report_path}")
    
    # Generate extension-specific reports
    extension_files = {}
    for file in results['files']:
        ext = file['extension']
        if ext not in extension_files:
            extension_files[ext] = []
        extension_files[ext].append(file)
    
    for ext, files in extension_files.items():
        ext_report_path = os.path.join(args.output, f"{ext}_files.txt")
        with open(ext_report_path, 'w') as f:
            f.write(f"FILES WITH EXTENSION .{ext}\n")
            f.write("="*50 + "\n\n")
            for file in files:
                f.write(f"{os.path.basename(file['path'])} ({file['size']})\n")
                f.write(f"  Source: {file['link']}\n")
                f.write(f"  Path: {file['path']}\n\n")
        print(f"[+] {ext} files report saved to {ext_report_path}")

if __name__ == "__main__":
    main()