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
import datetime

# Define binary file extensions that typically trigger downloads
BINARY_EXTENSIONS = {
    'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',  # Office documents
    'pdf',  # PDF files
    'zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'tar.gz', 'bz2',  # Archives
    'exe', 'dll', 'bin', 'iso', 'img', 'dmg', 'apk', 'msi',  # Executables/images
    'db', 'sqlite', 'bak', 'backup',  # Database files
}

def colored_text(text, color_code):
    """Add color to terminal output"""
    return f"\033[{color_code}m{text}\033[0m"

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

def analyze_all_extensions(links):
    """Analyze all extensions in the links"""
    print(f"[+] Analyzing extensions in {len(links)} links...")
    extension_counter = Counter()
    
    for link in links:
        extension = get_extension(link)
        if extension:  # Only count if there's a valid extension
            extension_counter[extension] += 1
    
    return extension_counter

def get_extension(url):
    """Extract extension from URL, only accepting known file extensions"""
    path = urlparse(url).path
    filename = os.path.basename(path)
    
    if '.' not in filename or not filename:
        return ''
    
    extension = Path(filename).suffix.lower()
    if extension.startswith('.'):
        extension = extension[1:]
    
    # Handle special cases
    if filename.endswith('.tar.gz'):
        return 'tar.gz'
    elif filename.endswith('.7z'):
        return '7z'
    
    # Define valid extensions
    valid_extensions = set(BINARY_EXTENSIONS).union({
        'xls', 'xml', 'xlsx', 'json', 'pdf', 'sql', 'doc', 'docx', 'pptx', 
        'txt', 'zip', 'tar.gz', 'tgz', 'bak', '7z', 'rar', 'log', 'cache', 
        'secret', 'db', 'backup', 'yml', 'gz', 'config', 'csv', 'yaml', 'md', 
        'md5', 'exe', 'dll', 'bin', 'ini', 'bat', 'sh', 'tar', 'deb', 'rpm', 
        'iso', 'img', 'apk', 'msi', 'dmg', 'tmp', 'crt', 'pem', 'key', 'pub', 'asc'
    })
    
    return extension if extension in valid_extensions else ''

def get_wayback_snapshot(url):
    """Get the latest snapshot URL from web.archive.org"""
    archive_url = f"https://web.archive.org/web/timemap/link/{url}"
    
    try:
        response = requests.get(archive_url)
        if response.status_code == 200:
            lines = response.text.splitlines()
            for line in reversed(lines):
                if '; rel="memento"' in line:
                    snapshot_url = line.split('<')[1].split('>')[0]
                    return snapshot_url
        return None
    except Exception:
        return None

def verify_binary_file(data, extension):
    """Check if file content matches expected binary signature"""
    signatures = {
        'pdf': b'%PDF',
        'zip': b'PK\x03\x04',
        'rar': b'Rar!\x1a\x07',
        'doc': b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1',
        'xls': b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1',
        'docx': b'PK\x03\x04',
        'xlsx': b'PK\x03\x04',
    }
    
    if extension in signatures:
        return data.startswith(signatures[extension])
    elif extension in BINARY_EXTENSIONS:
        try:
            first_chars = data[:20].decode('utf-8', errors='strict').lower()
            if '<!doctype html' in first_chars or '<html' in first_chars:
                return False
            return True
        except UnicodeDecodeError:
            return True
    
    return True

def generate_filename(url, extension):
    """Generate a unique filename based on URL"""
    base = os.path.basename(urlparse(url).path)
    if base and '.' in base:
        return base
    
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    return f"{url_hash}.{extension}"

