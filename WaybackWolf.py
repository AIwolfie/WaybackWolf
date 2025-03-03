import argparse
import asyncio
import json
import re
import aiohttp
import aiofiles
from collections import defaultdict
from urllib.parse import urlparse
from tqdm import tqdm
from colorama import init, Fore, Style
import waybackpy
from concurrent.futures import ThreadPoolExecutor
import time
import logging
import openai
import ollama
from rich.console import Console
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

# Initialize colorama, logging, and rich console
init()
logging.basicConfig(filename='waybackwolf.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
console = Console()

# Tool name and credit text with ASCII art
TOOL_NAME = "WaybackWolf"
CREDIT_TEXT = f"""
[bold cyan]

 __    __            _                _     __    __      _  __ 
/ / /\ \ \__ _ _   _| |__   __ _  ___| | __/ / /\ \ \___ | |/ _|
\ \/  \/ / _` | | | | '_ \ / _` |/ __| |/ /\ \/  \/ / _ \| | |_ 
 \  /\  / (_| | |_| | |_) | (_| | (__|   <  \  /\  / (_) | |  _|
  \/  \/ \__,_|\__, |_.__/ \__,_|\___|_|\_\  \/  \/ \___/|_|_|  
               |___/                                            

[/]
[green]A fast and efficient tool for analyzing archived URLs, retrieving snapshots, and detecting sensitive data.[/]

[green]Developed by: [white]AIwolfie[/]
[green]Version: [white]1.0.0[/]
[green]GitHub: [white]https://github.com/AIwolfie/waybackwolf[/]
[green]License: [white]MIT[/]

[yellow]----------------------------------[/]
"""

# Extensions and set for fast lookup
EXTENSIONS = [
    '.xls', '.xml', '.xlsx', '.json', '.pdf', '.sql', '.doc', '.docx', '.pptx', '.txt',
    '.zip', '.tar.gz', '.tgz', '.bak', '.7z', '.rar', '.log', '.cache', '.secret', '.db',
    '.backup', '.yml', '.gz', '.config', '.csv', '.yaml', '.md', '.md5', '.exe', '.dll',
    '.bin', '.ini', '.bat', '.sh', '.tar', '.deb', '.rpm', '.iso', '.img', '.apk', '.msi',
    '.dmg', '.tmp', '.crt', '.pem', '.key', '.pub', '.asc'
]
EXTENSIONS_SET = set(EXTENSIONS)

# Cache and thread pool
WAYBACK_CACHE = {}
WAYBACK_EXECUTOR = ThreadPoolExecutor(max_workers=5)

# OpenAI setup (replace with your API key)
openai.api_key = "YOUR_OPENAI_API_KEY"

def get_extension(url):
    path = urlparse(url).path
    if '.' not in path:
        return None
    ext = '.' + path.split('.')[-1]
    return ext if ext in EXTENSIONS_SET else None

def matches_domain(url, domain):
    if not domain:
        return True
    return urlparse(url).netloc.endswith(domain)

async def read_urls(file_path):
    try:
        async with aiofiles.open(file_path, 'r') as f:
            async for line in f:
                url = line.strip()
                if url:
                    yield url
    except Exception as e:
        logging.error(f"Failed to read input file {file_path}: {e}")
        raise

def count_extensions(urls, domain=None):
    ext_counts = defaultdict(int)
    for url in urls:
        if matches_domain(url, domain):
            ext = get_extension(url)
            if ext:
                ext_counts[ext] += 1
    return ext_counts

async def fetch_content(url, session, semaphore):
    async with semaphore:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    return await response.text()
        except Exception as e:
            logging.error(f"Failed to fetch content from {url}: {e}")
    return None

async def analyze_content(content, ai_choice):
    prompt = "Analyze this content for sensitive or confidential data (e.g., PII, credentials, financial info). Provide a brief summary of findings."
    try:
        if ai_choice == "chatgpt":
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": f"{prompt}\n\n{content[:4000]}"}],
                max_tokens=200
            )
            return response.choices[0].message["content"]
        elif ai_choice == "deepseek":
            response = ollama.chat(model="deepseek-r1", messages=[{"role": "user", "content": f"{prompt}\n\n{content[:4000]}"}])
            return response["message"]["content"]
    except Exception as e:
        logging.error(f"AI analysis failed: {e}")
        return "Analysis failed due to error."
    return "No sensitive data detected."