def download_content(url, output_dir, extension):
    """Download content from URL or its archive snapshot"""
    filename = generate_filename(url, extension)
    output_path = os.path.join(output_dir, extension, filename)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    error_message = None
    wayback_url = None
    
    try:
        response = requests.get(url, stream=True, timeout=15)
        if response.status_code == 200:
            if extension in BINARY_EXTENSIONS:
                content_preview = next(response.iter_content(chunk_size=1024), b'')
                if not verify_binary_file(content_preview, extension):
                    error_message = "Content doesn't match expected binary format"
                    raise ValueError(error_message)
                response = requests.get(url, stream=True, timeout=15)
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            if os.path.getsize(output_path) < 100:
                with open(output_path, 'rb') as f:
                    content = f.read()
                    if b'404' in content or b'not found' in content.lower():
                        os.remove(output_path)
                        error_message = "File too small or contains error message"
                        raise ValueError(error_message)
            
            return True, "direct", output_path, None, None
    except Exception as e:
        error_message = str(e) if not error_message else error_message
        if os.path.exists(output_path):
            os.remove(output_path)
    
    wayback_url = get_wayback_snapshot(url)
    if wayback_url:
        try:
            if extension in BINARY_EXTENSIONS:
                response = requests.get(wayback_url, stream=True, timeout=15, allow_redirects=True)
                content_preview = next(response.iter_content(chunk_size=1024), b'')
                if not verify_binary_file(content_preview, extension):
                    error_message = "Archived content doesn't match expected binary format"
                    raise ValueError(error_message)
                response = requests.get(wayback_url, stream=True, timeout=15)
                
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                if os.path.getsize(output_path) < 100:
                    with open(output_path, 'rb') as f:
                        content = f.read()
                        if b'404' in content or b'not found' in content.lower():
                            os.remove(output_path)
                            error_message = "Archived file too small or contains error message"
                            raise ValueError(error_message)
                
                return True, "archive", output_path, None, wayback_url
        except Exception as e:
            archive_error = str(e) if not error_message else error_message
            if os.path.exists(output_path):
                os.remove(output_path)
    
    return False, None, None, error_message, wayback_url

def process_link(link, output_dir):
    """Process a single link"""
    extension = get_extension(link)
    if not extension:
        return link, None, (False, None, None, f"No valid extension detected", None)
    
    success, source, filepath, error_message, wayback_url = download_content(link, output_dir, extension)
    file_info = None
    
    if success:
        try:
            size = os.path.getsize(filepath)
            file_info = {
                "size": size,
                "size_readable": f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/1024/1024:.1f} MB",
                "path": filepath
            }
        except:
            pass
            
    return link, extension, (success, source, file_info, error_message, wayback_url)

def save_links_by_extension(all_links, extension_counter, output_dir, target_extensions):
    """Save links by specified extensions to text files"""
    print("[+] Saving links by specified extensions to text files...")
    
    links_dir = os.path.join(output_dir, "links")
    os.makedirs(links_dir, exist_ok=True)
    
    all_links_path = os.path.join(links_dir, "all_links.txt")
    with open(all_links_path, 'w', encoding='utf-8') as f:      
        for link in all_links:
            f.write(f"{link}\n")
    
    for ext in target_extensions:
        ext_links = [link for link in all_links if get_extension(link) == ext]
        if ext_links:
            ext_path = os.path.join(links_dir, f"{ext}_links.txt")
            with open(ext_path, 'w', encoding='utf-8') as f:
                for link in ext_links:
                    f.write(f"{link}\n")
    
    return links_dir