async def check_url(url, session, semaphore, connect_timeout=5, read_timeout=10, retries=3, delay=2):
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.head(url, timeout=aiohttp.ClientTimeout(total=None, connect=connect_timeout, sock_read=read_timeout), allow_redirects=True, max_redirects=10) as response:
                    return response.status, None
            except aiohttp.ClientConnectionError:
                error = "ConnectionError"
            except aiohttp.ClientResponseError as e:
                error = str(e)
            except asyncio.TimeoutError:
                error = "Timeout"
            except Exception as e:
                error = str(e)
                logging.error(f"URL check failed for {url}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
                continue
        return None, f"{error} (Failed after {retries} retries)"

async def get_latest_snapshot(url, semaphore, max_retries=3, backoff=2):
    if url in WAYBACK_CACHE:
        return WAYBACK_CACHE[url]
    
    async with semaphore:
        loop = asyncio.get_event_loop()
        for attempt in range(max_retries):
            try:
                wayback = await loop.run_in_executor(WAYBACK_EXECUTOR, waybackpy.Url, url)
                latest = await loop.run_in_executor(WAYBACK_EXECUTOR, lambda w: w.newest(), wayback)
                snapshot_url = latest.url if latest else None
                WAYBACK_CACHE[url] = snapshot_url
                return snapshot_url
            except Exception as e:
                logging.error(f"Wayback snapshot failed for {url}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                WAYBACK_CACHE[url] = None
                return None

async def process_url(url, session, url_semaphore, wayback_semaphore, ai_choice=None, ai_extensions=None, live=None):
    ext = get_extension(url)
    if not ext:
        return None, None

    layout = Layout()
    layout.split_column(
        Layout(name="url", size=1),
        Layout(name="content", size=10),
        Layout(name="analysis", size=5)
    )
    layout["url"].update(Panel(f"Processing: {url}", style="cyan"))

    status, error = await check_url(url, session, url_semaphore)
    cli_result = ""
    json_result = {"url": url, "status": status, "error": error, "snapshot": None, "ai_analysis": None}
    content = None

    if status == 200:
        cli_result = f"{Fore.GREEN}✔ {url} - 200 OK (Accessible){Style.RESET_ALL}"
        if ai_choice and ext in ai_extensions:
            content = await fetch_content(url, session, url_semaphore)
    else:
        snapshot_url = await get_latest_snapshot(url, wayback_semaphore)
        error_msg = f"{status if status else error}"
        if snapshot_url:
            cli_result = f"{Fore.RED}✗ {url} - {error_msg} (Latest Snapshot: {Fore.CYAN}{snapshot_url}{Style.RESET_ALL})"
            json_result["snapshot"] = snapshot_url
            if ai_choice and ext in ai_extensions:
                content = await fetch_content(snapshot_url, session, url_semaphore)
        else:
            cli_result = f"{Fore.YELLOW}⚠ {url} - {error_msg} (No Snapshot Available){Style.RESET_ALL}"

    if content:
        layout["content"].update(Panel(content[:1000], title="Content Preview", style="yellow"))
        analysis = await analyze_content(content, ai_choice)
        layout["analysis"].update(Panel(analysis, title="AI Analysis", style="blue"))
        cli_result += f"\n    {Fore.BLUE}AI Analysis: {analysis}{Style.RESET_ALL}"
        json_result["ai_analysis"] = analysis
    else:
        layout["content"].update(Panel("No content available", style="yellow"))
        layout["analysis"].update(Panel("N/A", style="blue"))

    if live:
        with live:
            live.update(layout)
            await asyncio.sleep(2)

    return cli_result, json_result

async def process_urls(input_file, domain=None, output_file=None, json_file=None, max_workers=10, wayback_workers=5, ai_choice=None, ai_extensions=None, interactive=False):
    # Display credit text with ASCII art
    console.print(CREDIT_TEXT)

    urls = set()
    async for url in read_urls(input_file):
        urls.add(url)
    
    if domain:
        urls = {url for url in urls if matches_domain(url, domain)}

    if not urls:
        console.print(f"{Fore.RED}✗ No URLs found matching criteria!{Style.RESET_ALL}")
        return

    ext_counts = count_extensions(urls, domain)
    console.print(f"\n{Fore.CYAN}=== URL Breakdown by Extension ==={Style.RESET_ALL}")
    console.print(f"{Fore.YELLOW}----------------------------------{Style.RESET_ALL}")
    for ext, count in sorted(ext_counts.items()):
        console.print(f"{Fore.GREEN}{ext:<10}{Style.RESET_ALL} : {Fore.MAGENTA}{count:>3}{Style.RESET_ALL}")
    console.print(f"{Fore.YELLOW}----------------------------------{Style.RESET_ALL}")

    results = {"accessible": [], "inaccessible": []}
    url_semaphore = asyncio.Semaphore(max_workers)
    wayback_semaphore = asyncio.Semaphore(wayback_workers)
    console.print(f"\n{Fore.CYAN}=== Checking URL Status ==={Style.RESET_ALL}")

    async with aiohttp.ClientSession() as session:
        if interactive and (ai_choice or ai_extensions):
            live = Live(console=console, refresh_per_second=4)
            live.start()
            for url in urls:
                cli_result, json_result = await process_url(url, session, url_semaphore, wayback_semaphore, ai_choice, ai_extensions, live)
                if cli_result:
                    if "✔" in cli_result:
                        results["accessible"].append((cli_result, json_result))
                    else:
                        results["inaccessible"].append((cli_result, json_result))
            live.stop()
        else:
            tasks = [process_url(url, session, url_semaphore, wayback_semaphore, ai_choice, ai_extensions) for url in urls]
            for future in tqdm(asyncio.as_completed(tasks), total=len(urls), desc=f"{Fore.BLUE}Processing{Style.RESET_ALL}", bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}"):
                cli_result, json_result = await future
                if cli_result:
                    if "✔" in cli_result:
                        results["accessible"].append((cli_result, json_result))
                    else:
                        results["inaccessible"].append((cli_result, json_result))

    console.print(f"\n{Fore.CYAN}=== Results ==={Style.RESET_ALL}")
    console.print(f"{Fore.GREEN}Accessible URLs:{Style.RESET_ALL}")
    for cli_result, _ in sorted(results["accessible"]):
        console.print(cli_result)
    console.print(f"\n{Fore.RED}Inaccessible URLs:{Style.RESET_ALL}")
    for cli_result, _ in sorted(results["inaccessible"]):
        console.print(cli_result)

    if output_file:
        try:
            async with aiofiles.open(output_file, 'w') as f:
                plain_results = [re.sub(r'\x1b\[[0-9;]*m', '', cli_result) for cli_result, _ in results["accessible"] + results["inaccessible"]]
                await f.write("\n".join(plain_results))
            console.print(f"\n{Fore.GREEN}✓ Results saved to {output_file}{Style.RESET_ALL}")
        except Exception as e:
            logging.error(f"Failed to write to output file {output_file}: {e}")

    if json_file:
        try:
            async with aiofiles.open(json_file, 'w') as f:
                json_data = {
                    "accessible": [jr for _, jr in results["accessible"]],
                    "inaccessible": [jr for _, jr in results["inaccessible"]]
                }
                await f.write(json.dumps(json_data, indent=2))
            console.print(f"{Fore.GREEN}✓ JSON results saved to {json_file}{Style.RESET_ALL}")
        except Exception as e:
            logging.error(f"Failed to write to JSON file {json_file}: {e}")

def main():
    parser = argparse.ArgumentParser(
        description=f"A colorful CLI tool ({TOOL_NAME}) to analyze URLs by file extension, check their accessibility, retrieve Wayback Machine snapshots, and optionally analyze content with AI interactively.",
        epilog=f"Example: python waybackwolf.py -i out.txt -d example.com -o results.txt -j results.json -w 20 -ww 5 --ai chatgpt --extensions .sql .json --interactive"
    )
    parser.add_argument('-i', '--input', required=True, help="Path to the input file with URLs (one per line), e.g., 'out.txt'.")
    parser.add_argument('-o', '--output', help="Path to save plain text results (optional), e.g., 'results.txt'.")
    parser.add_argument('-j', '--json', help="Path to save JSON results (optional), e.g., 'results.json'.")
    parser.add_argument('-d', '--domain', help="Filter URLs by domain (including subdomains), e.g., 'example.com'.")
    parser.add_argument('-w', '--workers', type=int, default=10, help="Max concurrent URL checks (default: 10).")
    parser.add_argument('-ww', '--wayback-workers', type=int, default=5, help="Max concurrent Wayback requests (default: 5).")
    parser.add_argument('--ai', choices=['chatgpt', 'deepseek'], help="AI to use for content analysis: 'chatgpt' or 'deepseek'.")
    parser.add_argument('--extensions', nargs='+', help="List of extensions to analyze with AI, e.g., '.sql .json'.")
    parser.add_argument('--interactive', action='store_true', help="Enable interactive mode to display content and AI analysis on screen.")
    args = parser.parse_args()

    ai_extensions = set(args.extensions) if args.extensions else set()
    asyncio.run(process_urls(args.input, args.domain, args.output, args.json, args.workers, args.wayback_workers, args.ai, ai_extensions, args.interactive))

if __name__ == "__main__":
    main()