def main():
    ascii_art = r"""
 __      __                  __                __      ___                             
/\ \  __/\ \                /\ \              /\ \   /'___\                            
\ \ \/\ \ \ \     __     __\ \ \____    __   \ \ \ /\ \__/  ___     ___      __   _ __  
 \ \ \ \ \ \ \  /'__`\ /'__`\ \ '__`\ /'__`\  \ \ \\ \ ,__\/ __`\ /' _ `\  /'__`\/\`'__\
  \ \ \_/ \_\ \/\  __//\ \L\ \ \ \L\ /\ \L\.\_\_\ \\ \ \_/\ \L\ \/\ \/\ \/\  __/\ \ \/ 
   \ `\___x___/\ \____\ \____/\ \_,__\ \__/.\_\____/ \ \_\\ \____/\ \_\ \_\ \____\\ \_\ 
    '\/__//__/  \/____/\/___/  \/___/ \/__/\/_/___/   \/_/ \/___/  \/_/\/_/\/____/ \/_/ 
    """
    
    credits = """
    Wayback Machine RECON Tool
    For OSINT and Security Research
    Created by: Security Researcher
    Version: 1.0.0
    """
    
    print(colored_text(ascii_art, "36"))
    print(colored_text(credits, "35"))
    
    parser = argparse.ArgumentParser(description="Wayback Machine RECON Tool")
    parser.add_argument("domain", help="Target domain (e.g., example.com)")
    parser.add_argument("-o", "--output", default="recon_output", help="Output directory")
    parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads")
    parser.add_argument("-f", "--filter", action="store_true", help="Only show summary, don't download files")
    parser.add_argument("-e", "--extensions", help="Comma-separated list of extensions to search for and download")
    parser.add_argument("-a", "--analyze-only", action="store_true", help="Only analyze extensions, don't download")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output")
    args = parser.parse_args()
    
    # Debug: Confirm filter flag status
    print(f"[DEBUG] Filter flag status: {args.filter}")
    
    default_extensions = [
        'xls', 'xml', 'xlsx', 'json', 'pdf', 'sql', 'doc', 'docx', 'pptx', 
        'txt', 'zip', 'tar.gz', 'tgz', 'bak', '7z', 'rar', 'log', 'cache', 
        'secret', 'db', 'backup', 'yml', 'gz', 'config', 'csv', 'yaml', 'md', 
        'md5', 'exe', 'dll', 'bin', 'ini', 'bat', 'sh', 'tar', 'deb', 'rpm', 
        'iso', 'img', 'apk', 'msi', 'dmg', 'tmp', 'crt', 'pem', 'key', 'pub', 'asc'
    ]
    
    start_time = time.time()
    print(f"[+] Starting recon on {args.domain} at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    all_links = get_domain_links(args.domain)
    if not all_links:
        print("[!] No links found. Exiting...")
        sys.exit(1)
    
    print(f"[+] Found {len(all_links)} total links")
    
    all_extensions_counter = analyze_all_extensions(all_links)
    
    print("\n=== Complete Extension Analysis ===")
    print(f"Found {len(all_extensions_counter)} unique extensions")
    
    max_ext_len = max([len(ext) for ext in all_extensions_counter.keys()]) if all_extensions_counter else 10
    max_count_len = max([len(str(count)) for count in all_extensions_counter.values()]) if all_extensions_counter else 5
    
    format_str = f"{{:{max_ext_len}}} | {{:{max_count_len}}} | {{}}"
    print(format_str.format("Extension", "Count", "Type"))
    print("-" * (max_ext_len + max_count_len + 15))
    
    for ext, count in sorted(all_extensions_counter.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            ext_type = "Binary" if ext in BINARY_EXTENSIONS else "Text"
            ext_color = "33" if ext in BINARY_EXTENSIONS else "32"
            print(format_str.format(colored_text(ext, ext_color), count, ext_type))
    
    extensions_to_process = []
    if args.extensions:
        extensions_to_process = args.extensions.split(',')
        print(f"\n[+] Filtering for extensions: {', '.join(extensions_to_process)}")
        filtered_links = filter_links_by_extension(all_links, extensions_to_process)
        print(f"[+] Found {len(filtered_links)} links with specified extensions")
    else:
        print("\n[+] Using default extensions for processing")
        filtered_links = filter_links_by_extension(all_links, default_extensions)
        extensions_to_process = default_extensions
        print(f"[+] Found {len(filtered_links)} links with default extensions")
    
    links_dir = save_links_by_extension(all_links, all_extensions_counter, args.output, extensions_to_process)
    print(f"[+] Saved links to {links_dir}")
    
    # Exit if filter mode is enabled (moved up to ensure no further processing)
    if args.filter:
        print("[+] Summary complete. Exiting as requested (--filter)")
        sys.exit(0)
    
    if args.analyze_only:
        print("[+] Analysis complete. Exiting as requested (--analyze-only)")
        sys.exit(0)
    
    # Download phase (should not execute if -f is used)
    output_dir = os.path.join(args.output, args.domain)
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"\n[+] Downloading files with workers: {args.threads}")
    
    success_count = 0
    archive_count = 0
    error_count = 0
    
    log_file = os.path.join(output_dir, "download_results.log")
    with open(log_file, 'w', encoding='utf-8') as log:
        log.write(f"Download results for {args.domain} - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.write("=" * 80 + "\n\n")
    
        with ThreadPoolExecutor(max_workers=args.threads) as executor:
            futures = {executor.submit(process_link, link, output_dir): link for link in filtered_links}
            
            for i, future in enumerate(futures):
                link = futures[future]
                try:
                    original_link, extension, result = future.result()
                    success, source, file_info, error_message, wayback_url = result
                    
                    if success:
                        success_count += 1
                        if source == "archive":
                            archive_count += 1
                        
                        log_entry = f"[SUCCESS] {original_link}\n"
                        log_entry += f"  Source: {source}\n"
                        log_entry += f"  Saved as: {file_info['path']}\n"
                        log_entry += f"  Size: {file_info['size_readable']}\n"
                        if wayback_url:
                            log_entry += f"  Archive URL: {wayback_url}\n"
                        log.write(log_entry + "\n")
                        
                        if args.verbose:
                            print(f"[{i+1}/{len(filtered_links)}] [SUCCESS] {source.upper()}: {original_link} -> {file_info['path']} ({file_info['size_readable']})")
                    else:
                        error_count += 1
                        
                        log_entry = f"[FAIL] {original_link}\n"
                        log_entry += f"  Error: {error_message}\n"
                        if wayback_url:
                            log_entry += f"  Archive URL available: {wayback_url}\n"
                        log.write(log_entry + "\n")
                        
                        if args.verbose:
                            print(f"[{i+1}/{len(filtered_links)}] [FAIL] {original_link} - {error_message}")
                
                except Exception as e:
                    error_count += 1
                    log.write(f"[ERROR] {link}\n  Exception: {str(e)}\n\n")
                    if args.verbose:
                        print(f"[{i+1}/{len(filtered_links)}] [ERROR] {link} - {str(e)}")
                
                if not args.verbose and (i+1) % 10 == 0:
                    print(f"Progress: {i+1}/{len(filtered_links)} links processed", end="\r")
    
    total_time = time.time() - start_time
    minutes, seconds = divmod(int(total_time), 60)
    
    print("\n" + "=" * 60)
    print(f"SUMMARY FOR: {args.domain}")
    print("=" * 60)
    print(f"Total Links: {len(all_links)}")
    print(f"Filtered Links: {len(filtered_links)}")
    print(f"Successfully Downloaded: {success_count} files")
    print(f"  - Direct Downloads: {success_count - archive_count}")
    print(f"  - Archive Downloads: {archive_count}")
    print(f"Failed Downloads: {error_count}")
    print(f"Time Elapsed: {minutes}m {seconds}s")
    print(f"Results saved to: {log_file}")
    print("=" * 60)
    
    html_report = os.path.join(output_dir, "recon_report.html")
    with open(html_report, 'w', encoding='utf-8') as report:
        report.write(f"""<!DOCTYPE html>
<html>
<head>
    <title>Wayback Machine Recon Report: {args.domain}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1, h2 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
        th, td {{ text-align: left; padding: 8px; border: 1px solid #ddd; }}
        th {{ background-color: #f2f2f2; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        .success {{ color: green; }}
        .fail {{ color: red; }}
        .archive {{ color: orange; }}
    </style>
</head>
<body>
    <h1>Wayback Machine Recon Report</h1>
    <p><strong>Domain:</strong> {args.domain}</p>
    <p><strong>Date:</strong> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <p><strong>Total Duration:</strong> {minutes}m {seconds}s</p>
    
    <h2>Statistics</h2>
    <ul>
        <li>Total Links Found: {len(all_links)}</li>
        <li>Links Processed: {len(filtered_links)}</li>
        <li>Successfully Downloaded: {success_count} files</li>
        <li>Failed Downloads: {error_count}</li>
    </ul>
    
    <h2>Extension Analysis</h2>
    <table>
        <tr>
            <th>Extension</th>
            <th>Count</th>
            <th>Type</th>
        </tr>
""")
        
        for ext, count in sorted(all_extensions_counter.items(), key=lambda x: x[1], reverse=True):
            if count > 0:
                ext_type = "Binary" if ext in BINARY_EXTENSIONS else "Text"
                report.write(f"        <tr><td>{ext}</td><td>{count}</td><td>{ext_type}</td></tr>\n")
        
        report.write("""    </table>
    
    <h2>Download Summary</h2>
    <p>Detailed results can be found in the download_results.log file.</p>
</body>
</html>
""")
    
    print(f"[+] HTML report saved to: {html_report}")
    print(f"[+] Recon complete. Exiting...")
    
if __name__ == "__main__":
    main()